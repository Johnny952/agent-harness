"""A started epic's run (ADR NNN): its limit, its follow-on, *Stop* and *Resume*.

Own config: hive and state dirs are under tmp_path, never the real ones.
"""
import json
from pathlib import Path

from dispatcher import actions, context_transfer, epic_runs
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
from tests.board_service.test_epic_stop import stop_epic


def run_file(state: Path, epic_id="T-1") -> dict:
    return json.loads((state / "actions" / "epics" / f"{epic_id}.json").read_text())


def spend(hive: Path, task_id: str, tokens: int) -> None:
    """One phase's `result` line in the task's usage log, `tokens` all input."""
    context_transfer.append_usage(str(hive), task_id, {
        "input_tokens": tokens, "output_tokens": 0,
        "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
    })


def chain(hive: Path) -> None:
    """T-1 split into S1, S2 after S1, and S3 after S2."""
    epic(hive, **{"T-1-S2": {"depends_on": ["T-1-S1"]}, "T-1-S3": {"depends_on": ["T-1-S2"]}})


def merge(hive: Path, task_id: str) -> None:
    """The task's merge as `record_merge` writes it, without the follow-on."""
    from dispatcher import tasks
    tasks.mark_merged(str(hive), task_id, "main")


def test_the_start_writes_the_run_with_its_limit(client, hive, state):
    epic(hive)
    res = start_epic(client, body={"project": "demo", "note": "go", "limit": 5000})
    assert res.status_code == 201, res.get_json()
    assert res.get_json()["limit"] == 5000
    run = run_file(state)
    assert run["epic"] == "T-1" and run["limit"] == 5000
    assert run["project"] == "demo" and run["note"] == "go"
    assert run["stopped_at"] is None and run["paused"] is None
    ids = {r["task_id"]: r["id"] for r in pending(state)}
    assert {tid: e["action"] for tid, e in run["queued"].items()} == ids
    assert {e["usage_offset"] for e in run["queued"].values()} == {0}


def test_a_usage_log_already_written_is_not_the_epics_spend(client, hive, state):
    epic(hive)
    spend(hive, "T-1-S1", 900)  # a cycle before the epic was started
    start_epic(client, body={"project": "demo", "limit": 1000})
    run = actions.read_epic_run(state, "T-1")
    assert run["queued"]["T-1-S1"]["usage_offset"] == 1
    assert epic_runs.spend(str(hive), run) == (0, 0)
    spend(hive, "T-1-S1", 300)
    context_transfer.append_usage(str(hive), "T-1-S2", {"measured": False})
    assert epic_runs.spend(str(hive), run) == (300, 1)


def test_a_merge_queues_the_next_task_once(client, hive, state):
    chain(hive)
    assert [r["task_id"] for r in start_epic(client).get_json()["queued"]] == ["T-1-S1"]
    merge(hive, "T-1-S1")
    assert epic_runs.follow_on(str(hive), str(state)) == ["epic T-1: queued T-1-S2"]
    started = [r for r in pending(state) if r["task_id"] == "T-1-S2"]
    assert len(started) == 1
    assert started[0]["params"] == {"limit": None, "epic": "T-1"}
    assert started[0]["project"] == "demo" and started[0]["note"] == "go"
    # A second merge record (merge-task run again) queues nothing more.
    assert epic_runs.follow_on(str(hive), str(state)) == []
    assert len(pending(state)) == 2
    assert set(run_file(state)["queued"]) == {"T-1-S1", "T-1-S2"}


def test_a_task_the_run_queued_before_is_not_queued_again(client, hive, state):
    """S2's start ran and its phase failed back to Ready: the person starts it."""
    chain(hive)
    start_epic(client)
    merge(hive, "T-1-S1")
    epic_runs.follow_on(str(hive), str(state))
    for path in (state / "actions" / "pending").glob("*.json"):
        path.unlink()  # the worker took and finished both
    assert epic_runs.follow_on(str(hive), str(state)) == []
    assert pending(state) == []


def test_a_task_already_started_by_itself_is_not_queued(client, hive, state):
    chain(hive)
    start_epic(client)
    merge(hive, "T-1-S1")
    one = client.post("/api/tasks/T-1-S2/start", json={"project": "demo"}, headers=AUTH)
    assert one.status_code == 201
    assert epic_runs.follow_on(str(hive), str(state)) == []
    assert [r["task_id"] for r in pending(state)] == ["T-1-S1", "T-1-S2"]


def test_a_stopped_epic_queues_nothing_more(client, hive, state):
    chain(hive)
    start_epic(client)
    assert stop_epic(client).status_code == 201
    assert run_file(state)["stopped_at"] is not None
    merge(hive, "T-1-S1")
    before = pending(state)
    assert epic_runs.follow_on(str(hive), str(state)) == []
    assert pending(state) == before


def test_stop_between_two_tasks_stops_the_run(client, hive, state):
    """Nothing queued (S1 done, S2 not yet Ready) is not a refusal while the run goes."""
    chain(hive)
    start_epic(client)
    for path in (state / "actions" / "pending").glob("*.json"):
        path.unlink()
    res = stop_epic(client)
    assert res.status_code == 201, res.get_json()
    assert res.get_json()["dropped"] == [] and res.get_json()["left"] == []
    assert run_file(state)["stopped_at"] is not None
    again = stop_epic(client)
    assert again.status_code == 409
    assert again.get_json()["error"] == "epic T-1 has no start queued"


def test_the_limit_pauses_the_run_and_resume_takes_a_new_one(client, hive, state):
    chain(hive)
    start_epic(client, body={"project": "demo", "limit": 1000})
    spend(hive, "T-1-S1", 1200)
    merge(hive, "T-1-S1")
    assert epic_runs.follow_on(str(hive), str(state)) == [
        "epic T-1: paused, its limit is reached"
    ]
    assert [r["task_id"] for r in pending(state)] == ["T-1-S1"]
    run = run_file(state)
    assert run["paused"] == actions.PAUSED_LIMIT
    assert epic_runs.view(str(hive), run)["limit_reached"] is True
    # A paused run waits for *Resume*; another merge does not queue.
    assert epic_runs.follow_on(str(hive), str(state)) == []

    low = start_epic(client, body={"project": "demo", "limit": 1200})
    assert low.status_code == 409
    assert low.get_json()["error"] == "the limit is already spent: 1200 measured tokens of 1200"
    assert run_file(state)["limit"] == 1000

    # *Resume*: S1's start is still queued, so it is skipped; S2 is queued.
    res = start_epic(client, body={"project": "demo", "limit": 5000})
    assert res.status_code == 201, res.get_json()
    assert [r["task_id"] for r in res.get_json()["queued"]] == ["T-1-S2"]
    run = run_file(state)
    assert run["limit"] == 5000 and run["paused"] is None
    assert run["queued"]["T-1-S1"]["usage_offset"] == 0
    assert epic_runs.view(str(hive), run)["spent"] == {"tokens": 1200, "unmeasured": 0}


def test_resume_with_nothing_in_ready_is_not_refused(client, hive, state):
    chain(hive)
    start_epic(client, body={"project": "demo", "limit": 10})
    spend(hive, "T-1-S1", 10)
    epic_runs.follow_on(str(hive), str(state))
    assert run_file(state)["paused"] == actions.PAUSED_LIMIT
    res = start_epic(client, body={"project": "demo", "limit": 100})
    assert res.status_code == 201, res.get_json()
    assert res.get_json()["queued"] == []
    assert run_file(state)["paused"] is None


def test_a_start_after_stop_is_a_new_run(client, hive, state):
    chain(hive)
    start_epic(client, body={"project": "demo", "limit": 100})
    spend(hive, "T-1-S1", 500)
    stop_epic(client)
    for path in (state / "actions" / "pending").glob("*.json"):
        path.unlink()  # the worker dropped the start
    res = start_epic(client, body={"project": "demo", "limit": 100})
    assert res.status_code == 201, res.get_json()
    run = run_file(state)
    assert run["stopped_at"] is None
    assert run["queued"]["T-1-S1"]["usage_offset"] == 1
    assert epic_runs.view(str(hive), run)["spent"]["tokens"] == 0


def test_the_view_shows_a_limit_reached_before_any_merge_looked(client, hive, state):
    epic(hive)
    start_epic(client, body={"project": "demo", "limit": 100})
    spend(hive, "T-1-S2", 60)
    spend(hive, "T-1-S3", 40)
    view = epic_runs.view(str(hive), run_file(state))
    assert view["paused"] is None and view["limit_reached"] is True
    assert view["spent"] == {"tokens": 100, "unmeasured": 0}


def test_resume_at_the_limit_before_any_merge_looked(client, hive, state):
    """The pause is lazy: a run at its limit resumes with nothing in Ready all the same."""
    chain(hive)
    start_epic(client, body={"project": "demo", "limit": 10})
    spend(hive, "T-1-S1", 10)
    assert run_file(state)["paused"] is None
    res = start_epic(client, body={"project": "demo", "limit": 100})
    assert res.status_code == 201, res.get_json()
    assert res.get_json()["queued"] == [] and run_file(state)["limit"] == 100
