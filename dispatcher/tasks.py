"""The task module: the one writer of the board's files.

`docs/plans/board-service.md` section 5 gives a task three files and one
module that writes them. Two of them are written here: the description — the
ask, in sections, at `<hive root>/board/<task_id>/description.md` — and the
task file itself — status, lock, handoffs — whose writers (`acquire_lock`,
`refresh_heartbeat`, `release_stale_lock`, `handoff`, `set_resolved_debt`,
`file_split`, `mark_merged`, `mark_blocked`, `close_epic`) all end in
`_write_task_file`.

It also keeps the per-project base lock (`BaseLock`, ADR 80): not a board
file but the lock under `<state_dir>/base-locks/` that serializes every
operation on a project's checkout.
`context_transfer` keeps
the `TaskFile` shape, its reader and the serializer `write_task_file`, which
nothing under `dispatcher/` but this module calls.

The description used to be the frontmatter key `description` of the task
file. `read_task_file` no longer reads that key, so a write to a task file
that still has one would drop the description silently. `_write_task_file`
therefore refuses such a file by name (`UnmigratedTaskError`), as the
description's own reader and writer do; `migrate` is the one path that writes
over one, having stored the description first. `unmigrated_ids` is what lets
an entry point refuse the whole hive until `dispatch migrate` has been run.

The epic format of `docs/plans/task-split.md` section 2 is written here too:
`file_split` files a split's sub-tasks and turns their parent into an epic,
`mark_merged` records a merge on the task and `unmet_dependencies` reads it.
`mark_blocked` records a task that stopped blocked. `close_epic` closes an
epic on its last merged sub-task and `epic_problems` checks one written by
hand. The dispatcher calls all of them; `file_split` only through
`dispatcher._file_split` (`docs/implementations/T-031.md`).
"""
from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import fcntl
import json
import os
import re
import socket
import threading
import time
import uuid
from pathlib import Path

import yaml

from dispatcher import context_transfer
# By name: this module's own `handoff` is a task-file writer.
from dispatcher.handoff import _SPLIT_MAX, _SPLIT_MIN, SubTask, split_problems

# `_makedirs_readable`, `_write_atomic`, `_is_single_path_component`,
# `_read_or_new` and `_FRONTMATTER_DELIM` are private to `context_transfer` and used here on
# purpose: the two modules are one seam mid-move, and renaming the helpers
# would touch every doc that cites them.

BOARD_DIR_NAME = "board"
DESCRIPTION_FILE = "description.md"
REQUIRED = ("Goal", "Done when")
OPTIONAL = ("Surface", "Steps", "Out of scope")
MIGRATE_COMMAND = "dispatch migrate"
#: What `migrate` writes under `## Done when` for a description from before
#: the sections existed: the old text goes whole under `## Goal`.
LEGACY_DONE_WHEN = "Not stated separately; see Goal."

_LEGACY_KEY = "description"
_SECTION_RE = re.compile(
    r"^## (" + "|".join(re.escape(name) for name in REQUIRED + OPTIONAL) + r")[ \t]*$"
)


class DescriptionError(ValueError):
    """A description that is not in sections. The message follows "the task description "."""


class UnmigratedTaskError(ValueError):
    """A task file that still carries its description in the frontmatter."""

    def __init__(self, task_id: str, path: str) -> None:
        super().__init__(
            f"task {task_id}: {path} still carries its description in the frontmatter; "
            f"run `{MIGRATE_COMMAND}` first"
        )
        self.task_id = task_id
        self.path = path


class SplitError(ValueError):
    """A split `file_split` will not file. One error, every problem."""

    def __init__(self, parent_id: str, problems: list[str]) -> None:
        super().__init__(
            f"task {parent_id}: the split cannot be filed: " + "; ".join(problems)
        )
        self.task_id = parent_id
        self.problems = list(problems)


@dataclasses.dataclass(frozen=True)
class Description:
    """A description's sections, each with its surrounding blank lines stripped.

    An optional section that is absent or blank is `None`.
    """

    goal: str
    done_when: str
    surface: str | None = None
    steps: str | None = None
    out_of_scope: str | None = None

    def present_optional(self) -> tuple[str, ...]:
        """The names of the optional sections this description has, in `OPTIONAL`'s order."""
        return tuple(name for name in OPTIONAL if getattr(self, _field(name)) is not None)


@dataclasses.dataclass(frozen=True)
class MigrateReport:
    """What `migrate` did: `moved` is (task id, description path), `refused` is (task id, reason)."""

    moved: list[tuple[str, str]]
    emptied: list[str]
    refused: list[tuple[str, str]]
    already: int


def board_root(hive_dir: str) -> str:
    """The board's directory, a sibling of the hive tasks dir.

    Derived, not configured, for the reason `context_transfer.hive_root` and
    `learnings.root_dir` give.
    """
    return os.path.join(context_transfer.hive_root(hive_dir), BOARD_DIR_NAME)


def description_path(hive_dir: str, task_id: str) -> str:
    """Where this task's description lives. `ValueError` for an id that is not one path component."""
    if not context_transfer._is_single_path_component(task_id):
        raise ValueError(f"task id {task_id!r} is not a single path component")
    return os.path.join(board_root(hive_dir), task_id, DESCRIPTION_FILE)


def _field(name: str) -> str:
    return name.lower().replace(" ", "_")


def parse_description(text: str) -> Description:
    """Split a description into its sections, or say which rule it broke.

    A section starts at a column-zero line that is exactly `## ` and one of
    the five names; any other line, other `##` headings included, is text of
    the section it is in. Sections may come in any order. Fenced code blocks
    are not special-cased. A required section with only whitespace under it
    counts as missing.
    """
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        match = _SECTION_RE.match(line)
        if match:
            name = match.group(1)
            if name in sections:
                raise DescriptionError(f"has two `## {name}` sections")
            current = sections[name] = []
        elif current is not None:
            current.append(line)
        elif line.strip():
            raise DescriptionError(
                f"has text before its first section; start with `## {REQUIRED[0]}`"
            )
    bodies = {name: "\n".join(lines).strip() for name, lines in sections.items()}
    missing = [name for name in REQUIRED if not bodies.get(name)]
    if missing:
        named = " and ".join(f"`## {name}`" for name in missing)
        raise DescriptionError(
            f"is missing its {named} section{'s' if len(missing) > 1 else ''}"
        )
    return Description(**{_field(name): bodies.get(name) or None for name in REQUIRED + OPTIONAL})


def _read_frontmatter(path: str) -> object:
    """A task file's frontmatter as YAML made it. Same split as `read_task_file`."""
    text = Path(path).read_text()
    _, fm_text, _ = text.split(context_transfer._FRONTMATTER_DELIM, 2)
    return yaml.safe_load(fm_text)


def _path_is_unmigrated(path: str) -> bool:
    try:
        fm = _read_frontmatter(path)
    except (OSError, ValueError, yaml.YAMLError):
        return False
    return isinstance(fm, dict) and _LEGACY_KEY in fm


def is_unmigrated(hive_dir: str, task_id: str) -> bool:
    """Does this task's file still have a `description` key in its frontmatter?

    A task file that is missing or will not parse answers `False`: it is not
    unmigrated, it is someone else's error to report.
    """
    return _path_is_unmigrated(context_transfer.task_file_path(hive_dir, task_id))


def unmigrated_ids(hive_dir: str) -> list[str]:
    """Every task whose file `migrate` still has to move, sorted."""
    return sorted(
        task_id
        for task_id in context_transfer.list_task_ids(hive_dir)
        if is_unmigrated(hive_dir, task_id)
    )


def _refuse_unmigrated(hive_dir: str, task_id: str) -> None:
    if is_unmigrated(hive_dir, task_id):
        raise UnmigratedTaskError(task_id, context_transfer.task_file_path(hive_dir, task_id))


def read_description(hive_dir: str, task_id: str) -> str | None:
    """The stored description, exactly as written, or None when the task has none.

    Raises `UnmigratedTaskError` for a task file `migrate` has not moved yet,
    and `OSError` for a file that is there and will not open. Never writes.
    """
    _refuse_unmigrated(hive_dir, task_id)
    try:
        # newline="": what comes back is what was written, to the byte.
        with open(description_path(hive_dir, task_id), encoding="utf-8", newline="") as fh:
            return fh.read()
    except (FileNotFoundError, NotADirectoryError):
        return None


def _write(hive_dir: str, task_id: str, text: str) -> str:
    path = description_path(hive_dir, task_id)
    # Below the hive root, so `board/` and the task's directory are 0755
    # whatever the umask and `.hive/` itself is left alone
    # (`docs/learnings/a-0644-file-under-hive-still-sits-in-a-umask-directory.md`).
    context_transfer._makedirs_readable(
        context_transfer.hive_root(hive_dir), os.path.dirname(path)
    )
    context_transfer._write_atomic(path, text)
    return path


def write_description(hive_dir: str, task_id: str, text: str) -> str:
    """Store a description verbatim, and return the file it went in.

    Refuses an unmigrated task file (`UnmigratedTaskError`) and a text missing
    a required section (`DescriptionError`), writing nothing in either case.
    """
    _refuse_unmigrated(hive_dir, task_id)
    parse_description(text)
    return _write(hive_dir, task_id, text)


def _write_task_file(path: str, task: context_transfer.TaskFile) -> None:
    """Write a task file, unless the one on disk still carries its description.

    Every writer below reads the file, changes a field and ends here, and
    `read_task_file` does not carry the `description` key: written over, it
    would be gone. So the refusal is the writer's own and no caller has to
    remember a guard (`docs/debt/T-026-D1.md`). A file that is not there yet
    is written; one that will not parse never gets this far, because the
    writer's own read raised first. Atomic, as `write_task_file` is.
    """
    if _path_is_unmigrated(path):
        raise UnmigratedTaskError(task.task_id, path)
    context_transfer.write_task_file(path, task)


def set_resolved_debt(hive_dir: str, task_id: str, entries: list[str]) -> None:
    """Record which debt entries this task's work resolved.

    Replaces rather than appends: a re-run's implementation is the current
    claim about what this task fixes, and the previous round's claim was
    about code that has since been rewritten.
    """
    path, task = context_transfer._read_or_new(hive_dir, task_id)
    task.resolved_debt = list(entries)
    _write_task_file(path, task)


def acquire_lock(
    hive_dir: str, task_id: str, owner: str, ttl_seconds: int | None = None
) -> context_transfer.TaskFile:
    path, task = context_transfer._read_or_new(hive_dir, task_id)
    # Two dispatcher processes, or a stale retry, must not both drive the same
    # task. This is a read-then-write check, not a cross-process mutex — a
    # real mutual-exclusion guard (e.g. flock) is separate future work.
    if task.owner is not None and task.owner != owner:
        # A live holder always refreshes its heartbeat, so a foreign owner
        # with heartbeat None can only be a hand-edited or legacy file, not
        # an active dispatcher. Treat it as stale when the caller opted into
        # takeovers at all (ttl_seconds given) rather than locking the task
        # forever with no way to reap it.
        heartbeat_missing = task.heartbeat is None
        if ttl_seconds is None or not (
            heartbeat_missing or context_transfer.is_lock_expired(task, ttl_seconds)
        ):
            raise context_transfer.LockHeldError(task_id, task.owner)
    task.owner = owner
    task.heartbeat = dt.datetime.now(dt.timezone.utc).isoformat()
    # Running a blocked task again is the operator's decision to retry (the
    # user's ruling of 2026-10-11, `docs/debt/T-028-D3.md`). `epic` and `done`
    # stay as they are.
    if task.status in ("pending", "blocked"):
        task.status = "in_progress"
    _write_task_file(path, task)
    return task


def refresh_heartbeat(hive_dir: str, task_id: str) -> None:
    path = context_transfer.task_file_path(hive_dir, task_id)
    task = context_transfer.read_task_file(path)
    task.heartbeat = dt.datetime.now(dt.timezone.utc).isoformat()
    _write_task_file(path, task)


def release_stale_lock(hive_dir: str, task_id: str) -> None:
    path = context_transfer.task_file_path(hive_dir, task_id)
    task = context_transfer.read_task_file(path)
    task.owner = None
    task.heartbeat = None
    _write_task_file(path, task)


# --- The base lock (`docs/plans/autonomy.md` decision 6, task B-1, ADR 80) ---
#
# The project's checkout under `projects_root` is one resource: an update of
# its branch, a mapping commit, a pre-merge check, a merge and a push all
# work on it, and two of them at once — two dispatcher processes, or the
# dispatcher and an operator's `merge-task` — can interleave a fetch with a
# merge, or restore the wrong owner over each other's `read_owner` window.
# The base lock serializes them, one project at a time, and is held only for
# the one operation.
#
# It is crash-safe the way the task lock is: the file names its holder (host,
# pid and a token of its own per acquisition), a heartbeat thread keeps it
# fresh, and a lock whose heartbeat is older than the TTL is broken — but only
# after the holder is checked. A holder on this host whose pid is still alive
# is never broken, however old its heartbeat: a process stuck in a long merge
# is not a dead one. Unlike the task lock, every read-check-write takes a
# short `flock` on a guard file next to the lock, so two acquirers can never
# both see the lock free.


class BaseLockHeldError(RuntimeError):
    """Raised when a project's base lock is held by a live holder."""

    def __init__(
        self,
        slug: str,
        holder: dict,
        operation: str,
        waited_seconds: float | None = None,
    ) -> None:
        self.slug = slug
        self.holder = holder
        self.operation = operation
        self.waited_seconds = waited_seconds
        held_by = holder.get("owner") or "an unknown holder"
        held_for = holder.get("operation") or "an unknown operation"
        since = holder.get("acquired") or "an unknown time"
        waited = f" after waiting {waited_seconds:.0f}s" if waited_seconds else ""
        super().__init__(
            f"project {slug!r}: the base is locked by {held_by} "
            f"({held_for}, since {since}); {operation} was refused{waited}"
        )


def base_lock_path(state_dir: str, slug: str) -> str:
    """Where a project's base lock lives: `<state_dir>/base-locks/<slug>.lock`."""
    if not context_transfer._is_single_path_component(slug):
        raise ValueError(f"project slug {slug!r} is not a single path component")
    return os.path.join(state_dir, "base-locks", f"{slug}.lock")


def new_base_lock_owner() -> str:
    """A holder identity unique to one acquisition: `<host>:<pid>:<token>`."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


@contextlib.contextmanager
def _base_lock_guard(path: str):
    """Hold an exclusive `flock` on the lock's guard file for one read-check-write."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(f"{path}.guard", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _read_base_lock(path: str) -> dict | None:
    """The lock's record, None when there is none; a corrupt file reads as `{}`."""
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return None
    try:
        record = json.loads(content)
    except ValueError:
        return {}
    return record if isinstance(record, dict) else {}


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # It exists, under another user.
        return True
    except OSError:
        return False
    return True


def base_lock_is_live(
    record: dict, ttl_seconds: int, now: dt.datetime | None = None
) -> bool:
    """Is this lock's holder still there? A live holder is never broken.

    A heartbeat no older than the TTL is live. A stale or missing one is
    still live when the holder is on this host and its pid is alive: the
    holder is checked, not presumed dead. A holder on another host with a
    stale heartbeat cannot be checked and counts as dead, as the task lock's
    does. A corrupt record is stale.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    heartbeat = record.get("heartbeat")
    if isinstance(heartbeat, str):
        try:
            beat = dt.datetime.fromisoformat(heartbeat)
        except ValueError:
            beat = None
        if beat is not None and (now - beat).total_seconds() <= ttl_seconds:
            return True
    pid = record.get("pid")
    if record.get("host") == socket.gethostname() and isinstance(pid, int) and pid > 0:
        return _pid_alive(pid)
    return False


def acquire_base_lock(
    state_dir: str,
    slug: str,
    owner: str,
    operation: str,
    ttl_seconds: int,
    now: dt.datetime | None = None,
) -> dict:
    """Take a project's base lock now, or raise `BaseLockHeldError`.

    A lock already held by `owner` is taken again (its heartbeat refreshed);
    one held by a live foreign holder raises; one whose holder is gone is
    broken and taken.
    """
    path = base_lock_path(state_dir, slug)
    stamp = (now or dt.datetime.now(dt.timezone.utc)).isoformat()
    with _base_lock_guard(path):
        record = _read_base_lock(path)
        if (
            record is not None
            and record.get("owner") != owner
            and base_lock_is_live(record, ttl_seconds, now)
        ):
            raise BaseLockHeldError(slug, record, operation)
        mine = {
            "owner": owner,
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "operation": operation,
            "acquired": stamp,
            "heartbeat": stamp,
        }
        context_transfer._write_atomic(path, json.dumps(mine, indent=2) + "\n")
    return mine


def refresh_base_heartbeat(state_dir: str, slug: str, owner: str) -> bool:
    """Refresh the heartbeat of a lock `owner` holds; False when it is not theirs."""
    path = base_lock_path(state_dir, slug)
    with _base_lock_guard(path):
        record = _read_base_lock(path)
        if not record or record.get("owner") != owner:
            return False
        record["heartbeat"] = dt.datetime.now(dt.timezone.utc).isoformat()
        context_transfer._write_atomic(path, json.dumps(record, indent=2) + "\n")
    return True


def release_base_lock(state_dir: str, slug: str, owner: str) -> bool:
    """Remove the lock if `owner` holds it; a lock someone else took is left."""
    path = base_lock_path(state_dir, slug)
    with _base_lock_guard(path):
        record = _read_base_lock(path)
        if not record or record.get("owner") != owner:
            return False
        os.remove(path)
    return True


class BaseLock:
    """A project's base lock held for one operation, as a context manager.

    `acquire` waits up to `wait_seconds` for a live holder to let go, then
    raises `BaseLockHeldError`; `with` acquires (unless `acquire` was called
    already, so a caller can handle the refusal before entering) and always
    releases. While held, a daemon thread refreshes the heartbeat every
    `heartbeat_interval_seconds`. The lock is not reentrant: an operation that
    runs inside another's window (the push inside a merge) takes no lock of
    its own.
    """

    def __init__(
        self,
        state_dir: str,
        slug: str,
        operation: str,
        *,
        ttl_seconds: int,
        heartbeat_interval_seconds: float,
        wait_seconds: float = 0,
        poll_seconds: float = 2,
    ) -> None:
        self.state_dir = state_dir
        self.slug = slug
        self.operation = operation
        self.ttl_seconds = ttl_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.wait_seconds = wait_seconds
        self.poll_seconds = poll_seconds
        self.owner = new_base_lock_owner()
        self.held = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def acquire(self) -> "BaseLock":
        start = time.monotonic()
        while True:
            try:
                acquire_base_lock(
                    self.state_dir, self.slug, self.owner, self.operation, self.ttl_seconds
                )
                break
            except BaseLockHeldError as exc:
                waited = time.monotonic() - start
                if waited >= self.wait_seconds:
                    raise BaseLockHeldError(
                        self.slug, exc.holder, self.operation, waited
                    ) from None
                time.sleep(max(0.0, min(self.poll_seconds, self.wait_seconds - waited)))
        self.held = True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._beat, name=f"base-lock-{self.slug}", daemon=True
        )
        self._thread.start()
        return self

    def _beat(self) -> None:
        while not self._stop.wait(self.heartbeat_interval_seconds):
            try:
                refresh_base_heartbeat(self.state_dir, self.slug, self.owner)
            except Exception:  # noqa: BLE001 — a missed beat is retried on the next one
                pass

    def release(self) -> None:
        """Let go of the lock. Idempotent, and never raises."""
        if not self.held:
            return
        self.held = False
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        try:
            release_base_lock(self.state_dir, self.slug, self.owner)
        except Exception:  # noqa: BLE001 — a lock left behind goes stale and is broken
            pass

    def __enter__(self) -> "BaseLock":
        if not self.held:
            self.acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()


def handoff(
    hive_dir: str,
    task_id: str,
    new_status: str,
    body: str,
    depends_on: list[str] | None = None,
) -> None:
    path, task = context_transfer._read_or_new(hive_dir, task_id)
    task.status = new_status
    task.owner = None
    task.heartbeat = None
    # Accumulate phase summaries rather than replacing task.body — the next
    # role needs the full trail of prior phases, not just the last one.
    task.body = f"{task.body}\n\n{body}" if task.body else body
    if depends_on is not None:
        task.depends_on = depends_on
    _write_task_file(path, task)


def mark_blocked(hive_dir: str, task_id: str) -> None:
    """Record that this task stopped blocked, and release its lock.

    The body and every other field stay. A task with no file raises, as
    `refresh_heartbeat` does: there is nothing to mark, and creating one here
    would invent a task. `acquire_lock` takes a blocked task again.
    """
    path = context_transfer.task_file_path(hive_dir, task_id)
    task = context_transfer.read_task_file(path)
    task.status = "blocked"
    task.owner = None
    task.heartbeat = None
    _write_task_file(path, task)


def mark_merged(hive_dir: str, task_id: str, target: str) -> None:
    """Record on the task that its branch was merged into `target`.

    `docs/plans/task-split.md` section 3, "Merged is recorded on the task":
    this block, not `status: done`, is what a dependency waits for. Status,
    lock and body are left as they were; a second call replaces the block. A
    task with no file raises rather than getting one.
    """
    path = context_transfer.task_file_path(hive_dir, task_id)
    task = context_transfer.read_task_file(path)
    task.merged = {"into": target, "at": dt.datetime.now(dt.timezone.utc).isoformat()}
    _write_task_file(path, task)


def _read_for_check(
    hive_dir: str, task_id: object
) -> tuple[context_transfer.TaskFile | None, str | None]:
    """Another task's file, for a check that reports instead of raising.

    The file, or None and the one line saying why there is none to read.
    """
    if not isinstance(task_id, str) or not context_transfer._is_single_path_component(task_id):
        return None, f"{task_id!r} is not a task id"
    try:
        return (
            context_transfer.read_task_file(context_transfer.task_file_path(hive_dir, task_id)),
            None,
        )
    except FileNotFoundError:
        return None, f"{task_id} has no task file"
    except (OSError, TypeError, ValueError, KeyError, yaml.YAMLError):
        return None, f"{task_id}: its task file cannot be read"


def unmet_dependencies(hive_dir: str, task: context_transfer.TaskFile) -> list[str]:
    """Why this task cannot start yet, one line per dependency. Empty means it can.

    A dependency is met once its task file carries a `merged` block; its
    status does not matter, `done` is not merged. Never writes.
    """
    if not isinstance(task.depends_on, list):
        return [f"depends_on is {task.depends_on!r}, not a list"]
    unmet = []
    for dep in task.depends_on:
        dependency, why = _read_for_check(hive_dir, dep)
        if dependency is None:
            unmet.append(why)
        elif not dependency.merged:
            unmet.append(f"{dep} is not merged")
    return unmet


def epic_problems(hive_dir: str, task: context_transfer.TaskFile) -> list[str]:
    """What is wrong with the epic this task is part of, one line each.

    Empty for a task with no `epic`, and for a child of a well-formed one.
    `docs/plans/task-split.md` section 4, "A hand-written epic gets the
    equivalent checks on its files": what `split_problems` and `file_split`
    refuse in a split, read off the files an operator wrote instead. Every
    problem is reported, not the first. Never writes.
    """
    if not task.epic:
        return []
    # The id is the one the child names, which is the file that was read: a
    # hand-written parent's own `task_id` key is not what anything looks up.
    parent_id = task.epic
    parent, why = _read_for_check(hive_dir, parent_id)
    if parent is None:
        return [f"its epic {why}"]
    problems = []
    if parent.status != "epic":
        problems.append(f"its epic {parent_id} has status {parent.status}, not epic")
    if parent.epic:
        problems.append(
            f"its epic {parent_id} is part of epic {parent.epic}; "
            "a task inside an epic cannot become an epic"
        )
    children = parent.split_into
    if not isinstance(children, list):
        return problems + [f"epic {parent_id}: split_into is {children!r}, not a list"]
    if task.task_id not in children:
        problems.append(f"epic {parent_id} does not name {task.task_id} in its split_into")
    if not _SPLIT_MIN <= len(children) <= _SPLIT_MAX:
        problems.append(
            f"epic {parent_id}: an epic has {_SPLIT_MIN} to {_SPLIT_MAX} sub-tasks, "
            f"this one has {len(children)}"
        )
    seen: list[object] = []
    for child_id in children:
        if child_id in seen:
            problems.append(f"epic {parent_id}: split_into names {child_id} more than once")
            continue
        seen.append(child_id)
        child, why = _read_for_check(hive_dir, child_id)
        if child is None:
            problems.append(f"epic {parent_id}: {why}")
            continue
        if child.epic != parent_id:
            problems.append(f"{child_id}: its epic is {child.epic or 'not set'}, not {parent_id}")
        if not isinstance(child.depends_on, list):
            problems.append(f"{child_id}: depends_on is {child.depends_on!r}, not a list")
            continue
        for dep in child.depends_on:
            if dep == child_id or dep not in children:
                problems.append(f"{child_id}: depends_on names {dep}, which is not a sibling")
    return problems


def close_epic(hive_dir: str, task_id: str) -> str | None:
    """Close the epic this task is part of, if it was the last one to merge.

    `docs/plans/task-split.md` section 3, the epic check both merge paths run
    after `mark_merged`. The parent is set to `status: done` when this task
    has `epic`, the parent is `status: epic` and names it in `split_into`, and
    every id there has a task file with a `merged` block; its lock, body and
    other fields stay. Returns the parent's id when it closed it and None
    when it wrote nothing: a parent already `done` is left alone, and a file
    that is missing or will not read closes nothing.
    """
    task, _ = _read_for_check(hive_dir, task_id)
    if task is None or not task.epic:
        return None
    parent, _ = _read_for_check(hive_dir, task.epic)
    if parent is None or parent.status != "epic":
        return None
    children = parent.split_into
    if not isinstance(children, list) or task_id not in children:
        return None
    for child_id in children:
        child, _ = _read_for_check(hive_dir, child_id)
        if child is None or not child.merged:
            return None
    parent.status = "done"
    _write_task_file(context_transfer.task_file_path(hive_dir, task.epic), parent)
    return task.epic


def _with_epic_line(text: str, parent_id: str, surface: str) -> str:
    """A sub-task's description with its epic and surface under `## Goal`.

    The line goes after the section's last non-blank line and every other
    line stays as stated. The surface's whitespace is collapsed so a newline
    in it cannot open a section. `text` has already parsed, so it has a Goal.
    """
    lines = text.splitlines()
    start = next(
        i for i, line in enumerate(lines)
        if (match := _SECTION_RE.match(line)) and match.group(1) == REQUIRED[0]
    )
    end = next(
        (i for i in range(start + 1, len(lines)) if _SECTION_RE.match(lines[i])), len(lines)
    )
    while not lines[end - 1].strip():
        end -= 1
    lines[end:end] = ["", f"Part of epic {parent_id}. Surface: {' '.join(surface.split())}."]
    return "\n".join(lines) + "\n"


def file_split(hive_dir: str, parent_id: str, subtasks: list[SubTask]) -> list[str]:
    """File a split: one `<parent>-S<n>` task per entry, and the parent an epic.

    `docs/plans/task-split.md` section 2. Each sub-task is `pending`, tagged
    with its `epic`, with `depends_on` from the entry's `after` and its ask in
    its own `description.md`, never in the frontmatter. Returns the sub-task
    ids in list order.

    Everything is checked before anything is written, and every problem is
    reported in one `SplitError`. Filing the same split again writes nothing:
    a description is written only where none is stored, a sub-task file only
    where there is none (its phases may have run), and the parent only while
    its `split_into` is not this split's already — so a run that died part
    way is finished by running it again, and an epic closed since stays
    closed. The parent is never created here. An unmigrated file raises
    `UnmigratedTaskError`, as from every other writer.
    """
    if not context_transfer._is_single_path_component(parent_id):
        raise ValueError(f"task id {parent_id!r} is not a single path component")
    ids = [f"{parent_id}-S{n}" for n in range(1, len(subtasks) + 1)]
    for task_id in [parent_id] + ids:
        _refuse_unmigrated(hive_dir, task_id)

    problems = list(split_problems(subtasks))
    parent_path = context_transfer.task_file_path(hive_dir, parent_id)
    parent = None
    if not os.path.exists(parent_path):
        problems.append(f"task {parent_id} has no task file")
    else:
        parent = context_transfer.read_task_file(parent_path)
        if parent.epic:
            problems.append(
                f"task {parent_id} is part of epic {parent.epic}; "
                "a task inside an epic cannot become an epic"
            )
        if parent.split_into and parent.split_into != ids:
            problems.append(
                f"task {parent_id} is already split into "
                + ", ".join(str(item) for item in parent.split_into)
            )
    texts: dict[str, str] = {}
    stored: dict[str, str | None] = {}
    for position, (task_id, subtask) in enumerate(zip(ids, subtasks), start=1):
        if not all(
            isinstance(value, str) and value.strip()
            for value in (subtask.description, subtask.surface)
        ):
            continue  # `split_problems` already said so.
        try:
            parse_description(subtask.description)
        except DescriptionError as exc:
            problems.append(f"sub-task {position}: its description {exc}")
            continue
        texts[task_id] = _with_epic_line(subtask.description, parent_id, subtask.surface)
        stored[task_id] = read_description(hive_dir, task_id)
        if stored[task_id] is None:
            if os.path.exists(context_transfer.task_file_path(hive_dir, task_id)):
                problems.append(f"{task_id} already exists with no description")
        elif stored[task_id] != texts[task_id]:
            problems.append(f"{task_id} already exists with a different description")
    if problems:
        raise SplitError(parent_id, problems)

    for task_id, subtask in zip(ids, subtasks):
        # The description first, as `migrate` does.
        if stored[task_id] is None:
            write_description(hive_dir, task_id, texts[task_id])
        path = context_transfer.task_file_path(hive_dir, task_id)
        if not os.path.exists(path):
            _write_task_file(
                path,
                context_transfer.TaskFile(
                    task_id=task_id,
                    status="pending",
                    owner=None,
                    depends_on=[ids[i - 1] for i in subtask.after],
                    heartbeat=None,
                    body="",
                    epic=parent_id,
                ),
            )
    if parent.split_into != ids:
        parent.status = "epic"
        parent.split_into = ids
        _write_task_file(parent_path, parent)
    return ids


def _legacy_text(old: str) -> str:
    return f"## {REQUIRED[0]}\n\n{old.strip()}\n\n## {REQUIRED[1]}\n\n{LEGACY_DONE_WHEN}\n"


def _lock_is_live(task: context_transfer.TaskFile, ttl_seconds: int) -> bool:
    """Is a dispatcher still writing this task file?

    `release_stale_lock` leaves `status: in_progress` with no heartbeat, and
    `is_lock_expired` answers False for a missing one, so the heartbeat is
    tested first: without one there is no heartbeat thread to race, which is
    how `acquire_lock` reads it too.
    """
    return (
        task.status == "in_progress"
        and task.heartbeat is not None
        and not context_transfer.is_lock_expired(task, ttl_seconds)
    )


def migrate(hive_dir: str, ttl_seconds: int) -> MigrateReport:
    """Move the description out of every task file that still has one, once.

    `description.md` is written first and the task file second, so a run that
    died between the two is repaired by running it again. The task file is
    rewritten by `write_task_file` from what `read_task_file` read, which is
    by construction what the dispatcher's next write would have left. A file
    with no `description` key is not rewritten at all. Anything this cannot
    move without guessing is left untouched and reported in `refused`.

    So is a task whose lock is live — `in_progress` with a heartbeat younger
    than `ttl_seconds`: its dispatcher's heartbeat thread rewrites the file
    every few seconds, and this rewrite would race it. This is the one caller
    of the raw `write_task_file`, because it is the one that must write over
    a file `_write_task_file` refuses.
    """
    moved: list[tuple[str, str]] = []
    emptied: list[str] = []
    refused: list[tuple[str, str]] = []
    already = 0
    for task_id in sorted(context_transfer.list_task_ids(hive_dir)):
        task_path = context_transfer.task_file_path(hive_dir, task_id)
        try:
            fm = _read_frontmatter(task_path)
            if not isinstance(fm, dict):
                raise ValueError("its frontmatter is not a mapping")
            if _LEGACY_KEY not in fm:
                already += 1
                continue
            task = context_transfer.read_task_file(task_path)
            live = _lock_is_live(task, ttl_seconds)
        except (OSError, TypeError, ValueError, KeyError, yaml.YAMLError) as exc:
            refused.append((task_id, f"unreadable task file: {exc!r}"))
            continue
        if live:
            refused.append(
                (
                    task_id,
                    f"its lock is live (owner {task.owner}): a dispatcher is still running "
                    f"it; run `{MIGRATE_COMMAND}` again when that cycle has ended",
                )
            )
            continue
        old = fm[_LEGACY_KEY]
        if old is None or (isinstance(old, str) and not old.strip()):
            context_transfer.write_task_file(task_path, task)
            emptied.append(task_id)
            continue
        if not isinstance(old, str):
            refused.append(
                (task_id, f"its description is a {type(old).__name__}, not text; migrate by hand")
            )
            continue
        text = _legacy_text(old)
        try:
            intact = parse_description(text).goal == old.strip()
        except DescriptionError:
            intact = False
        if not intact:
            refused.append(
                (task_id, "its description already has a section heading; migrate by hand")
            )
            continue
        try:
            path = description_path(hive_dir, task_id)
            with open(path, encoding="utf-8", newline="") as fh:
                existing: str | None = fh.read()
        except (FileNotFoundError, NotADirectoryError):
            existing = None
        except (OSError, ValueError) as exc:
            refused.append((task_id, f"cannot read its description file: {exc!r}"))
            continue
        if existing is not None and existing != text:
            refused.append((task_id, f"{path} already exists with different content"))
            continue
        _write(hive_dir, task_id, text)
        context_transfer.write_task_file(task_path, task)
        moved.append((task_id, path))
    return MigrateReport(moved=moved, emptied=emptied, refused=refused, already=already)
