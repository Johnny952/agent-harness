"""`GET /api/threads`, against a temporary hive (ADR 170, 177).

The mounts and the client are `test_api.py`'s `_harness`. Records are written
by `dispatcher/threads.py`'s own `create` and `update`, so a change to the
record's format shows up here as a failure rather than as two files that
disagree; the broken ones are written by hand.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from dispatcher import threads as thread_store
from dispatcher.config import load_config
from tests.observability.test_api import _get, _harness

PATH = "/api/threads"


def _at(day: int) -> dt.datetime:
    return dt.datetime(2026, 10, day, 12, 0, tzinfo=dt.timezone.utc)


def _setup(tmp_path: Path):
    harness = _harness(tmp_path, accounts=("cuenta1", "cuenta2"), primary_account="cuenta1")
    return harness, load_config(harness.config_path)


def test_no_threads_directory_is_an_empty_list(tmp_path: Path) -> None:
    harness, _ = _setup(tmp_path)

    body = _get(harness, PATH).get_json()

    assert body == {"data": [], "warnings": []}
    # The read made nothing: the api never writes the hive.
    assert not os.path.exists(thread_store.threads_root(harness.tasks_dir))


def test_the_main_thread_comes_first_and_the_rest_by_created(tmp_path: Path) -> None:
    harness, cfg = _setup(tmp_path)
    late = thread_store.create(harness.tasks_dir, cfg, title="Late", now=_at(15))
    early = thread_store.create(harness.tasks_dir, cfg, title="Early", now=_at(11))
    thread_store.create(harness.tasks_dir, cfg, main=True, now=_at(18))

    body = _get(harness, PATH).get_json()

    assert body["warnings"] == []
    assert [row["id"] for row in body["data"]] == ["main", early["id"], late["id"]]


def test_a_row_is_the_record_with_its_current_triple_and_its_segments(tmp_path: Path) -> None:
    harness, cfg = _setup(tmp_path)
    thread_store.create(harness.tasks_dir, cfg, main=True, now=_at(11))
    record = thread_store.update(
        harness.tasks_dir, cfg, "main", account="cuenta2", effort="high", now=_at(12)
    )

    [row] = _get(harness, PATH).get_json()["data"]

    assert row == record
    assert (row["account"], row["effort"]) == ("cuenta2", "high")
    assert [s["account"] for s in row["segments"]] == ["cuenta1", "cuenta2"]
    assert row["segments"][1]["from"] == _at(12).isoformat()


def test_an_archived_thread_is_served_with_its_status(tmp_path: Path) -> None:
    harness, cfg = _setup(tmp_path)
    kept = thread_store.create(harness.tasks_dir, cfg, title="Kept", now=_at(11))
    gone = thread_store.create(harness.tasks_dir, cfg, title="Gone", now=_at(12))
    thread_store.update(harness.tasks_dir, cfg, gone["id"], status="archived")

    rows = _get(harness, PATH).get_json()["data"]

    assert [(r["id"], r["status"]) for r in rows] == [
        (kept["id"], "open"),
        (gone["id"], "archived"),
    ]


def test_a_record_that_will_not_parse_is_a_warning_and_not_a_missing_list(
    tmp_path: Path,
) -> None:
    harness, cfg = _setup(tmp_path)
    thread_store.create(harness.tasks_dir, cfg, main=True, now=_at(11))
    root = Path(thread_store.threads_root(harness.tasks_dir))
    (root / "th-broken.json").write_text("{not json")
    (root / "th-list.json").write_text("[1, 2]")
    # A writer's leftover is not a record and costs nothing.
    (root / "main.json.tmp").write_text("{half")

    body = _get(harness, PATH).get_json()

    assert [row["id"] for row in body["data"]] == ["main"]
    assert len(body["warnings"]) == 2
    assert any("th-broken.json" in w for w in body["warnings"])
    assert any("th-list.json" in w and "not a JSON object" in w for w in body["warnings"])


def test_a_threads_path_that_cannot_be_listed_is_a_warning_and_not_a_500(
    tmp_path: Path,
) -> None:
    harness, _ = _setup(tmp_path)
    Path(thread_store.threads_root(harness.tasks_dir)).write_text("a file, not a directory")

    resp = _get(harness, PATH)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["data"] == []
    [warning] = body["warnings"]
    assert "unreadable threads directory" in warning
