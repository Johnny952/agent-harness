from __future__ import annotations

import dataclasses
import datetime as dt
import os
import tempfile
from pathlib import Path

import yaml

_FRONTMATTER_DELIM = "---"


class _TaskDumper(yaml.SafeDumper):
    """SafeDumper that emits multi-line strings as block scalars.

    A task description is usually several lines; safe_dump's default for
    those is a single-quoted scalar with the newlines folded, which makes
    the task file unreadable for the human who wrote the description and
    for anyone debugging a run. Subclassed rather than registered on
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
    """Raised when acquire_lock is asked to take a task whose lock is live under a different owner."""

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
    # The task as the operator stated it, kept in the frontmatter rather
    # than in `body`: handoff() appends each phase summary to `body`, so a
    # description written there would be duplicated on every re-run of the
    # same task id, and the roles could no longer tell the original ask
    # apart from what previous roles said about it. Last field (and last in
    # the frontmatter) so existing TaskFile(...) constructions still work.
    description: str | None = None
    # The uuid of the Vibe Kanban issue this task shows up as, when there is
    # a board at all. It lives here rather than in a dispatcher-side map
    # because the task file is what survives a restart, and because the id is
    # server-assigned: the harness can't derive it from the task id.
    kanban_issue_id: str | None = None


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


def ensure_scratch_dir(hive_dir: str, task_id: str) -> str:
    path = scratch_dir(hive_dir, task_id)
    os.makedirs(path, exist_ok=True)
    return path


def list_task_ids(hive_dir: str) -> list[str]:
    if not os.path.isdir(hive_dir):
        return []
    return [Path(f).stem for f in os.listdir(hive_dir) if f.endswith(".md")]


def read_task_file(path: str) -> TaskFile:
    text = Path(path).read_text()
    _, fm_text, body = text.split(_FRONTMATTER_DELIM, 2)
    fm = yaml.safe_load(fm_text) or {}
    return TaskFile(
        task_id=fm["task_id"],
        status=fm["status"],
        owner=fm.get("owner"),
        depends_on=fm.get("depends_on", []),
        heartbeat=fm.get("heartbeat"),
        body=body.lstrip("\n"),
        # .get, not [...]: task files written before descriptions existed
        # have no such key and must stay readable.
        description=fm.get("description"),
        kanban_issue_id=fm.get("kanban_issue_id"),
    )


def _read_or_new(hive_dir: str, task_id: str) -> tuple[str, TaskFile]:
    path = task_file_path(hive_dir, task_id)
    if os.path.exists(path):
        return path, read_task_file(path)
    return path, TaskFile(
        task_id=task_id, status="pending", owner=None, depends_on=[], heartbeat=None, body=""
    )


def write_task_file(path: str, task: TaskFile) -> None:
    fm = {
        "task_id": task.task_id,
        "status": task.status,
        "owner": task.owner,
        "depends_on": task.depends_on,
        "heartbeat": task.heartbeat,
    }
    if task.kanban_issue_id is not None:
        fm["kanban_issue_id"] = task.kanban_issue_id
    if task.description is not None:
        # Last key so the (multi-line) description sits next to the body,
        # with the short bookkeeping fields readable above it.
        fm["description"] = task.description
    dumped = yaml.dump(fm, Dumper=_TaskDumper, sort_keys=False, default_flow_style=False)
    content = f"{_FRONTMATTER_DELIM}\n{dumped}{_FRONTMATTER_DELIM}\n\n{task.body}"
    parent = Path(path).parent
    parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file in the same directory so os.replace is atomic, and
    # give it a non-".md" suffix so a leftover from a crash is never picked up
    # by list_task_ids as a task.
    tmp = tempfile.NamedTemporaryFile(mode="w", dir=parent, prefix=f".{Path(path).stem}.", suffix=".tmp", delete=False)
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


def set_description(hive_dir: str, task_id: str, description: str) -> None:
    """Store the operator's task description, creating the task file if needed.

    Everything else on an existing file is preserved: re-seeding a task
    that already has phase history in its body only replaces the ask.
    """
    path, task = _read_or_new(hive_dir, task_id)
    task.description = description
    write_task_file(path, task)


def read_description(hive_dir: str, task_id: str) -> str | None:
    """The stored description, or None when the task file doesn't exist yet."""
    return _read_or_new(hive_dir, task_id)[1].description


def set_kanban_issue_id(hive_dir: str, task_id: str, issue_id: str) -> None:
    """Point this task at a Vibe Kanban issue, creating the task file if needed.

    Like set_description, everything else on an existing file is preserved:
    attaching a board to a task that already ran only adds the id.
    """
    path, task = _read_or_new(hive_dir, task_id)
    task.kanban_issue_id = issue_id
    write_task_file(path, task)


def read_kanban_issue_id(hive_dir: str, task_id: str) -> str | None:
    """The issue this task mirrors, or None when it mirrors none."""
    return _read_or_new(hive_dir, task_id)[1].kanban_issue_id


def acquire_lock(hive_dir: str, task_id: str, owner: str, ttl_seconds: int | None = None) -> TaskFile:
    path, task = _read_or_new(hive_dir, task_id)
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
        if ttl_seconds is None or not (heartbeat_missing or is_lock_expired(task, ttl_seconds)):
            raise LockHeldError(task_id, task.owner)
    task.owner = owner
    task.heartbeat = dt.datetime.now(dt.timezone.utc).isoformat()
    if task.status == "pending":
        task.status = "in_progress"
    write_task_file(path, task)
    return task


def refresh_heartbeat(hive_dir: str, task_id: str) -> None:
    path = task_file_path(hive_dir, task_id)
    task = read_task_file(path)
    task.heartbeat = dt.datetime.now(dt.timezone.utc).isoformat()
    write_task_file(path, task)


def is_lock_expired(task: TaskFile, ttl_seconds: int, now: dt.datetime | None = None) -> bool:
    if task.heartbeat is None:
        return False
    now = now or dt.datetime.now(dt.timezone.utc)
    heartbeat_dt = dt.datetime.fromisoformat(task.heartbeat)
    return (now - heartbeat_dt).total_seconds() > ttl_seconds


def release_stale_lock(hive_dir: str, task_id: str) -> None:
    path = task_file_path(hive_dir, task_id)
    task = read_task_file(path)
    task.owner = None
    task.heartbeat = None
    write_task_file(path, task)


def handoff(
    hive_dir: str,
    task_id: str,
    new_status: str,
    body: str,
    depends_on: list[str] | None = None,
) -> None:
    path, task = _read_or_new(hive_dir, task_id)
    task.status = new_status
    task.owner = None
    task.heartbeat = None
    # Accumulate phase summaries rather than replacing task.body — the next
    # role needs the full trail of prior phases, not just the last one.
    task.body = f"{task.body}\n\n{body}" if task.body else body
    if depends_on is not None:
        task.depends_on = depends_on
    write_task_file(path, task)
