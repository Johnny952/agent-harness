"""The board service's resolve-conflict route (ADR 285, T-133-S1).

The same path and auth model as approve's and request changes'; the fixtures
are `test_actions_routes.py`'s, under tmp_path.
"""
import json

import pytest

from board_service.app import create_app
from dispatcher import actions
from tests.board_service.test_actions_routes import (  # noqa: F401  (fixtures)
    AUTH, TOKEN, cfg, client, hive, pending, state,
)
from tests.board_service.test_app import task

COMMIT = "a" * 40
MERGED = {"into": "main", "at": "2026-10-11T16:00:36+00:00"}


def resolve(client, task_id="T-1", body=None, headers=AUTH, **kw):
    body = {"project": "demo"} if body is None else body
    return client.post(f"/api/tasks/{task_id}/resolve-conflict", json=body, headers=headers, **kw)


def comments(hive, task_id="T-1"):
    return (hive.parent / "board" / task_id / "comments.jsonl").exists()


def nothing_written(hive, state):
    assert not comments(hive)
    assert pending(state) == []


def test_a_resolve_queues_the_action_and_writes_no_line(client, hive, state):
    task(hive, "T-1", status="conflict")
    res = resolve(client)
    assert res.status_code == 201
    [path] = pending(state)
    on_disk = json.loads(path.read_text())
    assert on_disk == res.get_json()
    assert (on_disk["kind"], on_disk["project"], on_disk["task_id"]) == (
        "resolve_conflict", "demo", "T-1",
    )
    assert (on_disk["actor"], on_disk["params"], on_disk["status"]) == (
        "operator", {}, "pending",
    )
    assert actions.validate(on_disk) == []
    assert not comments(hive)
    assert "status: conflict" in (hive / "T-1.md").read_text()


@pytest.mark.parametrize("value", [None, ""])
def test_no_token_set_is_503(cfg, hive, state, monkeypatch, value):
    task(hive, "T-1", status="conflict")
    if value is None:
        monkeypatch.delenv("BOARD_ACTIONS_TOKEN", raising=False)
    else:
        monkeypatch.setenv("BOARD_ACTIONS_TOKEN", value)
    client = create_app(str(cfg)).test_client()
    assert resolve(client).status_code == 503
    nothing_written(hive, state)


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}],
)
def test_a_missing_or_wrong_bearer_is_401(client, hive, state, headers):
    task(hive, "T-1", status="conflict")
    res = resolve(client, headers=headers)
    assert res.status_code == 401
    assert TOKEN not in res.get_data(as_text=True)
    nothing_written(hive, state)


def test_an_origin_is_403_even_with_the_token(client, hive, state):
    task(hive, "T-1", status="conflict")
    res = resolve(client, headers={**AUTH, "Origin": "http://evil.example"})
    assert res.status_code == 403
    nothing_written(hive, state)


def test_a_query_parameter_is_400(client, hive, state):
    task(hive, "T-1", status="conflict")
    assert resolve(client, query_string={"x": "1"}).status_code == 400
    nothing_written(hive, state)


@pytest.mark.parametrize(
    "status, merged, needle",
    [
        ("awaiting_review", None, "task T-1 is awaiting_review"),
        ("done", None, "task T-1 is done"),
        ("pending", None, "task T-1 is pending"),
        ("blocked", None, "task T-1 is blocked"),
        ("conflict", MERGED, "it is merged"),
        ("merged", None, "task T-1 is merged"),
    ],
)
def test_a_task_not_in_conflict_is_refused_with_a_reason(
    client, hive, state, status, merged, needle
):
    task(hive, "T-1", status=status, **({"merged": merged} if merged else {}))
    res = resolve(client)
    assert res.status_code == 400
    assert needle in res.get_json()["error"]
    nothing_written(hive, state)


def test_no_task_is_404(client, hive, state):
    res = resolve(client, task_id="T-404")
    assert res.status_code == 404
    assert pending(state) == []


@pytest.mark.parametrize(
    "body, needle",
    [
        ({"project": "demo", "commit": COMMIT}, "unknown field 'commit'"),
        ({"project": "demo", "text": "x"}, "unknown field 'text'"),
        ({}, "project"),
        ({"project": "../x"}, "project"),
    ],
)
def test_a_bad_body_is_400(client, hive, state, body, needle):
    task(hive, "T-1", status="conflict")
    res = resolve(client, body=body)
    assert res.status_code == 400
    assert needle in res.get_json()["error"]
    nothing_written(hive, state)


def test_a_non_object_body_is_400(client, hive, state):
    task(hive, "T-1", status="conflict")
    assert resolve(client, body=[1]).status_code == 400
    nothing_written(hive, state)


def test_a_second_resolve_is_409(client, hive, state):
    task(hive, "T-1", status="conflict")
    assert resolve(client).status_code == 201
    res = resolve(client)
    assert res.status_code == 409
    assert "resolve_conflict" in res.get_json()["error"]
    assert len(pending(state)) == 1


def test_a_resolve_while_an_approve_or_request_changes_is_queued_is_409(client, hive, state):
    task(hive, "T-1", status="conflict")
    queued = actions.create(
        state, "approve", project="demo", task_id="T-1", params={"commit": COMMIT},
        actor="operator",
    )
    res = resolve(client)
    assert res.status_code == 409
    assert "approve" in res.get_json()["error"]
    assert queued["kind"] == "approve"
    assert len(pending(state)) == 1


@pytest.mark.parametrize("kind", ["approve", "request_changes"])
def test_a_review_action_while_a_resolve_is_queued_is_409(client, hive, state, kind):
    task(hive, "T-1", status="conflict")
    assert resolve(client).status_code == 201
    # an approve takes a task under review: put the task back there
    task(hive, "T-1", status="awaiting_review")
    route = "approve" if kind == "approve" else "request-changes"
    res = client.post(
        f"/api/tasks/T-1/{route}",
        json={"project": "demo", "commit": COMMIT, "text": "x"}, headers=AUTH,
    )
    assert res.status_code == 409
    assert "resolve_conflict" in res.get_json()["error"]
    assert len(pending(state)) == 1


def test_an_unwritable_queue_is_503(client, hive, state):
    task(hive, "T-1", status="conflict")
    state.write_text("a file where the state dir should be")
    res = resolve(client)
    assert res.status_code == 503
    assert "action queue" in res.get_json()["error"]
