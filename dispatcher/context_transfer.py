from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import re
import tempfile
from pathlib import Path

import yaml

_FRONTMATTER_DELIM = "---"


class _TaskDumper(yaml.SafeDumper):
    """SafeDumper that emits multi-line strings as block scalars.

    safe_dump's default for a multi-line value is a single-quoted scalar
    with the newlines folded, which makes the task file unreadable for
    anyone debugging a run. The frontmatter no longer carries the task
    description (`dispatcher/tasks.py` keeps it in its own file); block
    style is kept for any multi-line value, and `tasks.migrate`'s rewrite
    of a task file rests on it. Subclassed rather than registered on
    yaml.SafeDumper so the other safe_dump callers in this repo keep the
    stock behaviour. Fidelity is unaffected either way: the emitter falls
    back to a quoted style on its own whenever a block scalar can't
    represent the value (trailing spaces, \\r, and similar).
    """


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_TaskDumper.add_representer(str, _represent_str)


class LockHeldError(RuntimeError):
    """Raised when `tasks.acquire_lock` is asked to take a task whose lock is live under a different owner."""

    def __init__(self, task_id: str, owner: str) -> None:
        super().__init__(f"task {task_id} is locked by {owner}")
        self.task_id = task_id
        self.owner = owner


@dataclasses.dataclass
class TaskFile:
    task_id: str
    status: str
    owner: str | None
    depends_on: list[str]
    heartbeat: str | None
    body: str
    # The debt entries this task's implementation says it resolved. Kept here
    # because the task file is what survives a restart: the merge can happen
    # in a later process — `merge-task` by hand, days after the cycle — and
    # by then the handoff that claimed them is only prose.
    resolved_debt: list[str] = dataclasses.field(default_factory=list)
    # The epic format of `docs/plans/task-split.md` section 2, each written
    # only when set. On a sub-task, the id of the task it was split from.
    epic: str | None = None
    # On the parent, the ids of its sub-tasks in the order the split gave them.
    split_into: list[str] = dataclasses.field(default_factory=list)
    # {"into": <branch>, "at": <ISO-8601 UTC>}: a dependency counts once this
    # is there, not at `done`.
    merged: dict | None = None
    # Why the task stopped blocked, when a branch of the cycle says so: today
    # only `command_request` (ADR 117). Written only when set, and cleared when
    # the task is taken again.
    blocked_reason: str | None = None
    # The operator's verdicts on this task's command requests (ADR 119), one
    # `{"n", "verdict", "by", "at", "comment", "role", "filed_at"}` per block,
    # so a second verdict on the same block is refused. Written only when set.
    command_verdicts: list[dict] = dataclasses.field(default_factory=list)
    # The role of the phase running on the task now, `arquitecto` to
    # `integrador` (ADR 126). It goes with the lock: `acquire_lock` writes it,
    # and every writer that drops the lock clears it, so a value here with an
    # expired heartbeat is a dead dispatcher's, not a running phase. Written
    # only when set.
    phase: str | None = None


def hive_root(hive_dir: str) -> str:
    """The directory the task files and the learnings share.

    Derived, not configured, for the reason `learnings.root_dir` gives: both
    sides of the mount have to agree, and a second config key is a second
    thing that can disagree. The dispatcher hands this to a phase as an extra
    allowed directory — a role is told to read its task file and to file a
    learnings entry, and both sit outside the worktree, where the file tools
    refuse them.
    """
    return os.path.dirname(os.path.normpath(hive_dir))


def task_file_path(hive_dir: str, task_id: str) -> str:
    return os.path.join(hive_dir, f"{task_id}.md")


def scratch_dir(hive_dir: str, task_id: str) -> str:
    """Where a phase puts detail that only this task's later rounds need.

    Per-round working notes — review findings, test logs, a scratch plan —
    are too long to hand off and too short-lived for the repo, so they go
    here and the handoff cites the path. It sits beside the task file rather
    than inside the project: a reviewing role's checkout is rebuilt every
    round, so anything written there is gone by the next one.

    Safe as a sibling of `<task_id>.md` because `list_task_ids` only counts
    `.md` files, and `.hive/` is gitignored.
    """
    return os.path.join(hive_dir, task_id)


#: The mode of a directory the dispatcher creates under the hive tasks dir:
#: `_write_atomic`'s 0644 with the traverse bit, for the same readers.
_DIR_MODE = 0o755


def _makedirs_readable(hive_dir: str, path: str) -> None:
    """`os.makedirs(path, exist_ok=True)`, leaving what it made 0755 whatever the umask.

    The directory half of the rule `_write_atomic` and `append_usage` keep for
    files (`docs/decisions.md` ADR 55): a 0644 file in a directory made at a
    077 umask is still closed to `observability/api/` over its `:ro` mount as
    another uid, and to an agent container that is not root
    (`docs/learnings/a-0644-file-under-hive-still-sits-in-a-umask-directory.md`).

    Only below `hive_dir`, and only what this call created. The hive tasks dir
    itself is made, if missing, at the umask as before: it and everything above
    it belong to whoever set up `.hive/`, and widening them is not this
    module's call. Each component under it is `os.mkdir`ed on its own, and a
    `FileExistsError` — there already, or made by another process a moment ago
    — leaves its mode alone, so this never changes a directory some other uid
    owns, and the `exist_ok` race has one answer: whoever's `mkdir` won owns
    the mode. The chmod is on a descriptor opened `O_NOFOLLOW`, because the
    scratch dir is writable by the phases and a name swapped for a symlink in
    the window between `mkdir` and chmod must not redirect it.

    Raises `ValueError` for a `path` that is not under `hive_dir`, and `OSError`
    to its caller, chmod failures included: `append_usage`'s rule, so each
    caller decides whether its write may fail on this — `_UsageRecorder`
    swallows, `save_handoff` and the cycle's `ensure_scratch_dir` do not.
    """
    hive = os.path.abspath(hive_dir)
    rel = os.path.relpath(os.path.abspath(path), hive)
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        raise ValueError(f"{path!r} is not under the hive tasks dir {hive_dir!r}")
    os.makedirs(hive, exist_ok=True)
    if rel == os.curdir:
        return
    current = hive
    for part in rel.split(os.sep):
        current = os.path.join(current, part)
        try:
            os.mkdir(current, _DIR_MODE)
        except FileExistsError:
            continue
        fd = os.open(current, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fchmod(fd, _DIR_MODE)  # The umask masked the mode above.
        finally:
            os.close(fd)


def ensure_scratch_dir(hive_dir: str, task_id: str) -> str:
    path = scratch_dir(hive_dir, task_id)
    _makedirs_readable(hive_dir, path)
    return path


#: Where the dispatcher keeps its own copy of what a phase returned, under the
#: task's scratch dir. Namespaced into a subdirectory rather than dropped in
#: beside the roles' notes: the scratch dir is handed to every phase as a
#: writable directory, and a role that decides to call its own file
#: `revisor.json` must not be able to overwrite the dispatcher's record of what
#: the revisor said.
_HANDOFF_SUBDIR = "handoffs"


def handoff_path(hive_dir: str, task_id: str, role: str) -> str:
    return os.path.join(scratch_dir(hive_dir, task_id), _HANDOFF_SUBDIR, f"{role}.json")


#: `save_handoff`'s default for `permission_denials`: the caller did not say,
#: so the envelope has no key. Not `None`, which is a value the key can carry
#: and means something else (`docs/decisions.md` ADR 116).
PERMISSION_DENIALS_ABSENT = object()

#: `save_handoff`'s default for `command_requests`, for the same reason: only
#: the roles that can file one record the key (`docs/decisions.md` ADR 117).
COMMAND_REQUESTS_ABSENT = object()


def save_handoff(
    hive_dir: str,
    task_id: str,
    role: str,
    payload: dict | None,
    round_num: int | None = None,
    permission_denials: object = PERMISSION_DENIALS_ABSENT,
    command_requests: object = COMMAND_REQUESTS_ABSENT,
    session_id: str | None = None,
    account: str | None = None,
) -> str:
    """Keep the structured return of a phase, not just its prose rendering.

    The handoff a role writes is parsed into a dict, rendered into the task
    file as prose, and then lost with the process. Everything the cycle does
    with it afterwards — the debt a task declared, the debt a revisor accepted,
    the entries a merge has to close — needs the dict and cannot be recovered
    from the prose. So the cycle could only ever do that work in the one run
    that held all of it in memory, and a task driven phase by phase, by hand,
    silently got none of it.

    One file per role, overwritten each round, is enough because of how the
    cycle terminates: it stops looping when the revisor approves, so the last
    file a role left is the round that was approved, which is exactly the round
    whose debt is real. `round` is kept in the envelope anyway — for a reader
    trying to work out what happened, the round number is the difference
    between a task that was approved first time and one that took three.

    Written even when `payload` is None, so that a re-run whose handoff failed
    to parse clears the previous round's file rather than leaving it standing
    as if it were this round's answer.

    `permission_denials` is the reduced list of what the CLI refused during the
    call whose handoff this is (`docs/decisions.md` ADR 116): `None` when that
    call did not report it, `[]` when it reported none. Left at its default,
    the envelope carries no such key at all, so every caller that does not
    pass it writes exactly the four keys it always did, and a reader takes a
    missing key as "not recorded".

    `command_requests` is the check of the phase's requests (ADR 117):
    `{"accepted": [...], "refused": [{"request": ..., "reason": ...}]}`.
    Absent the same way, so a role that cannot file one writes no such key.

    `session_id` and `account` are the call's CLI session and the account it
    ran on, written only when given (`docs/decisions.md` ADR 119): a command
    request's verdict resumes that session, on that account, days after the
    process that ran the phase is gone.
    """
    path = handoff_path(hive_dir, task_id, role)
    envelope = {
        "role": role,
        "round": round_num,
        "saved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "handoff": payload,
    }
    if permission_denials is not PERMISSION_DENIALS_ABSENT:
        envelope["permission_denials"] = permission_denials
    if command_requests is not COMMAND_REQUESTS_ABSENT:
        envelope["command_requests"] = command_requests
    if session_id:
        envelope["session_id"] = session_id
    if account:
        envelope["account"] = account
    # Made here rather than by `_write_atomic`'s own mkdir, which cannot tell
    # the hive from what is under it: `handoffs/` and the scratch dir have to
    # be traversable by `/api/phases`' uid, the file alone is not enough.
    _makedirs_readable(hive_dir, os.path.dirname(path))
    _write_atomic(path, json.dumps(envelope, indent=2, sort_keys=True) + "\n")
    return path


#: Where a command request's run is logged, `<n>.log` per request, under the
#: task's scratch dir, where the resumed phase can read it (ADR 119). Its own
#: directory, for the reason `_HANDOFF_SUBDIR` gives: the scratch dir is
#: writable by the phases.
_COMMANDS_SUBDIR = "commands"


def command_log_path(hive_dir: str, task_id: str, n: int) -> str:
    return os.path.join(scratch_dir(hive_dir, task_id), _COMMANDS_SUBDIR, f"{n}.log")


def save_command_log(hive_dir: str, task_id: str, n: int, text: str) -> str:
    """Write the log of command request `n`, atomically. Raises OSError. Returns its path."""
    path = command_log_path(hive_dir, task_id, n)
    _makedirs_readable(hive_dir, os.path.dirname(path))
    _write_atomic(path, text)
    return path


#: What a scoped commit left uncommitted, per role: `<role>.json` names the
#: paths and `<role>.patch` keeps their content. Its own directory, for the
#: reason `_HANDOFF_SUBDIR` gives.
_LEFT_BEHIND_SUBDIR = "left-behind"


def left_behind_record_path(hive_dir: str, task_id: str, role: str) -> str:
    return os.path.join(scratch_dir(hive_dir, task_id), _LEFT_BEHIND_SUBDIR, f"{role}.json")


def left_behind_patch_path(hive_dir: str, task_id: str, role: str) -> str:
    return os.path.join(scratch_dir(hive_dir, task_id), _LEFT_BEHIND_SUBDIR, f"{role}.patch")


def save_left_behind(
    hive_dir: str,
    task_id: str,
    role: str,
    round_num: int | None,
    outside: list[str],
    excluded: list[str],
    patch: str,
) -> str:
    """Keep the paths a scoped commit left behind, and their diff. Raises OSError.

    The patch goes first: a record never points at a patch that is not there.
    Returns the patch path.
    """
    record_path = left_behind_record_path(hive_dir, task_id, role)
    patch_path = left_behind_patch_path(hive_dir, task_id, role)
    _makedirs_readable(hive_dir, os.path.dirname(record_path))
    _write_atomic(patch_path, patch)
    record = {
        "role": role,
        "round": round_num,
        "saved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "outside": list(outside),
        "excluded": list(excluded),
        "patch": patch_path,
    }
    _write_atomic(record_path, json.dumps(record, indent=2, sort_keys=True) + "\n")
    return patch_path


def clear_left_behind(hive_dir: str, task_id: str, role: str) -> None:
    """Drop a role's earlier record: a clean re-run must not leave a stale one."""
    for path in (
        left_behind_record_path(hive_dir, task_id, role),
        left_behind_patch_path(hive_dir, task_id, role),
    ):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def read_left_behind(hive_dir: str, task_id: str) -> list[dict]:
    """Every role's record, sorted by file name; unreadable ones are skipped."""
    directory = os.path.join(scratch_dir(hive_dir, task_id), _LEFT_BEHIND_SUBDIR)
    if not os.path.isdir(directory):
        return []
    records: list[dict] = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            records.append(data)
    return records


def list_handoff_roles(hive_dir: str, task_id: str) -> list[str]:
    """The roles that left a record on this task, sorted, empty when none did.

    `list_task_ids`' shape, including its `os.path.isdir` guard: a task that
    predates `save_handoff`, or one no phase has finished yet, has no handoffs
    directory at all and that is a task with no records rather than damage.

    Sorted by name and not ordered by the dispatcher's role list, which is not
    this function's business. A file named after a role this harness does not
    run is still a file, and a reader that walks these has to be told about it
    rather than have it disappear.
    """
    directory = os.path.join(scratch_dir(hive_dir, task_id), _HANDOFF_SUBDIR)
    if not os.path.isdir(directory):
        return []
    return sorted(Path(f).stem for f in os.listdir(directory) if f.endswith(".json"))


def read_handoff_envelope(hive_dir: str, task_id: str, role: str) -> dict:
    """The whole envelope `save_handoff` wrote — its four keys, `permission_denials`
    when the phase recorded them (ADR 116), and `command_requests` when its
    role can file them (ADR 117) — or an exception.

    For the reader that has to tell an operator *why* a record could not be
    read, which `read_handoff` cannot: it answers `None` for a file that is
    missing, a file that will not parse and a file holding the wrong shape, and
    those are three different sentences on a screen. `observability/api/app.py`
    is that reader and names the path in a warning.

    Raises `OSError` for a file that will not open and `ValueError` for one
    that will not parse or is not a JSON object — `json.JSONDecodeError` is a
    `ValueError` already, which is why a caller catches the family and not the
    subclass (`docs/learnings/a-lookup-that-never-raises-catches-valueerror.md`).
    """
    with open(handoff_path(hive_dir, task_id, role)) as fh:
        envelope = json.load(fh)
    if not isinstance(envelope, dict):
        raise ValueError(
            f"a handoff is a JSON object of role, round, saved_at and handoff, "
            f"not a {type(envelope).__name__}"
        )
    return envelope


def read_handoff(hive_dir: str, task_id: str, role: str) -> dict | None:
    """What that role returned, or None if this task has no record of it.

    Every caller reads a handoff to ask what it declared, and every one of
    those questions answers "nothing" for a payload that is missing, empty or
    malformed — `debt.declarations`, `debt.rulings` and `debt.resolved` all
    take `dict | None` for that reason. So a damaged file is not worth an
    exception here: the paths that read this are repair paths, and a task
    whose record is unreadable should still be closeable by hand.

    Written in terms of `read_handoff_envelope` so there is one definition of
    where a handoff lives and what shape it has; the swallow is this function's
    contract and not the module's.
    """
    try:
        envelope = read_handoff_envelope(hive_dir, task_id, role)
    except (OSError, ValueError):
        return None
    payload = envelope.get("handoff")
    return payload if isinstance(payload, dict) else None


#: Where the dispatcher keeps what each `claude` call it made for this task
#: cost (`docs/decisions.md` ADR 53). Namespaced into its own subdirectory for
#: the reason `_HANDOFF_SUBDIR` gives, and a sibling of it rather than a file
#: inside it: `list_handoff_roles` reads every `.json` in that directory as a
#: role, and this log is the dispatcher's own observation, not a role's return.
_USAGE_SUBDIR = "usage"
_USAGE_FILE = "calls.jsonl"


def usage_log_path(hive_dir: str, task_id: str) -> str:
    return os.path.join(scratch_dir(hive_dir, task_id), _USAGE_SUBDIR, _USAGE_FILE)


def append_usage(hive_dir: str, task_id: str, record: dict) -> str:
    """Add one line to this task's usage log, and return the file it went in.

    Appended with `O_APPEND` rather than through `_write_atomic`, which is the
    one place in this module that difference is deliberate. That helper exists
    for a JSON *document* another process may read while it is being replaced;
    this is a log, every line of it is final once written, and by convention
    the dispatcher holding the task lock is the only thing that writes it — so
    re-serialising the whole file per `claude` call would buy nothing and lose
    the series as it grows. Convention is all it is: the scratch dir is mounted
    into the agent containers at the same path, so an aggregator must read the
    reader's half of the contract as the guarantee and not the writer count —
    skip a line that will not parse, and do not assume the last line is
    complete, rather than calling the file damaged
    (`docs/learnings/atomic-writes-copy-state-machine.md` is about the document
    case and does not fire here).

    The mode is `_write_atomic`'s 0644 and for its reason — agent containers
    and the host operator both read what this module writes under `.hive/`,
    and the reader ADR 53 names is `observability/api/` over a `:ro` mount as
    another uid. `open(..., "a")` would create at the process umask, so the
    file is created `O_EXCL` and `fchmod`ed instead: only at creation, on the
    descriptor, so this never changes the mode of a file another uid owns. The
    directories it creates on the way are 0755 by `_makedirs_readable`, for
    the same reader (ADR 55).

    Raises `OSError` to its caller. Swallowing belongs at the call site, where
    the dispatcher knows this is bookkeeping on a path that may already be
    failing and must never be what fails a phase.
    """
    path = usage_log_path(hive_dir, task_id)
    _makedirs_readable(hive_dir, os.path.dirname(path))
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        pass  # Already there, and whoever created it owns its mode.
    else:
        try:
            os.fchmod(fd, 0o644)  # The umask masked the mode above.
        finally:
            os.close(fd)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return path


@dataclasses.dataclass(frozen=True)
class UsageLog:
    """What `read_usage` found: the lines that parsed, and how many did not.

    `skipped` is there so a reader that must not drop anything silently — the
    api's envelope is one — can say that it did, without this module deciding
    how that is worded. Blank lines are not counted: they are not records.
    """

    lines: list[dict]
    skipped: int


def _reject_non_finite(constant: str) -> float:
    """`json.loads`' hook for `NaN` and `Infinity`, which it accepts by default.

    Neither is JSON, and a line carrying one would parse here and then be served
    by a JSON encoder that writes it back out as a bare `NaN` no client can read.
    `append_usage` cannot write one — `_number_or_none` keeps what the CLI
    printed, and the CLI prints JSON — so a line holding one was written by
    something else and is skipped like any other that is not a record.
    """
    raise ValueError(f"{constant} is not a JSON number")


def read_usage(hive_dir: str, task_id: str) -> UsageLog:
    """Every line of this task's usage log that is a record, in the order written.

    The reader's half of the contract `append_usage` states, and the guarantee
    rather than the writer count: the scratch dir is writable from the agent
    containers, so a line may be anything. Each line is decoded and parsed on
    its own: a blank line is skipped, and one that is not UTF-8, one that will
    not parse, and one that parses into something other than an object are
    skipped and counted rather than raised on. So is a last line with no
    newline after it:
    `append_usage` writes each record and its newline in one call, so a line
    without one is a write still in progress or one that was cut off, and in
    neither case the record its writer meant. A line is never repaired, merged
    with the next one or defaulted — what comes back is what was written.

    A task with no log — never dispatched, dispatched before ADR 53, or no
    scratch dir at all — is an empty log, not an error. `PermissionError` and
    every other `OSError` are raised: a log this process may not read is a
    different fact from a log that is not there, and the caller is the one who
    knows how to say so.

    `task_id` must be one path component, and `ValueError` is raised otherwise,
    before anything is opened. `observability/api/app.py:_is_bare_task_id` is a
    check on the *file* name `<task_id>.md`, and it admits `.` and `..` — whose
    scratch dirs are the hive dir itself and its parent. This is the check on
    the *directory* name, made here so no caller can reach outside the hive dir
    through this function whatever it checked first.
    """
    if not _is_single_path_component(task_id):
        raise ValueError(f"task id {task_id!r} is not a single path component")
    path = usage_log_path(hive_dir, task_id)
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except (FileNotFoundError, NotADirectoryError):
        return UsageLog(lines=[], skipped=0)
    chunks = data.split(b"\n")
    # The element after the last newline: empty when the file ends in one, and
    # otherwise the incomplete line the docstring describes.
    tail = chunks.pop()
    skipped = 1 if tail.strip() else 0
    lines: list[dict] = []
    for chunk in chunks:
        if not chunk.strip():
            continue
        try:
            value = json.loads(chunk.decode("utf-8"), parse_constant=_reject_non_finite)
        except (ValueError, RecursionError):
            # `UnicodeDecodeError` and `JSONDecodeError` are both `ValueError`s;
            # `RecursionError` is what a line of ten thousand `[` costs `json`.
            skipped += 1
            continue
        if not isinstance(value, dict):
            skipped += 1
            continue
        lines.append(value)
    return UsageLog(lines=lines, skipped=skipped)


def _is_single_path_component(name: str) -> bool:
    """Is this a name that can only ever be one entry inside a directory?"""
    if not name or name in (".", "..") or "\0" in name:
        return False
    separators = {os.sep, "/"} | ({os.altsep} if os.altsep else set())
    return not any(sep in name for sep in separators)


def list_task_ids(hive_dir: str) -> list[str]:
    if not os.path.isdir(hive_dir):
        return []
    return [Path(f).stem for f in os.listdir(hive_dir) if f.endswith(".md")]


def _as_heartbeat_str(value: object) -> str | None:
    """The heartbeat as the dataclass declares it, whatever YAML made of it.

    `write_task_file` quotes the timestamp, so a card this harness wrote reads
    back as a `str`. A hand-edited card does not, and PyYAML resolves an
    unquoted ISO timestamp to a `datetime` — which then reaches
    `is_lock_expired` as `fromisoformat: argument must be str`. A hand-edited
    card is exactly what somebody is holding when they go looking at locks, so
    the coercion happens here, once, rather than at every reader.
    """
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, dt.datetime):
        # No offset means UTC: every heartbeat this harness writes is, and a
        # naive one compared against an aware `now` raises rather than expires.
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    # Anything else is not a timestamp, and a field that cannot be compared is
    # worth less than no field: an unheld card is takeable, a card held by
    # garbage would be held forever.
    return None


def read_task_file(path: str) -> TaskFile:
    text = Path(path).read_text()
    _, fm_text, body = text.split(_FRONTMATTER_DELIM, 2)
    fm = yaml.safe_load(fm_text) or {}
    return TaskFile(
        task_id=fm["task_id"],
        status=fm["status"],
        owner=fm.get("owner"),
        depends_on=fm.get("depends_on", []),
        heartbeat=_as_heartbeat_str(fm.get("heartbeat")),
        body=body.lstrip("\n"),
        resolved_debt=fm.get("resolved_debt") or [],
        epic=fm.get("epic") or None,
        split_into=fm.get("split_into") or [],
        merged=fm.get("merged") or None,
        blocked_reason=fm.get("blocked_reason") or None,
        command_verdicts=fm.get("command_verdicts") or [],
        phase=fm.get("phase") or None,
    )


def has_phase_section(hive_dir: str, task_id: str, role: str) -> bool:
    """The dispatcher's own record that this role's phase returned on this task.

    `tasks.handoff()` appends one `## <label>` block per phase that finished, and the
    phase loop only builds that label after `run_phase` returned something — so
    a section is there because a phase came back, and it outlives the worktree,
    the container and the process the phase ran in. That makes it the local
    stand-in for "this role ran" that `learnings.drop_promoted` gates on
    (`docs/decisions.md` ADR 34, `docs/debt/T-012-D1.md`).

    The heading is matched in the parsed body rather than in the file's text,
    anchored, with exactly two hashes, and `auditor (round 2)` counts as well as
    `auditor` because `run-phase --round` labels a hand-resumed phase that way.

    A card that is missing or will not parse answers `False`: it is absence of
    evidence, and the one caller's `True` moves files. That is the exception
    `a-never-500-read-wraps-the-use-not-the-parse` leaves room for — the
    predicate's `False` already *means* "no evidence", so widening at the caller
    would only turn a damaged card into a crashed `merge-task`. Silent because
    this module has no logger; the caller logs what it kept.

    The tuple is the five that learning names, because `read_task_file` raises
    every one: no file (`OSError`), no `---` to split on (`ValueError`),
    frontmatter that does not scan (`yaml.YAMLError`), one that scans but is
    missing a key (`KeyError`), and one that scans into something that is not a
    mapping at all — `TODO write this up`, or a list of bullets — where
    `fm["task_id"]` subscripts a `str` or a `list` (`TypeError`). A hand-edited
    card is the normal state of the cycles `merge-task` runs on, so that last
    one is the realistic shape of a damaged card rather than a contrived one.
    `AttributeError` is not in it: `fm["task_id"]` is evaluated before any
    `fm.get`, so the subscript raises first for every non-mapping PyYAML returns.
    """
    try:
        body = read_task_file(task_file_path(hive_dir, task_id)).body
    except (OSError, TypeError, ValueError, KeyError, yaml.YAMLError):
        return False
    heading = re.compile(
        rf"^## {re.escape(role)}(?: \(round \d+\))?$", re.MULTILINE
    )
    return heading.search(body) is not None


def _read_or_new(hive_dir: str, task_id: str) -> tuple[str, TaskFile]:
    path = task_file_path(hive_dir, task_id)
    if os.path.exists(path):
        return path, read_task_file(path)
    return path, TaskFile(
        task_id=task_id, status="pending", owner=None, depends_on=[], heartbeat=None, body=""
    )


def _write_atomic(path: str, content: str) -> None:
    """Replace a file's contents in one step, readable by whoever reads it.

    Every file under `.hive/` is written while something else may be reading
    it — the heartbeat thread rewrites the card under the phase that is
    holding it, a later process reads a handoff the cycle is still adding to —
    so nothing here is written in place. The temp file goes in the same
    directory, because `os.replace` is only atomic within one filesystem, and
    carries a non-".md" suffix so a leftover from a crash is never picked up
    by `list_task_ids` as a task.

    The file is 0644 whatever the umask; the mkdir below is not, and is left
    at the umask on purpose, because for a task file its parent is the hive
    tasks dir itself. A caller writing under a task's scratch dir makes the
    parent with `_makedirs_readable` first, as `save_handoff` does, so this
    mkdir finds it there.
    """
    parent = Path(path).parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", dir=parent, prefix=f".{Path(path).stem}.", suffix=".tmp", delete=False
    )
    try:
        with tmp:
            tmp.write(content)
            tmp.flush()
        # NamedTemporaryFile creates the file 0600, and os.replace keeps that
        # mode. Agent containers (possibly non-root) and the host operator
        # both need to read this file, so restore 0644 before the replace.
        # Not os.umask(): that's process-wide and would race the heartbeat
        # thread's own writes.
        os.chmod(tmp.name, 0o644)
        os.replace(tmp.name, path)
    except BaseException:
        try:
            os.remove(tmp.name)
        except FileNotFoundError:
            pass
        raise


def write_task_file(path: str, task: TaskFile) -> None:
    """Serialize a task file, atomically. Not the dispatcher's way in.

    The writers — the lock, the heartbeat, the handoff — live in
    `dispatcher/tasks.py`, behind a writer that refuses a file still carrying
    its description. That module is the only caller of this one under
    `dispatcher/`, and a test in `tests/dispatcher/test_tasks.py` holds it.
    """
    fm = {
        "task_id": task.task_id,
        "status": task.status,
        "owner": task.owner,
        "depends_on": task.depends_on,
        "heartbeat": task.heartbeat,
    }
    if task.resolved_debt:
        fm["resolved_debt"] = task.resolved_debt
    # After `resolved_debt`, so a file that has it keeps its bytes.
    if task.epic:
        fm["epic"] = task.epic
    if task.split_into:
        fm["split_into"] = task.split_into
    if task.merged:
        fm["merged"] = task.merged
    if task.blocked_reason:
        fm["blocked_reason"] = task.blocked_reason
    if task.command_verdicts:
        fm["command_verdicts"] = task.command_verdicts
    if task.phase:
        fm["phase"] = task.phase
    dumped = yaml.dump(fm, Dumper=_TaskDumper, sort_keys=False, default_flow_style=False)
    _write_atomic(path, f"{_FRONTMATTER_DELIM}\n{dumped}{_FRONTMATTER_DELIM}\n\n{task.body}")


def read_resolved_debt(hive_dir: str, task_id: str) -> list[str]:
    """The debt entries this task claims to have resolved, empty when none."""
    return _read_or_new(hive_dir, task_id)[1].resolved_debt


def is_lock_expired(task: TaskFile, ttl_seconds: int, now: dt.datetime | None = None) -> bool:
    if task.heartbeat is None:
        return False
    now = now or dt.datetime.now(dt.timezone.utc)
    heartbeat_dt = dt.datetime.fromisoformat(task.heartbeat)
    return (now - heartbeat_dt).total_seconds() > ttl_seconds
