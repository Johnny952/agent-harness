"""The board service's turn route (ADR 181).

Own config: hive and state dirs are under tmp_path, never the real ones.
"""
import json
from pathlib import Path

import pytest

from board_service.app import create_app
from dispatcher import actions
from tests.board_service.test_app import CONFIG_YAML

AUTH = {"Authorization": "Bearer s3cret"}


@pytest.fixture
def hive(tmp_path: Path) -> Path:
    path = tmp_path / ".hive" / "tasks"
    path.mkdir(parents=True)
    return path


@pytest.fixture
def state(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture
def client(tmp_path: Path, hive: Path, state: Path, monkeypatch):
    monkeypatch.setenv("BOARD_ACTIONS_TOKEN", "s3cret")
    path = tmp_path / "config.yaml"
    path.write_text(
        CONFIG_YAML.format(hive=hive).replace("/data/dispatcher_state", str(state))
        + "thread_models: [m1, m2]\n"
    )
    (tmp_path / "config.d").mkdir()
    return create_app(str(path)).test_client()


def new_thread(client, **body) -> str:
    return client.post("/api/threads", json={"title": "t", **body}).get_json()["id"]


def pending(state: Path) -> list[Path]:
    return sorted((state / "actions" / "pending").glob("*.json"))


def turn(client, tid, body, headers=AUTH):
    return client.post(f"/api/threads/{tid}/turns", json=body, headers=headers)


def test_a_turn_is_created_pending(client, state):
    tid = new_thread(client)
    res = turn(client, tid, {"text": "hello"})
    assert res.status_code == 201
    record = res.get_json()
    files = pending(state)
    assert len(files) == 1 and json.loads(files[0].read_text()) == record
    assert record["kind"] == "turn"
    assert record["params"] == {"thread": tid, "text": "hello"}
    assert record["project"] is None and record["task_id"] is None
    assert record["note"] is None and record["actor"] == "operator"
    claimed = actions.claim(state, record["id"])
    assert claimed is not None and claimed["id"] == record["id"]


def test_the_main_thread_takes_a_turn(client):
    assert client.post("/api/threads", json={"main": True}).status_code == 201
    assert turn(client, "main", {"text": "hi"}).status_code == 201


def test_the_bearer_guards(client, state):
    tid = new_thread(client)
    res = turn(client, tid, {"text": "x"}, headers={})
    assert res.status_code == 401 and res.headers["WWW-Authenticate"] == "Bearer"
    res = turn(client, tid, {"text": "x"}, headers={"Authorization": "Bearer nope"})
    assert res.status_code == 401
    res = turn(client, tid, {"text": "x"}, headers={**AUTH, "Origin": "http://e"})
    assert res.status_code == 403
    assert pending(state) == []


def test_no_token_set_is_503(tmp_path, hive, state, monkeypatch):
    monkeypatch.delenv("BOARD_ACTIONS_TOKEN", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text(
        CONFIG_YAML.format(hive=hive).replace("/data/dispatcher_state", str(state))
        + "thread_models: [m1, m2]\n"
    )
    (tmp_path / "config.d").mkdir()
    client = create_app(str(path)).test_client()
    tid = new_thread(client)
    assert turn(client, tid, {"text": "x"}).status_code == 503
    assert pending(state) == []


@pytest.mark.parametrize(
    "body",
    [
        {}, {"text": None}, {"text": 5}, {"text": ["x"]}, {"text": ""},
        {"text": "  \n"}, {"text": "hi", "thread": "x"}, ["text"],
    ],
)
def test_a_bad_body_is_400(client, state, body):
    tid = new_thread(client)
    assert turn(client, tid, body).status_code == 400
    assert pending(state) == []


def test_the_size_limit(client, state):
    tid = new_thread(client)
    assert turn(client, tid, {"text": "x" * 20_000}).status_code == 201
    res = turn(client, tid, {"text": "x" * 20_001})
    assert res.status_code == 400 and "20000" in res.get_json()["error"]
    assert len(pending(state)) == 1


@pytest.mark.parametrize("tid", ["th-20261019T120000Z-abcdef", "nope"])
def test_an_unknown_thread_is_404(client, state, tid):
    assert turn(client, tid, {"text": "x"}).status_code == 404
    assert pending(state) == []


def test_an_archived_thread_is_409(client, state):
    tid = new_thread(client)
    assert client.patch(f"/api/threads/{tid}", json={"status": "archived"}).status_code == 200
    res = turn(client, tid, {"text": "x"})
    assert res.status_code == 409 and "archived" in res.get_json()["error"]
    assert pending(state) == []


def test_two_posts_queue_on_one_serial_key(client, state):
    tid = new_thread(client)
    r1 = turn(client, tid, {"text": "one"}).get_json()
    r2 = turn(client, tid, {"text": "two"}).get_json()
    assert r1["id"] != r2["id"] and len(pending(state)) == 2
    assert actions.serial_key(r1) == actions.serial_key(r2) == f"thread:{tid}"


def test_a_turn_is_stopped_by_the_cancel_route(client):
    tid = new_thread(client)
    turn_id = turn(client, tid, {"text": "x"}).get_json()["id"]
    res = client.post(f"/api/actions/{turn_id}/cancel", json={}, headers=AUTH)
    assert res.status_code == 201
    record = res.get_json()
    assert record["kind"] == "cancel" and record["params"] == {"target": turn_id}
