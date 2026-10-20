"""One cycle per project (docs/decisions.md ADR 200): the lock, the cycle that
takes it, the CLI that refuses on it, the queue that holds it.

`flock` is per open file description, so a second `ProjectLock.take` in this
process excludes the first exactly as another process would; the one test that
needs a real other process starts one.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import dispatcher.cli as cli_mod
from dispatcher import project_lock, tasks
from dispatcher import dispatcher as core
from dispatcher import queue as queue_mod
from dispatcher.project_lock import ProjectBusy, ProjectLock
from tests.dispatcher.test_cli import _write_config
from tests.dispatcher.test_dispatcher import _make_config
from tests.dispatcher.test_parallel_queue import _task as _parallel_task
from tests.dispatcher.test_queue import _task

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def cfg(tmp_path):
    return _make_config(tmp_path)


def test_a_second_take_is_busy_and_names_the_holder(cfg):
    held = ProjectLock.take(cfg.state_dir, "proj", "T-1", "run-task")
    with pytest.raises(ProjectBusy) as busy:
        ProjectLock.take(cfg.state_dir, "proj", "T-2", "worker")
    assert busy.value.holder["task_id"] == "T-1"
    assert "task T-1 holds it (run-task, pid" in str(busy.value)
    held.release()
    ProjectLock.take(cfg.state_dir, "proj", "T-2", "worker").release()


def test_release_empties_the_record_and_is_idempotent(cfg):
    held = ProjectLock.take(cfg.state_dir, "proj", "T-1", "run-task")
    path = project_lock.project_lock_path(cfg.state_dir, "proj")
    assert Path(path).read_text().strip()
    held.release()
    held.release()
    assert Path(path).exists() and Path(path).read_text() == ""


def test_a_slug_that_is_not_one_path_component_is_refused(cfg):
    with pytest.raises(ValueError):
        ProjectLock.take(cfg.state_dir, "../x", "T-1", "run-task")


def test_two_projects_hold_at_once(cfg):
    a = ProjectLock.take(cfg.state_dir, "alpha", "T-1", "run-task")
    b = ProjectLock.take(cfg.state_dir, "beta", "T-2", "run-task")
    a.release()
    b.release()


def test_a_process_that_dies_gives_the_lock_back(cfg):
    code = (
        "import sys, time\n"
        "from dispatcher.project_lock import ProjectLock\n"
        "ProjectLock.take(sys.argv[1], 'proj', 'T-9', 'run-task')\n"
        "print('held', flush=True)\n"
        "time.sleep(60)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code, cfg.state_dir], cwd=REPO,
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "held"
        with pytest.raises(ProjectBusy) as busy:
            ProjectLock.take(cfg.state_dir, "proj", "T-1", "run-task")
        assert busy.value.holder["task_id"] == "T-9"
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    ProjectLock.take(cfg.state_dir, "proj", "T-1", "run-task").release()


# ---- the cycle ----------------------------------------------------------------


def test_open_cycle_takes_the_lock_and_close_cycle_gives_it_back(cfg, monkeypatch):
    _task(cfg, "T-1")
    ctx = core.open_cycle(cfg, "T-1", "proj", via="run-task")
    assert ctx is not None and ctx.owns_project_lock
    with pytest.raises(ProjectBusy):
        ProjectLock.take(cfg.state_dir, "proj", "T-2", "worker")
    core.close_cycle(ctx, completed=False)
    ProjectLock.take(cfg.state_dir, "proj", "T-2", "worker").release()


def test_close_cycle_gives_it_back_on_a_foreign_task_lock(cfg):
    _task(cfg, "T-1")
    ctx = core.open_cycle(cfg, "T-1", "proj")
    ctx.foreign_lock = True
    core.close_cycle(ctx, completed=False)
    ProjectLock.take(cfg.state_dir, "proj", "T-2", "worker").release()


def test_a_lock_the_caller_holds_is_not_released_by_the_cycle(cfg):
    _task(cfg, "T-1")
    held = ProjectLock.take(cfg.state_dir, "proj", "T-1", "worker")
    ctx = core.open_cycle(cfg, "T-1", "proj", project_lock=held)
    assert ctx is not None and not ctx.owns_project_lock
    core.close_cycle(ctx, completed=False)
    with pytest.raises(ProjectBusy):
        ProjectLock.take(cfg.state_dir, "proj", "T-2", "worker")
    held.release()


def test_a_busy_project_writes_nothing_for_the_refused_task(cfg):
    _task(cfg, "T-1")
    ProjectLock.take(cfg.state_dir, "proj", "T-2", "worker")
    before = tasks.read_description(cfg.hive_tasks_dir, "T-1")
    with pytest.raises(ProjectBusy):
        core.open_cycle(cfg, "T-1", "proj", description="## Goal\n\nNew.\n\n## Done when\n\nYes.\n")
    assert tasks.read_description(cfg.hive_tasks_dir, "T-1") == before


def test_a_refused_open_does_not_take_the_lock(cfg):
    assert core.open_cycle(cfg, "T-404", "proj") is None
    ProjectLock.take(cfg.state_dir, "proj", "T-2", "worker").release()


def test_the_revision_round_takes_it_again(cfg):
    _task(cfg, "T-1")
    first = core.open_cycle(cfg, "T-1", "proj", via="run-task")
    core.close_cycle(first, completed=False)  # the task reached review
    second = core.open_cycle(cfg, "T-1", "proj", via="run-phase")
    assert second is not None and second.owns_project_lock
    with pytest.raises(ProjectBusy) as busy:
        ProjectLock.take(cfg.state_dir, "proj", "T-2", "worker")
    assert busy.value.holder["via"] == "run-phase"
    core.close_cycle(second, completed=False)


def test_run_task_cycle_holds_the_project_across_its_phases(cfg, monkeypatch):
    _task(cfg, "T-1")
    seen = []

    def phase(ctx, role, *a, **kw):
        try:
            ProjectLock.take(cfg.state_dir, "proj", "T-2", "worker")
            seen.append("free")
        except ProjectBusy:
            seen.append("busy")
        return None

    monkeypatch.setattr(core, "run_phase", phase)
    monkeypatch.setattr(core, "update_project_branch", lambda cfg, slug: None)
    monkeypatch.setattr(core, "_needs_mapping", lambda cfg, slug: False)
    core.run_task_cycle(cfg, "T-1", "proj")
    assert seen == ["busy"]
    ProjectLock.take(cfg.state_dir, "proj", "T-2", "worker").release()


# ---- the CLI ------------------------------------------------------------------


def test_cli_run_task_against_a_held_project_exits_75(tmp_path, capsys, monkeypatch):
    cfg = _make_config(tmp_path)
    _task(cfg, "T-1")
    holder = ProjectLock.take(cfg.state_dir, "proj", "T-9", "worker")
    monkeypatch.setattr(sys, "argv", [
        "ia-harness-dispatcher", "--config", str(_write_config(tmp_path)),
        "run-task", "--task-id", "T-1", "--project", "proj",
    ])
    with pytest.raises(SystemExit) as exit_:
        cli_mod.main()
    assert exit_.value.code == cli_mod.EXIT_HELD
    lines = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert len(lines) == 1
    assert "project proj is busy: task T-9 holds it (worker" in lines[0]
    assert lines[0].endswith("T-1 was not started")
    holder.release()


# ---- the queue ----------------------------------------------------------------


@pytest.fixture
def accounts(monkeypatch):
    monkeypatch.setattr(core, "pick_idle_account", lambda cfg, exclude=None, role=None: "cuenta1")
    monkeypatch.setattr(core, "_recheck_cooling_accounts", lambda cfg: [])


@pytest.mark.parametrize("parallel", [1, 2])
def test_a_queue_holds_the_project_and_hands_its_lock_to_every_cycle(
    cfg, accounts, monkeypatch, parallel,
):
    for task_id in ("T-1", "T-2"):
        _parallel_task(cfg, task_id, f"`dir{task_id}/`")
    seen: list = []
    # Both cycles must be inside at once under --parallel 2: the lock they
    # share does not serialise them back to one.
    together = threading.Barrier(2, timeout=5) if parallel == 2 else None

    def run_task_cycle(cfg_, task_id, slug, project_lock=None, **kw):
        seen.append(project_lock)
        try:
            ProjectLock.take(cfg.state_dir, slug, "outside", "worker")
            seen.append("outsider got in")
        except ProjectBusy:
            pass
        if together is not None:
            together.wait()
        tasks.mark_merged(cfg.hive_tasks_dir, task_id, "main")
        return core.CycleOutcome.FINISHED

    monkeypatch.setattr(core, "run_task_cycle", run_task_cycle)
    queue = queue_mod.plan(cfg, "proj", ["T-1", "T-2"], limit=10_000)
    queue.parallel = parallel
    queue = queue_mod.run(cfg, queue, sleep=lambda s: None)
    assert queue.status == queue_mod.Q_DONE
    assert len(seen) == 2 and seen[0] is seen[1] and isinstance(seen[0], ProjectLock)
    ProjectLock.take(cfg.state_dir, "proj", "T-3", "worker").release()  # given back


def test_a_queue_waits_for_a_busy_project(cfg):
    held = ProjectLock.take(cfg.state_dir, "proj", "T-9", "run-task")
    queue = SimpleNamespace(queue_id="q", project="proj")
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        held.release()

    lock = queue_mod._take_project(cfg, queue, sleep)
    assert sleeps == [queue_mod.WAIT_POLL_SECONDS]
    lock.release()
