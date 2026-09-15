import base64
from pathlib import Path

from observability.collector import db as collector_db
from observability.dashboard.app import create_app


def _auth_header(username: str, password: str) -> dict:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_index_requires_auth(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    collector_db.init_db(db_path)
    app = create_app(db_path, "admin", "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8")
    client = app.test_client()

    resp = client.get("/")

    assert resp.status_code == 401


def test_index_with_valid_auth_shows_events(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    collector_db.init_db(db_path)
    collector_db.insert_event(db_path, "agent-cuenta1", "PreToolUse", {"tool": "Bash"})
    # sha256("password") = 5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8
    app = create_app(db_path, "admin", "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8")
    client = app.test_client()

    resp = client.get("/", headers=_auth_header("admin", "password"))

    assert resp.status_code == 200
    assert b"agent-cuenta1" in resp.data
