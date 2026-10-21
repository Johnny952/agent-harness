"""The board service's approve route (ADR 258 decision 1, ADR 267, T-125-S2).

The fixtures are `test_actions_routes.py`'s: the state dir and the hive are
under tmp_path, never the shared `cfg` fixture's real `/data/dispatcher_state`.
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


def approve(client, task_id="T-1", body=None, headers=AUTH, **kw):
    body = {"project": "demo", "commit": COMMIT, "text": "Looks right."} if body is None else body
    return client.post(f"/api/tasks/{task_id}/approve", json=body, headers=headers, **kw)


def lines(hive, task_id="T-1"):
    path = hive.parent / "board" / task_id / "comments.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def nothing_written(hive, state):
    assert lines(hive) == []
    assert pending(state) == []


@pytest.mark.parametrize("status", ["awaiting_review", "done"])
def test_an_approve_writes_the_line_and_queues_the_action(client, hive, state, status):
    task(hive, "T-1", status=status)
    res = approve(client)
    assert res.status_code == 201
    body = res.get_json()
    [line] = lines(hive)
    assert line["verdict"] == {"kind": "approve", "commit": COMMIT}
    assert (line["author"], line["text"]) == ("operator", "Looks right.")
    assert body["verdict"]["comment"] == line
    assert body["verdict"]["comment_count"] == 1
    [path] = pending(state)
    on_disk = json.loads(path.read_text())
    assert on_disk == body["action"]
    assert (on_disk["kind"], on_disk["project"], on_disk["task_id"]) == ("approve", "demo", "T-1")
    assert (on_disk["actor"], on_disk["params"], on_disk["status"]) == (
        "operator", {"commit": COMMIT}, "pending",
    )
    assert actions.validate(on_disk) == []


def test_the_line_is_written_before_the_action(client, hive, state, monkeypatch):
    task(hive, "T-1", status="done")
    seen = []
    real = actions.create

    def create(*args, **kwargs):
        seen.append((len(lines(hive)), len(pending(state))))
        return real(*args, **kwargs)

    monkeypatch.setattr(actions, "create", create)
    assert approve(client).status_code == 201
    assert seen == [(1, 0)]


@pytest.mark.parametrize("value", [None, ""])
def test_no_token_set_is_503(cfg, hive, state, monkeypatch, value):
    task(hive, "T-1", status="done")
    if value is None:
        monkeypatch.delenv("BOARD_ACTIONS_TOKEN", raising=False)
    else:
        monkeypatch.setenv("BOARD_ACTIONS_TOKEN", value)
    client = create_app(str(cfg)).test_client()
    assert approve(client).status_code == 503
    assert approve(client, headers={}).status_code == 503
    nothing_written(hive, state)


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Bearer "}, {"Authorization": TOKEN},
     {"Authorization": f"Basic {TOKEN}"}],
)
def test_a_missing_or_wrong_bearer_is_401(client, hive, state, headers):
    task(hive, "T-1", status="done")
    res = approve(client, headers=headers)
    assert res.status_code == 401
    assert res.headers["WWW-Authenticate"] == "Bearer"
    assert TOKEN not in res.get_data(as_text=True)
    nothing_written(hive, state)


def test_an_origin_is_403_even_with_the_token(client, hive, state):
    task(hive, "T-1", status="done")
    assert approve(client, headers={**AUTH, "Origin": "http://evil.example"}).status_code == 403
    nothing_written(hive, state)


def test_a_query_parameter_is_400(client, hive, state):
    task(hive, "T-1", status="done")
    assert approve(client, query_string={"x": "1"}).status_code == 400
    nothing_written(hive, state)


def test_the_token_is_checked_before_the_task(client):
    assert approve(client, task_id="T-404", headers={}).status_code == 401


@pytest.mark.parametrize(
    "status, merged, code, needle",
    [
        ("conflict", None, 400, "resolve conflict"),
        ("done", MERGED, 400, "it is merged"),
        ("awaiting_review", MERGED, 400, "it is merged"),
        ("merged", None, 400, "it is merged"),
        ("pending", None, 400, "not under review"),
        ("in_progress", None, 400, "not under review"),
    ],
)
def test_a_task_not_approvable_is_refused_and_writes_nothing(
    client, hive, state, status, merged, code, needle
):
    task(hive, "T-1", status=status, **({"merged": merged} if merged else {}))
    res = approve(client)
    assert res.status_code == code
    assert needle in res.get_json()["error"]
    assert "verdict" not in res.get_json()
    nothing_written(hive, state)


def test_no_task_is_404_and_writes_nothing(client, hive, state):
    res = approve(client, task_id="T-404")
    assert res.status_code == 404
    assert pending(state) == []
    assert lines(hive, "T-404") == []


@pytest.mark.parametrize(
    "body, needle",
    [
        ({"project": "demo", "commit": COMMIT, "text": "x", "note": "go"}, "unknown field"),
        ({"project": "demo", "commit": COMMIT, "text": "x", "kind": "approve"}, "unknown field"),
        ({"project": "demo", "commit": COMMIT, "text": "x", "author": "x"}, "unknown field"),
        ({"commit": COMMIT, "text": "x"}, "project"),
        ({"project": "../x", "commit": COMMIT, "text": "x"}, "project"),
        ({"project": "demo", "text": "x"}, "commit"),
        ({"project": "demo", "commit": "main", "text": "x"}, "commit"),
        ({"project": "demo", "commit": 5, "text": "x"}, "commit"),
        ({"project": "demo", "commit": COMMIT}, "text"),
        ({"project": "demo", "commit": COMMIT, "text": "  "}, "text"),
        ({"project": "demo", "commit": COMMIT, "text": 5}, "text"),
    ],
)
def test_a_bad_body_is_400_and_writes_nothing(client, hive, state, body, needle):
    task(hive, "T-1", status="done")
    res = approve(client, body=body)
    assert res.status_code == 400
    assert needle in res.get_json()["error"]
    nothing_written(hive, state)


@pytest.mark.parametrize("raw", [[], "x", 5])
def test_a_body_that_is_not_an_object_is_400(client, hive, state, raw):
    task(hive, "T-1", status="done")
    assert approve(client, body=raw).status_code == 400
    nothing_written(hive, state)


def test_a_body_that_is_not_json_is_400(client, hive, state):
    task(hive, "T-1", status="done")
    res = client.post("/api/tasks/T-1/approve", data="nope", headers=AUTH)
    assert res.status_code == 400
    nothing_written(hive, state)


def test_a_second_approve_is_409_with_its_line_kept(client, hive, state):
    task(hive, "T-1", status="done")
    assert approve(client).status_code == 201
    res = approve(client, body={"project": "demo", "commit": COMMIT, "text": "Again."})
    assert res.status_code == 409
    body = res.get_json()
    assert "error" in body
    assert body["verdict"]["comment"]["text"] == "Again."
    assert body["verdict"]["comment_count"] == 2
    assert [line["text"] for line in lines(hive)] == ["Looks right.", "Again."]
    assert len(pending(state)) == 1


def test_an_unwritable_queue_is_503_with_the_line_kept(client, hive, state):
    task(hive, "T-1", status="done")
    state.write_text("a file where the state dir should be")
    res = approve(client)
    assert res.status_code == 503
    assert TOKEN not in res.get_data(as_text=True)
    body = res.get_json()
    assert "action queue" in body["error"]
    assert body["verdict"]["comment"] == lines(hive)[0]
    assert len(lines(hive)) == 1


def test_the_verdict_route_refuses_approve_naming_the_new_route(client, hive, state):
    task(hive, "T-1", status="done")
    res = client.post(
        "/api/tasks/T-1/verdict", json={"kind": "approve", "text": "x", "commit": COMMIT}
    )
    assert res.status_code == 400
    assert "/api/tasks/T-1/approve" in res.get_json()["error"]
    nothing_written(hive, state)
