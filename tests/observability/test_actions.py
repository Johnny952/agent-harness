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


# --- one action by its id (ADR NNN) -------------------------------------------


def test_one_action_is_found_in_any_state(tmp_path):
    harness = _harness(tmp_path)
    pending = _record(harness.state_dir, actions.PENDING, 1, waiting="behind a start")
    claimed = _record(harness.state_dir, actions.CLAIMED, 2)
    done = _record(harness.state_dir, actions.DONE, 3, result={"detail": "cloned main"})
    for record in (pending, claimed, done):
        data, warnings = _data(harness, f"{PATH}/{record['id']}")
        assert (data, warnings) == (record, [])


def test_one_action_is_found_past_the_queues_finished_limit(tmp_path):
    # The case the route is for: the setup action finished first, and more than
    # the console's twenty newest finished have finished since.
    harness = _harness(tmp_path)
    first = _record(harness.state_dir, actions.DONE, 1, kind="keygen")
    for n in range(2, 30):
        _record(harness.state_dir, actions.DONE, n)
    queue, _ = _data(harness, f"{PATH}?limit=20")
    assert first["id"] not in [r["id"] for r in queue["done"]]
    data, _ = _data(harness, f"{PATH}/{first['id']}")
    assert data == first


def test_one_action_moved_on_is_read_where_it_is_now(tmp_path):
    harness = _harness(tmp_path)
    record = _record(harness.state_dir, actions.PENDING, 1)
    root = actions.actions_dir(harness.state_dir)
    (root / actions.DONE).mkdir(parents=True)
    moved = {**record, "status": "done"}
    (root / actions.DONE / f"{record['id']}.json").write_text(json.dumps(moved))
    (root / actions.PENDING / f"{record['id']}.json").unlink()
    data, _ = _data(harness, f"{PATH}/{record['id']}")
    assert data["status"] == "done"


def test_one_action_with_no_record_or_no_id_is_a_404(tmp_path):
    harness = _harness(tmp_path)
    # No queue at all, then a queue without this id.
    resp = _get(harness, f"{PATH}/{_id(1)}")
    assert resp.status_code == 404 and _id(1) in resp.get_json()["error"]
    _record(harness.state_dir, actions.DONE, 2)
    assert _get(harness, f"{PATH}/{_id(1)}").status_code == 404
    for bad in ("worker", "a-1", f"{_id(2)}.json", "..%2Fworker"):
        assert _get(harness, f"{PATH}/{bad}").status_code == 404


def test_one_unreadable_action_is_null_and_a_warning(tmp_path):
    harness = _harness(tmp_path)
    done = actions.actions_dir(harness.state_dir) / actions.DONE
    done.mkdir(parents=True)
    (done / f"{_id(1)}.json").write_text("{not json")
    data, warnings = _data(harness, f"{PATH}/{_id(1)}")
    assert data is None
    assert len(warnings) == 1 and "unreadable action" in warnings[0]


def test_one_action_takes_no_parameter_and_no_write(tmp_path):
    harness = _harness(tmp_path)
    record = _record(harness.state_dir, actions.PENDING, 1)
    path = f"{PATH}/{record['id']}"
    assert _get(harness, f"{path}?limit=1").status_code == 400
    assert harness.client.get(path).status_code == 401
    for method in (harness.client.post, harness.client.put, harness.client.delete):
        assert method(path).status_code == 405


def test_one_action_read_makes_nothing(tmp_path):
    harness = _harness(tmp_path)
    before = _tree(Path(harness.state_dir))
    assert _get(harness, f"{PATH}/{_id(1)}").status_code == 404
    assert _tree(Path(harness.state_dir)) == before
