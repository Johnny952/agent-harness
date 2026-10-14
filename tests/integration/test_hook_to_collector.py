"""End-to-end check of the observability pipeline (Critical #4).

`hooks/emit_event.py` runs inside the agent container and the collector runs in
its own container; the unit test in tests/hooks/ mocks `requests.post`, so it
cannot catch a mismatch between what the hook sends and what the collector
accepts. This test runs the real Flask app on a real loopback socket and lets
the hook's real `requests.post` reach it, then asserts the event landed in
SQLite.

Since ADR 72 the hook also has to carry the agent's write token, and the
collector stores the source_app that token is bound to. The tokens below are
placeholders made up for the test.
"""

from __future__ import annotations

import io
import json
import threading
from pathlib import Path

import pytest
import requests
from werkzeug.serving import make_server

import hooks.emit_event as emit_event_mod
from observability.collector import db
from observability.collector.server import create_app

READ = "read-token-operator"
WRITE_TOKENS = {"agent-cuenta1": "write-token-cuenta1", "agent-cuenta2": "write-token-cuenta2"}


@pytest.fixture
def collector(tmp_path: Path):
    """Run the collector on an ephemeral loopback port for the test's lifetime."""
    db_path = str(tmp_path / "events.db")
    server = make_server("127.0.0.1", 0, create_app(db_path, READ, WRITE_TOKENS))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", db_path
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _as_agent(monkeypatch, base_url: str, source_app: str, event: dict, token: str | None) -> None:
    monkeypatch.setattr(emit_event_mod.sys, "stdin", io.StringIO(json.dumps(event)))
    monkeypatch.setenv("COLLECTOR_URL", base_url)
    monkeypatch.setenv("SOURCE_APP", source_app)
    if token is None:
        monkeypatch.delenv("COLLECTOR_TOKEN", raising=False)
    else:
        monkeypatch.setenv("COLLECTOR_TOKEN", token)


def test_hook_event_reaches_collector_database(collector, monkeypatch) -> None:
    base_url, db_path = collector
    event = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "pytest -q"},
        "session_id": "sess-1",
    }
    _as_agent(monkeypatch, base_url, "agent-cuenta2", event, WRITE_TOKENS["agent-cuenta2"])

    emit_event_mod.main()

    stored = db.list_events(db_path)
    assert len(stored) == 1
    assert stored[0]["source_app"] == "agent-cuenta2"
    assert stored[0]["event_type"] == "PostToolUse"
    assert stored[0]["payload"] == event


def test_hook_events_from_both_agents_are_distinguishable(collector, monkeypatch) -> None:
    base_url, db_path = collector
    for source_app, token in WRITE_TOKENS.items():
        _as_agent(monkeypatch, base_url, source_app, {"hook_event_name": "Stop"}, token)
        emit_event_mod.main()

    assert len(db.list_events(db_path, source_app="agent-cuenta1")) == 1
    assert len(db.list_events(db_path, source_app="agent-cuenta2")) == 1


def test_an_agent_claiming_the_other_source_app_is_stored_as_itself(collector, monkeypatch) -> None:
    base_url, db_path = collector
    _as_agent(
        monkeypatch, base_url, "agent-cuenta2", {"hook_event_name": "Stop"},
        WRITE_TOKENS["agent-cuenta1"],
    )

    emit_event_mod.main()

    assert [e["source_app"] for e in db.list_events(db_path)] == ["agent-cuenta1"]


def test_a_hook_without_a_token_is_refused_in_one_line(collector, monkeypatch, capsys) -> None:
    base_url, db_path = collector
    _as_agent(monkeypatch, base_url, "agent-cuenta1", {"hook_event_name": "Stop"}, None)

    emit_event_mod.main()

    assert db.list_events(db_path) == []
    err_lines = capsys.readouterr().err.strip("\n").split("\n")
    assert len(err_lines) == 1
    assert "401" in err_lines[0]


def test_only_the_read_token_reads_over_the_wire(collector) -> None:
    base_url, _ = collector

    assert requests.get(f"{base_url}/events", timeout=5).status_code == 401
    assert requests.get(
        f"{base_url}/events",
        headers={"Authorization": f"Bearer {WRITE_TOKENS['agent-cuenta1']}"},
        timeout=5,
    ).status_code == 401
    assert requests.get(
        f"{base_url}/events", headers={"Authorization": f"Bearer {READ}"}, timeout=5
    ).status_code == 200
