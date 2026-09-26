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


def test_index_with_wrong_username_and_correct_password(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    collector_db.init_db(db_path)
    app = create_app(db_path, "admin", "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8")
    client = app.test_client()

    resp = client.get("/", headers=_auth_header("not-admin", "password"))

    assert resp.status_code == 401


def test_index_with_non_ascii_username_returns_401_not_500(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    collector_db.init_db(db_path)
    app = create_app(db_path, "admin", "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8")
    client = app.test_client()

    resp = client.get("/", headers=_auth_header("josé", "password"))

    assert resp.status_code == 401


def test_index_with_bearer_auth_returns_401_not_500(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    collector_db.init_db(db_path)
    app = create_app(db_path, "admin", "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8")
    client = app.test_client()

    resp = client.get("/", headers={"Authorization": "Bearer abc"})

    assert resp.status_code == 401


def test_index_with_digest_auth_returns_401_not_500(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    collector_db.init_db(db_path)
    app = create_app(db_path, "admin", "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8")
    client = app.test_client()

    resp = client.get("/", headers={"Authorization": 'Digest username="admin", realm="x"'})

    assert resp.status_code == 401


def test_index_with_non_ascii_password_hash_returns_401_not_500(tmp_path: Path) -> None:
    # A misconfigured DASHBOARD_PASSWORD_HASH shouldn't 500 every login;
    # compare_digest needs bytes on both sides to tolerate that.
    db_path = str(tmp_path / "events.db")
    collector_db.init_db(db_path)
    app = create_app(db_path, "admin", "not-ascii-é")
    client = app.test_client()

    resp = client.get("/", headers=_auth_header("admin", "password"))

    assert resp.status_code == 401


def test_index_works_before_the_collector_has_created_the_database(tmp_path: Path) -> None:
    # Cold start: nothing has run init_db, so the dashboard must create the
    # schema itself instead of 500ing on a missing events table.
    db_path = str(tmp_path / "events.db")
    app = create_app(db_path, "admin", "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8")
    client = app.test_client()

    resp = client.get("/", headers=_auth_header("admin", "password"))

    assert resp.status_code == 200


def test_the_401_still_names_this_services_own_realm(tmp_path: Path) -> None:
    # The auth check moved to observability/auth.py when the read API arrived,
    # and the realm is the one string the two services do not share. Pinned so
    # the extraction cannot quietly rename this one.
    db_path = str(tmp_path / "events.db")
    collector_db.init_db(db_path)
    app = create_app(db_path, "admin", "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8")
    client = app.test_client()

    resp = client.get("/")

    assert resp.headers["WWW-Authenticate"] == 'Basic realm="ia-harness dashboard"'
    assert resp.data == b"Authentication required"
