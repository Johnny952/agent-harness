"""The board service's request-changes route (`docs/plans/review.md` step 6, ADR 272, T-128).

The same path and auth model as approve's (`test_approve_route.py`); the
fixtures are `test_actions_routes.py`'s, under tmp_path.
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


def request_changes(client, task_id="T-1", body=None, headers=AUTH, **kw):
    body = {"project": "demo", "commit": COMMIT, "text": "Rename it."} if body is None else body
    return client.post(f"/api/tasks/{task_id}/request-changes", json=body, headers=headers, **kw)


def lines(hive, task_id="T-1"):
    path = hive.parent / "board" / task_id / "comments.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def nothing_written(hive, state):
    assert lines(hive) == []
    assert pending(state) == []


def test_a_request_writes_the_line_and_queues_the_action(client, hive, state):
    task(hive, "T-1", status="awaiting_review")
    res = request_changes(client)
    assert res.status_code == 201
    body = res.get_json()
    [line] = lines(hive)
    assert line["verdict"] == {"kind": "request_changes", "commit": COMMIT}
    assert (line["author"], line["text"]) == ("operator", "Rename it.")
    assert body["verdict"]["comment"] == line
    [path] = pending(state)
    on_disk = json.loads(path.read_text())
    assert on_disk == body["action"]
    assert (on_disk["kind"], on_disk["project"], on_disk["task_id"]) == (
        "request_changes", "demo", "T-1",
    )
    assert (on_disk["actor"], on_disk["params"], on_disk["status"]) == (
        "operator", {"commit": COMMIT}, "pending",
    )
    assert actions.validate(on_disk) == []


def test_the_request_starts_nothing_and_leaves_the_task_awaiting_review(client, hive, state):
    task(hive, "T-1", status="awaiting_review")
    assert request_changes(client).status_code == 201
    assert "status: awaiting_review" in (hive / "T-1.md").read_text()


@pytest.mark.parametrize("value", [None, ""])
def test_no_token_set_is_503(cfg, hive, state, monkeypatch, value):
    task(hive, "T-1", status="awaiting_review")
    if value is None:
        monkeypatch.delenv("BOARD_ACTIONS_TOKEN", raising=False)
    else:
        monkeypatch.setenv("BOARD_ACTIONS_TOKEN", value)
    client = create_app(str(cfg)).test_client()
    assert request_changes(client).status_code == 503
    nothing_written(hive, state)


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}],
)
def test_a_missing_or_wrong_bearer_is_401(client, hive, state, headers):
    task(hive, "T-1", status="awaiting_review")
    res = request_changes(client, headers=headers)
    assert res.status_code == 401
    assert TOKEN not in res.get_data(as_text=True)
    nothing_written(hive, state)


def test_an_origin_is_403_even_with_the_token(client, hive, state):
    task(hive, "T-1", status="awaiting_review")
    res = request_changes(client, headers={**AUTH, "Origin": "http://evil.example"})
    assert res.status_code == 403
    nothing_written(hive, state)


def test_a_query_parameter_is_400(client, hive, state):
    task(hive, "T-1", status="awaiting_review")
    assert request_changes(client, query_string={"x": "1"}).status_code == 400
    nothing_written(hive, state)


@pytest.mark.parametrize(
    "status, merged, needle",
    [
        ("conflict", None, "resolve conflict"),
        ("done", None, "not awaiting_review"),
        ("awaiting_review", MERGED, "it is merged"),
        ("merged", None, "it is merged"),
        ("pending", None, "not awaiting_review"),
        ("in_progress", None, "not awaiting_review"),
    ],
)
def test_a_task_not_awaiting_review_is_refused_and_writes_nothing(
    client, hive, state, status, merged, needle
):
    task(hive, "T-1", status=status, **({"merged": merged} if merged else {}))
    res = request_changes(client)
    assert res.status_code == 400
    assert needle in res.get_json()["error"]
    assert "verdict" not in res.get_json()
    nothing_written(hive, state)


def test_no_task_is_404_and_writes_nothing(client, hive, state):
    res = request_changes(client, task_id="T-404")
    assert res.status_code == 404
    assert pending(state) == []
    assert lines(hive, "T-404") == []


@pytest.mark.parametrize(
    "body, needle",
    [
        ({"project": "demo", "commit": COMMIT, "text": "x", "note": "go"}, "unknown field"),
        ({"commit": COMMIT, "text": "x"}, "project"),
        ({"project": "demo", "text": "x"}, "commit"),
        ({"project": "demo", "commit": "main", "text": "x"}, "commit"),
        ({"project": "demo", "commit": COMMIT}, "text"),
        ({"project": "demo", "commit": COMMIT, "text": "  "}, "text"),
    ],
)
def test_a_bad_body_is_400_and_writes_nothing(client, hive, state, body, needle):
    task(hive, "T-1", status="awaiting_review")
    res = request_changes(client, body=body)
    assert res.status_code == 400
    assert needle in res.get_json()["error"]
    nothing_written(hive, state)


def test_a_second_request_is_409_with_its_line_kept(client, hive, state):
    task(hive, "T-1", status="awaiting_review")
    assert request_changes(client).status_code == 201
    res = request_changes(client, body={"project": "demo", "commit": COMMIT, "text": "Also."})
    assert res.status_code == 409
    assert res.get_json()["verdict"]["comment"]["text"] == "Also."
    assert [line["text"] for line in lines(hive)] == ["Rename it.", "Also."]
    assert len(pending(state)) == 1


def test_a_request_while_an_approve_is_queued_is_409(client, hive, state):
    task(hive, "T-1", status="awaiting_review")
    approved = client.post(
        "/api/tasks/T-1/approve",
        json={"project": "demo", "commit": COMMIT, "text": "Ship it."}, headers=AUTH,
    )
    assert approved.status_code == 201
    res = request_changes(client)
    assert res.status_code == 409
    assert "approve" in res.get_json()["error"]
    assert [json.loads(p.read_text())["kind"] for p in pending(state)] == ["approve"]


def test_an_unwritable_queue_is_503_with_the_line_kept(client, hive, state):
    task(hive, "T-1", status="awaiting_review")
    state.write_text("a file where the state dir should be")
    res = request_changes(client)
    assert res.status_code == 503
    body = res.get_json()
    assert "action queue" in body["error"]
    assert body["verdict"]["comment"] == lines(hive)[0]


def test_the_verdict_route_refuses_request_changes_naming_the_new_route(client, hive, state):
    task(hive, "T-1", status="awaiting_review")
    res = client.post(
        "/api/tasks/T-1/verdict", json={"kind": "request_changes", "text": "x", "commit": COMMIT}
    )
    assert res.status_code == 400
    assert "/api/tasks/T-1/request-changes" in res.get_json()["error"]
    nothing_written(hive, state)
