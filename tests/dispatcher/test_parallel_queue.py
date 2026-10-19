"""The parallel queue (docs/plans/autonomy.md B-3, docs/decisions.md ADR 83), over stubbed cycles.

Two levels, neither starting a container. A fake cycle drives the queue's
scheduler the way `run_task_cycle` does (an arquitecto, then the write window,
then an implementador), so the slots and the windows are read from what it
records. Then `run_task_cycle` itself, with its phases and the base merge
faked, shows the single mapping and the stale-plan note.
"""
from __future__ import annotations

import threading
import time
import types

import pytest

from dispatcher import cli as cli_mod
from dispatcher import tasks
from dispatcher import dispatcher as core
from dispatcher import queue as queue_mod
from dispatcher.config import AccountConfig
from tests.dispatcher.test_dispatcher import _make_config
from tests.dispatcher.test_queue import _usage

_DESCRIPTION = (
    "## Goal\n\nDo {id}.\n\n## Surface\n\n{surface}\n\n## Done when\n\nIt is done.\n"
)


def _task(cfg, task_id, surface, depends_on=None):
    tasks.handoff(cfg.hive_tasks_dir, task_id, "pending", "", depends_on=depends_on or [])
    tasks.write_description(
        cfg.hive_tasks_dir, task_id, _DESCRIPTION.format(id=task_id, surface=surface),
    )


@pytest.fixture
def cfg(tmp_path):
    return _make_config(tmp_path)


@pytest.fixture(autouse=True)
def accounts(monkeypatch):
    monkeypatch.setattr(core, "pick_idle_account", lambda cfg, exclude=None, role=None: "cuenta1")
    monkeypatch.setattr(core, "_recheck_cooling_accounts", lambda cfg: [])


def _no_sleep(seconds):
    raise AssertionError("the queue waited with an account open")


class ScheduledCycle:
    """A cycle that uses the scheduler as `run_task_cycle` does.

    `arquitecto_barrier` makes every arquitecto wait for the others, so the
    test fails unless they run at once; `window_barrier` does the same inside
    the write window. `hold` keeps a window open a while, so an overlapping
    task has time to try for it. `before_window(task_id, scheduler)` runs
    just before the cycle asks for its window and `in_window(task_id,
    scheduler)` just after it gets it, so a test can order the windows by
    events instead of by `hold`.
    """

    def __init__(self, cfg, *, arquitecto_barrier=None, window_barrier=None, hold=0.0,
                 before_window=None, in_window=None):
        self.cfg = cfg
        self.arquitecto_barrier = arquitecto_barrier
        self.window_barrier = window_barrier
        self.hold = hold
        self.before_window = before_window
        self.in_window = in_window
        self.events: list[tuple[str, str]] = []
        self.scheduler = None
        self._lock = threading.Lock()

    def _log(self, task_id, what):
        with self._lock:
            self.events.append((task_id, what))

    def __call__(self, cfg, task_id, slug, description=None, authorized=True,
                 on_approve=None, may_start_phase=None, scheduler=None):
        assert scheduler is not None, "a parallel queue passes its scheduler"
        self.scheduler = scheduler
        if may_start_phase is not None and not may_start_phase():
            return core.CycleOutcome.HELD
        self._log(task_id, "arquitecto")
        if self.arquitecto_barrier is not None:
            self.arquitecto_barrier.wait(timeout=5)
        _usage(cfg, task_id, 100)
        entries = core._task_surface(tasks.read_description(cfg.hive_tasks_dir, task_id))
        if self.before_window is not None:
            self.before_window(task_id, scheduler)
        scheduler.enter_write_window(task_id, entries)
        try:
            self._log(task_id, "enter")
            if self.in_window is not None:
                self.in_window(task_id, scheduler)
            if self.window_barrier is not None:
                self.window_barrier.wait(timeout=5)
            time.sleep(self.hold(task_id) if callable(self.hold) else self.hold)
            _usage(cfg, task_id, 100)
            tasks.mark_merged(cfg.hive_tasks_dir, task_id, "main")
            self._log(task_id, "leave")
        finally:
            scheduler.leave_write_window(task_id)
        return core.CycleOutcome.FINISHED


def _run(cfg, monkeypatch, fake, task_ids, parallel=2, limit=10_000):
    monkeypatch.setattr(core, "run_task_cycle", fake)
    queue = queue_mod.plan(cfg, "proj", task_ids, limit=limit)
    queue.parallel = parallel
    return queue_mod.run(cfg, queue, sleep=_no_sleep)


def _windows_of(events):
    """Each task's (enter, leave) indexes in the event log."""
    return {
        task: (events.index((task, "enter")), events.index((task, "leave")))
        for task in {t for t, _ in events}
    }


def test_two_arquitectos_run_at_once_on_overlapping_surfaces_but_never_write_at_once(
    cfg, monkeypatch,
):
    _task(cfg, "T-1", "`dispatcher/queue.py`")
    _task(cfg, "T-2", "`dispatcher/`")
    fake = ScheduledCycle(cfg, arquitecto_barrier=threading.Barrier(2), hold=0.2)

    queue = _run(cfg, monkeypatch, fake, ["T-1", "T-2"])

    assert queue.status == queue_mod.Q_DONE
    assert [e.state for e in queue.entries] == [queue_mod.MERGED] * 2
    assert queue.spent == 400
    # Both arquitectos passed the barrier: they ran at once.
    assert fake.events[:2] == [("T-1", "arquitecto"), ("T-2", "arquitecto")] or \
        fake.events[:2] == [("T-2", "arquitecto"), ("T-1", "arquitecto")]
    # The windows did not overlap: one entered and left before the other entered.
    [(a, e1), (a2, l1), (b, e2), (b2, l2)] = fake.scheduler.windows
    assert (e1, l1, e2, l2) == ("enter", "leave", "enter", "leave")
    assert a == a2 and b == b2 and {a, b} == {"T-1", "T-2"}
    first, second = _windows_of(fake.events)[a], _windows_of(fake.events)[b]
    assert first[1] < second[0]


def test_disjoint_surfaces_write_at_once(cfg, monkeypatch):
    _task(cfg, "T-1", "`dispatcher/queue.py`")
    _task(cfg, "T-2", "`front/app.ts`, and `docs/decisions.md` for the ADR")
    fake = ScheduledCycle(cfg, window_barrier=threading.Barrier(2))

    queue = _run(cfg, monkeypatch, fake, ["T-1", "T-2"])

    assert queue.status == queue_mod.Q_DONE
    # Both were in their window when the barrier let them through.
    windows = _windows_of(fake.events)
    assert windows["T-1"][0] < windows["T-2"][1] and windows["T-2"][0] < windows["T-1"][1]


_DEADLINE = 10.0


def _wait_until(predicate, what):
    """Poll `predicate` until it holds, failing the test past a generous deadline."""
    deadline = time.monotonic() + _DEADLINE
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.005)


@pytest.mark.parametrize("first", ["T-1", "T-2"])
def test_a_whole_repo_task_writes_alone(cfg, monkeypatch, first):
    # T-1 claims the whole repo, T-2 one file. Whichever takes its window
    # first keeps it until the other is seen waiting for its own, so the
    # second really asked while the first was writing; it must not enter
    # until the first has left. Ordered by events, not by `hold` (T-065-D1):
    # with sleeps alone, either cycle could reach its window first.
    _task(cfg, "T-1", "Whatever it needs.")
    _task(cfg, "T-2", "`front/app.ts`")
    second = {"T-1": "T-2", "T-2": "T-1"}[first]
    first_in = threading.Event()

    def before_window(task_id, scheduler):
        if task_id == second:
            assert first_in.wait(timeout=_DEADLINE), f"{first} never entered its window"

    def in_window(task_id, scheduler):
        if task_id == first:
            first_in.set()
            _wait_until(
                lambda: second in scheduler._waiting, f"{second} to wait for its window",
            )
            # Give a wrongly granted window every chance to show up.
            time.sleep(0.05)

    fake = ScheduledCycle(cfg, before_window=before_window, in_window=in_window)

    queue = _run(cfg, monkeypatch, fake, ["T-1", "T-2"])

    assert queue.status == queue_mod.Q_DONE
    windows = _windows_of(fake.events)
    assert windows[first][1] < windows[second][0]
    assert fake.scheduler.windows == [
        (first, "enter"), (first, "leave"), (second, "enter"), (second, "leave"),
    ]


def test_the_queue_order_decides_a_contested_window(cfg, monkeypatch):
    # T-1 holds `a/`. T-3 asks for an overlapping window first, T-2 after it;
    # when T-1 leaves, T-2 goes in before T-3 because it is earlier in order.
    _task(cfg, "T-1", "`a/`")
    _task(cfg, "T-2", "`a/x.py`")
    _task(cfg, "T-3", "`a/y.py`, `a/x.py`")
    delays = {"T-1": 0.0, "T-2": 0.3, "T-3": 0.1}
    holds = {"T-1": 0.6, "T-2": 0.05, "T-3": 0.05}

    class Staggered(ScheduledCycle):
        def __call__(self, cfg, task_id, slug, **kwargs):
            time.sleep(delays[task_id])
            return super().__call__(cfg, task_id, slug, **kwargs)

    fake = Staggered(cfg, hold=holds.get)
    queue = _run(cfg, monkeypatch, fake, ["T-1", "T-2", "T-3"], parallel=3)

    assert queue.status == queue_mod.Q_DONE
    entered = [t for t, what in fake.scheduler.windows if what == "enter"]
    assert entered == ["T-1", "T-2", "T-3"]


def test_a_dependency_still_running_holds_its_dependent(cfg, monkeypatch):
    _task(cfg, "T-1", "`a.py`")
    _task(cfg, "T-2", "`b.py`", depends_on=["T-1"])
    fake = ScheduledCycle(cfg, hold=0.1)

    queue = _run(cfg, monkeypatch, fake, ["T-1", "T-2"])

    assert queue.status == queue_mod.Q_DONE
    assert fake.events.index(("T-1", "leave")) < fake.events.index(("T-2", "arquitecto"))


def test_the_limit_is_read_live_across_cycles(cfg, monkeypatch):
    _task(cfg, "T-1", "`a.py`")
    _task(cfg, "T-2", "`b.py`")
    _task(cfg, "T-3", "`c.py`")
    fake = ScheduledCycle(cfg, arquitecto_barrier=threading.Barrier(2))

    # Two cycles spend 400 between them; the third never starts.
    queue = _run(cfg, monkeypatch, fake, ["T-1", "T-2", "T-3"], limit=400)

    assert queue.status == queue_mod.Q_LIMIT
    assert [e.state for e in queue.entries] == [
        queue_mod.MERGED, queue_mod.MERGED, queue_mod.PENDING,
    ]


# --- The cycle itself, with its phases faked ---------------------------------


class _Phases:
    """`run_phase`, faked: records each call, answers by role."""

    def __init__(self, implementador_blocks=False, mapper_wait=None):
        self.calls: list[tuple[str, str, dict]] = []
        self.implementador_blocks = implementador_blocks
        self.mapper_wait = mapper_wait
        self._lock = threading.Lock()

    def __call__(self, ctx, role, **kwargs):
        with self._lock:
            self.calls.append((ctx.task_id, role, kwargs))
        if role == core.project_docs.MAPPER_ROLE:
            if self.mapper_wait is not None:
                self.mapper_wait()
            return types.SimpleNamespace(handoff={}, gates=None, result_text="")
        if role == "arquitecto":
            return types.SimpleNamespace(
                handoff={"changed": ["`dispatcher/queue.py` — the scheduler"]},
                gates=None, result_text="",
            )
        if role == "implementador":
            status = "blocked" if self.implementador_blocks else "done"
            return types.SimpleNamespace(
                handoff={"status": status, "pending": ["replan the scheduler"]},
                gates=None, result_text="",
            )
        # The revisor: stop the cycle there, which is all these tests read.
        return None

    def of(self, role):
        return [(task, kwargs) for task, r, kwargs in self.calls if r == role]


@pytest.fixture
def cycle(cfg, monkeypatch):
    """`run_task_cycle` with everything around its phases stubbed."""
    state = {"move": None, "needs_mapping": False}

    def open_cycle(cfg, task_id, slug, description=None):
        return types.SimpleNamespace(
            cfg=cfg, task_id=task_id, slug=slug, held=False, foreign_lock=False,
            may_start_phase=None, completed=False, write_window=False,
            description=tasks.read_description(cfg.hive_tasks_dir, task_id),
        )

    monkeypatch.setattr(core, "open_cycle", open_cycle)
    monkeypatch.setattr(core, "close_cycle", lambda ctx, completed: None)
    monkeypatch.setattr(core, "_mark_blocked", lambda ctx, reason=None: None)
    monkeypatch.setattr(core, "update_project_branch", lambda cfg, slug: None)
    monkeypatch.setattr(core, "_needs_mapping", lambda cfg, slug: state["needs_mapping"])
    monkeypatch.setattr(
        core, "merge_base_at_write_window", lambda cfg, task_id, slug: state["move"],
    )
    return state


def _scheduler(cfg, task_ids):
    queue = queue_mod.plan(cfg, "proj", task_ids, limit=10_000)
    return queue_mod._Scheduler(cfg, queue, threading.Condition(threading.RLock()))


def test_the_implementador_hears_of_a_base_that_moved_under_the_plan(cfg, monkeypatch, cycle):
    _task(cfg, "T-2", "`dispatcher/`")
    cycle["move"] = core.BaseMove(
        changed=("dispatcher/queue.py", "docs/decisions.md"), merged_tasks=("T-1",),
    )
    phases = _Phases()
    monkeypatch.setattr(core, "run_phase", phases)
    scheduler = _scheduler(cfg, ["T-2"])

    core.run_task_cycle(cfg, "T-2", "proj", scheduler=scheduler)

    [(_, kwargs)] = phases.of("implementador")
    note = kwargs["note"]
    assert note.startswith("## The base moved under the plan")
    assert "T-1" in note and "`dispatcher/queue.py`" in note
    # The append-only log is never news to the plan.
    assert "docs/decisions.md" not in note
    assert "status: blocked" in note
    # The window was left when the cycle ended.
    assert scheduler.windows == [("T-2", "enter"), ("T-2", "leave")]


@pytest.mark.parametrize("move", [
    None,  # the branch already held the base
    core.BaseMove(changed=("front/app.ts", "docs/ROADMAP.md"), merged_tasks=("T-1",)),
])
def test_no_note_when_the_base_did_not_move_under_the_plan(cfg, monkeypatch, cycle, move):
    _task(cfg, "T-2", "`dispatcher/`")
    cycle["move"] = move
    phases = _Phases()
    monkeypatch.setattr(core, "run_phase", phases)

    core.run_task_cycle(cfg, "T-2", "proj", scheduler=_scheduler(cfg, ["T-2"]))

    [(_, kwargs)] = phases.of("implementador")
    assert "note" not in kwargs


def test_a_path_only_the_plan_names_counts_too(cfg, monkeypatch, cycle):
    # The surface is narrower than what the arquitecto said it would change.
    _task(cfg, "T-2", "`dispatcher/cli.py`")
    cycle["move"] = core.BaseMove(changed=("dispatcher/queue.py",), merged_tasks=("T-1",))
    phases = _Phases()
    monkeypatch.setattr(core, "run_phase", phases)

    core.run_task_cycle(cfg, "T-2", "proj", scheduler=_scheduler(cfg, ["T-2"]))

    [(_, kwargs)] = phases.of("implementador")
    assert "`dispatcher/queue.py`" in kwargs["note"]


def test_the_implementador_may_block_back_on_a_moved_base(cfg, monkeypatch, cycle):
    _task(cfg, "T-2", "`dispatcher/`")
    cycle["move"] = core.BaseMove(changed=("dispatcher/queue.py",), merged_tasks=("T-1",))
    phases = _Phases(implementador_blocks=True)
    monkeypatch.setattr(core, "run_phase", phases)
    scheduler = _scheduler(cfg, ["T-2"])

    outcome = core.run_task_cycle(cfg, "T-2", "proj", scheduler=scheduler)

    assert outcome is core.CycleOutcome.BLOCKED
    assert phases.of("revisor") == []
    assert scheduler.windows[-1] == ("T-2", "leave")


def test_without_a_scheduler_the_cycle_never_merges_at_a_window(cfg, monkeypatch, cycle):
    _task(cfg, "T-2", "`dispatcher/`")

    def refuse(*args):
        raise AssertionError("a serial cycle has no write window")

    monkeypatch.setattr(core, "merge_base_at_write_window", refuse)
    phases = _Phases()
    monkeypatch.setattr(core, "run_phase", phases)

    core.run_task_cycle(cfg, "T-2", "proj")

    [(_, kwargs)] = phases.of("implementador")
    assert "note" not in kwargs


def test_two_cycles_that_need_a_map_map_once(cfg, monkeypatch, cycle):
    _task(cfg, "T-1", "`a.py`")
    _task(cfg, "T-2", "`b.py`")
    # The map lands on the first task's branch, so the index stays missing
    # for the second; it still does not map again.
    cycle["needs_mapping"] = True
    second_waiting = threading.Event()

    def mapper_wait():
        # Hold the mapping until the other cycle is at the mapping too.
        second_waiting.wait(timeout=0.5)

    phases = _Phases(mapper_wait=mapper_wait)
    monkeypatch.setattr(core, "run_phase", phases)
    scheduler = _scheduler(cfg, ["T-1", "T-2"])

    threads = [
        threading.Thread(target=core.run_task_cycle, args=(cfg, t, "proj"),
                         kwargs={"scheduler": scheduler})
        for t in ("T-1", "T-2")
    ]
    for t in threads:
        t.start()
    time.sleep(0.1)
    second_waiting.set()
    for t in threads:
        t.join(timeout=10)

    assert len(phases.of(core.project_docs.MAPPER_ROLE)) == 1
    assert len(phases.of("arquitecto")) == 2


# --- The CLI -----------------------------------------------------------------


def test_cli_runs_a_parallel_queue_up_to_the_accounts(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path, accounts=[
        AccountConfig(name="cuenta1", container="agent-cuenta1"),
        AccountConfig(name="cuenta2", container="agent-cuenta2"),
    ])
    _task(cfg, "T-1", "`a.py`")
    _task(cfg, "T-2", "`b.py`")
    fake = ScheduledCycle(cfg, arquitecto_barrier=threading.Barrier(2))
    monkeypatch.setattr(core, "run_task_cycle", fake)
    config = tmp_path / "config.yaml"
    config.write_text("")
    monkeypatch.setattr(cli_mod, "load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(
        "sys.argv",
        ["dispatch", "--config", str(config), "run-queue", "--project", "proj",
         "--limit", "10000", "--parallel", "2", "--yes", "--task-id", "T-1", "T-2"],
    )
    try:
        cli_mod.main()
    except SystemExit as exc:
        assert exc.code in (0, None)
    assert {t for t, what in fake.events if what == "leave"} == {"T-1", "T-2"}

    monkeypatch.setattr(
        "sys.argv",
        ["dispatch", "--config", str(config), "run-queue", "--project", "proj",
         "--limit", "10000", "--parallel", "3", "--yes", "--task-id", "T-1"],
    )
    with pytest.raises(SystemExit) as exit_:
        cli_mod.main()
    assert exit_.value.code == 2
