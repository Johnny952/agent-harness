"""A drop's `by` and an action's `actor` name a signed-in person (ADR 387, T-162).

Fixtures and sign-in helpers are `test_signin.py`'s, under tmp_path.
"""
import json

from dispatcher import actions, tasks as task_store
from board_service.app import ToolError, task_start
from tests.board_service.test_app import task
from tests.board_service.test_signin import (  # noqa: F401  (fixtures)
    auth_dir, clock, cfg, client, fast_scrypt, hive, secrets_, session,
)
from tests.board_service.test_verdict_author import BEARER, COMMIT

import pytest


def queued(tmp_path):
    root = tmp_path / "state" / "actions" / "pending"
    return [json.loads(p.read_text()) for p in sorted(root.glob("*.json"))] if root.exists() else []


def drop(client, hive, headers=None):
    task(hive, "T-002", status="proposed")
    body = {"to": "dropped", "reason": "dup"}
    return client.post("/api/tasks/T-002/accept", json=body, headers=headers)


def test_a_signed_in_drop_names_the_person(client, hive, secrets_, clock):
    _, csrf, _ = session(client, secrets_, clock, "ana")
    assert drop(client, hive, {**BEARER, **csrf}).status_code == 200
    assert task_store.read_all_tasks(str(hive))["T-002"].dropped_by == "ana"


def test_a_drop_with_no_cookie_is_the_operators(client, hive):
    assert drop(client, hive, BEARER).status_code == 200
    assert task_store.read_all_tasks(str(hive))["T-002"].dropped_by == "operator"


def test_a_signed_in_start_and_approve_name_the_person(client, hive, secrets_, clock, tmp_path):
    _, csrf, _ = session(client, secrets_, clock, "ana")
    headers = {**BEARER, **csrf}
    assert client.post("/api/tasks/T-001/start", json={"project": "demo"}, headers=headers).status_code == 201
    task(hive, "T-002", status="awaiting_review")
    review = {"project": "demo", "commit": COMMIT, "text": "Fine."}
    assert client.post("/api/tasks/T-002/approve", json=review, headers=headers).status_code == 201
    kinds = {r["kind"]: r["actor"] for r in queued(tmp_path)}
    assert kinds and set(kinds.values()) == {"ana"}


def test_a_start_with_no_cookie_is_the_operators(client, hive, tmp_path):
    res = client.post("/api/tasks/T-001/start", json={"project": "demo"}, headers=BEARER)
    assert res.status_code == 201
    assert {r["actor"] for r in queued(tmp_path)} == {"operator"}


def test_a_signed_in_turn_is_admitted_by_task_start_and_a_thread_turn_is_not(
    client, hive, secrets_, clock, tmp_path
):
    _, csrf, _ = session(client, secrets_, clock, "ana")
    headers = {**BEARER, **csrf}
    thread = client.post("/api/threads", json={"title": "t", "project": "demo"}, headers=headers)
    assert thread.status_code == 201, thread.get_data(as_text=True)
    thread_id = thread.get_json()["id"]
    res = client.post(f"/api/threads/{thread_id}/turns", json={"text": "start T-001"}, headers=headers)
    assert res.status_code in (200, 201), res.get_data(as_text=True)
    [turn] = [r for r in queued(tmp_path) if r["kind"] == "turn"]
    assert turn["actor"] == "ana"
    state = str(tmp_path / "state")
    actions.claim(state, turn["id"])
    result = task_start(str(hive), state, BEARER["Authorization"], turn["id"], "T-001")
    assert result["actor"] == "thread"
    forged = actions.create(
        state, "turn", params={"thread": thread_id, "text": "start T-001"}, actor="thread"
    )
    actions.claim(state, forged["id"])
    with pytest.raises(ToolError, match="no running turn"):
        task_start(str(hive), state, BEARER["Authorization"], forged["id"], "T-001")
