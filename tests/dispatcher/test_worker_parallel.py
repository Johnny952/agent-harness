"""The worker runs up to `project_parallel` starts of one project at once (ADR 306).

The cycle is a stub that takes its write window from the group's scheduler the
way `run_task_cycle(scheduler=)` does. Ordering is by events, never by sleeps.
"""

from __future__ import annotations

import threading
import time

import pytest

from dispatcher import actions
from dispatcher import dispatcher as core
from dispatcher import worker as worker_mod
from dispatcher.config import AccountConfig
from dispatcher.project_lock import ProjectBusy, ProjectLock
from tests.dispatcher.test_dispatcher import _make_config
from tests.dispatcher.test_worker import WAIT, Cycle, start, where

DISJOINT = {"T-1": frozenset({"src/a/"}), "T-2": frozenset({"src/b/"})}
OVERLAP = {"T-1": frozenset({"src/a/"}), "T-2": frozenset({"src/a/x.py"})}


class WindowCycle(Cycle):
    """A `Cycle` that asks its scheduler for a write window, holds it until
    `release`, and records who was inside at once."""

    def __init__(self, surfaces):
        super().__init__()
        self.surfaces = surfaces
        self.schedulers: list = []
        self.inside: set[str] = set()
        self.max_inside = 0
        self.in_window: dict[str, threading.Event] = {}
        self._guard = threading.Lock()

    def wait_in_window(self, task_id):
        assert self.in_window.setdefault(task_id, threading.Event()).wait(WAIT)

    def __call__(self, cfg, task_id, slug, description=None, authorized=True,
                 on_approve=None, may_start_phase=None, scheduler=None, project_lock=None,
                 token_limit=None, usage_offset=0):
        self.schedulers.append(scheduler)
        self.locks.append(project_lock)
        self.calls.append((task_id, slug))
        self.entered.setdefault(task_id, threading.Event()).set()
        if scheduler is None:
            return core.CycleOutcome.FINISHED
        scheduler.enter_write_window(task_id, self.surfaces[task_id])
        with self._guard:
            self.inside.add(task_id)
            self.max_inside = max(self.max_inside, len(self.inside))
        self.in_window.setdefault(task_id, threading.Event()).set()
        try:
            assert self.gates[task_id].wait(WAIT)
        finally:
            with self._guard:
                self.inside.discard(task_id)
            scheduler.leave_write_window(task_id)
        return core.CycleOutcome.FINISHED


def make(tmp_path, ceiling, surfaces, monkeypatch):
    cfg = _make_config(
        tmp_path,
        accounts=[AccountConfig(name="cuenta1", container="agent-cuenta1", max_concurrent_phases=2)],
        project_parallel={"proj": ceiling} if ceiling else {},
    )
    cycle = WindowCycle(surfaces)
    for task_id in surfaces:
        cycle.hold(task_id)
    monkeypatch.setattr(core, "run_task_cycle", cycle)
    monkeypatch.setattr(core, "pick_idle_account", lambda cfg, exclude=None, role=None: "cuenta1")
    monkeypatch.setattr(core, "_recheck_cooling_accounts", lambda cfg: [])
    worker = worker_mod.Worker(cfg, account_poll=0)
    return cfg, cycle, worker


@pytest.fixture
def stop(request):
    workers = []
    yield workers.append

    def finish():
        for w, cycle in workers:
            w.stop()
            for gate in cycle.gates.values():
                gate.set()
            w.join()

    finish()


def wait_for(predicate):
    deadline = time.monotonic() + WAIT
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.005)


def test_disjoint_surfaces_have_their_write_windows_at_once(tmp_path, monkeypatch, stop):
    cfg, cycle, worker = make(tmp_path, 2, DISJOINT, monkeypatch)
    stop((worker, cycle))
    start(cfg, "T-1")
    start(cfg, "T-2")
    worker.tick()
    cycle.wait_in_window("T-1")
    cycle.wait_in_window("T-2")
    assert cycle.inside == {"T-1", "T-2"}
    assert cycle.schedulers[0] is cycle.schedulers[1] is not None
    for task_id in DISJOINT:
        cycle.release(task_id)
    worker.join()
    assert cycle.max_inside == 2


def test_overlapping_surfaces_never_have_their_windows_at_once(tmp_path, monkeypatch, stop):
    cfg, cycle, worker = make(tmp_path, 2, OVERLAP, monkeypatch)
    stop((worker, cycle))
    start(cfg, "T-1")
    start(cfg, "T-2")
    worker.tick()
    cycle.wait_entered("T-1")
    cycle.wait_entered("T-2")
    scheduler = cycle.schedulers[0]
    # One holds the window; the other is parked in the scheduler, asking.
    wait_for(lambda: len(scheduler.windows) == 1 and len(scheduler._waiting) == 1)
    assert len(cycle.inside) == 1
    first = scheduler.windows[0][0]
    cycle.release(first)
    second = "T-2" if first == "T-1" else "T-1"
    cycle.wait_in_window(second)
    cycle.release(second)
    worker.join()
    assert cycle.max_inside == 1
    assert [kind for _, kind in scheduler.windows] == ["enter", "leave", "enter", "leave"]


def test_the_project_lock_holds_until_the_last_start_ends(tmp_path, monkeypatch, stop):
    cfg, cycle, worker = make(tmp_path, 2, DISJOINT, monkeypatch)
    stop((worker, cycle))
    a = start(cfg, "T-1")
    start(cfg, "T-2")
    worker.tick()
    cycle.wait_in_window("T-1")
    cycle.wait_in_window("T-2")
    with pytest.raises(ProjectBusy) as busy:
        ProjectLock.take(cfg.state_dir, "proj", "T-9", "run-task")
    assert busy.value.holder["via"] == "worker"
    assert cycle.locks[0] is cycle.locks[1]

    cycle.release("T-1")
    wait_for(lambda: where(cfg, a["id"])[0] == "done")
    wait_for(lambda: len(worker._groups["proj"].members) == 1)
    with pytest.raises(ProjectBusy):
        ProjectLock.take(cfg.state_dir, "proj", "T-9", "run-task")

    cycle.release("T-2")
    worker.join()
    assert worker._groups == {}
    ProjectLock.take(cfg.state_dir, "proj", "T-9", "run-task").release()


def test_a_start_over_the_ceiling_waits_as_project(tmp_path, monkeypatch, stop):
    surfaces = {**DISJOINT, "T-3": frozenset({"src/c/"})}
    cfg, cycle, worker = make(tmp_path, 2, surfaces, monkeypatch)
    stop((worker, cycle))
    start(cfg, "T-1")
    start(cfg, "T-2")
    third = start(cfg, "T-3")
    worker.tick()
    cycle.wait_in_window("T-1")
    cycle.wait_in_window("T-2")
    state, rec = where(cfg, third["id"])
    assert state == "pending" and rec["waiting"] == "project"
    cycle.release("T-1")
    wait_for(lambda: len(worker._groups["proj"].members) == 1)
    worker.tick()
    cycle.wait_in_window("T-3")
    assert where(cfg, third["id"])[0] == "claimed"
    for task_id in surfaces:
        cycle.release(task_id)
    worker.join()
    assert where(cfg, third["id"])[0] == "done"


def test_a_ceiling_of_one_runs_starts_one_at_a_time_with_no_scheduler(tmp_path, monkeypatch, stop):
    cfg, cycle, worker = make(tmp_path, 0, {"T-1": frozenset(), "T-2": frozenset()}, monkeypatch)
    stop((worker, cycle))
    first = start(cfg, "T-1")
    second = start(cfg, "T-2")
    worker.tick()
    worker.join()
    states = {where(cfg, first["id"])[0], where(cfg, second["id"])[0]}
    assert states == {"done", "pending"}
    assert cycle.schedulers == [None]
    assert isinstance(cycle.locks[0], ProjectLock)
    assert worker._groups == {}
    worker.tick()
    worker.join()
    assert where(cfg, second["id"])[0] == "done"


def test_a_refused_start_gives_its_place_back(tmp_path, monkeypatch, stop):
    cfg, cycle, worker = make(tmp_path, 2, DISJOINT, monkeypatch)
    stop((worker, cycle))
    monkeypatch.setattr(worker, "_account_open", lambda: False)
    first = start(cfg, "T-1")
    worker.tick()
    assert where(cfg, first["id"])[1]["waiting"] == "account"
    assert worker._groups == {}
    ProjectLock.take(cfg.state_dir, "proj", "T-9", "run-task").release()


def test_a_start_waits_while_another_process_holds_the_project_under_a_ceiling(
    tmp_path, monkeypatch, stop,
):
    cfg, cycle, worker = make(tmp_path, 2, DISJOINT, monkeypatch)
    stop((worker, cycle))
    other = ProjectLock.take(cfg.state_dir, "proj", "T-9", "run-task")
    first = start(cfg, "T-1")
    worker.tick()
    assert where(cfg, first["id"])[1]["waiting"] == "project"
    assert worker._groups == {}
    other.release()
    assert actions.serial_key(first) == "start:proj:T-1"
