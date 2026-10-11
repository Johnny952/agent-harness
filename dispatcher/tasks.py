"""The task module: the one writer of the board's files.

`docs/plans/board-service.md` section 5 gives a task three files and one
module that writes them. Two of them are written here: the description — the
ask, in sections, at `<hive root>/board/<task_id>/description.md` — and the
task file itself — status, lock, handoffs — whose writers (`acquire_lock`,
`refresh_heartbeat`, `release_stale_lock`, `handoff`, `set_resolved_debt`,
`file_split`, `mark_merged`, `mark_blocked`) all end in `_write_task_file`.
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
`mark_blocked` records a task that stopped blocked. Nothing calls those four
yet.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import os
import re
from pathlib import Path

import yaml

from dispatcher import context_transfer
# By name: this module's own `handoff` is a task-file writer.
from dispatcher.handoff import SubTask, split_problems

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


def unmet_dependencies(hive_dir: str, task: context_transfer.TaskFile) -> list[str]:
    """Why this task cannot start yet, one line per dependency. Empty means it can.

    A dependency is met once its task file carries a `merged` block; its
    status does not matter, `done` is not merged. Never writes.
    """
    if not isinstance(task.depends_on, list):
        return [f"depends_on is {task.depends_on!r}, not a list"]
    unmet = []
    for dep in task.depends_on:
        if not isinstance(dep, str) or not context_transfer._is_single_path_component(dep):
            unmet.append(f"{dep!r} is not a task id")
            continue
        try:
            merged = context_transfer.read_task_file(
                context_transfer.task_file_path(hive_dir, dep)
            ).merged
        except FileNotFoundError:
            unmet.append(f"{dep} has no task file")
        except (OSError, TypeError, ValueError, KeyError, yaml.YAMLError):
            unmet.append(f"{dep}: its task file cannot be read")
        else:
            if not merged:
                unmet.append(f"{dep} is not merged")
    return unmet


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
