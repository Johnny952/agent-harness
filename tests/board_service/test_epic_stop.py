"""*Stop epic*'s route (ADR 211): the epic's waiting starts dropped, a running one left.

Own config: hive and state dirs are under tmp_path, never the real ones.
"""
from board_service.app import create_app
from dispatcher import actions
from tests.board_service.test_epic_start import (  # noqa: F401  (fixtures)
    AUTH,
    cfg,
    client,
    epic,
    hive,
    pending,
    start_epic,
    state,
)
from tests.board_service.test_app import task


def stop_epic(client, epic_id="T-1", body=None, headers=AUTH, **kw):
    return client.post(f"/api/epics/{epic_id}/stop", json={} if body is None else body,
                       headers=headers, **kw)


def test_queues_a_cancel_per_waiting_start_and_leaves_the_claimed_one(client, hive, state):
    epic(hive)
    queued = start_epic(client).get_json()["queued"]
    assert actions.claim(state, queued[0]["id"]) is not None  # the worker took it
    res = stop_epic(client)
    assert res.status_code == 201, res.get_json()
    out = res.get_json()
    assert out["epic"] == "T-1"
    assert out["dropped"] == [
        {"action": r["id"], "task_id": r["task_id"]} for r in queued[1:]
    ]
    assert out["left"] == [{"action": queued[0]["id"], "task_id": queued[0]["task_id"]}]
    cancels = [r for r in pending(state) if r["kind"] == "cancel"]
    assert [c["params"] for c in cancels] == [
        {"target": r["id"], "only_pending": True} for r in queued[1:]
    ]
    assert [c["id"] for c in out["cancels"]] == [c["id"] for c in cancels]
    assert all(c["actor"] == "operator" for c in cancels)
    # The starts themselves are not moved: the worker is the only mover.
    assert {r["id"] for r in pending(state) if r["kind"] == "start"} == {
        r["id"] for r in queued[1:]
    }


def test_a_start_queued_by_itself_is_not_the_epics(client, hive, state):
    epic(hive)
    one = client.post("/api/tasks/T-1-S1/start", json={"project": "demo"}, headers=AUTH)
    assert one.status_code == 201
    res = stop_epic(client)
    assert res.status_code == 409
    assert res.get_json()["error"] == "epic T-1 has no start queued"
    assert [r["kind"] for r in pending(state)] == ["start"]


def test_a_second_press_is_refused(client, hive, state):
    epic(hive)
    assert start_epic(client).status_code == 201
    assert stop_epic(client).status_code == 201
    before = pending(state)
    res = stop_epic(client)
    assert res.status_code == 409
    assert "already has a cancel queued" in res.get_json()["error"]
    assert pending(state) == before


def test_no_such_task_is_404_and_a_task_is_not_an_epic(client, hive, state):
    assert stop_epic(client, epic_id="T-9").status_code == 404
    task(hive, "T-2")
    res = stop_epic(client, epic_id="T-2")
    assert res.status_code == 409 and res.get_json()["error"] == "T-2 is not an epic"
    assert pending(state) == []


def test_a_body_is_400(client, hive, state):
    epic(hive)
    start_epic(client)
    before = pending(state)
    res = stop_epic(client, body={"note": "why"})
    assert res.status_code == 400 and "empty body" in res.get_json()["error"]
    assert pending(state) == before


def test_the_bearer_is_required(client, hive, state):
    epic(hive)
    start_epic(client)
    before = pending(state)
    assert stop_epic(client, headers={}).status_code == 401
    assert stop_epic(client, headers={**AUTH, "Origin": "http://x"}).status_code == 403
    assert pending(state) == before


def test_without_a_token_the_route_is_off(cfg, hive, state, monkeypatch):
    monkeypatch.delenv("BOARD_ACTIONS_TOKEN", raising=False)
    client = create_app(str(cfg)).test_client()
    epic(hive)
    assert stop_epic(client).status_code == 503

