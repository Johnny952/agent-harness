"""End-to-end check of the observability pipeline (Critical #4).

`hooks/emit_event.py` runs inside the agent container and the collector runs in
its own container; the unit test in tests/hooks/ mocks `requests.post`, so it
cannot catch a mismatch between what the hook sends and what the collector
accepts. This test runs the real Flask app on a real loopback socket and lets
the hook's real `requests.post` reach it, then asserts the event landed in
SQLite.
"""

from __future__ import annotations

import io
import json
import threading
from pathlib import Path

import pytest
from werkzeug.serving import make_server

import hooks.emit_event as emit_event_mod
from observability.collector import db
from observability.collector.server import create_app


@pytest.fixture
def collector(tmp_path: Path):
    """Run the collector on an ephemeral loopback port for the test's lifetime."""
    db_path = str(tmp_path / "events.db")
    server = make_server("127.0.0.1", 0, create_app(db_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", db_path
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_hook_event_reaches_collector_database(collector, monkeypatch) -> None:
    base_url, db_path = collector
    event = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "pytest -q"},
        "session_id": "sess-1",
    }
    monkeypatch.setattr(emit_event_mod.sys, "stdin", io.StringIO(json.dumps(event)))
    monkeypatch.setenv("COLLECTOR_URL", base_url)
    monkeypatch.setenv("SOURCE_APP", "agent-cuenta2")

    emit_event_mod.main()

    stored = db.list_events(db_path)
    assert len(stored) == 1
    assert stored[0]["source_app"] == "agent-cuenta2"
    assert stored[0]["event_type"] == "PostToolUse"
    assert stored[0]["payload"] == event


def test_hook_events_from_both_agents_are_distinguishable(collector, monkeypatch) -> None:
    base_url, db_path = collector
    for source_app in ("agent-cuenta1", "agent-cuenta2"):
        monkeypatch.setattr(
            emit_event_mod.sys,
            "stdin",
            io.StringIO(json.dumps({"hook_event_name": "Stop"})),
        )
        monkeypatch.setenv("COLLECTOR_URL", base_url)
        monkeypatch.setenv("SOURCE_APP", source_app)
        emit_event_mod.main()

    assert len(db.list_events(db_path, source_app="agent-cuenta1")) == 1
    assert len(db.list_events(db_path, source_app="agent-cuenta2")) == 1
