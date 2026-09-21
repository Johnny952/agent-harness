import datetime as dt
import os
import stat
import tempfile
from pathlib import Path

import pytest

from dispatcher.context_transfer import (
    acquire_lock,
    handoff,
    is_lock_expired,
    list_task_ids,
    LockHeldError,
    read_description,
    read_kanban_issue_id,
    read_task_file,
    refresh_heartbeat,
    release_stale_lock,
    set_description,
    set_kanban_issue_id,
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


def test_write_task_file_leaves_no_temp_files_on_success(tmp_path: Path) -> None:
    path = str(tmp_path / "task-1.md")
    task = TaskFile(task_id="task-1", status="pending", owner=None, depends_on=[], heartbeat=None, body="hello")

    write_task_file(path, task)

    assert os.listdir(tmp_path) == ["task-1.md"]


def test_write_task_file_preserves_content_and_cleans_up_temp_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = str(tmp_path / "task-1.md")
    original = TaskFile(task_id="task-1", status="pending", owner=None, depends_on=[], heartbeat=None, body="original")
    write_task_file(path, original)

    def raising_replace(*args, **kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("dispatcher.context_transfer.os.replace", raising_replace)

    updated = TaskFile(
        task_id="task-1", status="in_progress", owner="cuenta1", depends_on=[], heartbeat=None, body="updated"
    )
    with pytest.raises(OSError):
        write_task_file(path, updated)

    assert read_task_file(path) == original
    assert os.listdir(tmp_path) == ["task-1.md"]


def test_write_task_file_writes_tmp_in_same_dir_and_hides_it_from_list_task_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hive_dir = str(tmp_path)
    captured = []
    real_replace = os.replace

    def spying_replace(src, dst, *args, **kwargs):
        captured.append((os.path.dirname(src), list_task_ids(hive_dir)))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr("dispatcher.context_transfer.os.replace", spying_replace)

    task = TaskFile(task_id="task-1", status="pending", owner=None, depends_on=[], heartbeat=None, body="hello")
    write_task_file(task_file_path(hive_dir, "task-1"), task)
    write_task_file(task_file_path(hive_dir, "task-1"), task)

    first_src_dir, first_ids = captured[0]
    second_src_dir, second_ids = captured[1]
    assert first_src_dir == hive_dir
    assert second_src_dir == hive_dir
    assert first_ids == []
    assert second_ids == ["task-1"]


def test_write_task_file_sets_mode_0644(tmp_path: Path) -> None:
    path = str(tmp_path / "task-1.md")
    task = TaskFile(task_id="task-1", status="pending", owner=None, depends_on=[], heartbeat=None, body="hello")

    write_task_file(path, task)

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o644


def test_write_task_file_normalizes_mode_from_0600_to_0644(tmp_path: Path) -> None:
    path = str(tmp_path / "task-1.md")
    task = TaskFile(task_id="task-1", status="pending", owner=None, depends_on=[], heartbeat=None, body="hello")
    write_task_file(path, task)
    os.chmod(path, 0o600)

    write_task_file(path, task)

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o644


def test_write_task_file_removes_tmp_file_when_flush_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = str(tmp_path / "task-1.md")
    task = TaskFile(task_id="task-1", status="pending", owner=None, depends_on=[], heartbeat=None, body="hello")
    real_named_temp = tempfile.NamedTemporaryFile

    def raising_named_temp(*args, **kwargs):
        tmp = real_named_temp(*args, **kwargs)

        def raising_flush():
            raise OSError("simulated disk full")

        # Also make close() raise: a real ENOSPC/EIO means the underlying
        # stream is broken, so the implicit flush-on-close during cleanup
        # fails too. A fix that swallows the first failure and then calls
        # close() unprotected would hit this and skip the tmp-file cleanup.
        tmp.flush = raising_flush
        tmp.close = raising_flush
        return tmp

    monkeypatch.setattr("dispatcher.context_transfer.tempfile.NamedTemporaryFile", raising_named_temp)

    with pytest.raises(OSError):
        write_task_file(path, task)

    assert [f for f in os.listdir(tmp_path) if f.endswith(".tmp")] == []
    assert not os.path.exists(path)


def test_acquire_lock_different_owner_raises_when_no_ttl_given(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)
    original = acquire_lock(hive_dir, "task-1", owner="cuenta1")

    with pytest.raises(LockHeldError):
        acquire_lock(hive_dir, "task-1", owner="cuenta2")

    untouched = read_task_file(task_file_path(hive_dir, "task-1"))
    assert untouched.owner == "cuenta1"
    assert untouched.heartbeat == original.heartbeat


def test_acquire_lock_different_owner_raises_when_lock_is_fresh(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)
    original = acquire_lock(hive_dir, "task-1", owner="cuenta1")

    with pytest.raises(LockHeldError):
        acquire_lock(hive_dir, "task-1", owner="cuenta2", ttl_seconds=120)

    untouched = read_task_file(task_file_path(hive_dir, "task-1"))
    assert untouched.owner == "cuenta1"
    assert untouched.heartbeat == original.heartbeat


def test_acquire_lock_different_owner_raises_on_stale_heartbeat_when_no_ttl_given(tmp_path: Path) -> None:
    # No TTL means the caller never wants an automatic takeover, however old
    # the heartbeat is.
    hive_dir = str(tmp_path)
    path = task_file_path(hive_dir, "task-1")
    stale = TaskFile(
        task_id="task-1", status="in_progress", owner="cuenta1", depends_on=[],
        heartbeat=(dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=999)).isoformat(),
        body="",
    )
    write_task_file(path, stale)

    with pytest.raises(LockHeldError):
        acquire_lock(hive_dir, "task-1", owner="cuenta2")

    untouched = read_task_file(path)
    assert untouched.owner == "cuenta1"
    assert untouched.heartbeat == stale.heartbeat


def test_acquire_lock_different_owner_succeeds_when_heartbeat_is_none(tmp_path: Path) -> None:
    # A live holder always refreshes its heartbeat; owner set with heartbeat
    # None only happens via a hand-edited or legacy file, and a TTL means the
    # caller accepts takeovers, so it should count as stale rather than lock
    # the task forever.
    hive_dir = str(tmp_path)
    path = task_file_path(hive_dir, "task-1")
    legacy = TaskFile(task_id="task-1", status="in_progress", owner="cuenta1", depends_on=[], heartbeat=None, body="")
    write_task_file(path, legacy)

    task = acquire_lock(hive_dir, "task-1", owner="cuenta2", ttl_seconds=120)

    assert task.owner == "cuenta2"


def test_acquire_lock_different_owner_raises_when_heartbeat_is_none_and_no_ttl_given(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)
    path = task_file_path(hive_dir, "task-1")
    legacy = TaskFile(task_id="task-1", status="in_progress", owner="cuenta1", depends_on=[], heartbeat=None, body="")
    write_task_file(path, legacy)

    with pytest.raises(LockHeldError):
        acquire_lock(hive_dir, "task-1", owner="cuenta2")

    assert read_task_file(path).owner == "cuenta1"


def test_acquire_lock_different_owner_succeeds_when_lock_expired(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)
    path = task_file_path(hive_dir, "task-1")
    stale = TaskFile(
        task_id="task-1", status="in_progress", owner="cuenta1", depends_on=[],
        heartbeat=(dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=999)).isoformat(),
        body="",
    )
    write_task_file(path, stale)

    task = acquire_lock(hive_dir, "task-1", owner="cuenta2", ttl_seconds=120)

    assert task.owner == "cuenta2"


def test_acquire_lock_reacquire_by_same_owner_succeeds(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)
    acquire_lock(hive_dir, "task-1", owner="cuenta1")

    task = acquire_lock(hive_dir, "task-1", owner="cuenta1")

    assert task.owner == "cuenta1"


def test_acquire_lock_after_release_stale_lock_by_different_owner_succeeds(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)
    acquire_lock(hive_dir, "task-1", owner="cuenta1")
    release_stale_lock(hive_dir, "task-1")

    task = acquire_lock(hive_dir, "task-1", owner="cuenta2")

    assert task.owner == "cuenta2"


def test_acquire_lock_after_handoff_by_different_owner_succeeds(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)
    acquire_lock(hive_dir, "task-1", owner="cuenta1")
    handoff(hive_dir, "task-1", new_status="pending", body="handed off")

    task = acquire_lock(hive_dir, "task-1", owner="cuenta2")

    assert task.owner == "cuenta2"


_MULTILINE_DESCRIPTION = """Add a /healthz endpoint.

It must return 200 with {"status": "ok"} and skip auth.
"""


def test_description_roundtrips_and_is_written_as_a_block_scalar(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)

    set_description(hive_dir, "task-1", _MULTILINE_DESCRIPTION)

    assert read_description(hive_dir, "task-1") == _MULTILINE_DESCRIPTION
    # Not just readable by us: a description is written by a human and read by
    # one while debugging a run, so it has to survive as the lines they typed
    # instead of safe_dump's default single-quoted scalar with folded newlines.
    raw = Path(task_file_path(hive_dir, "task-1")).read_text()
    assert "description: |" in raw
    assert "  It must return 200" in raw


def test_description_with_trailing_whitespace_still_roundtrips(tmp_path: Path) -> None:
    """A block scalar can't represent a line with trailing spaces; the emitter
    falls back to a quoted style on its own, so fidelity doesn't depend on the
    value being block-friendly."""
    hive_dir = str(tmp_path)
    description = "first line   \nsecond line"

    set_description(hive_dir, "task-1", description)

    assert read_description(hive_dir, "task-1") == description


def test_set_description_keeps_the_phase_history_already_in_the_body(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)
    acquire_lock(hive_dir, "task-1", owner="cuenta1")
    handoff(hive_dir, "task-1", new_status="review", body="arquitecto: plan ready", depends_on=["task-0"])

    set_description(hive_dir, "task-1", "Add a /healthz endpoint.")

    task = read_task_file(task_file_path(hive_dir, "task-1"))
    assert task.description == "Add a /healthz endpoint."
    # Re-seeding a task mid-cycle replaces the ask and nothing else.
    assert task.body.strip() == "arquitecto: plan ready"
    assert task.status == "review"
    assert task.depends_on == ["task-0"]


def test_handoff_keeps_the_description_out_of_the_body(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)
    set_description(hive_dir, "task-1", _MULTILINE_DESCRIPTION)

    handoff(hive_dir, "task-1", new_status="review", body="arquitecto: plan ready")
    handoff(hive_dir, "task-1", new_status="review", body="implementador: done")

    task = read_task_file(task_file_path(hive_dir, "task-1"))
    # The body accumulates phase summaries, which is exactly why the ask lives
    # in the frontmatter: here it would have been appended twice.
    assert task.description == _MULTILINE_DESCRIPTION
    assert "healthz" not in task.body


def test_task_file_written_before_descriptions_existed_is_still_readable(tmp_path: Path) -> None:
    path = str(tmp_path / "task-1.md")
    Path(path).write_text(
        "---\ntask_id: task-1\nstatus: pending\nowner: null\ndepends_on: []\n"
        "heartbeat: null\n---\n\nold body\n"
    )

    task = read_task_file(path)

    assert task.description is None
    assert task.body.strip() == "old body"


def test_read_description_of_an_unseeded_task_is_none(tmp_path: Path) -> None:
    assert read_description(str(tmp_path), "task-1") is None
    # Reading must not create the file: list_task_ids feeds the dispatcher.
    assert list_task_ids(str(tmp_path)) == []


_ISSUE_ID = "0e1d2c3b-4a59-6878-9706-5a4b3c2d1e0f"


def test_kanban_issue_id_roundtrips(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)

    set_kanban_issue_id(hive_dir, "task-1", _ISSUE_ID)

    assert read_kanban_issue_id(hive_dir, "task-1") == _ISSUE_ID


def test_kanban_issue_id_is_written_above_the_description(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)
    set_description(hive_dir, "task-1", _MULTILINE_DESCRIPTION)

    set_kanban_issue_id(hive_dir, "task-1", _ISSUE_ID)

    raw = Path(task_file_path(hive_dir, "task-1")).read_text()
    # The description is a block scalar running to the end of the frontmatter;
    # a short bookkeeping key after it would read as part of the prose.
    assert raw.index("kanban_issue_id:") < raw.index("description:")


def test_set_kanban_issue_id_keeps_everything_else(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)
    set_description(hive_dir, "task-1", "Add a /healthz endpoint.")
    acquire_lock(hive_dir, "task-1", owner="cuenta1")
    handoff(hive_dir, "task-1", new_status="review", body="arquitecto: plan ready")

    # Attaching a board to a task that already ran is a legitimate move.
    set_kanban_issue_id(hive_dir, "task-1", _ISSUE_ID)

    task = read_task_file(task_file_path(hive_dir, "task-1"))
    assert task.kanban_issue_id == _ISSUE_ID
    assert task.description == "Add a /healthz endpoint."
    assert task.body.strip() == "arquitecto: plan ready"
    assert task.status == "review"


def test_handoff_keeps_the_kanban_issue_id(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)
    set_kanban_issue_id(hive_dir, "task-1", _ISSUE_ID)

    handoff(hive_dir, "task-1", new_status="review", body="implementador: done")

    # Every phase re-reads the file to find the issue it has to move along.
    assert read_kanban_issue_id(hive_dir, "task-1") == _ISSUE_ID


def test_task_file_written_before_the_board_existed_is_still_readable(tmp_path: Path) -> None:
    path = str(tmp_path / "task-1.md")
    Path(path).write_text(
        "---\ntask_id: task-1\nstatus: pending\nowner: null\ndepends_on: []\n"
        "heartbeat: null\ndescription: Add a /healthz endpoint.\n---\n\nold body\n"
    )

    task = read_task_file(path)

    assert task.kanban_issue_id is None
    assert task.description == "Add a /healthz endpoint."


def test_a_task_with_no_board_writes_no_kanban_key(tmp_path: Path) -> None:
    hive_dir = str(tmp_path)

    set_description(hive_dir, "task-1", "Add a /healthz endpoint.")

    assert read_kanban_issue_id(hive_dir, "task-1") is None
    # Absent, not null: a harness without a board leaves no trace of one.
    assert "kanban_issue_id" not in Path(task_file_path(hive_dir, "task-1")).read_text()


def test_read_kanban_issue_id_of_an_unseeded_task_is_none(tmp_path: Path) -> None:
    assert read_kanban_issue_id(str(tmp_path), "task-1") is None
    assert list_task_ids(str(tmp_path)) == []
