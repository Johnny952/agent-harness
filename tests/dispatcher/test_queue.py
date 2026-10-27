"""The queue (docs/plans/autonomy.md A-5, docs/decisions.md ADR 79), over a stubbed cycle.

Nothing here starts a container: `run_task_cycle`, the account pick and the
cooling re-probe are faked, and the fake cycle writes usage lines the way a
real phase would, so the limit is read from the same log it is in production.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from dispatcher import cli as cli_mod
from dispatcher import context_transfer, docker_exec, tasks
from dispatcher import handoff as handoff_mod
from dispatcher import dispatcher as core
from dispatcher import queue as queue_mod
from dispatcher.config import AccountConfig
from tests.dispatcher.test_dispatcher import _make_config

_DESCRIPTION = "## Goal\n\nDo {id}.\n\n## Done when\n\nIt is done.\n"


def _task(cfg, task_id, depends_on=None):
    tasks.handoff(cfg.hive_tasks_dir, task_id, "pending", "", depends_on=depends_on or [])
    tasks.write_description(cfg.hive_tasks_dir, task_id, _DESCRIPTION.format(id=task_id))


def _usage(cfg, task_id, tokens):
    context_transfer.append_usage(cfg.hive_tasks_dir, task_id, {
        "task_id": task_id, "measured": True,
        "input_tokens": tokens, "output_tokens": 0,
        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
    })


class FakeCycle:
    """A cycle per task: a list of phase spends, then an outcome.

    Each phase asks `may_start_phase` first, as `run_phase` does, and writes
    one usage line. `merge` marks the task merged, `refuse_push` hands the
    hook an approve whose push was refused.
    """

    def __init__(self, cfg, script):
        self.cfg = cfg
        self.script = script
        self.started: list[str] = []
        self.calls: list[dict] = []

    def __call__(self, cfg, task_id, slug, description=None, authorized=True,
                 on_approve=None, may_start_phase=None, project_lock=None,
                 before_merge=None, scheduler=None, start_at="arquitecto",
                 start_round=None, on_held=None):
        self.started.append(task_id)
        self.calls.append({
            "authorized": authorized, "slug": slug,
            "start_at": start_at, "start_round": start_round,
        })
        plan = self.script[task_id]
        for spend in plan.get("phases", [100]):
            if may_start_phase is not None and not may_start_phase():
                if on_held is not None and plan.get("held_at"):
                    on_held(*plan["held_at"])
                return core.CycleOutcome.HELD
            _usage(cfg, task_id, spend)
        outcome = plan.get("outcome", core.CycleOutcome.FINISHED)
        if plan.get("split") is not None:
            # `split`: the `after` tuples of the sub-tasks, as file_split gets them.
            tasks.file_split(cfg.hive_tasks_dir, task_id, [
                handoff_mod.SubTask(f"Part {n}", "src/", _DESCRIPTION.format(id=f"part {n}"), after)
                for n, after in enumerate(plan["split"], 1)
            ])
            outcome = core.CycleOutcome.SPLIT
        if outcome is core.CycleOutcome.FINISHED and plan.get("merge", True):
            tasks.mark_merged(cfg.hive_tasks_dir, task_id, "main")
            if on_approve is not None:
                status = (
                    docker_exec.PUSH_REFUSED if plan.get("refuse_push") else docker_exec.PUSHED
                )
                on_approve(core.ApproveResult(
                    merged=True, push=docker_exec.PushOutcome(status, "main", "rejected"),
                ))
        return outcome


@pytest.fixture
def cfg(tmp_path):
    return _make_config(tmp_path)


@pytest.fixture
def accounts(monkeypatch):
    """An account is open unless a test closes it."""
    state = {"open": True, "probes": 0}

    def pick(cfg, exclude=None, role=None):
        return "cuenta1" if state["open"] else None

    def recheck(cfg):
        state["probes"] += 1
        return []

    monkeypatch.setattr(core, "pick_idle_account", pick)
    monkeypatch.setattr(core, "_recheck_cooling_accounts", recheck)
    return state


def _install(monkeypatch, cfg, script):
    fake = FakeCycle(cfg, script)
    monkeypatch.setattr(core, "run_task_cycle", fake)
    return fake


def _no_sleep(seconds):
    raise AssertionError("the queue waited with an account open")


def test_order_follows_depends_on(cfg, accounts, monkeypatch):
    _task(cfg, "T-3", depends_on=["T-1"])
    _task(cfg, "T-1")
    _task(cfg, "T-2", depends_on=["T-3"])
    fake = _install(monkeypatch, cfg, {t: {} for t in ("T-1", "T-2", "T-3")})

    queue = queue_mod.plan(cfg, "proj", ["T-2", "T-3", "T-1"], limit=10_000)
    assert [e.task_id for e in queue.entries] == ["T-1", "T-3", "T-2"]
    queue = queue_mod.run(cfg, queue, sleep=_no_sleep)

    assert fake.started == ["T-1", "T-3", "T-2"]
    assert queue.status == queue_mod.Q_DONE
    assert [e.state for e in queue.entries] == [queue_mod.MERGED] * 3
    assert queue.spent == 300
    # Authorization is passed explicitly, from the owner's yes.
    assert all(c["authorized"] is True for c in fake.calls)


def test_cycle_in_dependencies_is_refused(cfg):
    _task(cfg, "T-1", depends_on=["T-2"])
    _task(cfg, "T-2", depends_on=["T-1"])
    with pytest.raises(queue_mod.QueueError, match="cycle"):
        queue_mod.plan(cfg, "proj", ["T-1", "T-2"], limit=10)


def test_task_without_description_is_refused(cfg):
    tasks.handoff(cfg.hive_tasks_dir, "T-1", "pending", "")
    with pytest.raises(queue_mod.QueueError, match="no description"):
        queue_mod.plan(cfg, "proj", ["T-1"], limit=10)


@pytest.mark.parametrize("status", tasks.BACKLOG_STATUSES)
def test_a_backlog_task_is_refused(cfg, status):
    """ADR 127: refused before the confirmation screen, as `open_cycle` would."""
    _task(cfg, "T-1")
    _task(cfg, "T-2")
    tasks.handoff(cfg.hive_tasks_dir, "T-2", status, "")
    with pytest.raises(queue_mod.QueueError, match=rf"task T-2 cannot run: it is in the backlog \(status {status}\)"):
        queue_mod.plan(cfg, "proj", ["T-1", "T-2"], limit=10)


def test_a_task_with_a_bad_source_is_refused(cfg):
    _task(cfg, "T-1")
    path = Path(context_transfer.task_file_path(cfg.hive_tasks_dir, "T-1"))
    path.write_text(path.read_text().replace("status: pending\n", "status: pending\nsource: me\n"))
    with pytest.raises(queue_mod.QueueError, match="its source is 'me'"):
        queue_mod.plan(cfg, "proj", ["T-1"], limit=10)


# --- The Ready order (ADR 130, ADR 132) ---------------------------------------


def _place(cfg, task_id, priority=None, rank=None):
    """Set a task's bucket and rank on its file, as `task_edit` leaves them."""
    path = context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)
    task = context_transfer.read_task_file(path)
    task.priority = priority
    task.rank = rank
    context_transfer.write_task_file(path, task)


def _ids(queue):
    return [e.task_id for e in queue.entries]


def test_ready_picks_by_bucket_then_rank_then_id(cfg):
    for task_id in ("T-1", "T-2", "T-3", "T-4", "T-10"):
        _task(cfg, task_id)
    _place(cfg, "T-1", rank=2048)
    _place(cfg, "T-2", priority="high", rank=4096)
    _place(cfg, "T-3", priority="low")
    _place(cfg, "T-4", rank=1024)
    # Unranked sorts as 0, ahead of the ranked, and T-10 after T-4 by number.
    _place(cfg, "T-10", rank=1024)

    queue = queue_mod.plan(cfg, "proj", [], limit=10, ready=4)

    assert _ids(queue) == ["T-2", "T-4", "T-10", "T-1"]
    assert all(e.from_ready for e in queue.entries)
    assert queue.passed_over == []


def test_ready_lifts_a_dependency_to_the_bucket_of_what_waits_on_it(cfg):
    _task(cfg, "T-1")
    _task(cfg, "T-2")
    _task(cfg, "T-3", depends_on=["T-2"])
    _place(cfg, "T-2", priority="low")
    _place(cfg, "T-3", priority="high")

    queue = queue_mod.plan(cfg, "proj", [], limit=10, ready=3)

    # T-3 is not ready, its dependency is not merged; it lifts T-2 to high.
    assert _ids(queue) == ["T-2", "T-1"]


def test_ready_takes_only_what_can_start(cfg):
    _task(cfg, "T-1")
    tasks.mark_merged(cfg.hive_tasks_dir, "T-1", "main")
    _task(cfg, "T-2", depends_on=["T-1"])
    _task(cfg, "T-3", depends_on=["T-9"])
    _task(cfg, "T-4")
    tasks.handoff(cfg.hive_tasks_dir, "T-4", "proposed", "")
    _place(cfg, "T-4", priority="high")
    _task(cfg, "T-5")
    tasks.handoff(cfg.hive_tasks_dir, "T-5", "done", "")

    queue = queue_mod.plan(cfg, "proj", [], limit=10, ready=5)

    # Merged, waiting on an unknown task, in the backlog, or not pending: none taken.
    assert _ids(queue) == ["T-2"]


def test_listed_tasks_keep_their_order_and_ready_ones_follow(cfg):
    for task_id in ("T-1", "T-2", "T-3", "T-4"):
        _task(cfg, task_id)
    _place(cfg, "T-1", priority="high")
    _place(cfg, "T-4", priority="low")

    queue = queue_mod.plan(cfg, "proj", ["T-4", "T-2"], limit=10, ready=5)

    # The owner's list first, as given, then the rest of Ready, without repeats.
    assert _ids(queue) == ["T-4", "T-2", "T-1", "T-3"]
    assert [e.from_ready for e in queue.entries] == [False, False, True, True]


def test_a_listed_task_that_depends_on_a_ready_one_still_waits_for_it(cfg):
    _task(cfg, "T-1")
    _task(cfg, "T-2", depends_on=["T-1"])

    queue = queue_mod.plan(cfg, "proj", ["T-2"], limit=10, ready=1)

    assert _ids(queue) == ["T-1", "T-2"]


def test_ready_passes_over_a_task_plan_would_refuse(cfg):
    tasks.handoff(cfg.hive_tasks_dir, "T-1", "pending", "")
    _task(cfg, "T-2")
    _task(cfg, "T-3")

    queue = queue_mod.plan(cfg, "proj", [], limit=10, ready=1)

    assert _ids(queue) == ["T-2"]
    assert queue.passed_over == ["task T-1 has no description"]
    shown = queue_mod.confirmation(cfg, queue)
    assert "T-2  Do T-2.  [from Ready]" in shown
    assert "Passed over from the Ready order:\n  task T-1 has no description" in shown


def test_a_listed_task_is_still_refused(cfg):
    tasks.handoff(cfg.hive_tasks_dir, "T-1", "pending", "")
    _task(cfg, "T-2")
    with pytest.raises(queue_mod.QueueError, match="no description"):
        queue_mod.plan(cfg, "proj", ["T-1"], limit=10, ready=1)


def test_nothing_ready_and_nothing_listed_is_refused(cfg):
    _task(cfg, "T-1", depends_on=["T-2"])
    _task(cfg, "T-2", depends_on=["T-1"])
    with pytest.raises(queue_mod.QueueError, match="no task is ready"):
        queue_mod.plan(cfg, "proj", [], limit=10, ready=2)
    with pytest.raises(queue_mod.QueueError, match="negative"):
        queue_mod.plan(cfg, "proj", ["T-1"], limit=10, ready=-1)


def test_ready_runs_in_the_order_it_picked(cfg, accounts, monkeypatch):
    for task_id in ("T-1", "T-2", "T-3"):
        _task(cfg, task_id)
    _place(cfg, "T-3", priority="high")
    fake = _install(monkeypatch, cfg, {t: {} for t in ("T-1", "T-2", "T-3")})

    queue = queue_mod.run(cfg, queue_mod.plan(cfg, "proj", [], 10_000, ready=3), sleep=_no_sleep)

    assert fake.started == ["T-3", "T-1", "T-2"]
    assert queue.status == queue_mod.Q_DONE


def test_the_pick_is_saved_and_a_file_from_before_it_loads(cfg):
    tasks.handoff(cfg.hive_tasks_dir, "T-1", "pending", "")
    _task(cfg, "T-2")
    queue = queue_mod.plan(cfg, "proj", [], limit=10, ready=1)
    path = queue_mod.save(cfg, queue)

    loaded = queue_mod.load(cfg, queue.queue_id)
    assert loaded.entries[0].from_ready is True
    assert loaded.passed_over == queue.passed_over

    data = json.loads(Path(path).read_text())
    del data["passed_over"]
    del data["entries"][0]["from_ready"]
    Path(path).write_text(json.dumps(data))
    old = queue_mod.load(cfg, queue.queue_id)
    assert old.entries[0].from_ready is False
    assert old.passed_over == []


def test_failed_dependency_skips_dependents_and_the_rest_go_on(cfg, accounts, monkeypatch):
    _task(cfg, "T-1")
    _task(cfg, "T-2", depends_on=["T-1"])
    _task(cfg, "T-3", depends_on=["T-2"])
    _task(cfg, "T-4")
    fake = _install(monkeypatch, cfg, {
        "T-1": {"outcome": core.CycleOutcome.BLOCKED},
        "T-2": {}, "T-3": {}, "T-4": {},
    })

    queue = queue_mod.run(cfg, queue_mod.plan(cfg, "proj", ["T-1", "T-2", "T-3", "T-4"], 10_000),
                          sleep=_no_sleep)

    assert fake.started == ["T-1", "T-4"]  # T-1 is not retried either
    states = {e.task_id: e.state for e in queue.entries}
    assert states == {
        "T-1": queue_mod.BLOCKED, "T-2": queue_mod.SKIPPED,
        "T-3": queue_mod.SKIPPED, "T-4": queue_mod.MERGED,
    }
    body = context_transfer.read_task_file(
        context_transfer.task_file_path(cfg.hive_tasks_dir, "T-2")
    ).body
    assert "depends on T-1" in body
    assert context_transfer.read_task_file(
        context_transfer.task_file_path(cfg.hive_tasks_dir, "T-2")
    ).status == "pending"


def test_unmerged_dependency_waits_and_the_queue_moves_on(cfg, accounts, monkeypatch):
    _task(cfg, "T-1")
    _task(cfg, "T-2", depends_on=["T-1"])
    _task(cfg, "T-3")
    fake = _install(monkeypatch, cfg, {"T-1": {"merge": False}, "T-2": {}, "T-3": {}})

    planned = queue_mod.plan(cfg, "proj", ["T-1", "T-2", "T-3"], 10_000)
    planned.max_wait_hours = 0
    queue = queue_mod.run(cfg, planned, sleep=_no_sleep)

    assert fake.started == ["T-1", "T-3"]
    assert queue.status == queue_mod.Q_WAIT_EXPIRED
    assert queue.entry("T-2").state == queue_mod.PENDING


# --- Waiting for the person's merge (ADR 498) ---------------------------------


class Clock:
    """A replaceable `queue._now`, moved by the fake sleep."""

    def __init__(self):
        import datetime as dt
        self.now = dt.datetime(2026, 10, 27, 12, 0, tzinfo=dt.timezone.utc)
        self.dt = dt

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += self.dt.timedelta(seconds=seconds)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(queue_mod, "_now", c)
    return c


def _sleeper(clock, cfg, at=None, do=None):
    """A sleep that advances the clock; on its `at`-th call it runs `do`."""
    calls = []

    def sleep(seconds):
        calls.append(seconds)
        clock.advance(seconds)
        if at is not None and len(calls) == at:
            do()

    sleep.calls = calls
    return sleep


def _waiting_queue(cfg, monkeypatch, **script):
    _task(cfg, "T-1")
    _task(cfg, "T-2", depends_on=["T-1"])
    fake = _install(monkeypatch, cfg, {"T-1": {"merge": False}, "T-2": {}, **script})
    return fake, queue_mod.plan(cfg, "proj", ["T-1", "T-2"], 10_000)


def _set_status(cfg, task_id, status):
    path = context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)
    task = context_transfer.read_task_file(path)
    task.status = status
    context_transfer.write_task_file(path, task)


def test_the_queue_waits_for_a_merge_then_starts_the_dependent(cfg, accounts, clock, monkeypatch):
    fake, planned = _waiting_queue(cfg, monkeypatch)
    seen = {}

    def merge():
        seen["file"] = json.loads(Path(queue_mod.queue_path(cfg, planned.queue_id)).read_text())
        seen["probes"] = accounts["probes"]
        tasks.mark_merged(cfg.hive_tasks_dir, "T-1", "main")

    sleep = _sleeper(clock, cfg, at=3, do=merge)
    queue = queue_mod.run(cfg, planned, sleep=sleep)

    assert sleep.calls == [queue_mod.MERGE_POLL_SECONDS] * 3
    assert fake.started == ["T-1", "T-2"]  # nothing started during the wait
    assert queue.status == queue_mod.Q_DONE and queue.waiting is None
    assert seen["file"]["status"] == "waiting"
    assert seen["file"]["waiting"]["on"] == ["T-1"]
    assert seen["file"]["max_wait_hours"] == 72
    assert accounts["probes"] == 0  # no /usage probe at all


def test_the_wait_releases_the_project_lock(cfg, accounts, clock, monkeypatch):
    from dispatcher.project_lock import ProjectBusy, ProjectLock

    fake, planned = _waiting_queue(cfg, monkeypatch)
    probe = {}

    def merge():
        try:
            held = ProjectLock.take(cfg.state_dir, "proj", None, "the merge's backup")
        except ProjectBusy:
            probe["free"] = False
        else:
            probe["free"] = True
            held.release()
        tasks.mark_merged(cfg.hive_tasks_dir, "T-1", "main")

    queue_mod.run(cfg, planned, sleep=_sleeper(clock, cfg, at=1, do=merge))
    assert probe["free"] is True


@pytest.mark.parametrize("status,state", [("dropped", queue_mod.DROPPED), ("blocked", queue_mod.BLOCKED)])
def test_a_dependency_that_ends_badly_while_waiting_skips_its_dependents(
    cfg, accounts, clock, monkeypatch, status, state
):
    fake, planned = _waiting_queue(cfg, monkeypatch)
    sleep = _sleeper(clock, cfg, at=1, do=lambda: _set_status(cfg, "T-1", status))

    queue = queue_mod.run(cfg, planned, sleep=sleep)

    assert fake.started == ["T-1"]
    assert queue.entry("T-1").state == state
    assert queue.entry("T-2").state == queue_mod.SKIPPED
    assert queue.status == queue_mod.Q_DONE


def test_a_task_file_gone_while_waiting_counts_as_dropped(cfg, accounts, clock, monkeypatch):
    fake, planned = _waiting_queue(cfg, monkeypatch)
    path = context_transfer.task_file_path(cfg.hive_tasks_dir, "T-1")
    queue = queue_mod.run(cfg, planned, sleep=_sleeper(clock, cfg, at=1, do=lambda: os.unlink(path)))
    assert queue.entry("T-1").state == queue_mod.DROPPED


def test_the_wait_expires_after_the_maximum(cfg, accounts, clock, monkeypatch):
    fake, planned = _waiting_queue(cfg, monkeypatch)
    planned.max_wait_hours = 1
    sleep = _sleeper(clock, cfg)

    queue = queue_mod.run(cfg, planned, sleep=sleep)

    assert queue.status == queue_mod.Q_WAIT_EXPIRED
    assert len(sleep.calls) == 60
    assert queue.waiting["on"] == ["T-1"]
    saved = queue_mod.load(cfg, queue.queue_id)
    assert saved.status == queue_mod.Q_WAIT_EXPIRED and saved.waiting == queue.waiting
    assert fake.started == ["T-1"]


def test_ctrl_c_cancels_the_wait(cfg, accounts, clock, monkeypatch):
    fake, planned = _waiting_queue(cfg, monkeypatch)

    def interrupt(seconds):
        raise KeyboardInterrupt

    queue = queue_mod.run(cfg, planned, sleep=interrupt)

    assert queue.status == queue_mod.Q_CANCELLED
    assert queue_mod.load(cfg, queue.queue_id).status == queue_mod.Q_CANCELLED
    assert not any(e.state == queue_mod.RUNNING for e in queue.entries)


def test_resume_of_a_waiting_queue_keeps_its_clock_and_waits(cfg, accounts, clock, monkeypatch):
    fake, planned = _waiting_queue(cfg, monkeypatch)
    queue_mod.run(cfg, planned, sleep=_sleeper(clock, cfg, at=2, do=lambda: (_ for _ in ()).throw(KeyboardInterrupt)))
    # A kill leaves the file `waiting` (SIGTERM): simulate by saving that status.
    killed = queue_mod.load(cfg, planned.queue_id)
    killed.status = queue_mod.Q_WAITING
    queue_mod.save(cfg, killed)
    since = killed.waiting["since"]

    resumed = queue_mod.reopen(cfg, planned.queue_id)
    assert resumed.waiting["since"] == since
    assert "Waiting since" in queue_mod.confirmation(cfg, resumed)
    assert "Merge wait: up to 72 hours" in queue_mod.confirmation(cfg, resumed)

    sleep = _sleeper(clock, cfg, at=1, do=lambda: tasks.mark_merged(cfg.hive_tasks_dir, "T-1", "main"))
    queue = queue_mod.run(cfg, resumed, sleep=sleep)
    assert fake.started == ["T-1", "T-2"] and queue.status == queue_mod.Q_DONE


def test_resume_of_an_expired_wait_starts_a_fresh_one(cfg, accounts, clock, monkeypatch):
    fake, planned = _waiting_queue(cfg, monkeypatch)
    planned.max_wait_hours = 0
    queue_mod.run(cfg, planned, sleep=_no_sleep)
    assert queue_mod.load(cfg, planned.queue_id).status == queue_mod.Q_WAIT_EXPIRED

    resumed = queue_mod.reopen(cfg, planned.queue_id)
    assert resumed.waiting is None and resumed.status == queue_mod.Q_RUNNING


def test_the_confirmation_says_the_queue_waits_for_merges(cfg, monkeypatch):
    _task(cfg, "T-1")
    text = queue_mod.confirmation(cfg, queue_mod.plan(cfg, "proj", ["T-1"], 10_000))
    assert "the queue waits for those merges" in text
    assert "Merge wait: up to 72 hours" in text


def test_a_dependency_outside_the_queue_stalls_instead_of_waiting(cfg, accounts, clock, monkeypatch):
    _task(cfg, "T-0")
    _task(cfg, "T-2", depends_on=["T-0"])
    _install(monkeypatch, cfg, {"T-2": {}})
    queue = queue_mod.run(cfg, queue_mod.plan(cfg, "proj", ["T-2"], 10_000), sleep=_no_sleep)
    assert queue.status == queue_mod.Q_STALLED


def test_a_parallel_queue_waits_too(cfg, accounts, clock, monkeypatch):
    fake, planned = _waiting_queue(cfg, monkeypatch)
    planned.parallel = 2
    sleep = _sleeper(clock, cfg, at=1, do=lambda: tasks.mark_merged(cfg.hive_tasks_dir, "T-1", "main"))
    queue = queue_mod.run(cfg, planned, sleep=sleep)
    assert fake.started == ["T-1", "T-2"] and queue.status == queue_mod.Q_DONE


def test_limit_reached_between_tasks(cfg, accounts, monkeypatch):
    for t in ("T-1", "T-2", "T-3"):
        _task(cfg, t)
    fake = _install(monkeypatch, cfg, {t: {"phases": [300, 300]} for t in ("T-1", "T-2", "T-3")})

    queue = queue_mod.run(cfg, queue_mod.plan(cfg, "proj", ["T-1", "T-2", "T-3"], 1_200),
                          sleep=_no_sleep)

    assert fake.started == ["T-1", "T-2"]
    assert queue.status == queue_mod.Q_LIMIT
    assert queue.spent == 1_200
    assert queue.entry("T-3").state == queue_mod.PENDING


def test_limit_reached_inside_a_cycle_starts_no_new_phase(cfg, accounts, monkeypatch):
    _task(cfg, "T-1")
    _task(cfg, "T-2")
    fake = _install(monkeypatch, cfg, {"T-1": {"phases": [400, 400, 400]}, "T-2": {}})

    queue = queue_mod.run(cfg, queue_mod.plan(cfg, "proj", ["T-1", "T-2"], 700), sleep=_no_sleep)

    # The second phase started under the limit and was let finish; the third
    # never started, and neither did T-2.
    assert fake.started == ["T-1"]
    assert queue.spent == 800
    assert queue.status == queue_mod.Q_LIMIT
    assert queue.entry("T-1").state == queue_mod.STOPPED


def _held_queue(cfg, monkeypatch, parallel, held_at, limit=200):
    _task(cfg, "T-1")
    fake = _install(monkeypatch, cfg, {"T-1": {"phases": [100, 100, 100], "held_at": held_at}})
    queue = queue_mod.plan(cfg, "proj", ["T-1"], limit)
    queue.parallel = parallel
    queue = queue_mod.run(cfg, queue, sleep=lambda s: None)
    return fake, queue


@pytest.mark.parametrize("parallel", [1, 2])
def test_a_resumed_limit_reached_task_starts_where_the_limit_held_it(
    cfg, accounts, monkeypatch, parallel,
):
    fake, queue = _held_queue(cfg, monkeypatch, parallel, ("implementador", 2))

    assert queue.entry("T-1").state == queue_mod.STOPPED
    stored = queue_mod.load(cfg, queue.queue_id).entry("T-1")
    assert stored.held_at == {"role": "implementador", "round": 2}

    queue_mod.resume(cfg, queue.queue_id, limit=10_000)
    reopened = queue_mod.load(cfg, queue.queue_id).entry("T-1")
    assert reopened.state == queue_mod.PENDING
    assert reopened.held_at == {"role": "implementador", "round": 2}

    queue = queue_mod.run(cfg, queue_mod.reopen(cfg, queue.queue_id), sleep=lambda s: None)

    assert fake.calls[0]["start_at"] == "arquitecto"
    assert fake.calls[1]["start_at"] == "implementador"
    assert fake.calls[1]["start_round"] == 2
    assert queue.entry("T-1").state == queue_mod.MERGED
    assert queue.entry("T-1").held_at is None


@pytest.mark.parametrize("role", ["arquitecto", core.project_docs.MAPPER_ROLE])
def test_a_task_held_at_the_arquitecto_or_mapper_restarts_with_the_defaults(
    cfg, accounts, monkeypatch, role,
):
    fake, queue = _held_queue(cfg, monkeypatch, 1, (role, 1))
    assert queue.entry("T-1").held_at == {"role": role, "round": 1}

    queue_mod.resume(cfg, queue.queue_id, limit=10_000)
    queue_mod.run(cfg, queue_mod.reopen(cfg, queue.queue_id), sleep=lambda s: None)

    assert fake.calls[1]["start_at"] == "arquitecto"
    assert fake.calls[1]["start_round"] is None


def test_a_limit_reached_end_without_a_held_report_stores_none(cfg, accounts, monkeypatch):
    _, queue = _held_queue(cfg, monkeypatch, 1, None)
    assert queue.entry("T-1").state == queue_mod.STOPPED
    assert queue.entry("T-1").held_at is None


def test_a_queue_file_from_before_held_at_loads_with_none(cfg):
    _task(cfg, "T-1")
    queue = queue_mod.plan(cfg, "proj", ["T-1"], 1_000)
    queue_mod.save(cfg, queue)
    path = queue_mod.queue_path(cfg, queue.queue_id)
    data = json.loads(Path(path).read_text())
    for entry in data["entries"]:
        entry.pop("held_at", None)
    Path(path).write_text(json.dumps(data))

    assert queue_mod.load(cfg, queue.queue_id).entry("T-1").held_at is None


@pytest.mark.parametrize("plan_", [
    {"outcome": core.CycleOutcome.FINISHED},
    {"outcome": core.CycleOutcome.BLOCKED},
    {"outcome": core.CycleOutcome.SPLIT},
    {"outcome": core.CycleOutcome.HELD},
])
def test_any_other_end_clears_held_at(cfg, accounts, monkeypatch, plan_):
    _task(cfg, "T-1")
    _install(monkeypatch, cfg, {"T-1": {"phases": [100], **plan_}})
    queue = queue_mod.plan(cfg, "proj", ["T-1"], 10_000)
    queue.entry("T-1").held_at = {"role": "implementador", "round": 2}

    queue = queue_mod.run(cfg, queue, sleep=lambda s: None)

    assert queue.entry("T-1").state != queue_mod.STOPPED
    assert queue.entry("T-1").held_at is None


def test_waits_for_quota_for_free(cfg, accounts, monkeypatch):
    _task(cfg, "T-1")
    fake = _install(monkeypatch, cfg, {"T-1": {}})
    accounts["open"] = False
    sleeps: list[float] = []

    def sleep(seconds):
        sleeps.append(seconds)
        assert fake.started == []  # nothing ran, nothing was spent
        if len(sleeps) == 3:
            accounts["open"] = True

    queue = queue_mod.run(cfg, queue_mod.plan(cfg, "proj", ["T-1"], 10_000), sleep=sleep)

    assert sleeps == [queue_mod.WAIT_POLL_SECONDS] * 3
    assert accounts["probes"] == 3  # the parked accounts were re-probed each time
    assert fake.started == ["T-1"]
    assert queue.status == queue_mod.Q_DONE


def test_held_before_spending_goes_back_to_wait(cfg, accounts, monkeypatch):
    _task(cfg, "T-1")
    fake = _install(monkeypatch, cfg, {"T-1": {}})
    held_once = {"done": False}
    real = fake.__call__

    def cycle(*args, **kwargs):
        if not held_once["done"]:
            held_once["done"] = True
            fake.started.append("T-1(held)")
            return core.CycleOutcome.HELD
        return real(*args, **kwargs)

    monkeypatch.setattr(core, "run_task_cycle", cycle)
    sleeps: list[float] = []
    queue = queue_mod.run(cfg, queue_mod.plan(cfg, "proj", ["T-1"], 10_000), sleep=sleeps.append)

    assert fake.started == ["T-1(held)", "T-1"]
    assert queue.entry("T-1").state == queue_mod.MERGED
    # The retry waits one poll, so a hold that repeats does not spin.
    assert sleeps == [queue_mod.WAIT_POLL_SECONDS]


def test_held_after_spending_is_not_retried(cfg, accounts, monkeypatch):
    _task(cfg, "T-1")
    _task(cfg, "T-2", depends_on=["T-1"])
    fake = _install(monkeypatch, cfg, {
        "T-1": {"outcome": core.CycleOutcome.HELD}, "T-2": {},
    })

    queue = queue_mod.run(cfg, queue_mod.plan(cfg, "proj", ["T-1", "T-2"], 10_000),
                          sleep=_no_sleep)

    assert fake.started == ["T-1"]
    assert queue.entry("T-1").state == queue_mod.HELD
    assert queue.entry("T-2").state == queue_mod.SKIPPED


def test_refused_push_stops_the_queue(cfg, accounts, monkeypatch):
    for t in ("T-1", "T-2", "T-3"):
        _task(cfg, t)
    fake = _install(monkeypatch, cfg, {"T-1": {}, "T-2": {"refuse_push": True}, "T-3": {}})

    queue = queue_mod.run(cfg, queue_mod.plan(cfg, "proj", ["T-1", "T-2", "T-3"], 10_000),
                          sleep=_no_sleep)

    assert fake.started == ["T-1", "T-2"]
    assert queue.status == queue_mod.Q_PUSH_REFUSED
    assert queue.entry("T-2").state == queue_mod.MERGED
    assert "push of the base was refused" in queue.entry("T-2").note
    # A resume runs nothing more: every later task would build on that base.
    again = queue_mod.run(cfg, queue_mod.resume(cfg, queue.queue_id), sleep=_no_sleep)
    assert again.status == queue_mod.Q_PUSH_REFUSED
    assert fake.started == ["T-1", "T-2"]


class _Killed(BaseException):
    """What a kill looks like from inside the cycle: nothing after it runs."""


def test_resume_after_a_kill_starts_no_task_twice(cfg, accounts, monkeypatch):
    for t in ("T-1", "T-2", "T-3"):
        _task(cfg, t)
    _task(cfg, "T-4", depends_on=["T-2"])
    fake = _install(monkeypatch, cfg, {t: {} for t in ("T-1", "T-2", "T-3", "T-4")})
    real = fake.__call__

    def dies_on_t2(cfg_, task_id, *args, **kwargs):
        if task_id == "T-2":
            fake.started.append(task_id)
            _usage(cfg_, task_id, 50)  # one phase spent before the kill
            raise _Killed()
        return real(cfg_, task_id, *args, **kwargs)

    monkeypatch.setattr(core, "run_task_cycle", dies_on_t2)
    queue = queue_mod.plan(cfg, "proj", ["T-1", "T-2", "T-3", "T-4"], 10_000)
    queue_mod.save(cfg, queue)
    with pytest.raises(_Killed):
        queue_mod.run(cfg, queue, sleep=_no_sleep)

    on_disk = json.loads(open(queue_mod.queue_path(cfg, queue.queue_id)).read())
    assert [e["state"] for e in on_disk["entries"]] == [
        queue_mod.MERGED, queue_mod.RUNNING, queue_mod.PENDING, queue_mod.PENDING,
    ]

    monkeypatch.setattr(core, "run_task_cycle", fake)
    resumed = queue_mod.run(cfg, queue_mod.resume(cfg, queue.queue_id), sleep=_no_sleep)

    assert fake.started == ["T-1", "T-2", "T-3"]  # T-2 once, T-1 once
    states = {e.task_id: e.state for e in resumed.entries}
    assert states == {
        "T-1": queue_mod.MERGED, "T-2": queue_mod.INTERRUPTED,
        "T-3": queue_mod.MERGED, "T-4": queue_mod.SKIPPED,
    }
    assert resumed.entry("T-2").tokens == 50  # what it spent still counts
    assert resumed.spent == 250


def test_unmeasured_lines_count_nothing_and_are_reported(cfg):
    assert queue_mod.measured_tokens({"input_tokens": 3, "output_tokens": 4,
                                      "cache_read_input_tokens": 5,
                                      "cache_creation_input_tokens": None}) == 12
    assert queue_mod.measured_tokens({"measured": False, "input_tokens": 9}) is None
    assert queue_mod.measured_tokens({"total_cost_usd": 1.0}) is None


def test_resume_refuses_a_bad_id(cfg):
    with pytest.raises(queue_mod.QueueError):
        queue_mod.resume(cfg, "../../etc/passwd")
    with pytest.raises(queue_mod.QueueError, match="no queue"):
        queue_mod.resume(cfg, "q-20261017T000000Z-abcd")


def _cli(monkeypatch, tmp_path, *argv, stdin="", config_of=None):
    config = tmp_path / "config.yaml"
    monkeypatch.setattr(
        cli_mod, "load_config", lambda path: (config_of or _make_config)(tmp_path),
    )
    config.write_text("")
    monkeypatch.setattr(sys, "argv", ["dispatch", "--config", str(config), *argv])
    monkeypatch.setattr("builtins.input", lambda prompt="": stdin)
    with pytest.raises(SystemExit) as exc:
        cli_mod.main()
        raise SystemExit(0)
    return exc.value.code


def test_cli_confirmation_and_no_start_without_yes(tmp_path, accounts, monkeypatch, capsys):
    cfg = _make_config(tmp_path)
    _task(cfg, "T-1")
    fake = _install(monkeypatch, cfg, {"T-1": {}})

    code = _cli(monkeypatch, tmp_path, "run-queue", "--project", "proj",
                "--task-id", "T-1", "--limit", "500000", stdin="n")

    out = capsys.readouterr().out
    assert code == cli_mod.EXIT_BLOCKED
    assert fake.started == []
    for shown in ("T-1", "Do T-1.", "arquitecto", "auditor", "Parallelism: 1", "Limit: 500000",
                  "can pass it by that one phase"):
        assert shown in out


def test_cli_yes_runs_the_queue(tmp_path, accounts, monkeypatch, capsys):
    cfg = _make_config(tmp_path)
    _task(cfg, "T-1")
    _task(cfg, "T-2")
    fake = _install(monkeypatch, cfg, {"T-1": {}, "T-2": {}})

    code = _cli(monkeypatch, tmp_path, "run-queue", "--project", "proj",
                "--task-id", "T-1", "T-2", "--limit", "500000", "--yes")

    assert code == 0
    assert fake.started == ["T-1", "T-2"]
    assert "done" in capsys.readouterr().out


def test_cli_ready_adds_tasks_in_ready_order(tmp_path, accounts, monkeypatch, capsys):
    cfg = _make_config(tmp_path)
    for task_id in ("T-1", "T-2", "T-3"):
        _task(cfg, task_id)
    _place(cfg, "T-3", priority="high")
    fake = _install(monkeypatch, cfg, {t: {} for t in ("T-1", "T-2", "T-3")})

    code = _cli(monkeypatch, tmp_path, "run-queue", "--project", "proj",
                "--task-id", "T-2", "--ready", "2", "--limit", "500000", "--yes")

    assert code == 0
    assert fake.started == ["T-2", "T-3", "T-1"]
    assert "2. T-3  Do T-3.  [from Ready]" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [
    ["--project", "proj", "--limit", "500000"],
    ["--project", "proj", "--ready", "-1", "--limit", "500000"],
    ["--resume", "q-20261017T000000Z-abcd", "--ready", "1"],
])
def test_cli_ready_usage_errors(tmp_path, accounts, monkeypatch, argv):
    assert _cli(monkeypatch, tmp_path, "run-queue", *argv, "--yes") == 2


def test_cli_refuses_parallel_above_one(tmp_path, accounts, monkeypatch):
    cfg = _make_config(tmp_path)
    _task(cfg, "T-1")
    code = _cli(monkeypatch, tmp_path, "run-queue", "--project", "proj",
                "--task-id", "T-1", "--limit", "500000", "--parallel", "2", "--yes")
    assert code == 2


def test_cli_limit_and_push_refused_exit_codes(tmp_path, accounts, monkeypatch):
    cfg = _make_config(tmp_path)
    _task(cfg, "T-1")
    _task(cfg, "T-2")
    _install(monkeypatch, cfg, {"T-1": {"phases": [600_000]}, "T-2": {}})
    assert _cli(monkeypatch, tmp_path, "run-queue", "--project", "proj",
                "--task-id", "T-1", "T-2", "--limit", "500000", "--yes") == cli_mod.EXIT_LIMIT

    _task(cfg, "T-3")
    _install(monkeypatch, cfg, {"T-3": {"refuse_push": True}})
    assert _cli(monkeypatch, tmp_path, "run-queue", "--project", "proj",
                "--task-id", "T-3", "--limit", "500000", "--yes") == cli_mod.EXIT_NOT_PUSHED


@pytest.mark.parametrize("limit", ["2", "99999"])
def test_cli_refuses_a_limit_that_is_not_measured_tokens(
    tmp_path, accounts, monkeypatch, capsys, limit
):
    cfg = _make_config(tmp_path)
    _task(cfg, "T-1")
    fake = _install(monkeypatch, cfg, {"T-1": {}})

    code = _cli(monkeypatch, tmp_path, "run-queue", "--project", "proj",
                "--task-id", "T-1", "--limit", limit, "--yes")

    err = capsys.readouterr().err
    assert code == 2
    for shown in (f"--limit {limit}", "measured tokens", "not tasks", str(cli_mod.MIN_QUEUE_LIMIT)):
        assert shown in err
    assert fake.started == []
    assert not os.path.isdir(queue_mod.queue_dir(cfg)) or not os.listdir(queue_mod.queue_dir(cfg))


def test_cli_accepts_the_minimum_limit(tmp_path, accounts, monkeypatch):
    cfg = _make_config(tmp_path)
    _task(cfg, "T-1")
    fake = _install(monkeypatch, cfg, {"T-1": {}})

    code = _cli(monkeypatch, tmp_path, "run-queue", "--project", "proj",
                "--task-id", "T-1", "--limit", str(cli_mod.MIN_QUEUE_LIMIT), "--yes")

    assert code == 0
    assert fake.started == ["T-1"]


def _stopped_queue(tmp_path, monkeypatch, script=None):
    """A queue T-1, T-2 at 500000 that T-1 spent 600000 of, stopped `limit_reached`."""
    cfg = _make_config(tmp_path)
    _task(cfg, "T-1")
    _task(cfg, "T-2")
    fake = _install(monkeypatch, cfg, script or {"T-1": {"phases": [600_000, 100]}, "T-2": {}})
    code = _cli(monkeypatch, tmp_path, "run-queue", "--project", "proj",
                "--task-id", "T-1", "T-2", "--limit", "500000", "--yes")
    assert code == cli_mod.EXIT_LIMIT
    queue_id = os.listdir(queue_mod.queue_dir(cfg))[0][:-len(".json")]
    return cfg, fake, queue_id


def test_resume_with_a_higher_limit_runs_on(tmp_path, accounts, monkeypatch, capsys):
    cfg, fake, queue_id = _stopped_queue(tmp_path, monkeypatch)
    assert queue_mod.load(cfg, queue_id).entry("T-1").state == queue_mod.STOPPED

    code = _cli(monkeypatch, tmp_path, "run-queue", "--resume", queue_id,
                "--limit", "2000000", "--yes")

    queue = queue_mod.load(cfg, queue_id)
    assert code == 0
    assert fake.started == ["T-1", "T-1", "T-2"]
    assert queue.entry("T-1").state == queue_mod.MERGED
    assert queue.entry("T-2").state == queue_mod.MERGED
    assert queue.spent >= 1_200_000  # the first 600000 still counts
    assert queue.limit == 2_000_000
    assert len(queue.changes) == 1
    assert queue.changes[0]["limit"] == {"from": 500_000, "to": 2_000_000}
    assert "parallel" not in queue.changes[0] and queue.changes[0]["at"]
    out = capsys.readouterr().out
    assert "Limit: 2000000" in out and "limit 500000 -> 2000000" in out


def test_resume_does_not_run_a_finished_task_again(tmp_path, accounts, monkeypatch):
    cfg, fake, queue_id = _stopped_queue(
        tmp_path, monkeypatch, {"T-1": {}, "T-2": {"phases": [600_000, 100]}},
    )
    assert fake.started == ["T-1", "T-2"]

    code = _cli(monkeypatch, tmp_path, "run-queue", "--resume", queue_id,
                "--limit", "2000000", "--yes")

    assert code == 0
    assert fake.started == ["T-1", "T-2", "T-2"]


@pytest.mark.parametrize("limit", ["600000", "550000"])
def test_resume_refuses_a_limit_not_above_the_spend(
    tmp_path, accounts, monkeypatch, capsys, limit
):
    cfg, fake, queue_id = _stopped_queue(tmp_path, monkeypatch)
    spent = queue_mod.load(cfg, queue_id).spent
    assert spent == 600_000
    before = Path(queue_mod.queue_path(cfg, queue_id)).read_text()
    capsys.readouterr()

    code = _cli(monkeypatch, tmp_path, "run-queue", "--resume", queue_id,
                "--limit", limit, "--yes")

    err = capsys.readouterr().err
    assert code == 2
    assert limit in err and str(spent) in err
    assert fake.started == ["T-1"]
    assert Path(queue_mod.queue_path(cfg, queue_id)).read_text() == before
    assert queue_mod.load(cfg, queue_id).changes == []


def test_resume_holds_a_new_limit_to_the_floor(tmp_path, accounts, monkeypatch, capsys):
    cfg, fake, queue_id = _stopped_queue(tmp_path, monkeypatch)
    capsys.readouterr()

    code = _cli(monkeypatch, tmp_path, "run-queue", "--resume", queue_id,
                "--limit", "99999", "--yes")

    assert code == 2
    assert str(cli_mod.MIN_QUEUE_LIMIT) in capsys.readouterr().err
    assert fake.started == ["T-1"]


@pytest.mark.parametrize("extra", [
    ["--project", "proj"], ["--task-id", "T-1"], ["--ready", "1"],
])
def test_resume_refuses_what_stays_the_queue_s_own(
    tmp_path, accounts, monkeypatch, capsys, extra
):
    cfg, fake, queue_id = _stopped_queue(tmp_path, monkeypatch)
    capsys.readouterr()

    code = _cli(monkeypatch, tmp_path, "run-queue", "--resume", queue_id,
                *extra, "--limit", "2000000", "--yes")

    err = capsys.readouterr().err
    assert code == 2
    assert "--limit" in err and "--parallel" in err
    assert fake.started == ["T-1"]


def test_resume_records_and_uses_a_new_parallelism(tmp_path, accounts, monkeypatch, capsys):
    def two_slots(path):
        return _make_config(path, accounts=[
            AccountConfig(name="cuenta1", container="agent-cuenta1", max_concurrent_phases=2),
        ])

    cfg = two_slots(tmp_path)
    _task(cfg, "T-1")
    queue = queue_mod.plan(cfg, "proj", ["T-1"], 500_000)
    queue_mod.save(cfg, queue)
    seen = []

    def fake_parallel(cfg, queue, sleep, lock):
        seen.append(queue.parallel)
        queue.status = queue_mod.Q_DONE
        return queue

    monkeypatch.setattr(queue_mod, "_run_parallel", fake_parallel)

    code = _cli(monkeypatch, tmp_path, "run-queue", "--resume", queue.queue_id,
                "--parallel", "2", "--yes", config_of=two_slots)

    saved = queue_mod.load(cfg, queue.queue_id)
    assert code == 0
    assert seen == [2]
    assert saved.parallel == 2
    assert saved.changes[0]["parallel"] == {"from": 1, "to": 2}
    assert "limit" not in saved.changes[0]
    assert "Parallelism: 2" in capsys.readouterr().out


def test_resume_that_changes_something_asks_and_a_no_saves_nothing(
    tmp_path, accounts, monkeypatch
):
    cfg, fake, queue_id = _stopped_queue(tmp_path, monkeypatch)
    before = Path(queue_mod.queue_path(cfg, queue_id)).read_text()

    code = _cli(monkeypatch, tmp_path, "run-queue", "--resume", queue_id,
                "--limit", "2000000", stdin="n")

    assert code == cli_mod.EXIT_BLOCKED
    assert fake.started == ["T-1"]
    assert Path(queue_mod.queue_path(cfg, queue_id)).read_text() == before


def test_a_plain_resume_asks_nothing_and_records_no_change(tmp_path, accounts, monkeypatch):
    cfg, fake, queue_id = _stopped_queue(tmp_path, monkeypatch)

    code = _cli(monkeypatch, tmp_path, "run-queue", "--resume", queue_id)

    # An answered "no" would be EXIT_BLOCKED: the spend is still at the limit.
    assert code == cli_mod.EXIT_LIMIT
    assert queue_mod.load(cfg, queue_id).changes == []
    assert fake.started == ["T-1"]


def test_a_queue_file_without_changes_loads(tmp_path, accounts, monkeypatch):
    cfg = _make_config(tmp_path)
    _task(cfg, "T-1")
    queue = queue_mod.plan(cfg, "proj", ["T-1"], 500_000)
    path = queue_mod.save(cfg, queue)
    data = json.loads(Path(path).read_text())
    data.pop("changes", None)
    Path(path).write_text(json.dumps(data))

    assert queue_mod.load(cfg, queue.queue_id).changes == []


# --- A split task's sub-tasks take its place (ADR 495) ---

_SPLIT = core.CycleOutcome.SPLIT


def _states(queue):
    return {e.task_id: e.state for e in queue.entries}


def test_a_split_task_is_replaced_by_its_sub_tasks_in_place(cfg, accounts, monkeypatch):
    _task(cfg, "T-1")
    _task(cfg, "T-2")
    fake = _install(monkeypatch, cfg, {
        "T-1": {"split": [(), (1,)]}, "T-1-S1": {}, "T-1-S2": {}, "T-2": {},
    })

    queue = queue_mod.plan(cfg, "proj", ["T-1", "T-2"], limit=10_000)
    queue = queue_mod.run(cfg, queue, sleep=_no_sleep)

    order = ["T-1", "T-1-S1", "T-1-S2", "T-2"]
    assert fake.started == order
    assert [e.task_id for e in queue.entries] == order
    assert queue.status == queue_mod.Q_DONE
    assert _states(queue) == {
        "T-1": queue_mod.SPLIT, "T-1-S1": queue_mod.MERGED,
        "T-1-S2": queue_mod.MERGED, "T-2": queue_mod.MERGED,
    }
    assert queue.entry("T-1").split_into == ["T-1-S1", "T-1-S2"]
    assert queue.entry("T-1-S2").epic == "T-1"
    assert queue.entry("T-1-S2").depends_on == ["T-1-S1"]
    assert not any(queue_mod.failed(e) for e in queue.entries)


def test_a_sub_task_waits_for_an_earlier_sibling_that_is_not_merged(cfg, accounts, monkeypatch):
    _task(cfg, "T-1")
    fake = _install(monkeypatch, cfg, {
        "T-1": {"split": [(), (1,)]}, "T-1-S1": {"merge": False}, "T-1-S2": {},
    })

    planned = queue_mod.plan(cfg, "proj", ["T-1"], 10_000)
    planned.max_wait_hours = 0
    queue = queue_mod.run(cfg, planned, sleep=_no_sleep)

    assert fake.started == ["T-1", "T-1-S1"]
    assert queue.status == queue_mod.Q_WAIT_EXPIRED
    assert _states(queue)["T-1-S2"] == queue_mod.PENDING


def test_a_dependent_waits_for_the_whole_epic(cfg, accounts, monkeypatch):
    _task(cfg, "T-1")
    _task(cfg, "T-2", depends_on=["T-1"])
    fake = _install(monkeypatch, cfg, {
        "T-1": {"split": [(), ()]}, "T-1-S1": {}, "T-1-S2": {}, "T-2": {},
    })

    queue = queue_mod.run(cfg, queue_mod.plan(cfg, "proj", ["T-1", "T-2"], 10_000), sleep=_no_sleep)

    assert fake.started == ["T-1", "T-1-S1", "T-1-S2", "T-2"]
    assert queue.entry("T-2").depends_on == ["T-1-S1", "T-1-S2"]
    file = context_transfer.read_task_file(
        context_transfer.task_file_path(cfg.hive_tasks_dir, "T-2")
    )
    assert file.depends_on == ["T-1-S1", "T-1-S2"]
    assert "was split into T-1-S1, T-1-S2" in file.body
    assert queue.status == queue_mod.Q_DONE


def test_a_dependent_waits_while_a_sub_task_is_unmerged(cfg, accounts, monkeypatch):
    _task(cfg, "T-1")
    _task(cfg, "T-2", depends_on=["T-1"])
    fake = _install(monkeypatch, cfg, {
        "T-1": {"split": [(), ()]}, "T-1-S1": {}, "T-1-S2": {"merge": False}, "T-2": {},
    })

    planned = queue_mod.plan(cfg, "proj", ["T-1", "T-2"], 10_000)
    planned.max_wait_hours = 0
    queue = queue_mod.run(cfg, planned, sleep=_no_sleep)

    assert "T-2" not in fake.started
    assert queue.status == queue_mod.Q_WAIT_EXPIRED


def test_a_failed_sub_task_skips_its_sibling_and_the_dependent(cfg, accounts, monkeypatch):
    _task(cfg, "T-1")
    _task(cfg, "T-2", depends_on=["T-1"])
    fake = _install(monkeypatch, cfg, {
        "T-1": {"split": [(), (1,)]},
        "T-1-S1": {"outcome": core.CycleOutcome.BLOCKED}, "T-1-S2": {}, "T-2": {},
    })

    queue = queue_mod.run(cfg, queue_mod.plan(cfg, "proj", ["T-1", "T-2"], 10_000), sleep=_no_sleep)

    assert fake.started == ["T-1", "T-1-S1"]
    assert _states(queue) == {
        "T-1": queue_mod.SPLIT, "T-1-S1": queue_mod.BLOCKED,
        "T-1-S2": queue_mod.SKIPPED, "T-2": queue_mod.SKIPPED,
    }
    assert queue.status == queue_mod.Q_DONE


def test_resume_goes_on_with_the_absorbed_sub_tasks(cfg, accounts, monkeypatch):
    _task(cfg, "T-1")
    _task(cfg, "T-2", depends_on=["T-1"])
    fake = _install(monkeypatch, cfg, {
        "T-1": {"split": [(), (1,)]}, "T-1-S1": {}, "T-1-S2": {}, "T-2": {},
    })
    queue = queue_mod.plan(cfg, "proj", ["T-1", "T-2"], limit=150)

    queue = queue_mod.run(cfg, queue, sleep=_no_sleep)

    assert queue.status == queue_mod.Q_LIMIT
    saved = queue_mod.load(cfg, queue.queue_id)
    assert saved.entry("T-1").split_into == ["T-1-S1", "T-1-S2"]
    assert saved.entry("T-1-S1").epic == "T-1"
    assert fake.started == ["T-1", "T-1-S1"]
    shown = queue_mod.confirmation(cfg, saved)
    assert "[split into T-1-S1, T-1-S2]" in shown
    assert "[from T-1's split]" in shown

    queue = queue_mod.run(
        cfg, queue_mod.resume(cfg, queue.queue_id, limit=10_000), sleep=_no_sleep,
    )

    assert fake.started == ["T-1", "T-1-S1", "T-1-S2", "T-2"]
    assert queue.status == queue_mod.Q_DONE


def test_an_old_split_entry_without_sub_tasks_still_skips_its_dependents(cfg, accounts, monkeypatch):
    _task(cfg, "T-1")
    _task(cfg, "T-2", depends_on=["T-1"])
    fake = _install(monkeypatch, cfg, {"T-2": {}})
    queue = queue_mod.plan(cfg, "proj", ["T-1", "T-2"], 10_000)
    queue.entry("T-1").state = queue_mod.SPLIT
    path = queue_mod.save(cfg, queue)
    data = json.loads(Path(path).read_text())
    for e in data["entries"]:
        e.pop("split_into")
        e.pop("epic")
    Path(path).write_text(json.dumps(data))

    loaded = queue_mod.load(cfg, queue.queue_id)
    assert queue_mod.failed(loaded.entry("T-1"))
    queue = queue_mod.run(cfg, queue_mod.resume(cfg, queue.queue_id), sleep=_no_sleep)

    assert fake.started == []
    assert _states(queue)["T-2"] == queue_mod.SKIPPED


def test_a_sub_task_that_cannot_be_read_is_skipped_with_its_reason(cfg, accounts, monkeypatch):
    _task(cfg, "T-1")
    _task(cfg, "T-2", depends_on=["T-1"])
    fake = _install(monkeypatch, cfg, {"T-1": {"outcome": _SPLIT}, "T-2": {}})
    parent = context_transfer.task_file_path(cfg.hive_tasks_dir, "T-1")
    task = context_transfer.read_task_file(parent)
    task.split_into = ["T-1-S1"]  # no description was ever written for it
    tasks._write_task_file(parent, task)

    queue = queue_mod.run(cfg, queue_mod.plan(cfg, "proj", ["T-1", "T-2"], 10_000), sleep=_no_sleep)

    assert fake.started == ["T-1"]
    assert _states(queue)["T-1-S1"] == queue_mod.SKIPPED
    assert queue.entry("T-1-S1").note.startswith("not started:")
    assert _states(queue)["T-2"] == queue_mod.SKIPPED


def test_a_split_without_readable_sub_tasks_stays_a_failure(cfg, accounts, monkeypatch):
    _task(cfg, "T-1")
    _task(cfg, "T-2", depends_on=["T-1"])
    fake = _install(monkeypatch, cfg, {"T-1": {"outcome": _SPLIT}, "T-2": {}})

    queue = queue_mod.run(cfg, queue_mod.plan(cfg, "proj", ["T-1", "T-2"], 10_000), sleep=_no_sleep)

    assert fake.started == ["T-1"]
    assert queue.entry("T-1").note == "split, but its sub-tasks could not be read"
    assert queue_mod.failed(queue.entry("T-1"))
    assert _states(queue)["T-2"] == queue_mod.SKIPPED


def test_the_confirmation_states_the_split_rule(cfg):
    _task(cfg, "T-1")
    queue = queue_mod.plan(cfg, "proj", ["T-1"], 10_000)
    assert "replaced by its sub-tasks" in queue_mod.confirmation(cfg, queue)


def test_cli_exits_zero_when_a_split_task_s_sub_tasks_all_merged(tmp_path, accounts, monkeypatch):
    cfg = _make_config(tmp_path)
    _task(cfg, "T-1")
    _install(monkeypatch, cfg, {"T-1": {"split": [(), ()]}, "T-1-S1": {}, "T-1-S2": {}})

    code = _cli(
        monkeypatch, tmp_path, "run-queue", "--project", "proj", "--task-id", "T-1",
        "--limit", "500000", "--yes",
    )

    assert code == 0


def test_cli_missing_limit_names_the_unit(tmp_path, accounts, monkeypatch, capsys):
    cfg = _make_config(tmp_path)
    _task(cfg, "T-1")
    code = _cli(monkeypatch, tmp_path, "run-queue", "--project", "proj", "--task-id", "T-1", "--yes")
    assert code == 2
    assert "--limit TOKENS" in capsys.readouterr().err
