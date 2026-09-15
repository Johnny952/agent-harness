from pathlib import Path

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
