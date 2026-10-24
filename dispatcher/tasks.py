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
epic once every sub-task is settled, merged or dropped (ADR 134), and
`epic_problems` checks one written by hand. The dispatcher calls all of
them; `file_split` only through `dispatcher._file_split`
(`docs/implementations/T-031.md`).

So are the drafts of `docs/plans/autonomy.md` *Decisions*, 9 (ADR 82): a
`draft-specs` run writes a set of them under `<hive root>/board/drafts/<draft
id>/`, `check_drafts` holds them to the section rules, and `accept_drafts`
files the ones the owner accepts as pending tasks with fresh ids. A draft is
not a task: no description is ever stored under a draft id, so nothing that
runs tasks can be pointed at one.

The task file also says which phase is running on it (`phase`, ADR 126), and
it rides on the lock rather than having writers of its own: `acquire_lock`
writes the role with the owner and the heartbeat, and the three writers that
drop the lock — `release_stale_lock`, `handoff`, `mark_blocked` — clear it with
them. A phase that ends, however it ends, ends in one of those, so a `phase`
is never left behind by a live dispatcher. A killed one leaves the lock and
the role together, and the lock's own reaper clears both:
`dispatcher.reap_expired_locks` at the next cycle, or `dispatch
release-account` by hand. Until then the heartbeat is past its TTL, which is
how a reader tells a dead phase from a running one.

The backlog of `docs/plans/board-service.md` section 4 is the same task files
(ADR 127): `BACKLOG_STATUSES` are the statuses of what is proposed and not yet
accepted, and `priority` and `source` the two fields an item carries, checked
by `field_problems`. Nothing in the backlog runs: `dispatch_problems` is what
`open_cycle` and `queue.plan` refuse a task for, and `acquire_lock` refuses a
backlog task itself, as the backstop.

The board service's write tools (section 6, ADR 129) end here too: `propose`
files a task or an epic as `proposed`, `edit_task` changes one before it has
started, `add_comment` appends to its `comments.jsonl` and `accept` is the
person's move out of the backlog. Each checks everything before it writes and
refuses with one `BoardWriteError`. A new id is taken under the `CREATE_LOCK`
write lock, which `accept_drafts` holds as well, so two creates never take
the same number.

Every task-file writer holds the task's write lock (`task_lock`) across its
read and its write, so two writers — the heartbeat thread and the phase it
runs under, two dispatcher processes, and the board service —
cannot both read the same file and the second write the first one's change
away. It is a `flock` on `<hive root>/locks/<task_id>.lock`, a different thing from the
ownership the frontmatter records: the `owner` and `heartbeat` that
`acquire_lock` writes say which dispatcher drives the task, for the length of
a phase, and `phase` rides on them; the write lock is held for one
read-modify-write and says nothing once it is let go.
"""
from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import fcntl
import functools
import json
import os
import re
import socket
import threading
import time
import uuid
from pathlib import Path

import yaml

from dispatcher import context_transfer, debt, notifications, surface
# By name: this module's own `handoff` is a task-file writer.
from dispatcher.handoff import COMMAND_REQUEST as COMMAND_REQUEST_REASON

# The `blocked_reason` of a cycle that stopped at its start's spending limit
# (ADR 230, `docs/plans/board-service.md` "Spending limit").
BUDGET_REACHED = "budget_reached"
from dispatcher.handoff import _SPLIT_MAX, _SPLIT_MIN, SubTask, split_problems

# `_makedirs_readable`, `_write_atomic`, `_is_single_path_component`,
# `_read_or_new` and `_FRONTMATTER_DELIM` are private to `context_transfer` and used here on
# purpose: the two modules are one seam mid-move, and renaming the helpers
# would touch every doc that cites them.

BOARD_DIR_NAME = "board"
#: The task write locks' directory under the hive root (`task_lock`, ADR 127).
LOCKS_DIR_NAME = "locks"
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

#: The backlog's statuses (section 4): proposed and not accepted, put off,
#: and dropped. A task with any other status is on the board.
BACKLOG_STATUSES = ("proposed", "deferred", "dropped")
#: The status a task has to be accepted into before it may run.
ACCEPTED_STATUS = "pending"
#: The status the pre-merge check gives a task whose merge it stopped
#: (`dispatcher.CONFLICT_STATUS`, which is this one): here because `handoff`
#: notifies the move into it (ADR 221).
CONFLICT_STATUS = "conflict"
#: The status a cycle that passed the auditor and was not merged ends in: the
#: task waits for a person's review (`docs/plans/review.md` step 1, ADR 240).
AWAITING_REVIEW_STATUS = "awaiting_review"
#: The priority buckets, highest first; `rank` is the order inside one (ADR 130).
PRIORITIES = ("high", "normal", "low")
#: The bucket of a task that names none: every task filed before the field,
#: and a debt entry filed with no impact (section 4).
DEFAULT_PRIORITY = "normal"
#: Who proposed an item. `follow_up` is the dispatcher filing a revisor-ruled
#: follow-up (ADR 393); no agent can claim it, the board sets the caller's role.
SOURCES = ("operator", "arquitecto", "debt", "follow_up")
#: Where `accept` takes a backlog item from, and where it may put one
#: (section 6's `backlog_accept`). `dropped` is final.
ACCEPT_FROM = ("proposed", "deferred")
ACCEPT_TO = (ACCEPTED_STATUS, "deferred", "dropped")
#: The gap between two ranks a write hands out (ADR 130): a new item enters its
#: bucket at the highest rank there plus this, so an item can later be put
#: between two others with one write, at any rank in the gap.
RANK_STEP = 1024
#: The highest rank a write takes: the largest integer a JSON client holds
#: exactly, so the console never reads back another number than it wrote.
MAX_RANK = 2**53 - 1
#: The most tags a task carries, and the longest one. A tag is a word to group
#: by, `debt` among them (step 5d), not a sentence.
MAX_TAGS = 20
MAX_TAG_CHARS = 50
#: The longest title a board write stores. A title is one line to scan.
MAX_TITLE_CHARS = 200
#: The longest comment `add_comment` appends. A comment is a note, not a file.
MAX_COMMENT_CHARS = 20_000
#: The longest comment author: a role, or `operator`.
MAX_AUTHOR_CHARS = 64
#: Under `locks/`, the lock every write that creates a task id or sets a title
#: holds (ADR 129), so the scan for a free id or a clashing title and the write
#: are one step. Not a task id: none starts with `_`.
CREATE_LOCK = "_create"
#: The tag every item filed from the debt index carries (ADR 131).
DEBT_TAG = "debt"
#: The comments' file, beside the description (section 2).
COMMENTS_FILE = "comments.jsonl"
#: The keys that make a `comments.jsonl` line a comment: `add_comment`'s three.
COMMENT_KEYS = ("at", "author", "text")
#: The verdicts `docs/plans/review.md` names: *comment* (step 3), *approve*
#: (step 4) and *request changes* (step 6, ADR 272). `VERDICT_KINDS_BUILT` is
#: what the writer takes; all three are, now.
VERDICT_KINDS = ("comment", "approve", "request_changes")
VERDICT_KINDS_BUILT = ("comment", "approve", "request_changes")
#: Where a review happens: a task waiting for review, one whose merge stopped
#: on a conflict, or an older `done` one not on main (ADR 240).
REVIEW_STATUSES = (AWAITING_REVIEW_STATUS, "done", CONFLICT_STATUS)

_LEGACY_KEY = "description"
#: A comment's `commit`: an abbreviated or full git object name.
_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
#: Shaped like a queue id (`queue._QUEUE_ID`), with its own prefix.
_DRAFT_ID_RE = re.compile(r"^d-\d{8}T\d{6}Z-[0-9a-f]{4}$")
#: A draft's name is its file's stem and what its siblings' `depends_on` say.
_DRAFT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_TASK_NUMBER_RE = re.compile(r"^T-(\d+)$")
#: A debt index entry's id, `debt.entry_id`'s shape: a task id and `-D<n>`.
_DEBT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*-D\d+$")
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


class NotMergedError(ValueError):
    """`mark_closed` on a task with no `merged` block (ADR 255)."""

    def __init__(self, task_id: str) -> None:
        super().__init__(
            f"task {task_id} has no `merged` block, so close-task does not close it; "
            "`merge-task` is the way to merge it"
        )
        self.task_id = task_id


class BacklogTaskError(ValueError):
    """A backlog task asked to run: `acquire_lock` refuses it, as `open_cycle` does."""

    def __init__(self, task_id: str, status: str) -> None:
        super().__init__(
            f"task {task_id} is in the backlog (status {status}); "
            f"only an accepted ({ACCEPTED_STATUS}) task may run"
        )
        self.task_id = task_id
        self.status = status


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


#: The longest title `display_title` serves when it falls back to the Goal's
#: first line. A row is meant to be scanned, and the whole ask is one read away.
#: Not `MAX_TITLE_CHARS`, which bounds what a write stores.
DISPLAY_TITLE_CHARS = 100


def display_title(title: object, description: str | None) -> str | None:
    """A task's frontmatter `title` when there is one, else the Goal's first line, else None.

    `backlog_propose` and `task_edit` write one (ADR 129); a task filed before
    them has none, and the Goal's first non-blank line stands in, cut to
    `DISPLAY_TITLE_CHARS`, so a list of ids reads as a list of asks (ADR 125).
    `title` is the raw frontmatter value (`TaskFile.title`): anything but a
    string with text falls through to the Goal. One rule for the board
    service's `board_list` and the read api's task rows, so the two answer one
    task the same way (ADR 133, ADR 137).
    """
    if isinstance(title, str) and title.strip():
        return title.strip()
    if description is None:
        return None
    try:
        goal = parse_description(description).goal
    except DescriptionError:
        return None
    for line in goal.splitlines():
        line = line.strip()
        if line:
            if len(line) <= DISPLAY_TITLE_CHARS:
                return line
            return line[: DISPLAY_TITLE_CHARS - 1] + "…"
    return None


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


def locks_root(hive_dir: str) -> str:
    """The write locks' directory, a sibling of the hive tasks dir and the board."""
    return os.path.join(context_transfer.hive_root(hive_dir), LOCKS_DIR_NAME)


def task_lock_path(hive_dir: str, task_id: str) -> str:
    """The task's write lock, `<hive root>/locks/<task_id>.lock`.

    In a directory of its own, not beside the task file or under the board:
    `list_task_ids` and `_next_task_number` list those, and whoever reads the
    hive's tasks directory reads task files only. `ValueError` for an id that
    is not one path component.
    """
    if not context_transfer._is_single_path_component(task_id):
        raise ValueError(f"task id {task_id!r} is not a single path component")
    return os.path.join(locks_root(hive_dir), f"{task_id}.lock")


#: The write locks this thread holds, by path, so a writer that calls another
#: writer on the same task does not wait on itself: a second `flock` on a new
#: descriptor of the same file blocks even in the process that holds the first.
_held_task_locks = threading.local()


@contextlib.contextmanager
def task_lock(hive_dir: str, task_id: str):
    """Hold the task's write lock (ADR 127) for one read-modify-write.

    An exclusive `flock`, as `_base_lock_guard` takes, and blocking: a writer
    holds it for one read and one atomic write, so a wait is that long.
    Re-entrant within a thread; another thread of the same process waits, which
    is what serializes the heartbeat thread with the phase under it. The
    file is opened read-only, which `flock` needs no more than, so a reader of
    another uid that can see the file can take the lock too, and made 0644
    whatever the umask by whoever creates it. It is never removed: removing a
    lock file another process has open and is waiting on would hand the next
    writer a fresh, unlocked file.
    """
    path = task_lock_path(hive_dir, task_id)
    held = getattr(_held_task_locks, "paths", None)
    if held is None:
        held = _held_task_locks.paths = set()
    if path in held:
        yield
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        fd = os.open(path, os.O_RDONLY)
    else:
        os.fchmod(fd, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        held.add(path)
        try:
            yield
        finally:
            held.discard(path)
    finally:
        os.close(fd)


def _locked(writer):
    """Run `writer(hive_dir, task_id, ...)` under that task's write lock."""

    @functools.wraps(writer)
    def wrapper(hive_dir: str, task_id: str, *args, **kwargs):
        with task_lock(hive_dir, task_id):
            return writer(hive_dir, task_id, *args, **kwargs)

    return wrapper


def field_problems(task: context_transfer.TaskFile) -> list[str]:
    """What is wrong with the backlog fields of a task file: empty when nothing.

    `priority` and `source`, when present, have to be one of `PRIORITIES` and
    `SOURCES`. Absent is fine for both: a task filed before the fields has
    neither, and its bucket is `DEFAULT_PRIORITY`. Validation on read, for the
    one path outside this module, the operator's hand edits (section 5);
    a writer that sets either field checks it here first.
    """
    problems = []
    if task.priority is not None and task.priority not in PRIORITIES:
        problems.append(
            f"its priority is {task.priority!r}, not one of {', '.join(PRIORITIES)}"
        )
    if task.source is not None and task.source not in SOURCES:
        problems.append(f"its source is {task.source!r}, not one of {', '.join(SOURCES)}")
    if task.tags is not None:
        problems.extend(f"its {problem}" for problem in tag_problems(task.tags))
    if task.rank is not None and not _is_rank(task.rank):
        problems.append(f"its rank is {task.rank!r}, not an integer from 0 to {MAX_RANK}")
    if task.debt_id is not None and not is_debt_id(task.debt_id):
        problems.append(f"its debt_id is {task.debt_id!r}, not a debt entry id like T-001-D1")
    for name in ("dropped_reason", "dropped_by", "proposed_by"):
        value = getattr(task, name)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            problems.append(f"its {name} is {value!r}, not a non-blank string")
    if task.project is not None and not is_project_slug(task.project):
        problems.append(f"its project is {task.project!r}, not a project slug")
    return problems


_PROJECT_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def is_project_slug(value: object) -> bool:
    """A project's slug, one directory name: the rule `projects.is_slug` holds the record to."""
    return isinstance(value, str) and bool(_PROJECT_SLUG.match(value))


def is_debt_id(value: object) -> bool:
    """Is this a debt index entry's id, `T-<n>-D<m>` as `debt.entry_id` makes them?"""
    return isinstance(value, str) and bool(_DEBT_ID_RE.match(value))


def tag_problems(tags: object) -> list[str]:
    """What is wrong with a `tags` value: empty when nothing.

    A list of at most `MAX_TAGS` strings, each with text once stripped, on one
    line and at most `MAX_TAG_CHARS` long. Surrounding blanks and repeats are
    not problems: `normal_tags` drops them (ADR 130).
    """
    if not isinstance(tags, list):
        return [f"tags is {tags!r}, not a list of strings"]
    problems = []
    if len(tags) > MAX_TAGS:
        problems.append(f"tags has {len(tags)} entries, more than {MAX_TAGS}")
    for tag in tags:
        if not isinstance(tag, str):
            problems.append(f"tags names {tag!r}, which is not a string")
        elif not tag.strip():
            problems.append("tags has a blank entry")
        elif "\n" in tag or "\r" in tag:
            problems.append(f"tags has {tag!r}, which is more than one line")
        elif len(tag.strip()) > MAX_TAG_CHARS:
            problems.append(f"tags has an entry longer than {MAX_TAG_CHARS} characters")
    return problems


def normal_tags(tags: object) -> list[str]:
    """`tags` stripped, with repeats dropped in first-seen order; [] for none.

    Entries that are not text, or are blank, are left out: they are named by
    `tag_problems`, and a list is still served. Case is kept and counts, so
    `Debt` and `debt` are two tags.
    """
    if not isinstance(tags, list):
        return []
    out: list[str] = []
    for tag in tags:
        if isinstance(tag, str) and tag.strip() and tag.strip() not in out:
            out.append(tag.strip())
    return out


def _is_rank(rank: object) -> bool:
    # `type() is int`, so True is not rank 1.
    return type(rank) is int and 0 <= rank <= MAX_RANK


def effective_priority(task: context_transfer.TaskFile) -> str:
    """The task's bucket: its `priority`, or `DEFAULT_PRIORITY` when it names none."""
    return task.priority if task.priority is not None else DEFAULT_PRIORITY


def bucket_of(task: context_transfer.TaskFile) -> str:
    """The bucket a task sorts in: `effective_priority`, and `DEFAULT_PRIORITY` for a bad one.

    A hand edit's `urgent` is warned about by `field_problems`; it does not
    also move the task out of every bucket.
    """
    priority = effective_priority(task)
    return priority if priority in PRIORITIES else DEFAULT_PRIORITY


def rank_of(task: context_transfer.TaskFile) -> int:
    """The task's position in its bucket: its `rank`, or 0 when it has none or a bad one.

    Every task filed before ADR 130 has none, and sorts by id ahead of the
    ones ranked since, which is the order they were filed in.
    """
    return task.rank if _is_rank(task.rank) else 0


def _id_key(task_id: str) -> tuple:
    """Ids in number order: `T-99` before `T-100`, `T-5-S2` before `T-5-S10`."""
    return tuple(
        (0, int(part), "") if part.isdigit() else (1, 0, part)
        for part in re.split(r"(\d+)", task_id) if part
    )


def order_key(task_id: str, bucket: str, rank: int) -> tuple:
    """The board's order (section 4): the bucket, highest first, then the rank, then the id."""
    return (PRIORITIES.index(bucket), rank, _id_key(task_id))


def effective_buckets(tasks: "dict[str, context_transfer.TaskFile]") -> dict[str, str]:
    """Every task's bucket for the Ready order: its own, raised by what waits on it.

    A dependency takes the highest bucket of the tasks that depend on it,
    directly or through others, and a task of an epic takes its epic's
    bucket when that is higher than its own: work a `high` item waits on is
    `high` work (section 4, "dependencies come before priority"). Raised
    only, never lowered. Pure: `tasks` is every task file read, by id, and a
    `depends_on` or `epic` naming an id not in it is ignored.

    Only a task out of the backlog lifts: a `proposed` item's bucket is the
    proposer's guess until the person accepts it, and a `deferred` or
    `dropped` one is not work anything waits for.

    A worklist that only ever raises a task, and a task can be raised at most
    `len(PRIORITIES) - 1` times, so a `depends_on` cycle a hand edit made ends
    like any other graph: every task in it gets the cycle's highest bucket.
    """
    level = {task_id: PRIORITIES.index(bucket_of(task)) for task_id, task in tasks.items()}
    # Who lifts whom: a dependent lifts each task it depends on, an epic
    # lifts each of its tasks.
    lifts: dict[str, list[str]] = {task_id: [] for task_id in tasks}
    for task_id, task in tasks.items():
        if is_backlog(task):
            continue
        if isinstance(task.depends_on, list):
            lifts[task_id].extend(
                dep for dep in task.depends_on if isinstance(dep, str) and dep in tasks
            )
        if isinstance(task.epic, str) and task.epic in tasks and not is_backlog(tasks[task.epic]):
            lifts[task.epic].append(task_id)
    work = list(tasks)
    while work:
        task_id = work.pop()
        for lifted in lifts[task_id]:
            if level[task_id] < level[lifted]:
                level[lifted] = level[task_id]
                work.append(lifted)
    return {task_id: PRIORITIES[index] for task_id, index in level.items()}


def ready_order(tasks: "dict[str, context_transfer.TaskFile]") -> list[str]:
    """The Ready column, in the order work should be taken: ids. Pure; never reads a file.

    A task is ready when it is `pending`, not merged, and every task in its
    `depends_on` is in `tasks` and merged; an epic itself is never ready, its
    tasks are. They are ordered by `effective_buckets`, then their own `rank`,
    then id. `tasks` is every task file read, by id: the buckets need the
    tasks that are not ready too, since they are what lifts the rest.
    """
    buckets = effective_buckets(tasks)
    ready = []
    for task_id, task in tasks.items():
        if task.status != ACCEPTED_STATUS or task.merged:
            continue
        deps = task.depends_on
        if not isinstance(deps, list) or not all(
            isinstance(dep, str) and dep in tasks and tasks[dep].merged for dep in deps
        ):
            continue
        ready.append(task_id)
    return sorted(
        ready, key=lambda task_id: order_key(task_id, buckets[task_id], rank_of(tasks[task_id]))
    )


def read_all_tasks(hive_dir: str) -> dict[str, context_transfer.TaskFile]:
    """Every task file that reads, by id. One that does not is left out, as a check would."""
    out = {}
    for task_id in context_transfer.list_task_ids(hive_dir):
        task, _ = _read_for_check(hive_dir, task_id)
        if task is not None:
            out[task_id] = task
    return out


def _end_of_bucket(
    hive_dir: str, bucket: str, exclude: "set[str] | frozenset[str]" = frozenset()
) -> int:
    """The rank a task entering `bucket` gets: the highest rank there plus `RANK_STEP`.

    Read across every task file, backlog and board, so one priority orders
    everything (section 4); an unranked task counts as rank 0. The caller
    holds `CREATE_LOCK`, as every write that hands out a rank does, so two
    writes never take the same end (ADR 130). `exclude` is the tasks being
    moved, whose old rank is not a place in the new bucket.
    """
    ranks = [
        rank_of(task)
        for task_id, task in read_all_tasks(hive_dir).items()
        if task_id not in exclude and bucket_of(task) == bucket
    ]
    return min(max(ranks, default=0) + RANK_STEP, MAX_RANK)


def is_backlog(task: context_transfer.TaskFile) -> bool:
    return task.status in BACKLOG_STATUSES


def dispatch_problems(task: context_transfer.TaskFile) -> list[str]:
    """Why this task may not be run, from its own file: empty when it may.

    A backlog task is not accepted yet, so it is the person's to accept, not
    the dispatcher's to run (section 4); and a field a hand edit got wrong is
    named here rather than met later. `open_cycle` and `queue.plan` refuse on
    any of these; the dependency and epic checks are `unmet_dependencies` and
    `epic_problems`, which need the rest of the hive.
    """
    problems = []
    if is_backlog(task):
        problems.append(
            f"it is in the backlog (status {task.status}): a person accepts it to "
            f"{ACCEPTED_STATUS} first"
        )
    return problems + field_problems(task)


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


@_locked
def set_resolved_debt(hive_dir: str, task_id: str, entries: list[str]) -> None:
    """Record which debt entries this task's work resolved.

    Replaces rather than appends: a re-run's implementation is the current
    claim about what this task fixes, and the previous round's claim was
    about code that has since been rewritten.
    """
    path, task = context_transfer._read_or_new(hive_dir, task_id)
    task.resolved_debt = list(entries)
    _write_task_file(path, task)


@_locked
def acquire_lock(
    hive_dir: str,
    task_id: str,
    owner: str,
    ttl_seconds: int | None = None,
    phase: str | None = None,
) -> context_transfer.TaskFile:
    """Take the task's lock for `owner`, and record which phase holds it.

    `phase` is the role about to run (ADR 126). It is written with the lock and
    replaces whatever was there, so a call without one leaves no stale role
    behind a new owner.
    """
    path, task = context_transfer._read_or_new(hive_dir, task_id)
    # The backstop for `open_cycle`'s refusal (ADR 127): every phase comes
    # through here, whichever entry point started it.
    if is_backlog(task):
        raise BacklogTaskError(task_id, task.status)
    # Two dispatcher processes, or a stale retry, must not both drive the same
    # task. The check and the write are one step under the task's write lock
    # (`_locked`, ADR 127), so two takers cannot both read it free.
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
    task.phase = phase
    _write_task_file(path, task)
    return task


@_locked
def refresh_heartbeat(hive_dir: str, task_id: str) -> None:
    path = context_transfer.task_file_path(hive_dir, task_id)
    task = context_transfer.read_task_file(path)
    task.heartbeat = dt.datetime.now(dt.timezone.utc).isoformat()
    _write_task_file(path, task)


@_locked
def release_stale_lock(hive_dir: str, task_id: str) -> None:
    path = context_transfer.task_file_path(hive_dir, task_id)
    task = context_transfer.read_task_file(path)
    task.owner = None
    task.heartbeat = None
    # The phase goes with the lock that names it (ADR 126): this is the
    # release of a phase that failed, and the reaper of one whose dispatcher died.
    task.phase = None
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


@_locked
def handoff(
    hive_dir: str,
    task_id: str,
    new_status: str,
    body: str,
    depends_on: list[str] | None = None,
) -> None:
    path, task = context_transfer._read_or_new(hive_dir, task_id)
    was = task.status
    task.status = new_status
    task.owner = None
    task.heartbeat = None
    task.phase = None
    # Accumulate phase summaries rather than replacing task.body — the next
    # role needs the full trail of prior phases, not just the last one.
    task.body = f"{task.body}\n\n{body}" if task.body else body
    if depends_on is not None:
        task.depends_on = depends_on
    _write_task_file(path, task)
    # Into a conflict, not a rewrite of one: the automatic approve hands a
    # conflicted task off again, and the person was told the first time.
    if new_status == CONFLICT_STATUS and was != CONFLICT_STATUS:
        notifications.notify_task(hive_dir, "conflict", task_id, "has a conflict", task.title)


@_locked
def append_note(hive_dir: str, task_id: str, body: str) -> None:
    """Add `body` under the task's trail, every other field as it was.

    For what the dispatcher found on a phase that hands nothing off: the
    status, the owner and the lock are the failure path's to settle (ADR 273).
    A task with no file raises, as `mark_blocked` does.
    """
    path = context_transfer.task_file_path(hive_dir, task_id)
    task = context_transfer.read_task_file(path)
    task.body = f"{task.body}\n\n{body}" if task.body else body
    _write_task_file(path, task)


@_locked
def mark_blocked(
    hive_dir: str, task_id: str, reason: str | None = None, *, failed: bool = False,
    spent: str | None = None,
) -> None:
    """Record that this task stopped blocked, and release its lock.

    The body and every other field stay. A task with no file raises, as
    `refresh_heartbeat` does: there is nothing to mark, and creating one here
    would invent a task. `acquire_lock` takes a blocked task again, and clears
    `reason`. A block without one clears a stale reason too (ADR 117).

    And one notification (ADR 221): `phase_failed` when `failed`, the stop of
    a phase that did not finish; `command_request` for a filed request; else
    `blocked`, a stop that waits on the person's decision. A stop at the
    spending limit (`BUDGET_REACHED`, ADR 230) is a `blocked` that says so,
    with `spent`, what the cycle spent, when the caller passes it.
    """
    path = context_transfer.task_file_path(hive_dir, task_id)
    task = context_transfer.read_task_file(path)
    task.status = "blocked"
    task.owner = None
    task.heartbeat = None
    task.phase = None
    task.blocked_reason = reason
    _write_task_file(path, task)
    if failed:
        notifications.notify_task(
            hive_dir, "phase_failed", task_id, "stopped: a phase did not finish", task.title
        )
    elif reason == COMMAND_REQUEST_REASON:
        notifications.notify_task(
            hive_dir, "command_request", task_id, "asks to run a command", task.title
        )
    elif reason == BUDGET_REACHED:
        text = "stopped: its spending limit is reached"
        notifications.notify_task(
            hive_dir, "blocked", task_id, f"{text} ({spent})" if spent else text, task.title
        )
    else:
        notifications.notify_task(hive_dir, "blocked", task_id, "is blocked", task.title)


@_locked
def mark_awaiting_review(hive_dir: str, task_id: str) -> bool:
    """Move a `done` task not on main to `awaiting_review`, and say so.

    The cycle's end calls it when the task passed the auditor and nothing
    merged it (ADR 240). Returns whether it moved: any other status, and a
    `done` task already merged, is left alone and not notified. A task with
    no file raises rather than getting one.
    """
    path = context_transfer.task_file_path(hive_dir, task_id)
    task = context_transfer.read_task_file(path)
    if task.status != "done" or task.merged:
        return False
    task.status = AWAITING_REVIEW_STATUS
    _write_task_file(path, task)
    notifications.notify_task(
        hive_dir, "awaiting_review", task_id, "is waiting for your review", task.title
    )
    return True


@_locked
def reopen_for_changes(hive_dir: str, task_id: str) -> bool:
    """Move an `awaiting_review` or `conflict` task not on main to `pending`.

    ADR 272, and ADR 278 for `conflict`.

    The `request_changes` action calls it before the cycle runs again from
    the arquitecto: the task is no longer waiting for review, so an approve
    queued on the old tip reads as stale, and the arquitecto's lock takes it
    from `pending` as any start's does. Returns whether it moved: any other
    status, and a task already merged, is left alone. Nothing is announced;
    the cycle's end tells the task is waiting for review again. A task with
    no file raises rather than getting one.
    """
    path = context_transfer.task_file_path(hive_dir, task_id)
    task = context_transfer.read_task_file(path)
    if task.status not in (AWAITING_REVIEW_STATUS, CONFLICT_STATUS) or task.merged:
        return False
    task.status = "pending"
    _write_task_file(path, task)
    return True


@_locked
def mark_merged(hive_dir: str, task_id: str, target: str) -> None:
    """Record on the task that its branch was merged into `target`.

    `docs/plans/task-split.md` section 3, "Merged is recorded on the task":
    this block, not `status: done`, is what a dependency waits for. Status,
    lock and body are left as they were, except that `awaiting_review` becomes
    `done` (ADR 240); a second call replaces the block. A task with no file
    raises rather than getting one.
    """
    path = context_transfer.task_file_path(hive_dir, task_id)
    task = context_transfer.read_task_file(path)
    first = not task.merged
    task.merged = {"into": target, "at": dt.datetime.now(dt.timezone.utc).isoformat()}
    if task.status == AWAITING_REVIEW_STATUS:
        task.status = "done"
    _write_task_file(path, task)
    # The first record only: `merge-task` run again repairs or replaces the
    # block, and the merge it describes was told already (ADR 221).
    if first:
        notifications.notify_task(hive_dir, "merged", task_id, f"merged into {target}", task.title)


@_locked
def mark_closed(hive_dir: str, task_id: str) -> str | None:
    """Set `status: done` on a merged task whose status was left behind (ADR 255).

    Returns the status it moved from, or None when the task was already done.
    Only `status` changes: owner, heartbeat, phase, blocked_reason and body stay,
    and nothing is announced. A task with no `merged` block raises
    `NotMergedError`, an unmigrated one `UnmigratedTaskError`, a task with no
    file `FileNotFoundError`; the file is left as it was in each case.
    """
    _refuse_unmigrated(hive_dir, task_id)
    path = context_transfer.task_file_path(hive_dir, task_id)
    task = context_transfer.read_task_file(path)
    if not task.merged:
        raise NotMergedError(task_id)
    if task.status == "done":
        return None
    was = task.status
    task.status = "done"
    _write_task_file(path, task)
    return was


@_locked
def record_command_verdict(hive_dir: str, task_id: str, verdict: dict) -> None:
    """Append an operator's verdict on one of this task's command requests.

    `verdict` is `{"n", "verdict", "by", "at", "comment", "role", "filed_at"}`
    (`docs/decisions.md` ADR 119), and `update_command_verdict` adds to it
    later (ADR 120); `command_run` refuses a second one on the
    same request before it gets here. Status, lock and body are left as they
    were. A task with no file raises rather than getting one.
    """
    path = context_transfer.task_file_path(hive_dir, task_id)
    task = context_transfer.read_task_file(path)
    task.command_verdicts = [*task.command_verdicts, dict(verdict)]
    _write_task_file(path, task)


@_locked
def update_command_verdict(hive_dir: str, task_id: str, role: str, filed_at: object, fields: dict) -> None:
    """Merge `fields` into the verdict on the block `role` filed at `filed_at`.

    What `command_run` keeps on a verdict after it is recorded: `ran`, what a
    *run* came to, and `resumed_at`, when the resumed phase took the task
    (ADR 120). Status, lock and body are left as they were. A task with no
    file, or no verdict on that block, raises rather than getting one.
    """
    path = context_transfer.task_file_path(hive_dir, task_id)
    task = context_transfer.read_task_file(path)
    for index, item in enumerate(task.command_verdicts):
        if item.get("role") == role and item.get("filed_at") == filed_at:
            entries = list(task.command_verdicts)
            entries[index] = {**item, **fields}
            task.command_verdicts = entries
            _write_task_file(path, task)
            return
    raise KeyError(f"task {task_id} has no verdict on the block the {role} filed at {filed_at}")


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


#: The status `close_epic` gives an epic none of whose sub-tasks merged (ADR 134).
EPIC_ALL_DROPPED_STATUS = "dropped"


def is_settled(task: context_transfer.TaskFile) -> bool:
    """Whether a sub-task no longer holds its epic open: merged, or dropped.

    ADR 134. Every other status waits, a `deferred` one included: it is an
    open decision of the person's, and closing its epic under it would strand
    it, since `accept` takes a task of an epic only while the epic is `epic`
    and `epic_problems` refuses to run one under a closed epic.
    """
    return bool(task.merged) or task.status == "dropped"


def close_epic(hive_dir: str, task_id: str) -> str | None:
    """Close the epic this task is part of, if it was the last one to settle.

    `docs/plans/task-split.md` section 3, the epic check both merge paths run
    after `mark_merged`, and `accept` runs after it drops a task of an epic
    (ADR 134). The parent is closed when this task has `epic`, the parent is
    `status: epic` and names it in `split_into`, and every id there has a task
    file that `is_settled`: merged, or dropped. It becomes `status: done` when
    at least one of them merged, and `EPIC_ALL_DROPPED_STATUS` when none did,
    since nothing of it shipped; its lock, body and other fields stay.
    Returns the parent's id when it closed it and None when it wrote nothing:
    a parent already closed is left alone, a child still pending, running,
    blocked or deferred keeps it open, and a file that is missing or will not
    read closes nothing.
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
    merged = False
    for child_id in children:
        child, _ = _read_for_check(hive_dir, child_id)
        if child is None or not is_settled(child):
            return None
        merged = merged or bool(child.merged)
    # The parent's lock, and only that: no writer takes a parent's lock while
    # holding a child's, so `file_split`, which holds its parent's while it
    # writes the children, cannot meet this one the other way round. The
    # parent is read again under it, so a write since the check is not lost.
    with task_lock(hive_dir, task.epic):
        parent_path = context_transfer.task_file_path(hive_dir, task.epic)
        parent = context_transfer.read_task_file(parent_path)
        if parent.status != "epic":
            return None
        parent.status = "done" if merged else EPIC_ALL_DROPPED_STATUS
        _write_task_file(parent_path, parent)
        # The *Merged, and epic closed* kind's second half (ADR 221).
        notifications.notify_task(
            hive_dir, "merged", task.epic,
            "closed: every sub-task is merged or dropped" if merged
            else "closed: every sub-task was dropped",
            parent.title,
        )
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


@_locked
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
        # The child's lock inside the parent's (`close_epic` says why that
        # order is the only one), around the check so a file made since is kept.
        with task_lock(hive_dir, task_id):
            if os.path.exists(path):
                continue
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
                    # A subtask is work on its epic's project.
                    project=parent.project,
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

    # The create lock (ADR 129) from the scan for a free id to the last task
    # file: a `backlog_propose` meanwhile would otherwise take the same number.
    with task_lock(hive_dir, CREATE_LOCK):
        number, width = _next_task_number(hive_dir)
        # Filed `pending` with no priority: at the end of `normal`, in name
        # order, as a proposal enters its bucket (ADR 130).
        rank = _end_of_bucket(hive_dir, DEFAULT_PRIORITY)
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
            with task_lock(hive_dir, task_id):
                if os.path.exists(path):
                    continue
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
                        rank=rank,
                    ),
                )
                rank = min(rank + RANK_STEP, MAX_RANK)
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
        # Each file under its write lock: the liveness check and the write are
        # one step, so a dispatcher that takes the task meanwhile is not written over.
        with task_lock(hive_dir, task_id):
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


@dataclasses.dataclass(frozen=True)
class StampReport:
    """What `stamp_project` did: `refused` is (task id, reason)."""

    stamped: list[str]
    refused: list[tuple[str, str]]
    already: int


def _card_project(hive_dir: str, task_id: str) -> tuple[bool, object]:
    """(readable, the `project` its frontmatter has) for one card, read as YAML made it."""
    try:
        fm = _read_frontmatter(context_transfer.task_file_path(hive_dir, task_id))
    except (OSError, ValueError, yaml.YAMLError):
        return False, None
    if not isinstance(fm, dict):
        return False, None
    return True, fm.get("project")


def unstamped_ids(hive_dir: str) -> list[str]:
    """The cards (tasks, epics, backlog items) that name no project, sorted."""
    return sorted(
        task_id for task_id in context_transfer.list_task_ids(hive_dir)
        if _card_project(hive_dir, task_id) == (True, None)
    )


def stamped_count(hive_dir: str) -> int:
    """How many cards already name a project."""
    return sum(
        1 for task_id in context_transfer.list_task_ids(hive_dir)
        if _card_project(hive_dir, task_id)[1] is not None
    )


def stamp_project(hive_dir: str, project: str, ttl_seconds: int) -> StampReport:
    """Give every card that names no project `project` (ADR 199), once.

    The other half of `dispatch migrate` after the records: a card with no
    `project` belongs to the one project the harness had before (projects.md,
    Decisions 3), and this writes that down on it. A card that names one is
    not rewritten, so a second run changes nothing. Each card under its write
    lock, with `migrate`'s guards: a live lock is refused rather than raced,
    and a card that still carries its description in the frontmatter is left
    for `migrate` to move first, since rewriting it here would drop it. The
    second caller of the raw `write_task_file`, for `migrate`'s reason: only
    the one key is added to what `read_task_file` read.
    """
    if not is_project_slug(project):
        raise ValueError(f"{project!r} is not a project slug")
    stamped: list[str] = []
    refused: list[tuple[str, str]] = []
    already = 0
    for task_id in sorted(context_transfer.list_task_ids(hive_dir)):
        with task_lock(hive_dir, task_id):
            task_path = context_transfer.task_file_path(hive_dir, task_id)
            try:
                fm = _read_frontmatter(task_path)
                if not isinstance(fm, dict):
                    raise ValueError("its frontmatter is not a mapping")
                if fm.get("project") is not None:
                    already += 1
                    continue
                if _LEGACY_KEY in fm:
                    refused.append((
                        task_id,
                        f"it still carries its description; `{MIGRATE_COMMAND}` moves it "
                        "first, then run it again",
                    ))
                    continue
                task = context_transfer.read_task_file(task_path)
                live = _lock_is_live(task, ttl_seconds)
            except (OSError, TypeError, ValueError, KeyError, yaml.YAMLError) as exc:
                refused.append((task_id, f"unreadable task file: {exc!r}"))
                continue
            if live:
                refused.append((
                    task_id,
                    f"its lock is live (owner {task.owner}): a dispatcher is still running "
                    f"it; run `{MIGRATE_COMMAND}` again when that cycle has ended",
                ))
                continue
            context_transfer.write_task_file(task_path, dataclasses.replace(task, project=project))
            stamped.append(task_id)
    return StampReport(stamped=stamped, refused=refused, already=already)


# The board's write tools (`docs/plans/board-service.md` section 6, ADR 129).
# `board_service` serves them as `backlog_propose`, `task_edit`, `task_comment`
# and `backlog_accept`. None moves a task once it is on the board, and none
# dispatches: `in_progress`, `phase`, `done`, `blocked` and `merged` stay the
# dispatcher's and the merge's.


class BoardWriteError(ValueError):
    """A board write that will not be made. One error, every problem; nothing was written."""

    def __init__(self, subject: str, problems: list[str]) -> None:
        super().__init__(f"{subject}: " + "; ".join(problems))
        self.subject = subject
        self.problems = list(problems)


class NoTaskError(BoardWriteError):
    """A board write naming a task that has no task file."""

    def __init__(self, task_id: str) -> None:
        super().__init__(f"task {task_id}", ["there is no such task"])
        self.task_id = task_id


def comments_path(hive_dir: str, task_id: str) -> str:
    """The task's `comments.jsonl`, beside its description. `ValueError` for a bad id."""
    return os.path.join(os.path.dirname(description_path(hive_dir, task_id)), COMMENTS_FILE)


def parse_comment_line(line: bytes) -> tuple[dict | None, str | None]:
    """One non-blank `comments.jsonl` line: `(value, None)`, or `(None, problem)`.

    A line is a comment when it is a JSON object with a string `at`, `author`
    and `text`, the three `add_comment` always writes; anything else is the
    problem, worded to follow the file and its line in a warning. The value is
    the whole object, unknown keys and a broken diff anchor included: what a
    reader serves of it is the reader's. The one rule both the readers and the
    counts apply, so a count is the comments a reader shows (ADR 172).
    """
    try:
        value = json.loads(line)
    except ValueError as exc:
        # `UnicodeDecodeError` is a `ValueError`, so bytes that are not UTF-8
        # land here too.
        return None, f"not a JSON line ({exc}), skipped"
    if not isinstance(value, dict):
        return None, f"not a JSON object but a {type(value).__name__}, skipped"
    missing = [key for key in COMMENT_KEYS if not isinstance(value.get(key), str)]
    if missing:
        return None, f"no string {', '.join(missing)}, skipped"
    return value, None


def count_comments(path: str, problems: list[str] | None = None) -> int:
    """The comments in the `comments.jsonl` at `path`, by `parse_comment_line`.

    Blank lines are not counted and not named; every other line that is not a
    comment is appended to `problems`, when given, as `<path>: line <n>:
    <problem>`, the read api's words for the same line. `OSError` propagates.
    """
    count = 0
    with open(path, "rb") as fh:
        for number, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            _, problem = parse_comment_line(line)
            if problem is None:
                count += 1
            elif problems is not None:
                problems.append(f"{path}: line {number}: {problem}")
    return count


#: The verdicts that close a review: the comments a request for changes
#: carries are the ones after the last of these (ADR 272).
_REVIEW_ENDING_VERDICTS = ("approve", "request_changes")


def review_comments(hive_dir: str, task_id: str, commit: str) -> list[dict]:
    """The comments a request for changes on `commit` carries (ADR 272).

    The last `request_changes` verdict on `commit`, and before it the same
    author's comments since the verdict before it that closed a review (an
    approve or an earlier request for changes): what the person wrote while
    reviewing this tip, diff comments included, in the order written. `[]`
    when there is no such verdict. Lines that are not comments are skipped, as
    every reader skips them. `OSError` propagates, and a missing file is `[]`.
    """
    try:
        with open(comments_path(hive_dir, task_id), "rb") as fh:
            lines = [parse_comment_line(line)[0] for line in fh if line.strip()]
    except FileNotFoundError:
        return []
    comments = [c for c in lines if c is not None]
    for index in range(len(comments) - 1, -1, -1):
        verdict = comments[index].get("verdict")
        if (
            isinstance(verdict, dict)
            and verdict.get("kind") == "request_changes"
            and verdict.get("commit") == commit
        ):
            break
    else:
        return []
    author = comments[index]["author"]
    start = index
    while start > 0:
        verdict = comments[start - 1].get("verdict")
        if isinstance(verdict, dict) and verdict.get("kind") in _REVIEW_ENDING_VERDICTS:
            break
        start -= 1
    return [c for c in comments[start:index] if c["author"] == author] + [comments[index]]


def _title_problems(title: object, what: str) -> list[str]:
    if not isinstance(title, str) or not title.strip():
        return [f"{what} is blank"]
    if "\n" in title or "\r" in title:
        return [f"{what} is more than one line"]
    if len(title.strip()) > MAX_TITLE_CHARS:
        return [f"{what} is longer than {MAX_TITLE_CHARS} characters"]
    return []


def _description_problems(text: object, what: str) -> list[str]:
    if not isinstance(text, str):
        return [f"{what} is not text"]
    try:
        parse_description(text)
    except DescriptionError as exc:
        return [f"{what} {exc}"]
    return []


def _priority_problems(priority: object) -> list[str]:
    if priority is not None and priority not in PRIORITIES:
        return [f"priority is {priority!r}, not one of {', '.join(PRIORITIES)}"]
    return []


def _check_id(task_id: object) -> str:
    """A task id a board write may name, or `BoardWriteError`."""
    if not isinstance(task_id, str) or not context_transfer._is_single_path_component(task_id):
        raise BoardWriteError(f"task {task_id!r}", ["that is not a task id"])
    if is_draft_id(task_id):
        raise BoardWriteError(
            f"task {task_id}",
            ["that names drafts, not a task; accept them with `dispatch accept-drafts`"],
        )
    return task_id


def _board_task(hive_dir: str, task_id: str) -> tuple[str, context_transfer.TaskFile]:
    """The task file's path and contents. `NoTaskError` when there is none."""
    path = context_transfer.task_file_path(hive_dir, task_id)
    if not os.path.isfile(path):
        raise NoTaskError(task_id)
    _refuse_unmigrated(hive_dir, task_id)
    return path, context_transfer.read_task_file(path)


def _find_titled(
    hive_dir: str, title: str, epic: str | None, exclude: str | None = None
) -> tuple[str, context_transfer.TaskFile] | None:
    """The task titled `title` in epic `epic` (None: outside any epic), if one is.

    The scope section 6's idempotence is on, "its title within its epic". A
    task with no `title` (one filed before ADR 129) never matches, and a file
    that will not read is skipped: it is not a title anyone can see.
    """
    for task_id in sorted(context_transfer.list_task_ids(hive_dir)):
        if task_id == exclude:
            continue
        task, _ = _read_for_check(hive_dir, task_id)
        if task is not None and task.title == title and (task.epic or None) == epic:
            return task_id, task
    return None


def _depends_problems(hive_dir: str, task_id: str | None, deps: object) -> list[str]:
    """What is wrong with a `depends_on` naming tasks outside any epic."""
    if not isinstance(deps, list) or not all(isinstance(dep, str) for dep in deps):
        return ["depends_on is not a list of task ids"]
    problems = []
    seen: set[str] = set()
    for dep in deps:
        if dep in seen:
            problems.append(f"depends_on names {dep} more than once")
            continue
        seen.add(dep)
        if dep == task_id:
            problems.append("depends_on names the task itself")
        elif is_draft_id(dep):
            problems.append(f"depends_on names {dep}, which is drafts, not a task")
        else:
            dependency, why = _read_for_check(hive_dir, dep)
            if dependency is None:
                problems.append(f"depends_on: {why}")
    return problems


def _reaches(hive_dir: str, start: list[str], target: str) -> bool:
    """Does following `depends_on` from `start` reach `target`? Unreadable files end a path."""
    stack, seen = list(start), set()
    while stack:
        task_id = stack.pop()
        if task_id == target:
            return True
        if task_id in seen:
            continue
        seen.add(task_id)
        task, _ = _read_for_check(hive_dir, task_id)
        if task is not None and isinstance(task.depends_on, list):
            stack.extend(dep for dep in task.depends_on if isinstance(dep, str))
    return False


def _write_new(
    hive_dir: str, task: context_transfer.TaskFile, description: str
) -> None:
    """Write a proposed task's file and description where either is missing.

    The task file first: it is `proposed`, so nothing runs it while its
    description is still to come, and a retry finds it by its title and
    writes the description then (ADR 129). Under the task's write lock, around
    the check, so a file made since is kept.
    """
    path = context_transfer.task_file_path(hive_dir, task.task_id)
    with task_lock(hive_dir, task.task_id):
        if not os.path.exists(path):
            _write_task_file(path, task)
        if read_description(hive_dir, task.task_id) is None:
            write_description(hive_dir, task.task_id, description)


def _proposed(
    task_id: str,
    title: str,
    source: str,
    priority: str | None,
    depends_on: list[str],
    epic: str | None = None,
    split_into: list[str] | None = None,
    tags: list[str] | None = None,
    rank: int | None = None,
    proposed_by: str | None = None,
) -> context_transfer.TaskFile:
    return context_transfer.TaskFile(
        task_id=task_id,
        status="proposed",
        owner=None,
        depends_on=list(depends_on),
        heartbeat=None,
        body="",
        epic=epic,
        split_into=list(split_into or []),
        priority=priority,
        source=source,
        title=title,
        tags=list(tags) if tags else None,
        rank=rank,
        proposed_by=proposed_by,
    )


def _tags_problems(tags: object) -> list[str]:
    return [] if tags is None else tag_problems(tags)


def _rank_problems(rank: object) -> list[str]:
    if rank is not None and not _is_rank(rank):
        return [f"rank is {rank!r}, not an integer from 0 to {MAX_RANK}"]
    return []


def _epic_entries(tasks: object) -> tuple[list[tuple[str, str, list[int]]], list[str]]:
    """An epic's tasks as (title, description, after), and what is wrong with them.

    `after` is 1-based positions of earlier entries, `split_problems`' rule:
    naming only earlier entries is what makes a cycle impossible.
    """
    if not isinstance(tasks, list):
        return [], ["tasks is not a list"]
    problems = []
    if not _SPLIT_MIN <= len(tasks) <= _SPLIT_MAX:
        problems.append(
            f"an epic has {_SPLIT_MIN} to {_SPLIT_MAX} tasks, this one has {len(tasks)}"
        )
    entries: list[tuple[str, str, list[int]]] = []
    titles: list[str] = []
    for position, entry in enumerate(tasks, start=1):
        what = f"task {position}"
        if not isinstance(entry, dict):
            problems.append(f"{what} is not an object")
            continue
        unknown = sorted(set(entry) - {"title", "description", "after"}, key=str)
        if unknown:
            problems.append(f"{what} has an unknown field {unknown[0]!r}")
        title, description = entry.get("title"), entry.get("description")
        after = entry.get("after")
        after = [] if after is None else after
        problems.extend(_title_problems(title, f"{what}: its title"))
        problems.extend(_description_problems(description, f"{what}: its description"))
        if isinstance(title, str) and title.strip():
            if title.strip() in titles:
                problems.append(f"{what}: another task of the epic has the title {title.strip()!r}")
            titles.append(title.strip())
        if not isinstance(after, list):
            problems.append(f"{what}: after is not a list of positions")
            after = []
        seen: list[int] = []
        for item in after:
            # `type() is int`, so True is not position 1.
            if type(item) is not int:
                problems.append(f"{what}: after names {item!r}, which is not an integer")
            elif item in seen:
                problems.append(f"{what}: after names {item} more than once")
            else:
                seen.append(item)
                if not 1 <= item < position:
                    problems.append(f"{what}: after names {item}, which is not a task before it")
        entries.append((title.strip() if isinstance(title, str) else "", description, seen))
    return entries, problems


def propose(
    hive_dir: str,
    *,
    title: str,
    description: str,
    source: str,
    priority: str | None = None,
    depends_on: list[str] | None = None,
    tasks: list[dict] | None = None,
    tags: list[str] | None = None,
    proposed_by: str | None = None,
) -> dict:
    """Section 6's `backlog_propose`: a task, or an epic with its tasks, as `proposed`.

    `proposed_by`, when given, is the person who filed it and goes on every
    file written (ADR 390); a repeat proposal neither compares nor changes it.

    A single task gets the next free `T-<n>`. An epic (`tasks` given) gets one
    for itself, `split_into` and its description holding the goal, and its 2
    to 4 tasks are `<epic>-S<n>`, as `file_split` names them, each `proposed`
    with `epic` set and `depends_on` from its `after`. `source` is required
    (ADR 127, point 9) and `priority` is the proposer's suggestion; both go on
    every file written, as do `tags`, stripped and without repeats. Each file
    written enters its bucket at the end, `_end_of_bucket` under the create
    lock, an epic's tasks after the epic in their order (ADR 130).

    Everything is checked before anything is written, and every problem is
    reported in one `BoardWriteError`, so an epic is all or nothing. A create
    is idempotent on its title within its epic (section 6): a proposal whose
    title a task outside any epic already has is answered with that task,
    `created: false`, when it asks for the same thing — and a run that died
    part way is finished by it — and refused when it asks for something else.
    Returns `{"id", "created", "tasks"}`, the last the epic's task ids.
    """
    problems = _title_problems(title, "its title")
    problems += _description_problems(description, "its description")
    if source not in SOURCES:
        problems.append(f"its source is {source!r}, not one of {', '.join(SOURCES)}")
    problems += _priority_problems(priority)
    problems += _tags_problems(tags)
    if proposed_by is not None and (not isinstance(proposed_by, str) or not proposed_by.strip()):
        problems.append(f"its proposed_by is {proposed_by!r}, not a non-blank string")
    deps: list[str] = [] if depends_on is None else depends_on
    entries: list[tuple[str, str, list[int]]] | None = None
    if tasks is not None:
        if deps:
            problems.append("an epic takes no depends_on; its tasks state theirs in `after`")
        entries, entry_problems = _epic_entries(tasks)
        problems += entry_problems
    else:
        problems += _depends_problems(hive_dir, None, deps)
    if problems:
        raise BoardWriteError("the proposal", problems)
    title = title.strip()
    tags = normal_tags(tags) if tags is not None else None
    bucket = priority if priority is not None else DEFAULT_PRIORITY

    with task_lock(hive_dir, CREATE_LOCK):
        found = _find_titled(hive_dir, title, None)
        if found is not None:
            return _propose_again(hive_dir, found, title, description, deps, entries, source, priority)
        number, width = _next_task_number(hive_dir)
        task_id = f"T-{number:0{width}d}"
        rank = _end_of_bucket(hive_dir, bucket)
        if entries is None:
            _write_new(
                hive_dir,
                _proposed(
                    task_id, title, source, priority, deps, tags=tags, rank=rank,
                    proposed_by=proposed_by,
                ),
                description,
            )
            return {"id": task_id, "created": True, "tasks": []}
        ids = [f"{task_id}-S{n}" for n in range(1, len(entries) + 1)]
        # The parent's lock, then each child's: `close_epic` says why that
        # order is the only one.
        with task_lock(hive_dir, task_id):
            _write_new(
                hive_dir,
                _proposed(
                    task_id, title, source, priority, [], split_into=ids, tags=tags, rank=rank,
                    proposed_by=proposed_by,
                ),
                description,
            )
            for position, (child_id, (child_title, child_text, after)) in enumerate(
                zip(ids, entries), start=1
            ):
                child = _proposed(
                    child_id, child_title, source, priority,
                    [ids[i - 1] for i in after], epic=task_id, tags=tags,
                    rank=min(rank + position * RANK_STEP, MAX_RANK),
                    proposed_by=proposed_by,
                )
                _write_new(hive_dir, child, child_text)
        return {"id": task_id, "created": True, "tasks": ids}


def find_debt_item(hive_dir: str, debt_id: str) -> str | None:
    """The id of the task filed from this debt entry, if one is (ADR 131).

    Any status counts, `dropped` and done included: an entry is filed once,
    and what became of its item is the operator's, not a reason to file again.
    """
    for task_id, task in sorted(read_all_tasks(hive_dir).items()):
        if task.debt_id == debt_id:
            return task_id
    return None


def propose_debt(
    hive_dir: str, *, debt_id: str, title: str, description: str, priority: str,
    depends_on: list[str] | None = None,
) -> dict:
    """A debt index entry as a backlog item: `proposed`, `source: debt` (ADR 131).

    The dispatcher's own write, never a service caller's (ADR 129): it is what
    mirrors an entry the auditor filed, and what `dispatch import-debt` files
    the index with. The item gets the next free `T-<n>`, `tags: [debt]`,
    `debt_id`, and the end of its `priority` bucket, under `CREATE_LOCK` as
    every create is. Idempotent on `debt_id`, not on the title: an entry
    already filed, under any status, is answered with its item and
    `created: false`, and a description a run that died part way did not
    write is written then. `depends_on` (ADR 396) gates a new item on tasks
    that must be readable; an item already filed keeps what it has. Returns
    `{"id", "created"}`.
    """
    deps = list(depends_on or [])
    problems = [] if is_debt_id(debt_id) else [f"its debt_id {debt_id!r} is not a debt entry id"]
    if deps:
        problems += _depends_problems(hive_dir, None, deps)
    problems += _title_problems(title, "its title")
    problems += _description_problems(description, "its description")
    if priority not in PRIORITIES:
        problems.append(f"priority is {priority!r}, not one of {', '.join(PRIORITIES)}")
    if problems:
        raise BoardWriteError(f"the debt entry {debt_id!r}", problems)
    with task_lock(hive_dir, CREATE_LOCK):
        found = find_debt_item(hive_dir, debt_id)
        if found is not None:
            path = context_transfer.task_file_path(hive_dir, found)
            if read_description(hive_dir, found) is None:
                _write_new(hive_dir, context_transfer.read_task_file(path), description)
            return {"id": found, "created": False}
        number, width = _next_task_number(hive_dir)
        task_id = f"T-{number:0{width}d}"
        task = dataclasses.replace(
            _proposed(
                task_id, title.strip(), "debt", priority, deps,
                tags=[DEBT_TAG], rank=_end_of_bucket(hive_dir, priority),
            ),
            debt_id=debt_id,
        )
        _write_new(hive_dir, task, description)
        return {"id": task_id, "created": True}


def file_debt(hive_dir: str, rows: list[dict], *, dry_run: bool = False) -> list[dict]:
    """Each open index row as a backlog item, through `propose_debt` (ADR 131).

    `rows` are `debt.index_rows`; resolved ones and ones with no entry id
    are skipped. One result per row filed or already filed, in the rows'
    order: `{"debt_id", "id", "created", "priority", "title", "blocked_by",
    "gated", "depends_on", "unknown_blocker"}`. With `dry_run` nothing is written: `id` is the
    existing item's or None, and `created` says whether a real run would make
    one. A row's blocker (`debt.blocked_by`, ADR 396) is a task id this reads
    (the item depends on it), a task id it cannot read (filed without, since
    a refusal would lock the import for good), or a plan step (`gated`: no
    item is filed, the row is the record). `unknown_blocker` is true only for
    the second case. A real row looks the entry up once, inside `propose_debt`.
    """
    results = []
    for row in rows:
        if not row.get("id") or row.get("resolved"):
            continue
        title, description, priority = debt.backlog_item(row)
        blocker = debt.blocked_by(row)
        kind = debt.blocker_kind(blocker)
        gated = kind == "step"
        deps = [blocker] if kind == "task" and not _depends_problems(hive_dir, None, [blocker]) else []
        unknown_blocker = kind == "task" and not deps
        if gated:
            result = {"id": find_debt_item(hive_dir, row["id"]), "created": False}
        elif dry_run:
            existing = find_debt_item(hive_dir, row["id"])
            result = {"id": existing, "created": existing is None}
        else:
            result = propose_debt(
                hive_dir, debt_id=row["id"], title=title,
                description=description, priority=priority, depends_on=deps,
            )
        results.append(
            {
                "debt_id": row["id"], **result, "priority": priority, "title": title,
                "blocked_by": blocker, "gated": gated, "depends_on": deps,
                "unknown_blocker": unknown_blocker,
            }
        )
    return results


def _propose_again(
    hive_dir: str,
    found: tuple[str, context_transfer.TaskFile],
    title: str,
    description: str,
    deps: list[str],
    entries: list[tuple[str, str, list[int]]] | None,
    source: str,
    priority: str | None,
) -> dict:
    """A proposal whose title is taken: the same ask, finished; another ask, refused."""
    task_id, task = found
    hint = "change it with task_edit, or propose it under another title"
    if entries is None:
        if task.split_into:
            raise BoardWriteError(f"task {task_id}", [f"an epic already has the title {title!r}"])
        differs = []
        stored = read_description(hive_dir, task_id)
        if stored is not None and stored != description:
            differs.append("description")
        if list(task.depends_on or []) != deps:
            differs.append("depends_on")
        if differs:
            raise BoardWriteError(
                f"task {task_id}",
                [f"it already has the title {title!r}, with another {' and '.join(differs)}; {hint}"],
            )
        if stored is None:
            _write_new(hive_dir, task, description)
        return {"id": task_id, "created": False, "tasks": []}

    if not task.split_into:
        raise BoardWriteError(f"task {task_id}", [f"a task that is not an epic already has the title {title!r}"])
    ids = list(task.split_into)
    differs = []
    stored = read_description(hive_dir, task_id)
    if stored is not None and stored != description:
        differs.append("its description")
    if len(ids) != len(entries):
        differs.append(f"{len(ids)} tasks, not {len(entries)}")
    missing = []
    for position, (child_id, (child_title, child_text, after)) in enumerate(zip(ids, entries), start=1):
        child, _ = _read_for_check(hive_dir, child_id)
        child_deps = [ids[i - 1] for i in after]
        if child is None:
            missing.append((child_id, child_title, child_text, child_deps))
            continue
        child_stored = read_description(hive_dir, child_id)
        if (
            child.title != child_title
            or child.epic != task_id
            or list(child.depends_on or []) != child_deps
            or (child_stored is not None and child_stored != child_text)
        ):
            differs.append(f"another task {position} ({child_id})")
        elif child_stored is None:
            missing.append((child_id, child_title, child_text, child_deps))
    if differs:
        raise BoardWriteError(
            f"epic {task_id}",
            [f"it already has the title {title!r}, with {', '.join(differs)}; {hint}"],
        )
    with task_lock(hive_dir, task_id):
        if stored is None:
            _write_new(hive_dir, task, description)
        for child_id, child_title, child_text, child_deps in missing:
            child_priority = task.priority or priority
            # A child whose file was never written enters its bucket now, at
            # the end; one whose description alone is missing keeps its rank,
            # since `_write_new` keeps the file that is there.
            child = _proposed(
                child_id, child_title, task.source or source, child_priority,
                child_deps, epic=task_id, tags=task.tags,
                rank=_end_of_bucket(hive_dir, child_priority or DEFAULT_PRIORITY),
                proposed_by=task.proposed_by,
            )
            _write_new(hive_dir, child, child_text)
    return {"id": task_id, "created": False, "tasks": ids}


def _started(hive_dir: str, task: context_transfer.TaskFile) -> str | None:
    """Why `task_edit` may no longer change this task, or None while it has not started.

    A backlog item has not. A `pending` one has not while no dispatcher holds
    it, nothing is merged and its task file has no handoff. An epic has not
    while none of its tasks has. Anything else is under way or over.
    """
    if task.status in BACKLOG_STATUSES:
        return None
    if task.status == ACCEPTED_STATUS:
        if task.owner:
            return f"a dispatcher holds it ({task.owner})"
        if task.merged:
            return "it is merged"
        if task.body.strip():
            return "its task file has handoffs"
        return None
    if task.status == "epic" and task.split_into and not task.epic:
        for child_id in task.split_into:
            child, _ = _read_for_check(hive_dir, child_id)
            why = None if child is None else _started(hive_dir, child)
            if why:
                return f"its task {child_id} has started: {why}"
        return None
    return f"its status is {task.status}"


def edit_task(
    hive_dir: str,
    task_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
    depends_on: list[str] | None = None,
    priority: str | None = None,
    tags: list[str] | None = None,
    rank: int | None = None,
) -> dict:
    """Section 6's `task_edit`: title, description, `depends_on`, `priority`, `tags`, `rank`.

    While the task has not started. `None` leaves a field as it is. `tags`
    replaces the list, stripped and without repeats; `[]` clears it. `rank`
    puts the task at that position in its bucket, and a `priority` that moves
    it to another bucket without one puts it at that bucket's end (ADR 130). The title stays unique within its epic,
    so a later propose cannot be confused about what it names. A task of an
    epic depends on its siblings only, and an epic on nothing; outside an
    epic, `depends_on` names tasks that exist and makes no cycle. Every
    problem is reported in one `BoardWriteError` and nothing is written.
    Returns `{"id", "changed"}`, the fields whose value changed.
    """
    task_id = _check_id(task_id)
    given = (title, description, depends_on, priority, tags, rank)
    if all(value is None for value in given):
        raise BoardWriteError(
            f"task {task_id}",
            ["nothing to change: give title, description, depends_on, priority, tags or rank"],
        )
    problems = [] if title is None else _title_problems(title, "the title")
    if description is not None:
        problems += _description_problems(description, "the description")
    problems += _priority_problems(priority)
    problems += _tags_problems(tags)
    problems += _rank_problems(rank)
    if problems:
        raise BoardWriteError(f"task {task_id}", problems)
    title = None if title is None else title.strip()
    # `[]` clears the field, so the file says nothing rather than `tags: []`.
    tags_given = tags is not None
    tags = (normal_tags(tags) or None) if tags_given else None

    # The create lock before the task's, as `propose` takes them, when a title
    # is to be checked against every other one, or a rank is set: every rank
    # write holds it, so the end of a bucket is read with none moving (ADR 130).
    create = (
        task_lock(hive_dir, CREATE_LOCK)
        if title is not None or priority is not None or rank is not None
        else contextlib.nullcontext()
    )
    with create, task_lock(hive_dir, task_id):
        path, task = _board_task(hive_dir, task_id)
        why = _started(hive_dir, task)
        if why:
            problems.append(f"it has started ({why}); task_edit changes a task only before that")
        if title is not None and title != task.title:
            clash = _find_titled(hive_dir, title, task.epic or None, exclude=task_id)
            if clash is not None:
                where = f"epic {task.epic}" if task.epic else "the tasks outside any epic"
                problems.append(f"{clash[0]} already has the title {title!r} in {where}")
        if depends_on is not None:
            problems += _edit_depends_problems(hive_dir, task_id, task, depends_on)
        if problems:
            raise BoardWriteError(f"task {task_id}", problems)

        changed = []
        if description is not None and read_description(hive_dir, task_id) != description:
            write_description(hive_dir, task_id, description)
            changed.append("description")
        old_bucket = bucket_of(task)
        for field, value in (("title", title), ("depends_on", depends_on), ("priority", priority)):
            if value is not None and getattr(task, field) != value:
                setattr(task, field, list(value) if field == "depends_on" else value)
                changed.append(field)
        if tags_given and task.tags != tags:
            task.tags = tags
            changed.append("tags")
        if rank is None and bucket_of(task) != old_bucket:
            rank = _end_of_bucket(hive_dir, bucket_of(task), exclude={task_id})
        if rank is not None and task.rank != rank:
            task.rank = rank
            changed.append("rank")
        if set(changed) - {"description"}:
            _write_task_file(path, task)
    return {"id": task_id, "changed": changed}


def _edit_depends_problems(
    hive_dir: str, task_id: str, task: context_transfer.TaskFile, deps: object
) -> list[str]:
    if task.split_into and not task.epic:
        return ["an epic takes no depends_on; its tasks state theirs"]
    if not task.epic:
        problems = _depends_problems(hive_dir, task_id, deps)
        if not problems:
            problems = [
                f"depends_on names {dep}, which depends on {task_id}: that is a cycle"
                for dep in deps if _reaches(hive_dir, [dep], task_id)
            ]
        return problems
    if not isinstance(deps, list) or not all(isinstance(dep, str) for dep in deps):
        return ["depends_on is not a list of task ids"]
    parent, why = _read_for_check(hive_dir, task.epic)
    if parent is None:
        return [f"its epic {why}"]
    siblings = [child for child in parent.split_into if child != task_id]
    problems = [
        f"depends_on names {dep}, which is not a sibling in epic {task.epic}"
        for dep in deps if dep not in siblings
    ]
    if len(set(deps)) != len(deps):
        problems.append("depends_on names a task more than once")
    problems += [
        f"depends_on names {dep}, which depends on {task_id}: that is a cycle"
        for dep in deps if dep in siblings and _reaches(hive_dir, [dep], task_id)
    ]
    return problems


def add_comment(
    hive_dir: str,
    task_id: str,
    author: str,
    text: str,
    *,
    file: str | None = None,
    line: int | None = None,
    commit: str | None = None,
) -> dict:
    """Section 6's `task_comment`: append one line to the task's `comments.jsonl`.

    The line is `{"at", "author", "text"}`, and on a diff `file`, `line` and
    `commit` too, all three or none (section 2). Any task with a task file may
    be commented on, whatever its status: comments are kept for good. The
    append is one `write` on an `O_APPEND` file under the task's write lock, so
    two comments never interleave. Returns `{"id", "comment", "comment_count"}`.
    """
    task_id = _check_id(task_id)
    problems = _comment_problems(author, text)
    anchor = (file, line, commit)
    if any(value is not None for value in anchor):
        if any(value is None for value in anchor):
            problems.append("a comment on a diff gives file, line and commit, all three")
        else:
            if (
                not isinstance(file, str) or not file.strip() or file != file.strip()
                or "\n" in file or os.path.isabs(file) or ".." in Path(file).parts
            ):
                problems.append(f"file is {file!r}, not a path inside the repository")
            if type(line) is not int or line < 1:
                problems.append(f"line is {line!r}, not a line number")
            if not isinstance(commit, str) or not _COMMIT_RE.match(commit):
                problems.append(f"commit is {commit!r}, not a commit hash")
    if problems:
        raise BoardWriteError(f"task {task_id}", problems)

    comment: dict = {
        "at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "author": author,
        "text": text,
    }
    if file is not None:
        comment.update(file=file, line=line, commit=commit)
    return _append_comment(hive_dir, task_id, comment)


def record_verdict(
    hive_dir: str, task_id: str, author: str, kind: str, text: str, commit: str
) -> dict:
    """A review verdict (`docs/plans/review.md` step 3): one `comments.jsonl` line.

    The line is a comment, `{"at", "author", "text"}`, with `verdict: {"kind",
    "commit"}` beside them: review.md's *Decisions* 3 keeps the review's record
    in `comments.jsonl`, so every reader that counts or lists comments reads a
    verdict too. It is its own key, not the `file`/`line`/`commit` anchor,
    which says a line and comes all three or none. `commit` is the tip the
    person reviewed (*Decisions* 4), as the console's diff named it; its shape
    is checked and nothing more, since this side holds no clone.

    Only the kinds in `VERDICT_KINDS_BUILT` are taken: a "comment" starts
    nothing (*Decisions* 1), so recording it is the whole of it; an "approve"
    and a "request_changes" are followed by their action, queued by the caller
    (ADR 267, ADR 272). The task must be under review, `awaiting_review`,
    `conflict` or `done` and not merged: a verdict on a task not finished, or
    already merged, reviewed nothing. An approve is refused on `conflict` too,
    which waits on resolve conflict. A request for changes is taken on
    `awaiting_review` and `conflict` (ADR 272, ADR 278): it reopens the cycle
    the task came out of, and on a `conflict` it is how the person answers a
    design conflict the integrador will not settle.
    Returns `add_comment`'s `{"id", "comment", "comment_count"}`.
    """
    task_id = _check_id(task_id)
    problems = []
    if kind not in VERDICT_KINDS:
        problems.append(f"the verdict is {kind!r}, not one of {', '.join(VERDICT_KINDS)}")
    elif kind not in VERDICT_KINDS_BUILT:
        problems.append(
            f"the {kind} verdict is not built yet; only {' and '.join(VERDICT_KINDS_BUILT)} are"
        )
    if not isinstance(commit, str) or not _COMMIT_RE.match(commit):
        problems.append(f"commit is {commit!r}, not a commit hash")
    problems += _comment_problems(author, text)
    if problems:
        raise BoardWriteError(f"task {task_id}", problems)

    comment = {
        "at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "author": author,
        "text": text,
        "verdict": {"kind": kind, "commit": commit},
    }

    def under_review(task: context_transfer.TaskFile) -> None:
        if kind == "approve" and task.status == CONFLICT_STATUS:
            raise BoardWriteError(
                f"task {task_id}",
                ["it is conflict: an approve waits on resolve conflict"],
            )
        if (
            kind == "request_changes"
            and not task.merged
            and task.status not in (AWAITING_REVIEW_STATUS, CONFLICT_STATUS)
        ):
            raise BoardWriteError(
                f"task {task_id}",
                [f"it is {task.status}, not awaiting_review or conflict: a request for "
                 "changes reopens a cycle that ended in review"],
            )
        if task.status in REVIEW_STATUSES and not task.merged:
            return
        state = "merged" if task.merged else task.status
        raise BoardWriteError(
            f"task {task_id}",
            [f"it is {state}, not under review: a verdict is for a finished task not on main"],
        )

    return _append_comment(hive_dir, task_id, comment, under_review)


def _comment_problems(author: object, text: object) -> list[str]:
    """What is wrong with a comment's author and text, `add_comment`'s rule."""
    problems = []
    if not isinstance(author, str) or not author.strip() or len(author) > MAX_AUTHOR_CHARS or any(
        ch.isspace() for ch in author
    ):
        problems.append(f"the author is {author!r}, not a role")
    if not isinstance(text, str) or not text.strip():
        problems.append("the text is blank")
    elif len(text) > MAX_COMMENT_CHARS:
        problems.append(f"the text is longer than {MAX_COMMENT_CHARS} characters")
    return problems


def _append_comment(hive_dir: str, task_id: str, comment: dict, check=None) -> dict:
    """Append `comment` as one line under the task's lock; `check(task)` may refuse first."""
    data = (json.dumps(comment, ensure_ascii=False) + "\n").encode("utf-8")
    path = comments_path(hive_dir, task_id)
    with task_lock(hive_dir, task_id):
        _, task = _board_task(hive_dir, task_id)
        if check is not None:
            check(task)
        context_transfer._makedirs_readable(
            context_transfer.hive_root(hive_dir), os.path.dirname(path)
        )
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        count = count_comments(path)
    return {"id": task_id, "comment": comment, "comment_count": count}


def _drop_problems(reason: object, by: object) -> list[str]:
    """What is wrong with a drop's reason and author: the one rule (ADR 366)."""
    problems = []
    if not isinstance(reason, str) or not reason.strip() or len(reason) > MAX_COMMENT_CHARS:
        problems.append("a drop needs a reason (non-blank, at most "
                        f"{MAX_COMMENT_CHARS} characters)")
    problems += [p for p in _comment_problems(by, "x")]
    return problems


def mark_dropped(task: context_transfer.TaskFile, reason: str, by: str) -> None:
    """Drop `task` in place with its reason and who dropped it (ADR 366).

    The one place a drop is made. Takes no lock and writes no file: the caller
    holds the card's `task_lock` and calls `_write_task_file`.
    """
    problems = _drop_problems(reason, by)
    if task.status not in ACCEPT_FROM:
        problems.append(f"it is {task.status}, not one of {', '.join(ACCEPT_FROM)}")
    if problems:
        raise BoardWriteError(f"task {task.task_id}", problems)
    task.status = "dropped"
    task.dropped_reason = reason
    task.dropped_by = by


def close_resolved_debt(hive_dir: str, task_id: str) -> tuple[list[str], list[str]]:
    """Drop the backlog cards mirroring the debt `task_id` resolved (ADR 369).

    Returns `(closed, warnings)`, one line each, and never raises: a failure
    is a warning and the next card goes on. For each id in the task's
    `resolved_debt`, every card with that `debt_id` (the task's own aside) is
    re-read under its lock: `proposed`/`deferred` is dropped by
    `mark_dropped` as `dispatcher`; `dropped` or missing is skipped silently,
    so a re-run is a no-op; any other status is left alone with a warning.
    `close_epic` runs after the locks, as `accept` does (ADR 134).
    """
    closed, warnings, epic_children = [], [], []
    try:
        ids = context_transfer.read_resolved_debt(hive_dir, task_id)
        cards = read_all_tasks(hive_dir) if ids else {}
    except Exception as exc:
        return [], [f"task {task_id}: its resolved debt could not be read: {exc}"]
    for debt_id in ids:
        for card_id in sorted(c for c, t in cards.items() if t.debt_id == debt_id and c != task_id):
            try:
                with task_lock(hive_dir, card_id):
                    try:
                        path, card = _board_task(hive_dir, card_id)
                    except NoTaskError:
                        continue
                    if card.status == "dropped":
                        continue
                    if card.status not in ACCEPT_FROM:
                        warnings.append(
                            f"debt {debt_id}: {task_id} resolved it, but its card "
                            f"{card_id} is {card.status}; left as it is"
                        )
                        continue
                    mark_dropped(card, f"resolved by {task_id}", "dispatcher")
                    _write_task_file(path, card)
                closed.append(f"debt {debt_id}: card {card_id} dropped, resolved by {task_id}")
                if card.epic:
                    epic_children.append(card_id)
            except Exception as exc:
                warnings.append(f"debt {debt_id}: card {card_id} could not be closed: {exc}")
    for card_id in epic_children:
        try:
            epic = close_epic(hive_dir, card_id)
            if epic:
                closed.append(f"epic {epic}: every sub-task is merged or dropped, so it is done")
        except Exception as exc:
            warnings.append(f"card {card_id}: its epic could not be closed: {exc}")
    return closed, warnings


def accept(
    hive_dir: str,
    task_id: str,
    to: str = ACCEPTED_STATUS,
    priority: str | None = None,
    reason: str | None = None,
    by: str | None = None,
) -> dict:
    """Section 6's `backlog_accept`: `proposed`/`deferred` → `pending`, `deferred` or `dropped`.

    A person's act (section 4): the service gives it to the operator alone.
    A drop needs a `reason` and `by`, recorded on the card by `mark_dropped`
    (ADR 366); a `reason` with any other `to` is refused.
    `priority` confirms or changes the proposer's bucket. Asking for the
    status a task already has is a no-op, so a retry is safe. A task on the
    board is refused, whatever `to` says: no tool moves it.

    An epic carries its tasks: accepted, it becomes `status: epic` and each of
    its `proposed` tasks `pending`, while one left `deferred` stays out
    without holding the rest; dropped, its backlog tasks are dropped with it;
    deferred, it goes alone. A task of an epic is accepted on its own only once
    its epic is. Returns `{"id", "status", "changed", "children"}`, the last
    each of an epic's tasks with its status after the call.

    Dropping a task of an epic on the board runs `close_epic` once the locks
    are let go, so the drop that settles an epic's last open task closes it
    (ADR 134); the result then also has `epic_closed`, the epic's id.

    A `priority` that moves an item to another bucket puts it at that
    bucket's end, an epic's tasks after the epic (ADR 130); that read is under
    the create lock, taken before the task's whenever a priority is given.
    """
    task_id = _check_id(task_id)
    problems = []
    if to not in ACCEPT_TO:
        problems.append(f"to is {to!r}, not one of {', '.join(ACCEPT_TO)}")
    problems += _priority_problems(priority)
    if to == "dropped":
        problems += _drop_problems(reason, by)
    elif reason is not None:
        problems.append("a reason is only for a drop")
    if problems:
        raise BoardWriteError(f"task {task_id}", problems)
    create = task_lock(hive_dir, CREATE_LOCK) if priority is not None else contextlib.nullcontext()
    with create, task_lock(hive_dir, task_id):
        path, task = _board_task(hive_dir, task_id)
        if task.split_into and not task.epic:
            return _accept_epic(hive_dir, task_id, path, task, to, priority, reason, by)
        status = task.status
        changed = status != to
        if not changed:
            _refuse_priority_on_noop(task_id, task, to, priority)
        else:
            _refuse_from(task_id, status)
            if to == ACCEPTED_STATUS and task.epic:
                parent, why = _read_for_check(hive_dir, task.epic)
                if parent is None or parent.status != "epic":
                    raise BoardWriteError(
                        f"task {task_id}",
                        [f"its epic {task.epic} is not accepted yet "
                         f"({why or 'status ' + parent.status}); accept the epic"],
                    )
            if to == "dropped":
                mark_dropped(task, reason, by)
            else:
                task.status = to
            if priority is not None:
                _rebucket(hive_dir, [task], priority)
            _write_task_file(path, task)
    result = {"id": task_id, "status": to, "changed": changed, "children": {}}
    # A dropped task of an epic may be the last one holding it open (ADR 134).
    # After both locks are let go: `close_epic` takes the parent's, which is
    # never taken under a child's. A retry of the drop runs it too, so a
    # close that a crash cut off is made then.
    if to == "dropped" and task.epic:
        closed = close_epic(hive_dir, task_id)
        if closed:
            result["epic_closed"] = closed
    return result


def _rebucket(
    hive_dir: str, moving: list[context_transfer.TaskFile], priority: str
) -> None:
    """Set `priority` on each task, and put the ones it moves at the new bucket's end, in order.

    A task already in that bucket keeps its rank: confirming a bucket is not
    a reorder. The caller holds `CREATE_LOCK`.
    """
    end = None
    for task in moving:
        if bucket_of(task) != priority:
            if end is None:
                end = _end_of_bucket(hive_dir, priority, exclude={t.task_id for t in moving})
            else:
                end = min(end + RANK_STEP, MAX_RANK)
            task.rank = end
        task.priority = priority


def _refuse_priority_on_noop(
    task_id: str, task: context_transfer.TaskFile, to: str, priority: str | None
) -> None:
    if priority is not None and effective_priority(task) != priority:
        raise BoardWriteError(
            f"task {task_id}",
            [f"it is already {to}; change its priority with task_edit"],
        )


def _refuse_from(task_id: str, status: str) -> None:
    if status in ACCEPT_FROM:
        return
    if status == "dropped":
        why = "it is dropped, which is final; propose it again"
    else:
        why = f"it is on the board (status {status}); no tool moves a task once it is there"
    raise BoardWriteError(f"task {task_id}", [why])


def _accept_epic(
    hive_dir: str,
    task_id: str,
    path: str,
    task: context_transfer.TaskFile,
    to: str,
    priority: str | None,
    reason: str | None = None,
    by: str | None = None,
) -> dict:
    """`accept` on an epic, under its lock: the parent's, then each child's."""
    current = ACCEPTED_STATUS if task.status == "epic" else task.status
    ids = list(task.split_into)
    with contextlib.ExitStack() as stack:
        for child_id in ids:
            if isinstance(child_id, str) and context_transfer._is_single_path_component(child_id):
                stack.enter_context(task_lock(hive_dir, child_id))
        children: dict[str, tuple[str, context_transfer.TaskFile]] = {}
        problems = []
        for child_id in ids:
            child, why = _read_for_check(hive_dir, child_id)
            if child is None:
                problems.append(f"{why}; propose the epic again to finish it")
            elif child.epic != task_id:
                problems.append(f"{child_id}: its epic is {child.epic or 'not set'}, not {task_id}")
            else:
                children[child_id] = (context_transfer.task_file_path(hive_dir, child_id), child)
        if current == to:
            _refuse_priority_on_noop(task_id, task, to, priority)
            return {
                "id": task_id, "status": task.status, "changed": False,
                "children": {cid: child.status for cid, (_, child) in children.items()},
            }
        _refuse_from(task_id, task.status)
        if problems:
            raise BoardWriteError(f"epic {task_id}", problems)
        moves = {
            ACCEPTED_STATUS: {"proposed": ACCEPTED_STATUS},
            "dropped": {"proposed": "dropped", "deferred": "dropped"},
            "deferred": {},
        }[to]
        moved = [
            (child_path, child) for child_path, child in children.values() if child.status in moves
        ]
        if priority is not None:
            # The epic first, then its tasks in their order, at the bucket's end.
            _rebucket(hive_dir, [task] + [child for _, child in moved], priority)
        for child_path, child in moved:
            if to == "dropped":
                mark_dropped(child, reason, by)
            else:
                child.status = moves[child.status]
            _write_task_file(child_path, child)
        # The parent last, so an epic is on the board only once its tasks are.
        if to == "dropped":
            mark_dropped(task, reason, by)
        else:
            task.status = "epic" if to == ACCEPTED_STATUS else to
        _write_task_file(path, task)
        return {
            "id": task_id, "status": task.status, "changed": True,
            "children": {cid: child.status for cid, (_, child) in children.items()},
        }
