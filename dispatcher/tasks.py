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

So are the drafts of `docs/plans/autonomy.md` *Decisions*, 9 (ADR 82): a
`draft-specs` run writes a set of them under `<hive root>/board/drafts/<draft
id>/`, `check_drafts` holds them to the section rules, and `accept_drafts`
files the ones the owner accepts as pending tasks with fresh ids. A draft is
not a task: no description is ever stored under a draft id, so nothing that
runs tasks can be pointed at one.
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

from dispatcher import context_transfer, surface
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

#: Under the board, beside the task directories: `drafts/<draft id>/<name>.md`.
DRAFTS_DIR_NAME = "drafts"
#: The record of which drafts of a set were accepted, as which task ids.
ACCEPTED_FILE = "accepted.yaml"
#: The sections a draft must have on top of `REQUIRED`: a task nobody has
#: scoped is the thing a draft exists to avoid (`docs/plans/autonomy.md`
#: *Decisions*, 9), and a Surface naming no path means the whole repo.
DRAFT_REQUIRED = ("Surface", "Out of scope")

_LEGACY_KEY = "description"
#: Shaped like a queue id (`queue._QUEUE_ID`), with its own prefix.
_DRAFT_ID_RE = re.compile(r"^d-\d{8}T\d{6}Z-[0-9a-f]{4}$")
#: A draft's name is its file's stem and what its siblings' `depends_on` say.
_DRAFT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_TASK_NUMBER_RE = re.compile(r"^T-(\d+)$")
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


class DraftError(ValueError):
    """A draft set, or part of one, that will not be filed. One error, every problem."""

    def __init__(self, draft_id: str, problems: list[str]) -> None:
        super().__init__(f"drafts {draft_id}: " + "; ".join(problems))
        self.draft_id = draft_id
        self.problems = list(problems)


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

    Refuses an unmigrated task file (`UnmigratedTaskError`), a text missing
    a required section (`DescriptionError`) and a draft id (`ValueError`),
    writing nothing in any case. The last is what keeps a draft from ever
    being run: `open_cycle` and `queue.plan` refuse a task with no
    description, and a draft id can never get one.
    """
    if is_draft_id(task_id):
        raise ValueError(
            f"{task_id!r} names drafts, not a task; accept them with `dispatch accept-drafts`"
        )
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
    # The block's reason goes with the block: a task taken again is running,
    # whatever stopped it last time (ADR 117).
    task.blocked_reason = None
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


def mark_blocked(hive_dir: str, task_id: str, reason: str | None = None) -> None:
    """Record that this task stopped blocked, and release its lock.

    The body and every other field stay. A task with no file raises, as
    `refresh_heartbeat` does: there is nothing to mark, and creating one here
    would invent a task. `acquire_lock` takes a blocked task again, and clears
    `reason`. A block without one clears a stale reason too (ADR 117).
    """
    path = context_transfer.task_file_path(hive_dir, task_id)
    task = context_transfer.read_task_file(path)
    task.status = "blocked"
    task.owner = None
    task.heartbeat = None
    task.blocked_reason = reason
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


def record_command_verdict(hive_dir: str, task_id: str, verdict: dict) -> None:
    """Append an operator's verdict on one of this task's command requests.

    `verdict` is `{"n", "verdict", "by", "at", "comment", "role", "filed_at"}`
    (`docs/decisions.md` ADR 119); `command_run` refuses a second one on the
    same request before it gets here. Status, lock and body are left as they
    were. A task with no file raises rather than getting one.
    """
    path = context_transfer.task_file_path(hive_dir, task_id)
    task = context_transfer.read_task_file(path)
    task.command_verdicts = [*task.command_verdicts, dict(verdict)]
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


# Drafts (`docs/plans/autonomy.md` *Decisions*, 9; ADR 82).


@dataclasses.dataclass(frozen=True)
class Draft:
    """One drafted task: its name in the set, the siblings it waits for, its description."""

    name: str
    depends_on: list[str]
    description: str


def is_draft_id(task_id: str) -> bool:
    """Is this a draft set's id, or the drafts directory itself, rather than a task id?"""
    return task_id == DRAFTS_DIR_NAME or bool(_DRAFT_ID_RE.match(task_id))


def new_draft_id(now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    return f"d-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:4]}"


def drafts_root(hive_dir: str) -> str:
    return os.path.join(board_root(hive_dir), DRAFTS_DIR_NAME)


def draft_set_dir(hive_dir: str, draft_id: str) -> str:
    """Where a draft set's files live. `ValueError` for an id that is not a draft id."""
    if not _DRAFT_ID_RE.match(draft_id):
        raise ValueError(f"{draft_id!r} is not a draft id")
    return os.path.join(drafts_root(hive_dir), draft_id)


def _draft_text(draft: Draft) -> str:
    fm = yaml.safe_dump({"depends_on": list(draft.depends_on)}, default_flow_style=True).strip()
    delim = context_transfer._FRONTMATTER_DELIM
    return f"{delim}\n{fm}\n{delim}\n{draft.description}"


def write_draft(hive_dir: str, draft_id: str, draft: Draft) -> str:
    """Store one draft in the format the phase is told to write, and return its path.

    The `draft-specs` phase writes these files itself; this is the same
    format from Python, for tests and for a set written by hand. Not checked
    here: `check_drafts` is the one check, whoever wrote the file.
    """
    if not _DRAFT_NAME_RE.match(draft.name):
        raise ValueError(f"draft name {draft.name!r} is not a plain name")
    path = os.path.join(draft_set_dir(hive_dir, draft_id), f"{draft.name}.md")
    context_transfer._makedirs_readable(
        context_transfer.hive_root(hive_dir), os.path.dirname(path)
    )
    context_transfer._write_atomic(path, _draft_text(draft))
    return path


def _natural_key(name: str) -> list[object]:
    """`D2` before `D10`: the order the phase numbered its steps in."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


def _read_draft(path: str, name: str) -> tuple[Draft | None, str | None]:
    """One draft file as a `Draft`, or the reason it is not one."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"draft {name}: cannot be read: {exc}"
    delim = context_transfer._FRONTMATTER_DELIM
    if not text.startswith(delim):
        return None, f"draft {name}: has no frontmatter; start it with `{delim}`"
    try:
        _, fm_text, body = text.split(delim, 2)
        fm = yaml.safe_load(fm_text)
    except (ValueError, yaml.YAMLError) as exc:
        return None, f"draft {name}: its frontmatter does not parse: {exc}"
    fm = {} if fm is None else fm
    if not isinstance(fm, dict):
        return None, f"draft {name}: its frontmatter is not a mapping"
    deps = fm.get("depends_on")
    deps = [] if deps is None else deps
    if not isinstance(deps, list) or not all(isinstance(dep, str) for dep in deps):
        return None, f"draft {name}: depends_on must be a list of draft names"
    return Draft(name=name, depends_on=list(deps), description=body.lstrip("\n")), None


def read_drafts(hive_dir: str, draft_id: str) -> tuple[list[Draft], list[str]]:
    """Every draft of a set in name order, and why each file that is not one is not.

    A `.md` file is a draft; nothing else in the directory is read
    (`accepted.yaml` among them). A set with no directory has no drafts.
    """
    directory = draft_set_dir(hive_dir, draft_id)
    try:
        entries = os.listdir(directory)
    except FileNotFoundError:
        return [], []
    drafts: list[Draft] = []
    problems: list[str] = []
    for entry in sorted(entries, key=_natural_key):
        if not entry.endswith(".md"):
            continue
        name = entry[: -len(".md")]
        if not _DRAFT_NAME_RE.match(name):
            problems.append(f"{entry}: {name!r} is not a plain draft name")
            continue
        draft, problem = _read_draft(os.path.join(directory, entry), name)
        if problem:
            problems.append(problem)
        else:
            drafts.append(draft)
    return drafts, problems


def draft_problems(draft: Draft, names: set[str]) -> list[str]:
    """What keeps one draft from being filed, given the names in its set.

    The section rules of a description plus `DRAFT_REQUIRED`, a Surface that
    names at least one path (`surface.parse`, *Decisions*, 5), and a
    `depends_on` that names only other drafts of the same set.
    """
    problems: list[str] = []
    try:
        parsed: Description | None = parse_description(draft.description)
    except DescriptionError as exc:
        problems.append(f"draft {draft.name}: the task description {exc}")
        parsed = None
    if parsed is not None:
        missing = [name for name in DRAFT_REQUIRED if getattr(parsed, _field(name)) is None]
        if missing:
            named = " and ".join(f"`## {name}`" for name in missing)
            problems.append(
                f"draft {draft.name}: is missing its {named} section"
                f"{'s' if len(missing) > 1 else ''}"
            )
        elif surface.is_whole_repo(surface.parse(parsed.surface)):
            # Empty is the whole repo, which no queue can run beside anything.
            problems.append(
                f"draft {draft.name}: its `## Surface` names no path; name the files or "
                "directories it touches, in backticks"
            )
    if draft.name in draft.depends_on:
        problems.append(f"draft {draft.name}: depends on itself")
    unknown = [dep for dep in draft.depends_on if dep not in names]
    if unknown:
        problems.append(
            f"draft {draft.name}: depends on {', '.join(unknown)}, which is not in this set"
        )
    return problems


def _cycle(drafts: list[Draft]) -> list[str] | None:
    """A dependency cycle among these drafts, as names, or None. Self-loops are left out:
    `draft_problems` names them on their own."""
    edges = {d.name: [dep for dep in d.depends_on if dep != d.name] for d in drafts}
    done: set[str] = set()
    path: list[str] = []

    def visit(name: str) -> list[str] | None:
        if name in path:
            return path[path.index(name):] + [name]
        if name in done or name not in edges:
            return None
        path.append(name)
        for dep in edges[name]:
            found = visit(dep)
            if found:
                return found
        path.pop()
        done.add(name)
        return None

    for name in edges:
        found = visit(name)
        if found:
            return found
    return None


def check_drafts(hive_dir: str, draft_id: str) -> list[Draft]:
    """Every draft of a set when the whole set holds, else `DraftError` naming every problem.

    What `draft-specs` runs on what its phase wrote. `accept_drafts` holds
    only the drafts it is filing to the rules, so one bad draft does not stop
    the owner from accepting the others with `--only`.
    """
    drafts, problems = read_drafts(hive_dir, draft_id)
    if not drafts and not problems:
        raise DraftError(draft_id, [f"no drafts in {draft_set_dir(hive_dir, draft_id)}"])
    names = {draft.name for draft in drafts}
    for draft in drafts:
        problems.extend(draft_problems(draft, names))
    cycle = _cycle(drafts)
    if cycle:
        problems.append("the drafts depend on each other in a cycle: " + " -> ".join(cycle))
    if problems:
        raise DraftError(draft_id, problems)
    return drafts


def accepted_drafts(hive_dir: str, draft_id: str) -> dict[str, str]:
    """Which drafts of a set were accepted, by name, as which task ids."""
    path = os.path.join(draft_set_dir(hive_dir, draft_id), ACCEPTED_FILE)
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a mapping of draft name to task id")
    return {str(name): str(task_id) for name, task_id in data.items()}


def _next_task_number(hive_dir: str) -> tuple[int, int]:
    """The number after the highest `T-<n>` on the board, and the width to pad it to.

    The task files and the description directories are both read: either
    can exist without the other, and a fresh id has to be fresh in both.
    """
    seen = list(context_transfer.list_task_ids(hive_dir))
    with contextlib.suppress(FileNotFoundError, NotADirectoryError):
        seen.extend(os.listdir(board_root(hive_dir)))
    numbers = [match.group(1) for name in seen if (match := _TASK_NUMBER_RE.match(name))]
    if not numbers:
        return 1, 3
    return max(int(n) for n in numbers) + 1, max(3, max(len(n) for n in numbers))


def accept_drafts(
    hive_dir: str, draft_id: str, only: list[str] | None = None
) -> dict[str, str]:
    """File drafts as `pending` tasks with fresh ids, and return draft name to task id.

    Every draft not yet accepted, or only the ones `only` names. Each gets the
    next free `T-<n>` in name order, its description stored as drafted, and
    its `depends_on` rewritten from sibling names to their task ids. A
    sibling it depends on has to be accepted in the same call or have been
    accepted before: a dependency on a draft is one nothing can ever merge.

    Everything is checked before anything is written, and every problem is
    reported in one `DraftError`. The ids go into `accepted.yaml` before any
    task is written, so a run that died part way is finished by running it
    again, with the same ids, and an accepted draft whose task file exists is
    never filed twice. An unmigrated file raises `UnmigratedTaskError`, as
    from every other writer.
    """
    drafts, problems = read_drafts(hive_dir, draft_id)
    by_name = {draft.name: draft for draft in drafts}
    record = accepted_drafts(hive_dir, draft_id)

    def filed(name: str) -> bool:
        return name in record and os.path.exists(
            context_transfer.task_file_path(hive_dir, record[name])
        )

    if only is None:
        targets = [draft for draft in drafts if not filed(draft.name)]
        if not targets and not problems:
            problems.append(
                "every draft is already accepted" if drafts
                else f"no drafts in {draft_set_dir(hive_dir, draft_id)}"
            )
    else:
        # A file nobody asked for is not judged: that is what `--only` is for.
        problems = []
        unknown = [name for name in only if name not in by_name]
        if unknown:
            problems.append(f"no draft named {', '.join(unknown)} in this set")
        targets = [by_name[name] for name in sorted(set(only) & set(by_name), key=_natural_key)]
        problems.extend(
            f"draft {draft.name}: already accepted as {record[draft.name]}"
            for draft in targets if filed(draft.name)
        )
    target_names = {draft.name for draft in targets}
    for draft in targets:
        problems.extend(draft_problems(draft, set(by_name)))
        waiting = [
            dep for dep in draft.depends_on
            if dep in by_name and dep != draft.name
            and dep not in target_names and dep not in record
        ]
        if waiting:
            problems.append(
                f"draft {draft.name}: depends on {', '.join(waiting)}, which is not accepted; "
                "accept it in the same call or first"
            )
    cycle = _cycle(targets)
    if cycle:
        problems.append("the drafts depend on each other in a cycle: " + " -> ".join(cycle))
    if problems:
        raise DraftError(draft_id, problems)

    number, width = _next_task_number(hive_dir)
    for draft in targets:
        # A draft already in the record is a run that died part way: same id.
        if draft.name not in record:
            record[draft.name] = f"T-{number:0{width}d}"
            number += 1
    ids = {draft.name: record[draft.name] for draft in targets}
    for task_id in ids.values():
        _refuse_unmigrated(hive_dir, task_id)
    context_transfer._write_atomic(
        os.path.join(draft_set_dir(hive_dir, draft_id), ACCEPTED_FILE),
        yaml.safe_dump(record, sort_keys=False),
    )

    for draft in targets:
        task_id = ids[draft.name]
        # The description first, as `migrate` and `file_split` do.
        if read_description(hive_dir, task_id) is None:
            write_description(hive_dir, task_id, draft.description)
        path = context_transfer.task_file_path(hive_dir, task_id)
        if not os.path.exists(path):
            _write_task_file(
                path,
                context_transfer.TaskFile(
                    task_id=task_id,
                    status="pending",
                    owner=None,
                    depends_on=[record[dep] for dep in draft.depends_on],
                    heartbeat=None,
                    body="",
                    epic=None,
                ),
            )
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
