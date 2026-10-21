"""The action queue: the console's writes, as files the worker drains.

A file queue under `<state_dir>/actions/` (docs/decisions.md ADR 142): the
board service `create`s an action, the worker `claim`s it, runs it, and
`finish`es it into `done/`. The directory a record sits in is its state;
moving it is an `os.rename`, which is atomic and succeeds once.

    tmp/       a record being written; never listed
    pending/   created, not yet taken
    claimed/   taken by the worker (running)
    done/      finished, whatever the status
    epics/     one run per started epic (ADR 218); not an action
    write.lock the creator's lock

One creator (`create`, under `write.lock`) and one mover (the worker): the
worker primitives take no lock. Stdlib only.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, NamedTuple

from dispatcher.board_mcp import THREAD
from dispatcher.tasks import _COMMIT_RE, MAX_COMMENT_CHARS

ACTIONS_DIR = "actions"
TMP, PENDING, CLAIMED, DONE = "tmp", "pending", "claimed", "done"
EPICS = "epics"
WRITE_LOCK = "write.lock"
VERSION = 1
STATUSES = ("pending", "running", "done", "failed", "cancelled", "interrupted")
FINAL = ("done", "failed", "cancelled", "interrupted")
WAITING = (None, "project", "thread", "account", "pool")
#: A start is not claimed while the primary account's free share is under its
#: floor, in percent (the pool's floor). A start names its own in
#: `params.floor`; null or absent is this.
DEFAULT_POOL_FLOOR_PCT = 5
VERDICTS = ("run", "deny", "allow")
ID_RE = re.compile(r"a-\d{8}T\d{12}Z-[0-9a-f]{4}")

#: The keys of a record, in the order they are written.
FIELDS = (
    "version", "id", "kind", "project", "task_id", "params", "actor", "note",
    "created_at", "claimed_at", "started_at", "finished_at", "status",
    "waiting", "result", "error",
)

UNKNOWN_KIND = "unknown kind"
_INTERRUPTED_VERDICT = (
    "interrupted before the verdict was applied; run `dispatch command-resume`"
)


class ActionRefused(Exception):
    """A create the queue will not take: 400 for a bad one, 409 for a clash."""

    def __init__(self, status: int, reason: str):
        super().__init__(f"{status}: {reason}")
        self.status = status
        self.reason = reason


def _utc_now() -> datetime:
    """The module's one clock seam."""
    return datetime.now(timezone.utc)


def _now() -> str:
    return _utc_now().isoformat()


def actions_dir(state_dir) -> Path:
    return Path(state_dir) / ACTIONS_DIR


def ensure_dirs(state_dir) -> Path:
    root = actions_dir(state_dir)
    for sub in (TMP, PENDING, CLAIMED, DONE):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def new_id() -> str:
    """`a-<UTC time>Z-<4 hex>`: names sort in creation order, as far as the
    clock is monotonic. `create` holds `write.lock`, so two creates do not
    share a microsecond in practice."""
    stamp = _utc_now().strftime("%Y%m%dT%H%M%S%f")
    return f"a-{stamp}Z-{secrets.token_hex(2)}"


# ---- kinds ----------------------------------------------------------------


def _is_text(value, *, whitespace_ok: bool = True) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return whitespace_ok or not any(c.isspace() for c in value)


def _slug_problem(label: str, value) -> list[str]:
    """The refusal of `project_keys._check_slug`, copied and not imported."""
    if not _is_text(value):
        return [f"{label} must be a non-empty string"]
    if "/" in value or "\\" in value or "\0" in value or value.startswith("."):
        return [f"{label} {value!r} is not a valid name"]
    return []


def _extra_keys(params: dict, allowed: tuple[str, ...]) -> list[str]:
    extra = sorted(set(params) - set(allowed))
    return [f"params has unexpected keys: {', '.join(extra)}"] if extra else []


def _both_required(record: dict) -> list[str]:
    problems = _slug_problem("project", record.get("project"))
    if not _is_text(record.get("task_id")):
        problems.append("task_id must be a non-empty string")
    return problems


def _validate_start(record: dict) -> list[str]:
    problems = _both_required(record)
    params = record["params"]
    problems += _extra_keys(params, ("limit", "thread", "turn", "epic", "floor"))
    # A task's own spending limit, in measured tokens (ADR 230); null is none.
    if not _limit_ok(params.get("limit")):
        problems.append(
            "params.limit must be null or a whole number of measured tokens of at least 1"
        )
    if not _floor_ok(params.get("floor")):
        problems.append("params.floor must be null or a whole percent from 0 to 100")
    # A start queued by *Start epic* names the epic it was queued for (ADR 207).
    if "epic" in params and not _is_text(params["epic"], whitespace_ok=False):
        problems.append("params.epic must be a task id")
    # A start queued by the thread's tool carries the thread and the turn that
    # authorized it; any other actor carries neither (ADR 187, 6).
    if record.get("actor") == THREAD:
        if not _is_text(params.get("thread")):
            problems.append("params.thread must be a non-empty string")
        turn = params.get("turn")
        if not isinstance(turn, str) or not ID_RE.fullmatch(turn):
            problems.append("params.turn must be an action id")
    else:
        for key in ("thread", "turn"):
            if key in params:
                problems.append(f"params.{key} is only for a start queued by the thread")
    return problems


def _validate_cancel(record: dict) -> list[str]:
    problems = []
    if record.get("project") is not None:
        problems.append("project must be null")
    if record.get("task_id") is not None:
        problems.append("task_id must be null")
    params = record["params"]
    problems += _extra_keys(params, ("target", "only_pending"))
    target = params.get("target")
    if not isinstance(target, str) or not ID_RE.fullmatch(target):
        problems.append("params.target must be an action id")
    if "only_pending" in params and params["only_pending"] is not True:
        problems.append("params.only_pending must be true when present")
    return problems


def _validate_verdict(record: dict) -> list[str]:
    problems = _both_required(record)
    params = record["params"]
    problems += _extra_keys(params, ("n", "verdict", "comment", "prefix"))
    n = params.get("n")
    if type(n) is not int or n < 1:
        problems.append("params.n must be an integer of at least 1")
    verdict = params.get("verdict")
    if verdict not in VERDICTS:
        problems.append(f"params.verdict must be one of {', '.join(VERDICTS)}")
    comment = params.get("comment")
    if comment is not None and (
        not isinstance(comment, str) or len(comment) > MAX_COMMENT_CHARS
    ):
        problems.append(
            f"params.comment must be null or text of at most "
            f"{MAX_COMMENT_CHARS} characters"
        )
    prefix = params.get("prefix")
    if prefix is not None:
        if type(prefix) is not int or prefix < 1:
            problems.append("params.prefix must be null or an integer of at least 1")
        elif verdict != "allow":
            problems.append("params.prefix only goes with the allow verdict")
    return problems


def _validate_approve(record: dict) -> list[str]:
    """An approve names the task and the commit the person's diff showed (ADR 258)."""
    problems = _both_required(record)
    params = record["params"]
    problems += _extra_keys(params, ("commit",))
    commit = params.get("commit")
    if not isinstance(commit, str) or not _COMMIT_RE.match(commit):
        problems.append("params.commit must be a commit hash")
    return problems


def _validate_turn(record: dict) -> list[str]:
    problems = []
    if record.get("task_id") is not None:
        problems.append("task_id must be null")
    if record.get("project") is not None:
        problems += _slug_problem("project", record["project"])
    params = record["params"]
    problems += _extra_keys(params, ("thread", "text"))
    for key in ("thread", "text"):
        if not _is_text(params.get(key)):
            problems.append(f"params.{key} must be a non-empty string")
    return problems


def _validate_project_setup(record: dict) -> list[str]:
    """`clone`, `init`, `keygen`, `check`: one project, no task (ADR 212).

    None of them carries a remote: the worker reads it from the project's
    record, which already refuses a credential (ADR 205), so no URL ever sits
    in an action file. Only `keygen` takes a param, whether to replace a key.
    """
    problems = _slug_problem("project", record.get("project"))
    if record.get("task_id") is not None:
        problems.append("task_id must be null")
    params = record["params"]
    if record.get("kind") == "keygen":
        problems += _extra_keys(params, ("replace",))
        if "replace" in params and type(params["replace"]) is not bool:
            problems.append("params.replace must be true or false")
    else:
        problems += _extra_keys(params, ())
    return problems


@dataclass(frozen=True)
class Kind:
    name: str
    #: The whole record -> its problems; `[]` is valid.
    validate: Callable[[dict], list[str]]
    #: What the worker serialises on; `None` runs beside anything.
    serial_key: Callable[[dict], str | None]
    spends_quota: bool
    #: "requeue_unless_started" | "interrupt" | "rerun"
    recovery: str
    #: The `error` line recovery writes when it interrupts.
    interrupted_error: str | None


KINDS: dict[str, Kind] = {
    k.name: k
    for k in (
        Kind(
            "start", _validate_start, lambda r: f"project:{r['project']}",
            True, "requeue_unless_started", "interrupted after it started",
        ),
        Kind("cancel", _validate_cancel, lambda r: None, False, "rerun", None),
        Kind(
            "command_verdict", _validate_verdict,
            lambda r: f"project:{r['project']}", True, "interrupt",
            _INTERRUPTED_VERDICT,
        ),
        Kind(
            "turn", _validate_turn, lambda r: f"thread:{r['params']['thread']}",
            True, "interrupt", "interrupted before the turn finished",
        ),
        # A new project's setup (ADR 212). A clone or an init makes the
        # project's directory, so it holds the project like a start does and
        # is not rerun after a crash: a half-made directory is the person's to
        # look at, and a rerun would only refuse it. A keygen may have written
        # the pair before the crash, so it is not rerun either; a check only
        # reads, so it is.
        Kind(
            "clone", _validate_project_setup, lambda r: f"project:{r['project']}",
            False, "interrupt",
            "interrupted during the clone; the project's directory may be partial",
        ),
        Kind(
            "init", _validate_project_setup, lambda r: f"project:{r['project']}",
            False, "interrupt",
            "interrupted during the init; the project's directory may be partial",
        ),
        Kind(
            "keygen", _validate_project_setup, lambda r: None, False, "interrupt",
            "interrupted during the keygen; look at the project's key before another",
        ),
        Kind("check", _validate_project_setup, lambda r: None, False, "rerun", None),
        # An approve merges and pushes the base (ADR 258). It serialises on the
        # project's base, takes no project flock and waits on no account; a
        # half-run merge is a person's to look at, so it is not rerun.
        Kind(
            "approve", _validate_approve, lambda r: f"base:{r['project']}",
            False, "interrupt",
            "interrupted during the approve; read the task's comments and the "
            "base before approving again",
        ),
    )
}

#: Kinds a second pending or claimed action for the same task is refused.
_ONE_PER_TASK = ("start", "command_verdict", "approve")
#: Kinds a second pending or claimed action for the same project is refused.
_ONE_PER_PROJECT = ("clone", "init", "keygen", "check")


def validate(record: dict) -> list[str]:
    """The problems with `record`; `[]` is valid. The worker calls it too."""
    kind = KINDS.get(record.get("kind"))
    if kind is None:
        return [UNKNOWN_KIND]
    problems = []
    if not isinstance(record.get("params"), dict):
        return ["params must be an object"]
    if not _is_text(record.get("actor"), whitespace_ok=False):
        problems.append("actor must be a non-empty string without whitespace")
    note = record.get("note")
    if note is not None:
        if kind.name != "start":
            problems.append("note is only for start")
        elif not isinstance(note, str) or len(note) > MAX_COMMENT_CHARS:
            problems.append(
                f"note must be null or text of at most {MAX_COMMENT_CHARS} characters"
            )
    return problems + kind.validate(record)


def serial_key(record: dict) -> str | None:
    return KINDS[record["kind"]].serial_key(record)


# ---- files ------------------------------------------------------------------


@contextmanager
def _write_locked(root: Path):
    fd = os.open(root / WRITE_LOCK, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _load(path: Path) -> dict:
    """The record at `path`; raises OSError or ValueError when it is not one."""
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    if data.get("version") != VERSION:
        raise ValueError(f"version {data.get('version')!r} is not {VERSION}")
    if data.get("id") != path.stem:
        raise ValueError("id does not match the file name")
    return data


def _store(root: Path, path: Path, record: dict) -> None:
    """Replace `path` with `record`, whole or not at all."""
    fd, tmp = tempfile.mkstemp(dir=root / TMP, prefix="rw-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(record))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Entry(NamedTuple):
    state: str  # "pending" | "claimed" | "done"
    record: dict


def _scan(root: Path, states) -> tuple[list[Entry], list[str]]:
    entries: list[Entry] = []
    warnings: list[str] = []
    for state in states:
        try:
            names = sorted(n for n in os.listdir(root / state) if n.endswith(".json"))
        except OSError as exc:
            warnings.append(f"{state}: cannot list: {exc}")
            continue
        for name in names:
            try:
                entries.append(Entry(state, _load(root / state / name)))
            except (OSError, ValueError) as exc:
                warnings.append(f"{state}/{name}: {exc}")
    return entries, warnings


def read_all(state_dir) -> tuple[list[Entry], list[str]]:
    """Every record in pending, claimed, done, and a warning per file skipped."""
    return _scan(ensure_dirs(state_dir), (PENDING, CLAIMED, DONE))


# ---- the creator ------------------------------------------------------------


def create(
    state_dir,
    kind: str,
    *,
    project=None,
    task_id=None,
    params=None,
    actor: str = "operator",
    note=None,
    ready: Callable[[str, str], bool] | None = None,
) -> dict:
    """Validate, then write a pending action; returns the record.

    `ready(project, task_id)` is the board service's check that a `start`'s
    task is in that project's Ready list.
    ADR 142 decision 7 has it made while the creator holds the lock, so it is
    called here, inside `write.lock`; `None` skips it.
    """
    record = _new_record(kind, project, task_id, params, actor, note)
    return _write_all(state_dir, [record], ready)[0]


def check_new(
    kind: str, *, project=None, task_id=None, params=None, actor: str = "operator", note=None
) -> None:
    """Raise `ActionRefused(400)` if `create` would refuse the record as malformed; write nothing."""
    _new_record(kind, project, task_id, params, actor, note)


def create_starts(
    state_dir,
    project,
    task_ids: list[str],
    *,
    params=None,
    actor: str = "operator",
    note=None,
    ready: Callable[[str, str], bool] | None = None,
) -> list[dict]:
    """One pending `start` per task in `task_ids`, in that order, or none (ADR 207).

    *Start epic*'s creator. Each record is checked as `create` checks one, all
    under one hold of `write.lock` and before any is written, so a refusal of
    one task refuses the batch and nothing is queued. The ids are taken in the
    list's order and each sorts after the one before, so the worker, which
    scans `pending/` by name, considers them in that order. Every record gets
    its own copy of `params` and the same `note`.
    """
    if not task_ids:
        raise ActionRefused(409, "no task to start")
    if len(set(task_ids)) != len(task_ids):
        raise ActionRefused(400, "a task is named more than once")
    records = [
        _new_record("start", project, task_id, dict(params or {"limit": None}), actor, note)
        for task_id in task_ids
    ]
    return _write_all(state_dir, records, ready)


def create_epic_cancels(
    state_dir, epic_id: str, *, actor: str = "operator", note=None
) -> dict:
    """*Stop epic*'s creator (ADR 211): one `cancel` per start of `epic_id` still waiting.

    Under one hold of `write.lock` it reads the starts *Start epic* queued for
    the epic (`params.epic`), and writes a cancel for each one in `pending/`
    that no pending cancel already names; each carries `only_pending`, so a
    start the worker takes before its cancel runs is left running (ADR 142:
    the worker stays the only mover, so nothing here moves a file). The
    starts in `claimed/` are left alone and named. Returns
    `{"cancels", "dropped", "left"}`: the cancels written, the start records
    they drop, and the start records running; with only running ones there
    are no cancels and nothing is written. Refused with 409 when the epic has
    no start pending or claimed, or when each pending one already has its
    cancel queued (a second press before the worker ran the first).

    The epic's run, when it has one still going, is marked stopped in the
    same hold, so no merge queues another of its tasks (ADR 218). A run
    still going with nothing queued (between one task's merge and the next
    one's start) is stopped and answered with empty lists, not refused.
    """
    root = ensure_dirs(state_dir)
    with _write_locked(root):
        held, _ = _scan(root, (PENDING, CLAIMED))
        named = {
            e.record["params"].get("target")
            for e in held
            if e.state == PENDING and e.record.get("kind") == "cancel"
        }
        mine = [
            e for e in held
            if e.record.get("kind") == "start"
            and (e.record.get("params") or {}).get("epic") == epic_id
        ]
        dropped = [
            e.record for e in mine
            if e.state == PENDING and e.record.get("id") not in named
        ]
        left = [e.record for e in mine if e.state == CLAIMED]
        run = _read_run(root, epic_id)
        going = run is not None and run.get("stopped_at") is None
        if not mine and not going:
            raise ActionRefused(409, f"epic {epic_id} has no start queued")
        if not dropped and not left and not going:
            raise ActionRefused(
                409, f"every start queued for epic {epic_id} already has a cancel queued"
            )
        records = [
            _new_record(
                "cancel", None, None,
                {"target": start["id"], "only_pending": True}, actor, note,
            )
            for start in dropped
        ]
        _place(root, records)
        if going:
            run["stopped_at"] = _now()
            _store_run(root, run)
    return {"cancels": records, "dropped": dropped, "left": left}


def _new_record(kind, project, task_id, params, actor, note) -> dict:
    """A pending record with no id yet; raises ActionRefused(400) when it is not valid."""
    if kind not in KINDS:
        raise ActionRefused(400, f"unknown kind {kind!r}")
    if kind == "start" and not params:
        params = {"limit": None}
    record = {
        "version": VERSION, "id": None, "kind": kind, "project": project,
        "task_id": task_id, "params": params, "actor": actor, "note": note,
        "created_at": _now(), "claimed_at": None, "started_at": None,
        "finished_at": None, "status": "pending", "waiting": None,
        "result": None, "error": None,
    }
    problems = validate(record)
    if problems:
        raise ActionRefused(400, "; ".join(problems))
    return record


def _refuse_locked(root: Path, record: dict, ready) -> None:
    """The checks made under `write.lock`: one start (verdict, approve) per task,
    one setup action of each kind per project, and Ready."""
    kind, project, task_id = record["kind"], record["project"], record["task_id"]
    if kind in _ONE_PER_TASK:
        held, _ = _scan(root, (PENDING, CLAIMED))
        if any(
            e.record.get("kind") == kind
            and e.record.get("project") == project
            and e.record.get("task_id") == task_id
            for e in held
        ):
            raise ActionRefused(409, f"a {kind} for task {task_id} is already queued")
    if kind in _ONE_PER_PROJECT:
        held, _ = _scan(root, (PENDING, CLAIMED))
        if any(
            e.record.get("kind") == kind and e.record.get("project") == project
            for e in held
        ):
            raise ActionRefused(409, f"a {kind} for project {project} is already queued")
    if kind == "start" and ready is not None and not ready(project, task_id):
        raise ActionRefused(409, f"task {task_id} is not in Ready")


def _write_all(state_dir, records: list[dict], ready) -> list[dict]:
    """Check, then write `records` into `pending/` in their order: all, or none refused."""
    root = ensure_dirs(state_dir)
    with _write_locked(root):
        for record in records:
            _refuse_locked(root, record, ready)
        _place(root, records)
    return records


def _place(root: Path, records: list[dict]) -> None:
    """Give `records` their ids and write them into `pending/`; `write.lock` is held."""
    # The ids are taken under the lock, so ids sort in the order creates land;
    # within one batch each is made to sort after the one before.
    last = None
    for record in records:
        record["id"] = new_id()
        while last is not None and record["id"] <= last:
            record["id"] = new_id()
        last = record["id"]
    tmps = [root / TMP / f"{record['id']}.json" for record in records]
    try:
        for tmp, record in zip(tmps, records):
            tmp.write_text(json.dumps(record))
        for tmp in tmps:
            os.rename(tmp, root / PENDING / tmp.name)
    except BaseException:
        for tmp in tmps:
            tmp.unlink(missing_ok=True)
        raise


# ---- epic runs --------------------------------------------------------------
#
# A started epic's run (ADR 218): `epics/<epic id>.json`, written under
# `write.lock` by *Start epic*, *Stop epic* and each merge's follow-on, and
# read by the console. It is not an action and the worker never reads it.
#
# `limit` is the epic's spending limit in measured tokens (ADR 79's count) or
# null; `floor` is the pool floor each of its starts carries, in percent, or
# null for the default; `paused` is null or "limit_reached"; `queued` maps
# each task the run queued a start for to `{"action", "usage_offset", "at"}`,
# the offset being
# the length of the task's usage log when it was first queued, so the run's
# spend is what each task's log wrote after it.

PAUSED_LIMIT = "limit_reached"
RUN_FIELDS = (
    "version", "epic", "project", "note", "actor", "limit", "floor", "started_at",
    "stopped_at", "paused", "paused_at", "queued",
)


def _run_path(root: Path, epic_id: str) -> Path:
    if _slug_problem("epic", epic_id):
        raise ActionRefused(400, f"epic {epic_id!r} is not a task id")
    return root / EPICS / f"{epic_id}.json"


def _load_run(path: Path) -> dict:
    """The run at `path`; raises OSError or ValueError when it is not one."""
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict) or data.get("version") != VERSION:
        raise ValueError(f"not a version {VERSION} epic run")
    if data.get("epic") != path.stem or not isinstance(data.get("queued"), dict):
        raise ValueError("not the epic run of this file's name")
    return data


def _read_run(root: Path, epic_id: str) -> dict | None:
    """The epic's run, or None when it has none (or one that will not load)."""
    try:
        return _load_run(_run_path(root, epic_id))
    except (OSError, ValueError, ActionRefused):
        return None


def _store_run(root: Path, run: dict) -> None:
    (root / EPICS).mkdir(exist_ok=True)
    _store(root, _run_path(root, run["epic"]), {k: run.get(k) for k in RUN_FIELDS})


def read_epic_runs(state_dir) -> tuple[list[dict], list[str]]:
    """Every epic run, by epic id, and a warning per file skipped.

    It makes no directory: the console's API reads the state dir read-only.
    """
    root = actions_dir(state_dir) / EPICS
    try:
        names = sorted(n for n in os.listdir(root) if n.endswith(".json"))
    except FileNotFoundError:
        return [], []
    except OSError as exc:
        return [], [f"{EPICS}: cannot list: {exc}"]
    runs, warnings = [], []
    for name in names:
        try:
            runs.append(_load_run(root / name))
        except (OSError, ValueError) as exc:
            warnings.append(f"{EPICS}/{name}: {exc}")
    return runs, warnings


def read_epic_run(state_dir, epic_id: str) -> dict | None:
    return _read_run(actions_dir(state_dir), epic_id)


def _floor_ok(floor) -> bool:
    return floor is None or (type(floor) is int and 0 <= floor <= 100)


def check_floor(floor) -> None:
    """A pool floor is null (the default) or a whole percent from 0 to 100."""
    if not _floor_ok(floor):
        raise ActionRefused(400, "floor must be null or a whole percent from 0 to 100")


def start_floor(record: dict) -> int:
    """The floor `record`, a start, waits on: its own, or the default."""
    floor = (record.get("params") or {}).get("floor")
    return DEFAULT_POOL_FLOOR_PCT if floor is None else floor


def _start_params(epic_id: str, floor) -> dict:
    """An epic run's start params; `floor` only when the run names one."""
    params = {"limit": None, "epic": epic_id}
    if floor is not None:
        params["floor"] = floor
    return params


def _limit_ok(limit) -> bool:
    return limit is None or (type(limit) is int and limit >= 1)


def start_limit(record: dict) -> int | None:
    """The spending limit `record`, a start, runs under (ADR 230): its own, or none."""
    return (record.get("params") or {}).get("limit")


def check_limit(limit) -> None:
    """A spending limit is null or a whole number of measured tokens, at least 1."""
    if not _limit_ok(limit):
        raise ActionRefused(
            400, "limit must be null or a whole number of measured tokens of at least 1"
        )


def start_epic_run(
    state_dir,
    epic_id: str,
    project,
    task_ids: list[str],
    *,
    offset: Callable[[str], int],
    spent: Callable[[dict], int],
    limit=None,
    floor=None,
    note=None,
    actor: str = "operator",
    ready: Callable[[str, str], bool] | None = None,
) -> tuple[list[dict], dict]:
    """*Start epic* with its run (ADR 218): the starts and the run, in one hold.

    A first press, or one after *Stop epic* (which begins a new run, its
    spend from zero), is `create_starts`' all or none over `task_ids`. A
    press on a run paused at its limit is *Resume*: a task that already has
    a start queued is skipped rather than refused, and none
    left is not a refusal, since the new limit is what was asked. Either way
    the run takes the new `limit` and is going again; a limit the run has
    already spent (`spent(run)`, its measured tokens so far) is refused with
    409. A task the run queued before keeps its usage offset, so a resumed
    run's spend counts from its first start. `offset(task_id)` is the length of
    the task's usage log now. `floor` is the run's pool floor, null for the
    default; like the limit, each press sets it, and each start carries it.
    """
    check_limit(limit)
    check_floor(floor)
    root = ensure_dirs(state_dir)
    with _write_locked(root):
        run = _read_run(root, epic_id)
        going = run is not None and run.get("stopped_at") is None
        # Paused, or at its limit before a merge looked (the pause is lazy).
        resume = going and (
            run.get("paused") is not None
            or (run.get("limit") is not None and spent(run) >= run["limit"])
        )
        if run is None or run.get("stopped_at") is not None:
            # A stopped run is over: a press after *Stop epic* starts a new one.
            run = {"version": VERSION, "epic": epic_id, "started_at": _now(), "queued": {}}
        if limit is not None and run["queued"]:
            so_far = spent(run)
            if so_far >= limit:
                raise ActionRefused(
                    409, f"the limit is already spent: {so_far} measured tokens of {limit}"
                )
        if resume:
            held, _ = _scan(root, (PENDING, CLAIMED))
            busy = {
                e.record.get("task_id") for e in held if e.record.get("kind") == "start"
            }
            task_ids = [tid for tid in task_ids if tid not in busy]
        elif not task_ids:
            raise ActionRefused(409, "no task to start")
        if len(set(task_ids)) != len(task_ids):
            raise ActionRefused(400, "a task is named more than once")
        records = [
            _new_record("start", project, tid, _start_params(epic_id, floor), actor, note)
            for tid in task_ids
        ]
        for record in records:
            _refuse_locked(root, record, ready)
        _place(root, records)
        run.update(
            project=project, note=note, actor=actor, limit=limit, floor=floor,
            stopped_at=None, paused=None, paused_at=None,
        )
        _note_queued(run, records, offset)
        _store_run(root, run)
    return records, run


def _note_queued(run: dict, records: list[dict], offset: Callable[[str], int]) -> None:
    """Each start's task into the run; a task queued before keeps its offset."""
    for record in records:
        before = run["queued"].get(record["task_id"]) or {}
        offset_now = before.get("usage_offset")
        run["queued"][record["task_id"]] = {
            "action": record["id"],
            "usage_offset": offset(record["task_id"]) if offset_now is None else offset_now,
            "at": _now(),
        }


def follow_epic_run(
    state_dir,
    epic_id: str,
    candidates: Callable[[], list[str]],
    *,
    offset: Callable[[str], int],
    spent: Callable[[dict], int],
) -> dict:
    """A merge's follow-on for one epic (ADR 218): queue its tasks now Ready.

    Under one hold of `write.lock`: nothing when the epic has no run, or its
    run is stopped or paused. A run whose spend (`spent(run)`) has reached
    its limit is paused instead, and nothing is queued. Otherwise a `start`
    is queued, as *Start epic* queues one, for each of `candidates()` (the
    epic's tasks in Ready, in the board's order, read inside the hold) that
    the run never queued and that no start pending or claimed names. So a
    task is queued once by its run, however many merges see it Ready; one
    whose phase failed back to Ready is the person's to start again.
    Returns `{"queued", "paused"}`: the starts written, and whether the run
    paused here.
    """
    root = ensure_dirs(state_dir)
    with _write_locked(root):
        run = _read_run(root, epic_id)
        if run is None or run.get("stopped_at") is not None or run.get("paused"):
            return {"queued": [], "paused": False}
        limit = run.get("limit")
        if limit is not None and spent(run) >= limit:
            run.update(paused=PAUSED_LIMIT, paused_at=_now())
            _store_run(root, run)
            return {"queued": [], "paused": True}
        held, _ = _scan(root, (PENDING, CLAIMED))
        busy = {e.record.get("task_id") for e in held if e.record.get("kind") == "start"}
        records = [
            _new_record(
                "start", run.get("project"), tid, _start_params(epic_id, run.get("floor")),
                run.get("actor") or "operator", run.get("note"),
            )
            for tid in candidates()
            if tid not in run["queued"] and tid not in busy
        ]
        if records:
            _place(root, records)
            _note_queued(run, records, offset)
            _store_run(root, run)
    return {"queued": records, "paused": False}


# ---- the worker -------------------------------------------------------------


def _find(root: Path, action_id: str, states) -> Path | None:
    for state in states:
        path = root / state / f"{action_id}.json"
        if path.exists():
            return path
    return None


def find(state_dir, action_id: str) -> Entry | None:
    """The record of `action_id` in pending, claimed or done, or None.

    None also when the file is unreadable; the queue's own warnings are
    `read_all`'s. An id that is not an action id is never looked up, so a
    caller's text cannot name a path.
    """
    if not isinstance(action_id, str) or not ID_RE.fullmatch(action_id):
        return None
    root = actions_dir(state_dir)
    for state in (PENDING, CLAIMED, DONE):
        try:
            return Entry(state, _load(root / state / f"{action_id}.json"))
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            return None
    return None


def rewrite(state_dir, record: dict) -> None:
    """Replace a pending or claimed record in place. FileNotFoundError if it
    is in neither."""
    root = ensure_dirs(state_dir)
    path = _find(root, record["id"], (PENDING, CLAIMED))
    if path is None:
        raise FileNotFoundError(record["id"])
    _store(root, path, record)


def finish(
    state_dir, action_id: str, status: str, *, result=None, error=None
) -> dict:
    """Write the final status and move the record to `done/`.

    Rewrite, then rename: a crash between leaves a final record in its old
    directory (`pending/` or `claimed/`), which `recover` moves on and `claim`
    refuses.
    """
    if status not in FINAL:
        raise ValueError(f"{status!r} is not a final status")
    root = ensure_dirs(state_dir)
    path = _find(root, action_id, (CLAIMED, PENDING))
    if path is None:
        raise FileNotFoundError(action_id)
    record = _load(path)
    record.update(status=status, finished_at=_now(), result=result, error=error)
    _store(root, path, record)
    os.rename(path, root / DONE / path.name)
    return record


def claim(state_dir, action_id: str) -> dict | None:
    """Take a pending action: it succeeds once. None if it is already taken,
    gone, unreadable (left in `claimed/`), already final (moved to `done/`
    unchanged) or invalid (finished as failed)."""
    root = ensure_dirs(state_dir)
    try:
        os.rename(root / PENDING / f"{action_id}.json", root / CLAIMED / f"{action_id}.json")
    except FileNotFoundError:
        return None
    path = root / CLAIMED / f"{action_id}.json"
    try:
        record = _load(path)
    except (OSError, ValueError):
        return None
    if record.get("status") in FINAL:
        # `finish` rewrote it and crashed before the rename: it is over.
        os.rename(path, root / DONE / path.name)
        return None
    problems = validate(record)
    if problems:
        finish(state_dir, action_id, "failed", error="; ".join(problems))
        return None
    record.update(status="running", claimed_at=_now(), waiting=None)
    _store(root, path, record)
    return record


def recover(state_dir) -> list[tuple[str, str]]:
    """Apply each kind's rule to what a dead worker left behind. Run it before
    the first loop. Returns `(id, what happened)`. Unparseable files stay where
    they are; `read_all` names them."""
    root = ensure_dirs(state_dir)
    entries, _ = _scan(root, (PENDING, CLAIMED))
    done: list[tuple[str, str]] = []
    for state, record in entries:
        aid = record["id"]
        kind = KINDS.get(record.get("kind"))
        if record.get("status") in FINAL:
            os.rename(root / state / f"{aid}.json", root / DONE / f"{aid}.json")
            done.append((aid, "moved to done"))
        elif kind is None:
            finish(state_dir, aid, "failed", error=UNKNOWN_KIND)
            done.append((aid, "failed: unknown kind"))
        elif state == CLAIMED:
            done.append((aid, _recover_claimed(state_dir, root, kind, record)))
    return done


def _recover_claimed(state_dir, root: Path, kind: Kind, record: dict) -> str:
    aid = record["id"]
    if kind.recovery == "interrupt" or (
        kind.recovery == "requeue_unless_started" and record.get("started_at")
    ):
        finish(state_dir, aid, "interrupted", error=kind.interrupted_error)
        return "interrupted"
    record.update(status="pending", claimed_at=None, waiting=None)
    path = root / CLAIMED / f"{aid}.json"
    _store(root, path, record)
    os.rename(path, root / PENDING / path.name)
    return "back to pending"
