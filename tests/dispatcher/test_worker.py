"""dispatcher/worker.py: the action worker (docs/decisions.md ADR 142, 148).

`run_task_cycle` is faked; the loop is driven by `Worker.tick()` and
`Worker.join()`, so nothing sleeps beyond Event waits that fail rather than hang.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone

import pytest

from dispatcher import actions, tasks, worker as worker_mod
from dispatcher import dispatcher as core
from tests.dispatcher.test_dispatcher import _make_config
from tests.dispatcher.test_queue import _task

WAIT = 5


class Cycle:
    """A fake cycle. `hold(task)` makes it block until `release(task)`; it asks
    `may_start_phase` after the block, as the real one does before a phase."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.entered: dict[str, threading.Event] = {}
        self.gates: dict[str, threading.Event] = {}
        self.raises: dict[str, Exception] = {}

    def hold(self, task_id):
        self.gates[task_id] = threading.Event()

    def release(self, task_id):
        self.gates[task_id].set()

    def wait_entered(self, task_id):
        assert self.entered.setdefault(task_id, threading.Event()).wait(WAIT)

    def __call__(self, cfg, task_id, slug, description=None, authorized=True,
                 on_approve=None, may_start_phase=None, scheduler=None):
        assert authorized is True
        self.calls.append((task_id, slug))
        self.entered.setdefault(task_id, threading.Event()).set()
        if task_id in self.raises:
            raise self.raises[task_id]
        gate = self.gates.get(task_id)
        if gate is not None:
            assert gate.wait(WAIT)
        if not may_start_phase():
            return core.CycleOutcome.HELD
        return core.CycleOutcome.FINISHED


@pytest.fixture
def cfg(tmp_path):
    return _make_config(tmp_path)


@pytest.fixture
def cycle(monkeypatch):
    fake = Cycle()
    monkeypatch.setattr(core, "run_task_cycle", fake)
    return fake


@pytest.fixture
def accounts(monkeypatch):
    state = {"open": True, "probes": 0}

    def pick(cfg, exclude=None, role=None):
        return "cuenta1" if state["open"] else None

    def recheck(cfg):
        state["probes"] += 1
        return []

    monkeypatch.setattr(core, "pick_idle_account", pick)
    monkeypatch.setattr(core, "_recheck_cooling_accounts", recheck)
    return state


@pytest.fixture
def clock(monkeypatch):
    now = {"t": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    monkeypatch.setattr(worker_mod, "_utc_now", lambda: now["t"])
    return now


@pytest.fixture
def worker(cfg, cycle, accounts):
    w = worker_mod.Worker(cfg, account_poll=0)
    yield w
    w.stop()
    for gate in cycle.gates.values():
        gate.set()
    w.join()


def start(cfg, task_id, project="proj", note=None, make_task=True):
    if make_task:
        _task(cfg, task_id)
    return actions.create(cfg.state_dir, "start", project=project, task_id=task_id,
                          note=note)


def cancel(cfg, target):
    return actions.create(cfg.state_dir, "cancel", params={"target": target})


def where(cfg, aid):
    root = actions.actions_dir(cfg.state_dir)
    for state in ("pending", "claimed", "done"):
        path = root / state / f"{aid}.json"
        if path.exists():
            return state, json.loads(path.read_text())
    return None, None


def comments(cfg, task_id):
    path = tasks.comments_path(cfg.hive_tasks_dir, task_id)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


# ---- the lock ---------------------------------------------------------------


def test_a_second_worker_exits_2(cfg, capsys):
    fd = worker_mod.acquire(cfg.state_dir)
    try:
        assert worker_mod.run_worker(cfg) == 2
        assert "worker.lock" in capsys.readouterr().err
    finally:
        os.close(fd)
    os.close(worker_mod.acquire(cfg.state_dir))


# ---- serial keys ------------------------------------------------------------


def test_one_claimed_start_per_project(cfg, worker, cycle):
    cycle.hold("T-1")
    first = start(cfg, "T-1")
    second = start(cfg, "T-2")
    worker.tick()
    cycle.wait_entered("T-1")
    assert where(cfg, first["id"])[0] == "claimed"
    state, rec = where(cfg, second["id"])
    assert state == "pending" and rec["waiting"] == "project"
    cycle.release("T-1")
    worker.join()
    worker.tick()
    worker.join()
    state, rec = where(cfg, second["id"])
    assert state == "done" and rec["status"] == "done" and rec["waiting"] is None
    assert [c[0] for c in cycle.calls] == ["T-1", "T-2"]


def test_two_projects_run_at_once(cfg, worker, cycle):
    cycle.hold("T-1")
    cycle.hold("T-2")
    a = start(cfg, "T-1", project="alpha")
    b = start(cfg, "T-2", project="beta")
    worker.tick()
    cycle.wait_entered("T-1")
    cycle.wait_entered("T-2")
    assert where(cfg, a["id"])[0] == "claimed" and where(cfg, b["id"])[0] == "claimed"
    cycle.release("T-1")
    cycle.release("T-2")
    worker.join()
    assert where(cfg, a["id"])[1]["status"] == "done"
    assert where(cfg, b["id"])[1]["result"]["outcome"] == "finished"


def test_a_handler_that_raises_fails_the_start_and_frees_the_project(cfg, worker, cycle):
    cycle.raises["T-1"] = RuntimeError("boom\nsecond line")
    first = start(cfg, "T-1")
    second = start(cfg, "T-2")
    worker.tick()
    worker.join()
    rec = where(cfg, first["id"])[1]
    assert rec["status"] == "failed" and rec["error"] == "RuntimeError: boom second line"
    assert worker.running == {}
    worker.tick()
    worker.join()
    assert where(cfg, second["id"])[1]["status"] == "done"


# ---- accounts ---------------------------------------------------------------


def test_a_start_waits_for_an_account(cfg, cycle, accounts, clock):
    w = worker_mod.Worker(cfg, account_poll=60)
    rec = start(cfg, "T-1")
    accounts["open"] = False
    w.tick()
    state, got = where(cfg, rec["id"])
    assert state == "pending" and got["waiting"] == "account"
    assert cycle.calls == []
    probes = accounts["probes"]
    w.tick()
    assert accounts["probes"] == probes  # paced: no second look within account_poll
    accounts["open"] = True
    clock["t"] += timedelta(seconds=61)
    w.tick()
    w.join()
    state, got = where(cfg, rec["id"])
    assert state == "done" and got["waiting"] is None
    assert cycle.calls == [("T-1", "proj")]


# ---- the note ---------------------------------------------------------------


def test_the_note_is_commented_once_before_started_at(cfg, worker, cycle):
    rec = start(cfg, "T-1", note="go carefully")
    worker.tick()
    worker.join()
    lines = comments(cfg, "T-1")
    assert len(lines) == 1
    assert lines[0]["author"] == "operator" and lines[0]["text"] == "go carefully"
    done = where(cfg, rec["id"])[1]
    assert done["result"]["noted_at"] <= done["started_at"]
    assert done["result"]["outcome"] == "finished"


def test_a_rerun_after_a_crash_does_not_comment_again(cfg, worker, cycle):
    rec = start(cfg, "T-1", note="once")
    claimed = actions.claim(cfg.state_dir, rec["id"])
    tasks.add_comment(cfg.hive_tasks_dir, "T-1", "operator", "once")
    claimed["result"] = {"noted_at": "2026-01-01T00:00:00+00:00"}
    actions.rewrite(cfg.state_dir, claimed)  # died before started_at
    assert worker.recover() == [(rec["id"], "back to pending")]
    worker.tick()
    worker.join()
    assert len(comments(cfg, "T-1")) == 1
    assert where(cfg, rec["id"])[1]["status"] == "done"
    assert cycle.calls == [("T-1", "proj")]


def test_a_refused_comment_fails_the_start_before_it_runs(cfg, worker, cycle):
    rec = start(cfg, "T-9", note="hello", make_task=False)
    worker.tick()
    worker.join()
    got = where(cfg, rec["id"])[1]
    assert got["status"] == "failed" and got["started_at"] is None
    assert cycle.calls == []


# ---- cancel -----------------------------------------------------------------


def test_a_cancel_on_a_pending_target(cfg, worker, cycle, accounts):
    accounts["open"] = False
    target = start(cfg, "T-1")
    c = cancel(cfg, target["id"])
    worker.tick()
    state, got = where(cfg, target["id"])
    assert state == "done" and got["status"] == "cancelled"
    assert got["result"] == {"cancelled_by": c["id"]}
    state, got = where(cfg, c["id"])
    assert state == "done" and got["status"] == "done"
    assert cycle.calls == []


def test_a_cancel_on_a_running_start(cfg, worker, cycle):
    cycle.hold("T-1")
    target = start(cfg, "T-1")
    worker.tick()
    cycle.wait_entered("T-1")
    c = cancel(cfg, target["id"])
    worker.tick()
    got = where(cfg, c["id"])[1]
    assert got["status"] == "done" and got["result"]["target_status"] == "running"
    assert where(cfg, target["id"])[0] == "claimed"
    cycle.release("T-1")
    worker.join()
    got = where(cfg, target["id"])[1]
    assert got["status"] == "cancelled" and got["result"]["outcome"] == "held"
    assert "last_phase" in got["result"]


def test_a_cancel_on_a_finished_target_fails(cfg, worker, cycle):
    done = start(cfg, "T-1")
    worker.tick()
    worker.join()
    c = cancel(cfg, done["id"])
    worker.tick()
    got = where(cfg, c["id"])[1]
    assert got["status"] == "failed" and "already finished as done" in got["error"]


def test_a_cancel_of_a_target_nobody_has_fails(cfg, worker, cycle):
    c = cancel(cfg, "a-20260101T000000000000Z-ffff")
    worker.tick()
    got = where(cfg, c["id"])[1]
    assert got["status"] == "failed" and got["error"].startswith("no action")


def test_a_cancel_rerun_after_it_cancelled_its_target_is_done(cfg, worker, cycle, accounts):
    accounts["open"] = False
    target = start(cfg, "T-1")
    c = cancel(cfg, target["id"])
    actions.finish(cfg.state_dir, target["id"], "cancelled",
                   result={"cancelled_by": c["id"]})  # then the worker died
    claimed = actions.claim(cfg.state_dir, c["id"])
    assert claimed is not None
    worker.recover()
    worker.tick()
    state, got = where(cfg, c["id"])
    assert state == "done" and got["status"] == "done"


# ---- interrupted starts -----------------------------------------------------


def test_a_start_interrupted_before_started_at_runs_again(cfg, worker, cycle):
    rec = start(cfg, "T-1")
    assert actions.claim(cfg.state_dir, rec["id"]) is not None
    assert worker.recover() == [(rec["id"], "back to pending")]
    assert where(cfg, rec["id"])[0] == "pending"
    worker.tick()
    worker.join()
    assert where(cfg, rec["id"])[1]["status"] == "done"
    assert cycle.calls == [("T-1", "proj")]


def test_a_start_interrupted_after_started_at_is_not_run_again(cfg, worker, cycle):
    rec = start(cfg, "T-1")
    claimed = actions.claim(cfg.state_dir, rec["id"])
    claimed["started_at"] = "2026-01-01T00:00:00+00:00"
    actions.rewrite(cfg.state_dir, claimed)
    assert worker.recover() == [(rec["id"], "interrupted")]
    worker.tick()
    worker.join()
    state, got = where(cfg, rec["id"])
    assert state == "done" and got["status"] == "interrupted"
    assert cycle.calls == []


# ---- kinds ------------------------------------------------------------------


def test_an_unknown_kind_fails(cfg, worker, cycle):
    actions.ensure_dirs(cfg.state_dir)
    rec = {k: None for k in actions.FIELDS}
    rec.update(version=1, id="a-20260101T000000000000Z-0001", kind="nope",
               params={}, actor="operator", status="pending")
    root = actions.actions_dir(cfg.state_dir)
    (root / "pending" / f"{rec['id']}.json").write_text(json.dumps(rec))
    worker.tick()
    state, got = where(cfg, rec["id"])
    assert state == "done" and got["status"] == "failed" and got["error"] == "unknown kind"


def test_command_verdict_and_turn_stay_pending_untouched(cfg, worker, cycle):
    v = actions.create(cfg.state_dir, "command_verdict", project="proj", task_id="T-1",
                       params={"n": 1, "verdict": "run"})
    t = actions.create(cfg.state_dir, "turn", params={"thread": "x", "text": "hi"})
    root = actions.actions_dir(cfg.state_dir) / "pending"
    before = {a["id"]: (root / f"{a['id']}.json").read_bytes() for a in (v, t)}
    worker.tick()
    worker.tick()
    for aid, data in before.items():
        assert (root / f"{aid}.json").read_bytes() == data
    assert worker.running == {}


# ---- heartbeat, shutdown ----------------------------------------------------


def test_the_heartbeat_is_written(cfg, clock):
    w = worker_mod.Worker(cfg)
    w.beat()
    path = actions.actions_dir(cfg.state_dir) / worker_mod.HEARTBEAT
    first = json.loads(path.read_text())
    assert set(first) == {"pid", "started_at", "beat_at"} and first["pid"] == os.getpid()
    clock["t"] += timedelta(seconds=5)
    w.beat()
    second = json.loads(path.read_text())
    assert second["started_at"] == first["started_at"]
    assert second["beat_at"] != first["beat_at"]


def test_run_beats_and_returns_once_stopped(cfg, cycle, accounts):
    w = worker_mod.Worker(cfg)
    timer = threading.Timer(0.1, w.stop)
    timer.start()
    w.run(interval=0.01)
    timer.join()
    assert (actions.actions_dir(cfg.state_dir) / worker_mod.HEARTBEAT).exists()


def test_stop_holds_the_running_start_and_claims_nothing_more(cfg, worker, cycle):
    cycle.hold("T-1")
    running = start(cfg, "T-1", project="alpha")
    worker.tick()
    cycle.wait_entered("T-1")
    waiting = start(cfg, "T-2", project="beta")
    worker.stop()
    cycle.release("T-1")
    worker.join()
    state, got = where(cfg, running["id"])
    assert state == "done" and got["status"] == "interrupted"
    assert got["error"] == worker_mod.STOPPED_ERROR
    worker.tick()
    assert where(cfg, waiting["id"])[0] == "pending"
    assert [c[0] for c in cycle.calls] == ["T-1"]
