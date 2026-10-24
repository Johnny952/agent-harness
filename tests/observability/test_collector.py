import logging
import sqlite3
import sys
import types
from pathlib import Path

import pytest

from observability.collector import db
from observability.collector.server import (
    HOST,
    PORT,
    THREADS,
    ConfigError,
    create_app,
    load_config,
    main,
    parse_write_tokens,
    run,
)

# Placeholder credentials, made up for these tests. Nothing here is a real token.
READ = "read-token-operator"
WRITE_A = "write-token-cuenta1"
WRITE_B = "write-token-cuenta2"
WRITE_TOKENS = {"agent-cuenta1": WRITE_A, "agent-cuenta2": WRITE_B}


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(tmp_path: Path):
    db_path = str(tmp_path / "events.db")
    app = create_app(db_path, READ, WRITE_TOKENS)
    return app.test_client(), db_path


def test_post_then_get_events(client) -> None:
    c, _ = client

    resp = c.post(
        "/events",
        json={"source_app": "agent-cuenta1", "event_type": "PreToolUse", "payload": {"tool": "Bash"}},
        headers=_bearer(WRITE_A),
    )
    assert resp.status_code == 201

    resp = c.get("/events", headers=_bearer(READ))
    assert resp.status_code == 200
    events = resp.get_json()
    assert len(events) == 1
    assert events[0]["source_app"] == "agent-cuenta1"
    assert events[0]["event_type"] == "PreToolUse"
    assert events[0]["payload"] == {"tool": "Bash"}


def test_post_event_requires_event_type(client) -> None:
    c, _ = client

    resp = c.post("/events", json={"payload": {}}, headers=_bearer(WRITE_A))

    assert resp.status_code == 400


# --- reading: the operator's token only (ADR 72) ---------------------------


def test_get_without_a_token_is_401(client) -> None:
    c, _ = client

    resp = c.get("/events")

    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"].startswith("Bearer")


def test_get_with_a_wrong_token_is_401(client) -> None:
    c, _ = client

    assert c.get("/events", headers=_bearer("not-the-read-token")).status_code == 401


def test_get_with_basic_auth_is_401(client) -> None:
    c, _ = client

    resp = c.get("/events", headers={"Authorization": "Basic b3BlcmF0b3I6cGFzcw=="})

    assert resp.status_code == 401


def test_get_with_the_read_token_is_200(client) -> None:
    c, _ = client

    resp = c.get("/events", headers=_bearer(READ))

    assert resp.status_code == 200
    assert resp.get_json() == []


@pytest.mark.parametrize("token", [WRITE_A, WRITE_B])
def test_a_write_token_cannot_read(client, token) -> None:
    # An agent's token is readable by the code running in its container; it
    # must not let that code read what the other agent did.
    c, _ = client

    assert c.get("/events", headers=_bearer(token)).status_code == 401


# --- writing: one token per agent, bound to its source_app -----------------


def test_post_without_a_token_is_401_and_stores_nothing(client) -> None:
    c, db_path = client

    resp = c.post("/events", json={"source_app": "agent-cuenta1", "event_type": "Stop"})

    assert resp.status_code == 401
    assert db.list_events(db_path) == []


def test_post_with_a_wrong_token_is_401_and_stores_nothing(client) -> None:
    c, db_path = client

    resp = c.post(
        "/events",
        json={"source_app": "agent-cuenta1", "event_type": "Stop"},
        headers=_bearer("not-a-write-token"),
    )

    assert resp.status_code == 401
    assert db.list_events(db_path) == []


def test_the_read_token_cannot_write(client) -> None:
    c, db_path = client

    resp = c.post(
        "/events", json={"source_app": "agent-cuenta1", "event_type": "Stop"}, headers=_bearer(READ)
    )

    assert resp.status_code == 401
    assert db.list_events(db_path) == []


def test_a_token_for_a_claims_b_and_is_stored_as_a(client) -> None:
    c, db_path = client

    resp = c.post(
        "/events",
        json={"source_app": "agent-cuenta2", "event_type": "PreToolUse", "payload": {}},
        headers=_bearer(WRITE_A),
    )

    assert resp.status_code == 201
    assert [e["source_app"] for e in db.list_events(db_path)] == ["agent-cuenta1"]


def test_a_body_with_no_source_app_is_stored_under_the_token(client) -> None:
    c, db_path = client

    resp = c.post("/events", json={"event_type": "Stop"}, headers=_bearer(WRITE_B))

    assert resp.status_code == 201
    assert [e["source_app"] for e in db.list_events(db_path)] == ["agent-cuenta2"]


def test_an_empty_token_map_accepts_nothing(tmp_path: Path) -> None:
    # `create_app` with nothing to accept closes the route rather than opening it.
    c = create_app(str(tmp_path / "events.db"), "", {}).test_client()

    assert c.post("/events", json={"event_type": "Stop"}).status_code == 401
    assert c.post("/events", json={"event_type": "Stop"}, headers=_bearer("")).status_code == 401
    assert c.get("/events").status_code == 401


# --- startup: no silent open mode -------------------------------------------


GOOD_ENV = {
    "COLLECTOR_READ_TOKEN": READ,
    "COLLECTOR_WRITE_TOKENS": f"agent-cuenta1={WRITE_A},agent-cuenta2={WRITE_B}",
}


def test_load_config_reads_both_variables() -> None:
    assert load_config(GOOD_ENV) == (READ, WRITE_TOKENS)


@pytest.mark.parametrize("variable", ["COLLECTOR_READ_TOKEN", "COLLECTOR_WRITE_TOKENS"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_startup_refuses_when_a_variable_is_missing(variable, value, tmp_path, capsys) -> None:
    env = dict(GOOD_ENV, COLLECTOR_DB_PATH=str(tmp_path / "events.db"))
    if value is None:
        del env[variable]
    else:
        env[variable] = value

    with pytest.raises(SystemExit) as excinfo:
        main(env)

    assert excinfo.value.code != 0
    assert variable in capsys.readouterr().err
    assert not (tmp_path / "events.db").exists()


def test_startup_with_both_variables_builds_the_app(tmp_path) -> None:
    env = dict(GOOD_ENV, COLLECTOR_DB_PATH=str(tmp_path / "events.db"))

    c = main(env).test_client()

    assert c.get("/events", headers=_bearer(READ)).status_code == 200


def _fake_waitress(monkeypatch) -> list:
    """A stand-in `waitress` in `sys.modules`: the agent venv has none (ADR 417)."""
    calls: list = []
    monkeypatch.setitem(
        sys.modules,
        "waitress",
        types.SimpleNamespace(serve=lambda app, **kwargs: calls.append((app, kwargs))),
    )
    return calls


def test_run_serves_the_app_from_waitress_on_the_same_host_and_port(
    tmp_path, monkeypatch
) -> None:
    calls = _fake_waitress(monkeypatch)
    env = dict(GOOD_ENV, COLLECTOR_DB_PATH=str(tmp_path / "events.db"))

    run(env)

    assert len(calls) == 1
    app, kwargs = calls[0]
    # The literals pin ADR 417: a change to either is a decision to revisit.
    assert kwargs == {"host": "0.0.0.0", "port": 8787, "threads": 4}
    assert (HOST, PORT, THREADS) == ("0.0.0.0", 8787, 4)
    assert app.test_client().get("/events", headers=_bearer(READ)).status_code == 200


def test_run_sends_waitress_start_line_and_queue_warnings_to_stderr(
    tmp_path, monkeypatch, capsys
) -> None:
    root = logging.getLogger()

    def serve(app, **kwargs):
        # waitress.serve() runs logging.basicConfig(): a stderr handler on root
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(logging.BASIC_FORMAT))
        root.addHandler(handler)
        try:
            logging.getLogger("waitress").info("Serving on http://0.0.0.0:8787")
            logging.getLogger("waitress.queue").warning("Task queue depth is 1")
        finally:
            root.removeHandler(handler)

    monkeypatch.setitem(sys.modules, "waitress", types.SimpleNamespace(serve=serve))
    env = dict(GOOD_ENV, COLLECTOR_DB_PATH=str(tmp_path / "events.db"))
    before = (root.level, list(root.handlers))

    run(env)

    err = capsys.readouterr().err
    assert err.count("INFO:waitress:Serving on") == 1  # ADR 423
    assert err.count("WARNING:waitress.queue:Task queue depth") == 1
    waitress_logger = logging.getLogger("waitress")
    assert waitress_logger.propagate is False
    assert len(waitress_logger.handlers) == 1
    assert (root.level, list(root.handlers)) == before  # only `waitress` changed


@pytest.mark.parametrize("installed", [True, False])
def test_run_refuses_a_missing_token_before_it_touches_waitress(
    installed, tmp_path, monkeypatch, capsys
) -> None:
    if installed:
        calls = _fake_waitress(monkeypatch)
    else:
        monkeypatch.setitem(sys.modules, "waitress", None)  # import raises
        calls = []
    env = dict(GOOD_ENV, COLLECTOR_DB_PATH=str(tmp_path / "events.db"))
    del env["COLLECTOR_READ_TOKEN"]

    with pytest.raises(SystemExit) as excinfo:
        run(env)

    assert excinfo.value.code == 2
    assert "COLLECTOR_READ_TOKEN" in capsys.readouterr().err
    assert calls == []


def test_the_map_tolerates_whitespace_and_line_wrapping() -> None:
    raw = f" agent-cuenta1 = {WRITE_A} ,\n  agent-cuenta2={WRITE_B},"

    assert parse_write_tokens(raw) == WRITE_TOKENS


def test_the_map_keeps_a_token_holding_an_equals_sign_whole() -> None:
    assert parse_write_tokens("agent-cuenta1=abc=def") == {"agent-cuenta1": "abc=def"}


@pytest.mark.parametrize(
    "raw, named",
    [
        # What compose writes when `.env` has no COLLECTOR_WRITE_TOKEN_CUENTA2.
        (f"agent-cuenta1={WRITE_A},agent-cuenta2=", "agent-cuenta2"),
        ("agent-cuenta1", "<source_app>=<token>"),
        (f"={WRITE_A}", "<source_app>=<token>"),
        (f"agent-cuenta1={WRITE_A},agent-cuenta1={WRITE_B}", "twice"),
        (f"agent-cuenta1={WRITE_A},agent-cuenta2={WRITE_A}", "agent-cuenta2"),
        (",,", "COLLECTOR_WRITE_TOKENS is not set"),
    ],
)
def test_a_map_that_would_be_a_hole_is_refused(raw, named) -> None:
    with pytest.raises(ConfigError) as excinfo:
        parse_write_tokens(raw)

    assert named in str(excinfo.value)
    assert "COLLECTOR_WRITE_TOKENS" in str(excinfo.value)


def test_a_write_token_equal_to_the_read_token_is_refused() -> None:
    env = dict(GOOD_ENV, COLLECTOR_WRITE_TOKENS=f"agent-cuenta1={READ}")

    with pytest.raises(ConfigError) as excinfo:
        load_config(env)

    assert "COLLECTOR_READ_TOKEN" in str(excinfo.value)


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
