"""`GET /api/actions`, against a temporary queue (ADR 142, step 4).

The mounts and the client are `test_api.py`'s `_harness`. Records are written
by hand in the layout `dispatcher/actions.py` keeps (`actions/<state>/<id>.json`,
the id the file's name), and the heartbeat as the `worker.json` the worker
beats, so the route is read the way the worker would read the same files.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from dispatcher import actions
from observability.api import app as api_app
from tests.observability.test_api import _get, _harness
from tests.observability.test_roadmap import _data

PATH = "/api/actions"


def _id(n: int) -> str:
    """An id in the queue's shape; a larger `n` is a later one by name."""
    return f"a-20261019T1200{n:02d}000000Z-{n:04x}"


def _record(state_dir: str, state: str, n: int, **fields) -> dict:
    record = {
        "version": actions.VERSION,
        "id": _id(n),
        "kind": "close_task",
        "project": "demo",
        "task_id": "T-001",
        "params": {},
        "actor": "operator",
        "note": None,
        "created_at": "2026-10-19T12:00:00+00:00",
        "claimed_at": None,
        "started_at": None,
        "finished_at": None,
        "status": state,
        "waiting": None,
        "result": None,
        "error": None,
    }
    record.update(fields)
    directory = actions.actions_dir(state_dir) / state
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{record['id']}.json").write_text(json.dumps(record))
    return record


def _beat(state_dir: str, seconds_ago: float) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    heartbeat = {
        "pid": 7,
        "started_at": (now - dt.timedelta(hours=1)).isoformat(),
        "beat_at": (now - dt.timedelta(seconds=seconds_ago)).isoformat(),
    }
    root = actions.actions_dir(state_dir)
    root.mkdir(parents=True, exist_ok=True)
    (root / "worker.json").write_text(json.dumps(heartbeat))
    return heartbeat


def _tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_no_actions_dir_is_an_empty_queue_and_makes_nothing(tmp_path):
    harness = _harness(tmp_path)
    before = _tree(Path(harness.state_dir))
    data, warnings = _data(harness, PATH)
    assert data == {
        "pending": [],
        "claimed": [],
        "done": [],
        "done_total": 0,
        "worker": {
            "heartbeat": None,
            "stale": True,
            "stale_after_seconds": api_app.WORKER_STALE_SECONDS,
        },
        "epics": [],
    }
    assert warnings == []
    assert not actions.actions_dir(harness.state_dir).exists()
    assert _tree(Path(harness.state_dir)) == before


def test_pending_in_take_order_with_waiting(tmp_path):
    harness = _harness(tmp_path)
    later = _record(harness.state_dir, actions.PENDING, 3)
    first = _record(
        harness.state_dir, actions.PENDING, 1, waiting="cuenta2 is cooling"
    )
    data, warnings = _data(harness, PATH)
    assert warnings == []
    assert data["pending"] == [first, later]
    assert data["pending"][0]["waiting"] == "cuenta2 is cooling"


def test_claimed_records_are_served(tmp_path):
    harness = _harness(tmp_path)
    running = _record(
        harness.state_dir,
        actions.CLAIMED,
        2,
        claimed_at="2026-10-19T12:01:00+00:00",
        started_at="2026-10-19T12:01:01+00:00",
    )
    data, warnings = _data(harness, PATH)
    assert warnings == []
    assert data["claimed"] == [running]
    assert data["pending"] == [] and data["done"] == []


def test_done_is_the_newest_by_name_up_to_the_limit(tmp_path):
    harness = _harness(tmp_path)
    for n in range(1, 6):
        _record(harness.state_dir, actions.DONE, n, result={"ok": n})
    data, warnings = _data(harness, f"{PATH}?limit=2")
    assert warnings == []
    assert [r["id"] for r in data["done"]] == [_id(5), _id(4)]
    assert data["done_total"] == 5
    data, _ = _data(harness, PATH)
    assert [r["id"] for r in data["done"]] == [_id(n) for n in (5, 4, 3, 2, 1)]


def test_bad_record_is_a_warning_not_a_500(tmp_path):
    harness = _harness(tmp_path)
    good = _record(harness.state_dir, actions.PENDING, 1)
    pending = actions.actions_dir(harness.state_dir) / actions.PENDING
    (pending / f"{_id(2)}.json").write_text("{not json")
    (pending / f"{_id(3)}.json").write_text(json.dumps({"version": 1, "id": "other"}))
    data, warnings = _data(harness, PATH)
    assert data["pending"] == [good]
    assert len(warnings) == 2
    assert all("unreadable action" in w for w in warnings)
    assert _id(2) in warnings[0] and _id(3) in warnings[1]


def test_fresh_heartbeat_is_not_stale(tmp_path):
    harness = _harness(tmp_path)
    heartbeat = _beat(harness.state_dir, 5)
    data, warnings = _data(harness, PATH)
    assert warnings == []
    assert data["worker"]["heartbeat"] == heartbeat
    assert data["worker"]["stale"] is False


def test_old_heartbeat_is_stale(tmp_path):
    harness = _harness(tmp_path)
    heartbeat = _beat(harness.state_dir, 300)
    data, warnings = _data(harness, PATH)
    assert warnings == []
    assert data["worker"]["heartbeat"] == heartbeat
    assert data["worker"]["stale"] is True


def test_unreadable_heartbeat_is_a_warning_and_stale(tmp_path):
    harness = _harness(tmp_path)
    root = actions.actions_dir(harness.state_dir)
    root.mkdir(parents=True)
    (root / "worker.json").write_text("[]")
    data, warnings = _data(harness, PATH)
    assert data["worker"]["heartbeat"] is None
    assert data["worker"]["stale"] is True
    assert len(warnings) == 1 and "worker heartbeat" in warnings[0]
    (root / "worker.json").write_text(json.dumps({"pid": 7, "beat_at": "yesterday"}))
    data, warnings = _data(harness, PATH)
    assert data["worker"]["heartbeat"] == {"pid": 7, "beat_at": "yesterday"}
    assert data["worker"]["stale"] is True
    assert len(warnings) == 1 and "beat_at" in warnings[0]


def test_stale_threshold_comes_from_the_worker_poll():
    from dispatcher import worker

    assert api_app.WORKER_STALE_SECONDS > worker.POLL_SECONDS
    assert api_app.WORKER_STALE_SECONDS % worker.POLL_SECONDS == 0


def test_limit_above_the_maximum_is_clamped_with_a_warning(tmp_path):
    harness = _harness(tmp_path)
    data, warnings = _data(harness, f"{PATH}?limit={api_app.MAX_ACTIONS_DONE + 1}")
    assert data["done"] == []
    assert len(warnings) == 1 and str(api_app.MAX_ACTIONS_DONE) in warnings[0]


def test_bad_parameters_are_a_400(tmp_path):
    harness = _harness(tmp_path)
    assert _get(harness, f"{PATH}?state=done").status_code == 400
    assert _get(harness, f"{PATH}?limit=0").status_code == 400
    assert _get(harness, f"{PATH}?limit=many").status_code == 400


def test_route_is_read_only(tmp_path):
    harness = _harness(tmp_path)
    _record(harness.state_dir, actions.PENDING, 1)
    _beat(harness.state_dir, 5)
    root = actions.actions_dir(harness.state_dir)
    before = {p: os.stat(p).st_mtime_ns for p in root.rglob("*")}
    _data(harness, PATH)
    assert {p: os.stat(p).st_mtime_ns for p in root.rglob("*")} == before


def test_a_started_epic_is_served_with_its_spend_and_limit(tmp_path):
    """The run file `start_epic_run` writes, read with its measured spend (ADR NNN)."""
    from dispatcher import context_transfer, epic_runs

    harness = _harness(tmp_path)
    hive = str(tmp_path / "hive" / "tasks")
    actions.start_epic_run(
        harness.state_dir, "T-1", "demo", ["T-1-S1"],
        offset=lambda tid: epic_runs.usage_offset(hive, tid),
        spent=lambda run: 0, limit=100, note="go",
    )
    context_transfer.append_usage(hive, "T-1-S1", {
        "input_tokens": 70, "output_tokens": 30,
        "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
    })
    context_transfer.append_usage(hive, "T-1-S1", {"measured": False})
    before = _tree(Path(harness.state_dir))
    data, warnings = _data(harness, PATH)
    assert warnings == []
    [run] = data["epics"]
    assert run["epic"] == "T-1" and run["project"] == "demo" and run["note"] == "go"
    assert run["limit"] == 100 and run["paused"] is None and run["stopped_at"] is None
    assert run["spent"] == {"tokens": 100, "unmeasured": 1}
    assert run["limit_reached"] is True
    assert list(run["queued"]) == ["T-1-S1"]
    assert _tree(Path(harness.state_dir)) == before


def test_an_unreadable_epic_run_is_a_warning(tmp_path):
    harness = _harness(tmp_path)
    directory = actions.actions_dir(harness.state_dir) / actions.EPICS
    directory.mkdir(parents=True)
    (directory / "T-1.json").write_text("{not json")
    data, warnings = _data(harness, PATH)
    assert data["epics"] == []
    assert len(warnings) == 1 and "T-1" in warnings[0]
