"""`GET /api/credentials`, against a hand-written snapshot (ADR 471).

The mounts and the client are `test_api.py`'s `_harness`; the snapshot is
written as the `credentials.json` the worker writes into the state dir.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from dispatcher import credentials
from observability.api import app as api_app
from tests.observability.test_api import _get, _harness
from tests.observability.test_roadmap import _data

PATH = "/api/credentials"


def _write(state_dir: str, seconds_ago: float = 10, **fields) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    record = {
        "version": 1,
        "written_at": (now - dt.timedelta(seconds=seconds_ago)).isoformat(),
        "accounts": [],
    }
    record.update(fields)
    Path(state_dir, credentials.SNAPSHOT).write_text(json.dumps(record))
    return record


def _tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_a_snapshot_is_served_whole_with_stale_false(tmp_path):
    harness = _harness(tmp_path)
    record = _write(harness.state_dir)
    data, warnings = _data(harness, PATH)
    assert warnings == []
    assert data == {**record, "stale": False, "stale_after_seconds": 900}
    assert api_app.CREDENTIALS_STALE_SECONDS == 900


def test_a_snapshot_older_than_its_window_is_stale(tmp_path):
    harness = _harness(tmp_path)
    record = _write(harness.state_dir, seconds_ago=901)
    data, warnings = _data(harness, PATH)
    assert warnings == []
    assert data == {**record, "stale": True, "stale_after_seconds": 900}


def test_no_snapshot_is_null_with_a_fixed_warning_and_makes_nothing(tmp_path):
    harness = _harness(tmp_path)
    before = _tree(Path(harness.state_dir))
    data, warnings = _data(harness, PATH)
    assert data is None
    assert warnings == ["no credentials snapshot: the worker has not written one"]
    assert _tree(Path(harness.state_dir)) == before


def test_a_file_that_does_not_parse_is_null_with_its_reason(tmp_path):
    harness = _harness(tmp_path)
    path = Path(harness.state_dir, credentials.SNAPSHOT)
    path.write_text("{truncated")
    data, warnings = _data(harness, PATH)
    assert data is None
    assert len(warnings) == 1
    assert warnings[0].startswith("unreadable credentials snapshot:")
    assert str(path) in warnings[0]


def test_a_file_that_is_not_an_object_is_null_with_its_reason(tmp_path):
    harness = _harness(tmp_path)
    Path(harness.state_dir, credentials.SNAPSHOT).write_text("[]")
    data, warnings = _data(harness, PATH)
    assert data is None
    assert len(warnings) == 1
    assert warnings[0].startswith("unreadable credentials snapshot:")


def test_an_unreadable_written_at_is_served_stale_with_a_warning(tmp_path):
    for bad in (None, "yesterday", "2026-10-26T12:00:00", 7):
        harness = _harness(tmp_path / str(len(str(bad))) / str(bad))
        record = _write(harness.state_dir, written_at=bad)
        data, warnings = _data(harness, PATH)
        assert data == {**record, "stale": True, "stale_after_seconds": 900}
        assert len(warnings) == 1
        assert "written_at" in warnings[0]


def test_a_written_at_in_the_future_is_not_stale(tmp_path):
    harness = _harness(tmp_path)
    _write(harness.state_dir, seconds_ago=-3600)
    data, warnings = _data(harness, PATH)
    assert warnings == []
    assert data["stale"] is False


def test_an_unknown_parameter_is_a_400(tmp_path):
    harness = _harness(tmp_path)
    assert _get(harness, PATH + "?x=1").status_code == 400
