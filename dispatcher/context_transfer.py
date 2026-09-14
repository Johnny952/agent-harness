from __future__ import annotations

import dataclasses
import datetime as dt
import os
from pathlib import Path

import yaml

_FRONTMATTER_DELIM = "---"


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
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content)


def acquire_lock(hive_dir: str, task_id: str, owner: str) -> TaskFile:
    path = task_file_path(hive_dir, task_id)
    if os.path.exists(path):
        task = read_task_file(path)
    else:
        task = TaskFile(task_id=task_id, status="pending", owner=None, depends_on=[], heartbeat=None, body="")
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
    task.body = body
    if depends_on is not None:
        task.depends_on = depends_on
    write_task_file(path, task)
