"""The board service's two action routes (ADR 142, ADR 151).

Own config: its `state_dir` is under tmp_path, never the shared `cfg`
fixture's real `/data/dispatcher_state`.
"""
import json
from pathlib import Path

import pytest

from board_service.app import create_app
from dispatcher import actions
from tests.board_service.test_app import CONFIG_YAML, DESCRIPTION, task

TOKEN = "s3cret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
TARGET = "a-20261019T120000000000Z-abcd"


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


def pending(state: Path) -> list[Path]:
    return sorted((state / "actions" / "pending").glob("*.json")) if (state / "actions").exists() else []


def start(client, task_id="T-1", body=None, headers=AUTH, **kw):
    body = {"project": "demo", "note": "go"} if body is None else body
    return client.post(f"/api/tasks/{task_id}/start", json=body, headers=headers, **kw)


def cancel(client, action_id=TARGET, body=None, headers=AUTH, **kw):
    return client.post(
        f"/api/actions/{action_id}/cancel", json={} if body is None else body, headers=headers, **kw
    )


def test_a_start_is_created_pending(client, hive, state):
    task(hive, "T-1")
    res = start(client)
    assert res.status_code == 201
    record = res.get_json()
    files = pending(state)
    assert [p.stem for p in files] == [record["id"]]
    on_disk = json.loads(files[0].read_text())
    assert on_disk == record
    assert (on_disk["kind"], on_disk["project"], on_disk["task_id"]) == ("start", "demo", "T-1")
    assert (on_disk["note"], on_disk["actor"], on_disk["params"]) == ("go", "operator", {"limit": None})


def test_a_start_needs_no_note(client, hive):
    task(hive, "T-1")
    assert start(client, body={"project": "demo"}).status_code == 201


def test_a_cancel_is_created_with_its_target(client, state):
    res = cancel(client)
    assert res.status_code == 201
    record = res.get_json()
    assert record["kind"] == "cancel"
    assert record["params"] == {"target": TARGET}
    assert record["actor"] == "operator"
    assert [p.stem for p in pending(state)] == [record["id"]]


@pytest.mark.parametrize("value", [None, ""])
def test_no_token_set_is_503(cfg, hive, state, monkeypatch, value):
    task(hive, "T-1")
    if value is None:
        monkeypatch.delenv("BOARD_ACTIONS_TOKEN", raising=False)
    else:
        monkeypatch.setenv("BOARD_ACTIONS_TOKEN", value)
    client = create_app(str(cfg)).test_client()
    assert start(client, headers={}).status_code == 503
    assert start(client, headers={"Authorization": "Bearer "}).status_code == 503
    assert cancel(client).status_code == 503
    assert pending(state) == []


def test_the_other_console_writes_work_with_no_token(cfg, hive, monkeypatch):
    monkeypatch.delenv("BOARD_ACTIONS_TOKEN", raising=False)
    client = create_app(str(cfg)).test_client()
    res = client.post("/api/backlog", json={"title": "A", "description": DESCRIPTION})
    assert res.status_code == 201, res.get_json()
    assert start(client).status_code == 503
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Bearer "}, {"Authorization": TOKEN},
     {"Authorization": f"Basic {TOKEN}"}],
)
def test_a_missing_or_wrong_bearer_is_401(client, hive, state, headers):
    task(hive, "T-1")
    for res in (start(client, headers=headers), cancel(client, headers=headers)):
        assert res.status_code == 401
        assert res.headers["WWW-Authenticate"] == "Bearer"
        assert TOKEN not in res.get_data(as_text=True)
    assert pending(state) == []


def test_the_bearer_scheme_is_case_insensitive(client, hive):
    task(hive, "T-1")
    assert start(client, headers={"Authorization": f"bearer {TOKEN}"}).status_code == 201


def test_a_token_is_checked_before_the_body(client):
    res = client.post("/api/tasks/T-1/start", data="not json", headers={})
    assert res.status_code == 401


def test_a_second_start_for_the_task_is_409(client, hive, state):
    task(hive, "T-1")
    assert start(client).status_code == 201
    res = start(client)
    assert res.status_code == 409
    assert "already queued" in res.get_json()["error"]
    assert len(pending(state)) == 1


def test_a_task_not_in_ready_is_409(client, hive, state):
    task(hive, "T-1", status="in_progress")
    task(hive, "T-2", depends_on=["T-9"])
    for task_id in ("T-1", "T-2", "T-404"):
        res = start(client, task_id)
        assert res.status_code == 409, task_id
        assert "not in Ready" in res.get_json()["error"]
    assert pending(state) == []


@pytest.mark.parametrize(
    "body",
    [[], "text", {}, {"project": ""}, {"project": 5}, {"project": "demo", "extra": 1},
     {"project": "demo", "params": {"limit": 3}}, {"project": "demo", "note": 7}],
)
def test_a_bad_start_body_is_400(client, hive, state, body):
    task(hive, "T-1")
    res = start(client, body=body)
    assert res.status_code == 400
    assert "error" in res.get_json()
    assert pending(state) == []


def test_a_start_body_that_is_not_json_is_400(client, hive):
    task(hive, "T-1")
    res = client.post("/api/tasks/T-1/start", data="nope", headers=AUTH)
    assert res.status_code == 400


@pytest.mark.parametrize("body", [{"x": 1}, [], "text"])
def test_a_bad_cancel_body_is_400(client, state, body):
    assert cancel(client, body=body).status_code == 400
    assert pending(state) == []


def test_a_cancel_of_a_non_action_id_is_400(client, state):
    assert cancel(client, "not-an-id").status_code == 400
    assert pending(state) == []


def test_a_query_string_is_400(client, hive, state):
    task(hive, "T-1")
    assert start(client, query_string={"x": "1"}).status_code == 400
    assert cancel(client, query_string={"x": "1"}).status_code == 400
    assert pending(state) == []


def test_an_origin_is_403_even_with_the_token(client, hive, state):
    task(hive, "T-1")
    headers = {**AUTH, "Origin": "http://evil.example"}
    assert start(client, headers=headers).status_code == 403
    assert cancel(client, headers=headers).status_code == 403
    assert pending(state) == []


def test_an_origin_is_403_before_the_token_check(client):
    assert start(client, headers={"Origin": "http://x"}).status_code == 403


def test_the_app_builds_without_touching_the_state_dir(cfg, state):
    create_app(str(cfg))
    assert not state.exists()


def test_an_unwritable_queue_is_503(client, hive, state):
    task(hive, "T-1")
    state.write_text("a file where the state dir should be")
    res = start(client)
    assert res.status_code == 503
    assert TOKEN not in res.get_data(as_text=True)


def test_the_record_validates(client, hive):
    task(hive, "T-1")
    assert actions.validate(start(client).get_json()) == []
