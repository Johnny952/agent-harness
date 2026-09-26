import sqlite3
from pathlib import Path

import pytest

from observability.collector import db
from observability.collector.server import create_app


def test_post_then_get_events(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    app = create_app(db_path)
    client = app.test_client()

    resp = client.post(
        "/events",
        json={"source_app": "agent-cuenta1", "event_type": "PreToolUse", "payload": {"tool": "Bash"}},
    )
    assert resp.status_code == 201

    resp = client.get("/events")
    assert resp.status_code == 200
    events = resp.get_json()
    assert len(events) == 1
    assert events[0]["source_app"] == "agent-cuenta1"
    assert events[0]["event_type"] == "PreToolUse"
    assert events[0]["payload"] == {"tool": "Bash"}


def test_post_event_requires_source_app_and_event_type(tmp_path: Path) -> None:
    app = create_app(str(tmp_path / "events.db"))
    client = app.test_client()

    resp = client.post("/events", json={"payload": {}})

    assert resp.status_code == 400


# --- what the read API needed from db.py ----------------------------------


def test_connect_read_only_does_not_create_the_database(tmp_path: Path) -> None:
    # `sqlite3.connect` would create the file here, and on the `:ro` mount the
    # read API is given it would raise instead. Neither tells a reader whether
    # the harness has ever run, which is why the reader gets its own opener.
    missing = tmp_path / "events.db"

    with pytest.raises(sqlite3.OperationalError):
        db.connect_read_only(str(missing))

    assert not missing.exists()


def test_a_read_only_connection_refuses_to_write(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    db.init_db(db_path)

    conn = db.connect_read_only(db_path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                "INSERT INTO events (source_app, event_type, payload) VALUES (?, ?, ?)",
                ("agent-cuenta1", "PreToolUse", "{}"),
            )
    finally:
        conn.close()


def test_since_filters_by_id_and_composes_with_limit(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    db.init_db(db_path)
    for index in range(5):
        db.insert_event(db_path, "agent-cuenta1", f"E{index}", {"i": index})

    assert [e["id"] for e in db.list_events(db_path, since=3)] == [5, 4]
    assert [e["id"] for e in db.list_events(db_path, since=1, limit=2)] == [5, 4]
    assert db.list_events(db_path, since=5) == []


def test_since_and_source_app_filter_together(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    db.init_db(db_path)
    db.insert_event(db_path, "agent-cuenta1", "E0", {})
    db.insert_event(db_path, "agent-cuenta2", "E1", {})
    db.insert_event(db_path, "agent-cuenta1", "E2", {})

    rows = db.list_events(db_path, source_app="agent-cuenta1", since=1)

    assert [e["event_type"] for e in rows] == ["E2"]


def test_an_empty_source_app_is_no_filter_as_it_always_was(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    db.init_db(db_path)
    db.insert_event(db_path, "agent-cuenta1", "E0", {})

    assert len(db.list_events(db_path, source_app="")) == 1


def test_list_events_reads_the_same_rows_read_only(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    db.init_db(db_path)
    db.insert_event(db_path, "agent-cuenta1", "E0", {"tool": "Bash"})

    assert db.list_events(db_path, read_only=True) == db.list_events(db_path)
