"""The board service's verdict route (ADR 142 split item 9, T-106).

The fixtures are `test_actions_routes.py`'s: the state dir and the hive are
under tmp_path, never the shared `cfg` fixture's real `/data/dispatcher_state`.
"""
import json

import pytest

from board_service.app import create_app
from dispatcher import actions
from dispatcher.tasks import MAX_COMMENT_CHARS
from tests.board_service.test_actions_routes import (  # noqa: F401  (fixtures)
    AUTH, TOKEN, cfg, client, hive, pending, state,
)
from tests.board_service.test_app import task


def verdict(client, task_id="T-1", n="1", body=None, headers=AUTH, **kw):
    body = {"project": "demo", "verdict": "run"} if body is None else body
    return client.post(
        f"/api/tasks/{task_id}/command-requests/{n}/verdict", json=body, headers=headers, **kw
    )


def test_a_verdict_is_created_pending(client, state):
    res = verdict(client, n="2", body={"project": "demo", "verdict": "deny", "comment": "no"})
    assert res.status_code == 201
    record = res.get_json()
    files = pending(state)
    assert [p.stem for p in files] == [record["id"]]
    on_disk = json.loads(files[0].read_text())
    assert on_disk == record
    assert (on_disk["kind"], on_disk["project"], on_disk["task_id"]) == (
        "command_verdict", "demo", "T-1",
    )
    assert on_disk["params"] == {"n": 2, "verdict": "deny", "comment": "no", "prefix": None}
    assert (on_disk["actor"], on_disk["note"], on_disk["status"]) == ("operator", None, "pending")
    assert actions.validate(on_disk) == []


def test_an_allow_carries_its_prefix(client):
    res = verdict(client, body={"project": "demo", "verdict": "allow", "prefix": 2})
    assert res.status_code == 201
    assert res.get_json()["params"] == {"n": 1, "verdict": "allow", "comment": None, "prefix": 2}


def test_the_request_is_not_looked_up(client, hive, state):
    # No task file, no envelope: the worker's `run_command_request` refuses
    # it on claim (T-106); the route only queues it.
    assert verdict(client, task_id="T-404").status_code == 201
    assert len(pending(state)) == 1
    assert not any(hive.iterdir())


@pytest.mark.parametrize("value", [None, ""])
def test_no_token_set_is_503(cfg, state, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("BOARD_ACTIONS_TOKEN", raising=False)
    else:
        monkeypatch.setenv("BOARD_ACTIONS_TOKEN", value)
    client = create_app(str(cfg)).test_client()
    assert verdict(client).status_code == 503
    assert verdict(client, headers={}).status_code == 503
    assert pending(state) == []


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Bearer "}, {"Authorization": TOKEN},
     {"Authorization": f"Basic {TOKEN}"}],
)
def test_a_missing_or_wrong_bearer_is_401(client, state, headers):
    res = verdict(client, headers=headers)
    assert res.status_code == 401
    assert res.headers["WWW-Authenticate"] == "Bearer"
    assert TOKEN not in res.get_data(as_text=True)
    assert pending(state) == []


def test_an_origin_is_403_even_with_the_token(client, state):
    assert verdict(client, headers={**AUTH, "Origin": "http://evil.example"}).status_code == 403
    assert verdict(client, headers={"Origin": "http://x"}).status_code == 403
    assert pending(state) == []


def test_the_token_is_checked_before_the_path_and_the_body(client):
    res = client.post("/api/tasks/T-1/command-requests/0/verdict", data="nope", headers={})
    assert res.status_code == 401


@pytest.mark.parametrize("n", ["0", "-1", "01", "x", "1.5", "+1"])
def test_a_bad_request_number_is_400(client, state, n):
    res = verdict(client, n=n)
    assert res.status_code == 400
    assert "error" in res.get_json()
    assert pending(state) == []


@pytest.mark.parametrize(
    "body",
    [
        {"project": "demo"},
        {"project": "demo", "verdict": "approve"},
        {"project": "demo", "verdict": None},
        {"verdict": "run"},
        {"project": "", "verdict": "run"},
        {"project": "../x", "verdict": "run"},
        {"project": "demo", "verdict": "run", "comment": 5},
        {"project": "demo", "verdict": "run", "comment": "x" * (MAX_COMMENT_CHARS + 1)},
        {"project": "demo", "verdict": "run", "prefix": 2},
        {"project": "demo", "verdict": "allow", "prefix": 0},
        {"project": "demo", "verdict": "allow", "prefix": True},
    ],
)
def test_a_bad_verdict_body_is_400(client, state, body):
    res = verdict(client, body=body)
    assert res.status_code == 400
    assert "error" in res.get_json()
    assert pending(state) == []


@pytest.mark.parametrize(
    "body",
    [{"project": "demo", "verdict": "run", "extra": 1},
     {"project": "demo", "verdict": "run", "n": 1},
     {"project": "demo", "verdict": "run", "note": "go"},
     {"project": "demo", "verdict": "run", "params": {}}],
)
def test_an_extra_key_is_400(client, state, body):
    res = verdict(client, body=body)
    assert res.status_code == 400
    assert "unknown field" in res.get_json()["error"]
    assert pending(state) == []


@pytest.mark.parametrize("body", [[], "text", 5])
def test_a_body_that_is_not_an_object_is_400(client, state, body):
    assert verdict(client, body=body).status_code == 400
    assert pending(state) == []


def test_a_body_that_is_not_json_is_400(client, state):
    res = client.post("/api/tasks/T-1/command-requests/1/verdict", data="nope", headers=AUTH)
    assert res.status_code == 400
    assert pending(state) == []


def test_a_query_string_is_400(client, state):
    assert verdict(client, query_string={"x": "1"}).status_code == 400
    assert pending(state) == []


def test_a_second_verdict_for_the_task_is_409(client, state):
    assert verdict(client).status_code == 201
    # Another request number on the same task is the same block (ADR 119, 3).
    res = verdict(client, n="2", body={"project": "demo", "verdict": "deny"})
    assert res.status_code == 409
    assert "already queued" in res.get_json()["error"]
    assert len(pending(state)) == 1


def test_a_claimed_verdict_still_blocks_a_second(client, state):
    first = verdict(client).get_json()
    assert actions.claim(state, first["id"]) is not None
    assert verdict(client).status_code == 409
    assert pending(state) == []


def test_a_verdict_and_a_start_do_not_clash_at_the_door(client, hive, state):
    task(hive, "T-1")
    res = client.post("/api/tasks/T-1/start", json={"project": "demo"}, headers=AUTH)
    assert res.status_code == 201
    assert verdict(client).status_code == 201
    assert len(pending(state)) == 2


def test_another_task_takes_its_own_verdict(client, state):
    assert verdict(client, task_id="T-1").status_code == 201
    assert verdict(client, task_id="T-2").status_code == 201
    assert len(pending(state)) == 2


def test_an_unwritable_queue_is_503(client, state):
    state.write_text("a file where the state dir should be")
    res = verdict(client)
    assert res.status_code == 503
    assert TOKEN not in res.get_data(as_text=True)
