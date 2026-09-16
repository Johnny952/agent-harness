from __future__ import annotations

import dataclasses
import datetime as dt
import os
import tempfile
from pathlib import Path

import yaml

_FRONTMATTER_DELIM = "---"


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


def task_file_path(hive_dir: str, task_id: str) -> str:
    return os.path.join(hive_dir, f"{task_id}.md")


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
    )


def write_task_file(path: str, task: TaskFile) -> None:
    fm = {
        "task_id": task.task_id,
        "status": task.status,
        "owner": task.owner,
        "depends_on": task.depends_on,
        "heartbeat": task.heartbeat,
    }
    content = f"{_FRONTMATTER_DELIM}\n{yaml.safe_dump(fm, sort_keys=False)}{_FRONTMATTER_DELIM}\n\n{task.body}"
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


def acquire_lock(hive_dir: str, task_id: str, owner: str, ttl_seconds: int | None = None) -> TaskFile:
    path = task_file_path(hive_dir, task_id)
    if os.path.exists(path):
        task = read_task_file(path)
    else:
        task = TaskFile(task_id=task_id, status="pending", owner=None, depends_on=[], heartbeat=None, body="")
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
    path = task_file_path(hive_dir, task_id)
    if os.path.exists(path):
        task = read_task_file(path)
    else:
        task = TaskFile(task_id=task_id, status="pending", owner=None, depends_on=[], heartbeat=None, body="")
    task.status = new_status
    task.owner = None
    task.heartbeat = None
    # Accumulate phase summaries rather than replacing task.body — the next
    # role needs the full trail of prior phases, not just the last one.
    task.body = f"{task.body}\n\n{body}" if task.body else body
    if depends_on is not None:
        task.depends_on = depends_on
    write_task_file(path, task)
