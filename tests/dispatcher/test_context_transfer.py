import datetime as dt
from pathlib import Path

from dispatcher.context_transfer import (
    acquire_lock,
    handoff,
    is_lock_expired,
    list_task_ids,
    read_task_file,
    refresh_heartbeat,
    release_stale_lock,
    task_file_path,
    TaskFile,
    write_task_file,
)


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    path = str(tmp_path / "task-1.md")
    task = TaskFile(task_id="task-1", status="pending", owner=None, depends_on=["task-0"], heartbeat=None, body="hello")

    write_task_file(path, task)
    result = read_task_file(path)

    assert result == task


def test_acquire_lock_sets_owner_and_moves_pending_to_in_progress(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)

    task = acquire_lock(hive_dir, "task-1", owner="cuenta1")

    assert task.owner == "cuenta1"
    assert task.status == "in_progress"
    assert task.heartbeat is not None


def test_refresh_heartbeat_updates_timestamp(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)
    first = acquire_lock(hive_dir, "task-1", owner="cuenta1")

    refresh_heartbeat(hive_dir, "task-1")

    updated = read_task_file(task_file_path(hive_dir, "task-1"))
    assert updated.heartbeat != first.heartbeat or updated.heartbeat is not None


def test_is_lock_expired(tmp_path: Path) -> None:
    stale = TaskFile(
        task_id="task-1", status="in_progress", owner="cuenta1", depends_on=[],
        heartbeat=(dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=200)).isoformat(),
        body="",
    )
    fresh = TaskFile(
        task_id="task-1", status="in_progress", owner="cuenta1", depends_on=[],
        heartbeat=dt.datetime.now(dt.timezone.utc).isoformat(), body="",
    )

    assert is_lock_expired(stale, ttl_seconds=120) is True
    assert is_lock_expired(fresh, ttl_seconds=120) is False


def test_release_stale_lock_clears_owner_and_heartbeat(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)
    acquire_lock(hive_dir, "task-1", owner="cuenta1")

    release_stale_lock(hive_dir, "task-1")

    task = read_task_file(task_file_path(hive_dir, "task-1"))
    assert task.owner is None
    assert task.heartbeat is None
    assert task.status == "in_progress"


def test_handoff_sets_status_and_body_and_clears_lock(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)
    acquire_lock(hive_dir, "task-1", owner="cuenta1")

    handoff(hive_dir, "task-1", new_status="pending", body="handoff summary", depends_on=["task-0"])

    task = read_task_file(task_file_path(hive_dir, "task-1"))
    assert task.status == "pending"
    assert task.body == "handoff summary"
    assert task.owner is None
    assert task.depends_on == ["task-0"]


def test_handoff_appends_to_existing_body_instead_of_replacing(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)
    acquire_lock(hive_dir, "task-1", owner="cuenta1")

    handoff(hive_dir, "task-1", new_status="pending", body="## arquitecto\n\nfirst phase summary")
    handoff(hive_dir, "task-1", new_status="done", body="## implementador\n\nsecond phase summary")

    task = read_task_file(task_file_path(hive_dir, "task-1"))
    assert "first phase summary" in task.body
    assert "second phase summary" in task.body
    assert task.status == "done"


def test_list_task_ids(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)
    acquire_lock(hive_dir, "task-1", owner="cuenta1")
    acquire_lock(hive_dir, "task-2", owner="cuenta1")

    assert sorted(list_task_ids(hive_dir)) == ["task-1", "task-2"]
