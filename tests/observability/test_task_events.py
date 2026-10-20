"""`GET /api/tasks/<id>/events`, against a temporary hive and events db (ADR 197).

The mounts and the client are `test_api.py`'s `_harness`; rows are written by
the collector's own `insert_event`, so a change to how it stores a payload
shows up here. The `cwd`s are the paths `docker_exec` gives a phase.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from observability.api import app as api_app
from observability.collector import db as collector_db
from tests.observability.test_api import _get, _harness, _task

PATH = "/api/tasks/T-1/events"
WORK = "/data/projects/ia-harness/worktrees/T-1/work"


def _event(harness, cwd: str | None, event_type: str = "PreToolUse", **payload) -> None:
    if cwd is not None:
        payload["cwd"] = cwd
    collector_db.insert_event(harness.db_path, "agent-cuenta1", event_type, payload)


def test_the_tasks_events_come_back_oldest_first_with_the_payload_parsed(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    _event(harness, WORK, tool_name="Bash", session_id="s-1")
    _event(harness, WORK, "PostToolUse", tool_name="Bash", session_id="s-1")

    body = _get(harness, PATH).get_json()

    assert [e["event_type"] for e in body["data"]] == ["PreToolUse", "PostToolUse"]
    assert body["data"][0]["payload"] == {"cwd": WORK, "tool_name": "Bash", "session_id": "s-1"}
    assert set(body["data"][0]) == {"id", "source_app", "event_type", "payload", "created_at"}
    assert body["warnings"] == []


def test_only_rows_under_the_tasks_own_worktrees_match(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    _event(harness, WORK, n=1)
    _event(harness, "/data/projects/ia-harness/worktrees/T-1/reviewer/src", n=2)
    _event(harness, "/data/projects/ia-harness/worktrees/T-10/work", n=3)
    _event(harness, "/data/projects/ia-harness/worktrees/T-1", n=4)
    _event(harness, "/data/projects/ia-harness", n=5)
    _event(harness, None, n=6)
    collector_db.insert_event(harness.db_path, "agent-cuenta1", "Stop", {"cwd": 7})

    rows = _get(harness, PATH).get_json()["data"]

    assert [r["payload"]["n"] for r in rows] == [1, 2, 4]


def test_a_payload_that_is_not_json_is_skipped_and_does_not_fail_the_read(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    conn = sqlite3.connect(harness.db_path)
    conn.execute(
        "INSERT INTO events (source_app, event_type, payload) VALUES (?, ?, ?)",
        ("agent-cuenta1", "PreToolUse", "{not json /worktrees/T-1/"),
    )
    conn.commit()
    conn.close()
    _event(harness, WORK)

    body = _get(harness, PATH).get_json()

    assert [r["id"] for r in body["data"]] == [2]


def test_since_walks_up_and_limit_bounds_the_page(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    for n in range(5):
        _event(harness, WORK, n=n)

    assert [r["id"] for r in _get(harness, f"{PATH}?since=1&limit=2").get_json()["data"]] == [2, 3]
    assert _get(harness, f"{PATH}?since=5").get_json() == {"data": [], "warnings": []}


def test_a_limit_above_the_maximum_is_clamped_and_says_so(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(api_app, "MAX_TASK_EVENT_LIMIT", 2)
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    for n in range(3):
        _event(harness, WORK, n=n)

    body = _get(harness, f"{PATH}?limit=3").get_json()

    assert [r["id"] for r in body["data"]] == [1, 2]
    [warning] = body["warnings"]
    assert "limit=3" in warning and "since=<last id>" in warning


def test_a_task_with_no_file_is_404_like_its_usage(tmp_path: Path) -> None:
    harness = _harness(tmp_path)

    assert _get(harness, PATH).status_code == 404
    assert _get(harness, "/api/tasks/T%2F1/events").status_code == 404


def test_bad_parameters_are_400(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")

    assert _get(harness, f"{PATH}?limit=0").status_code == 400
    assert _get(harness, f"{PATH}?since=soon").status_code == 400
    assert _get(harness, f"{PATH}?source_app=x").status_code == 400


def test_a_missing_events_database_is_an_empty_list_and_a_warning(tmp_path: Path) -> None:
    harness = _harness(tmp_path, with_db=False)
    _task(harness.tasks_dir, "T-1")

    resp = _get(harness, PATH)

    assert resp.status_code == 200
    assert resp.get_json()["data"] == []
    [warning] = resp.get_json()["warnings"]
    assert "no events database yet" in warning
    assert not Path(harness.db_path).exists()
