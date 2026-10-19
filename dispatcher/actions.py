"""The action queue: the console's writes, as files the worker drains.

A file queue under `<state_dir>/actions/` (docs/decisions.md ADR 142): the
board service `create`s an action, the worker `claim`s it, runs it, and
`finish`es it into `done/`. The directory a record sits in is its state;
moving it is an `os.rename`, which is atomic and succeeds once.

    tmp/       a record being written; never listed
    pending/   created, not yet taken
    claimed/   taken by the worker (running)
    done/      finished, whatever the status
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

from dispatcher.tasks import MAX_COMMENT_CHARS

ACTIONS_DIR = "actions"
TMP, PENDING, CLAIMED, DONE = "tmp", "pending", "claimed", "done"
WRITE_LOCK = "write.lock"
VERSION = 1
STATUSES = ("pending", "running", "done", "failed", "cancelled", "interrupted")
FINAL = ("done", "failed", "cancelled", "interrupted")
WAITING = (None, "project", "thread", "account")
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
    problems += _extra_keys(params, ("limit",))
    if params.get("limit") is not None:
        problems.append("params.limit must be null")
    return problems


def _validate_cancel(record: dict) -> list[str]:
    problems = []
    if record.get("project") is not None:
        problems.append("project must be null")
    if record.get("task_id") is not None:
        problems.append("task_id must be null")
    params = record["params"]
    problems += _extra_keys(params, ("target",))
    target = params.get("target")
    if not isinstance(target, str) or not ID_RE.fullmatch(target):
        problems.append("params.target must be an action id")
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
    )
}

#: Kinds a second pending or claimed action for the same task is refused.
_ONE_PER_TASK = ("start", "command_verdict")


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
    root = ensure_dirs(state_dir)
    with _write_locked(root):
        if kind in _ONE_PER_TASK:
            held, _ = _scan(root, (PENDING, CLAIMED))
            if any(
                e.record.get("kind") == kind
                and e.record.get("project") == project
                and e.record.get("task_id") == task_id
                for e in held
            ):
                raise ActionRefused(409, f"a {kind} for task {task_id} is already queued")
        if kind == "start" and ready is not None and not ready(project, task_id):
            raise ActionRefused(409, f"task {task_id} is not in Ready")
        # The id is taken under the lock, so ids sort in the order creates land.
        record["id"] = new_id()
        tmp = root / TMP / f"{record['id']}.json"
        try:
            tmp.write_text(json.dumps(record))
            os.rename(tmp, root / PENDING / tmp.name)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    return record


# ---- the worker -------------------------------------------------------------


def _find(root: Path, action_id: str, states) -> Path | None:
    for state in states:
        path = root / state / f"{action_id}.json"
        if path.exists():
            return path
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
