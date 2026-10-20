"""*Start epic*'s route (ADR 207): one `start` per Ready task of the epic.

Own config: hive and state dirs are under tmp_path, never the real ones.
"""
import json
from pathlib import Path

import pytest

from board_service.app import create_app
from dispatcher import actions
from tests.board_service.test_app import CONFIG_YAML, task

TOKEN = "s3cret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def hive(tmp_path: Path) -> Path:
    path = tmp_path / ".hive" / "tasks"
    path.mkdir(parents=True)
    return path


@pytest.fixture
def state(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture
def cfg(tmp_path: Path, hive: Path, state: Path) -> Path:
    path = tmp_path / "config.yaml"
    text = CONFIG_YAML.format(hive=hive).replace("/data/dispatcher_state", str(state))
    path.write_text(text)
    (tmp_path / "config.d").mkdir()
    return path


@pytest.fixture
def client(cfg: Path, monkeypatch):
    monkeypatch.setenv("BOARD_ACTIONS_TOKEN", TOKEN)
    return create_app(str(cfg)).test_client()


def pending(state: Path) -> list[dict]:
    root = state / "actions" / "pending"
    if not root.exists():
        return []
    return [json.loads(p.read_text()) for p in sorted(root.glob("*.json"))]


def epic(hive: Path, children=("T-1-S1", "T-1-S2", "T-1-S3"), epic_id="T-1", **extra) -> None:
    task(hive, epic_id, status="epic", split_into=list(children))
    for child in children:
        task(hive, child, epic=epic_id, **extra.get(child, {}))


def start_epic(client, epic_id="T-1", body=None, headers=AUTH, **kw):
    body = {"project": "demo", "note": "go"} if body is None else body
    return client.post(f"/api/epics/{epic_id}/start", json=body, headers=headers, **kw)


def test_queues_one_start_per_ready_task_in_the_boards_order(client, hive, state):
    # The board's order is not the id order: S3 is high, then S2 ranks before S1.
    epic(hive, **{"T-1-S3": {"priority": "high"}, "T-1-S1": {"rank": 2}, "T-1-S2": {"rank": 1}})
    res = start_epic(client)
    assert res.status_code == 201, res.get_json()
    out = res.get_json()
    assert out["epic"] == "T-1"
    assert [r["task_id"] for r in out["queued"]] == ["T-1-S3", "T-1-S2", "T-1-S1"]
    assert out["not_ready"] == []
    queued = pending(state)
    # The worker scans pending/ by name: the files sort in the queued order.
    assert [r["task_id"] for r in queued] == ["T-1-S3", "T-1-S2", "T-1-S1"]
    assert [r["id"] for r in queued] == [r["id"] for r in out["queued"]]
    for record in queued:
        assert actions.validate(record) == []
        assert record["kind"] == "start"
        assert record["project"] == "demo"
        assert record["actor"] == "operator"
        assert record["note"] == "go"
        assert record["params"] == {"limit": None, "epic": "T-1"}


def test_a_task_not_in_ready_is_not_queued_and_is_named(client, hive, state):
    task(hive, "T-1", status="epic", split_into=["T-1-S1", "T-1-S2", "T-1-S3"])
    task(hive, "T-1-S1", epic="T-1")
    task(hive, "T-1-S2", epic="T-1", depends_on=["T-1-S1"])
    task(hive, "T-1-S3", epic="T-1", status="in_progress")
    task(hive, "T-2")  # Ready, but not the epic's.
    res = start_epic(client)
    assert res.status_code == 201
    out = res.get_json()
    assert [r["task_id"] for r in out["queued"]] == ["T-1-S1"]
    assert out["not_ready"] == ["T-1-S2", "T-1-S3"]
    assert [r["task_id"] for r in pending(state)] == ["T-1-S1"]


def test_without_a_note_the_starts_have_none(client, hive, state):
    epic(hive)
    assert start_epic(client, body={"project": "demo"}).status_code == 201
    assert {r["note"] for r in pending(state)} == {None}


def test_a_null_limit_is_accepted(client, hive, state):
    epic(hive)
    assert start_epic(client, body={"project": "demo", "limit": None}).status_code == 201
    assert len(pending(state)) == 3


def test_a_second_press_is_refused_and_writes_nothing(client, hive, state):
    epic(hive)
    assert start_epic(client).status_code == 201
    first = pending(state)
    res = start_epic(client)
    assert res.status_code == 409
    assert "already queued" in res.get_json()["error"]
    assert pending(state) == first


def test_one_task_already_started_refuses_the_whole_epic(client, hive, state):
    epic(hive)
    one = client.post("/api/tasks/T-1-S2/start", json={"project": "demo"}, headers=AUTH)
    assert one.status_code == 201
    res = start_epic(client)
    assert res.status_code == 409
    assert "T-1-S2" in res.get_json()["error"]
    assert [r["task_id"] for r in pending(state)] == ["T-1-S2"]


def test_no_such_task_is_404(client, hive, state):
    res = start_epic(client, epic_id="T-9")
    assert res.status_code == 404
    assert pending(state) == []


def test_a_task_that_is_not_an_epic_is_refused(client, hive, state):
    task(hive, "T-1")
    res = start_epic(client)
    assert res.status_code == 409
    assert res.get_json()["error"] == "T-1 is not an epic"
    assert pending(state) == []


def test_an_epic_with_no_task_in_ready_is_refused(client, hive, state):
    task(hive, "T-1", status="epic", split_into=["T-1-S1", "T-1-S2"])
    task(hive, "T-1-S1", epic="T-1", status="in_progress")
    task(hive, "T-1-S2", epic="T-1", merged=True, status="done")
    res = start_epic(client)
    assert res.status_code == 409
    assert res.get_json()["error"] == "epic T-1 has no task in Ready"
    assert pending(state) == []


@pytest.mark.parametrize("body, needle", [
    ({"project": "demo", "limit": 0}, "limit must be"),
    ({"project": "demo", "limit": -5}, "limit must be"),
    ({"project": "demo", "limit": "1000"}, "limit must be"),
    ({"project": "demo", "limit": 1.5}, "limit must be"),
    ({"project": "demo", "limit": True}, "limit must be"),
    ({"project": "demo", "tasks": ["T-1-S1"]}, "unknown field 'tasks'"),
    ({"project": "demo", "note": 7}, "note"),
])
def test_a_bad_body_is_400(client, hive, state, body, needle):
    epic(hive)
    res = start_epic(client, body=body)
    assert res.status_code == 400
    assert needle in res.get_json()["error"]
    assert pending(state) == []


def test_the_bearer_is_required(client, hive, state):
    epic(hive)
    assert start_epic(client, headers={}).status_code == 401
    assert start_epic(client, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert pending(state) == []


def test_a_browser_origin_is_refused(client, hive, state):
    epic(hive)
    res = start_epic(client, headers={**AUTH, "Origin": "http://localhost:3000"})
    assert res.status_code == 403
    assert pending(state) == []


def test_a_query_parameter_is_refused(client, hive, state):
    epic(hive)
    res = client.post("/api/epics/T-1/start?all=1", json={"project": "demo"}, headers=AUTH)
    assert res.status_code == 400
    assert pending(state) == []


def test_without_a_token_the_route_is_off(cfg, hive, state, monkeypatch):
    monkeypatch.delenv("BOARD_ACTIONS_TOKEN", raising=False)
    client = create_app(str(cfg)).test_client()
    epic(hive)
    res = start_epic(client)
    assert res.status_code == 503
    assert pending(state) == []
