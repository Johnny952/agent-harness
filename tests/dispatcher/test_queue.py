"""The queue (docs/plans/autonomy.md A-5, docs/decisions.md ADR 79), over a stubbed cycle.

Nothing here starts a container: `run_task_cycle`, the account pick and the
cooling re-probe are faked, and the fake cycle writes usage lines the way a
real phase would, so the limit is read from the same log it is in production.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from dispatcher import cli as cli_mod
from dispatcher import context_transfer, docker_exec, tasks
from dispatcher import dispatcher as core
from dispatcher import queue as queue_mod
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
                 on_approve=None, may_start_phase=None):
        self.started.append(task_id)
        self.calls.append({"authorized": authorized, "slug": slug})
        plan = self.script[task_id]
        for spend in plan.get("phases", [100]):
            if may_start_phase is not None and not may_start_phase():
                return core.CycleOutcome.HELD
            _usage(cfg, task_id, spend)
        outcome = plan.get("outcome", core.CycleOutcome.FINISHED)
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

    queue = queue_mod.run(cfg, queue_mod.plan(cfg, "proj", ["T-1", "T-2", "T-3"], 10_000),
                          sleep=_no_sleep)

    assert fake.started == ["T-1", "T-3"]
    assert queue.status == queue_mod.Q_WAITING
    assert queue.entry("T-2").state == queue_mod.PENDING


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


def _cli(monkeypatch, tmp_path, *argv, stdin=""):
    config = tmp_path / "config.yaml"
    monkeypatch.setattr(cli_mod, "load_config", lambda path: _make_config(tmp_path))
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
                "--task-id", "T-1", "--limit", "500", stdin="n")

    out = capsys.readouterr().out
    assert code == cli_mod.EXIT_BLOCKED
    assert fake.started == []
    for shown in ("T-1", "Do T-1.", "arquitecto", "auditor", "Parallelism: 1", "Limit: 500",
                  "can pass it by that one phase"):
        assert shown in out


def test_cli_yes_runs_the_queue(tmp_path, accounts, monkeypatch, capsys):
    cfg = _make_config(tmp_path)
    _task(cfg, "T-1")
    _task(cfg, "T-2")
    fake = _install(monkeypatch, cfg, {"T-1": {}, "T-2": {}})

    code = _cli(monkeypatch, tmp_path, "run-queue", "--project", "proj",
                "--task-id", "T-1", "T-2", "--limit", "500", "--yes")

    assert code == 0
    assert fake.started == ["T-1", "T-2"]
    assert "done" in capsys.readouterr().out


def test_cli_refuses_parallel_above_one(tmp_path, accounts, monkeypatch):
    cfg = _make_config(tmp_path)
    _task(cfg, "T-1")
    code = _cli(monkeypatch, tmp_path, "run-queue", "--project", "proj",
                "--task-id", "T-1", "--limit", "500", "--parallel", "2", "--yes")
    assert code == 2


def test_cli_limit_and_push_refused_exit_codes(tmp_path, accounts, monkeypatch):
    cfg = _make_config(tmp_path)
    _task(cfg, "T-1")
    _task(cfg, "T-2")
    _install(monkeypatch, cfg, {"T-1": {"phases": [600]}, "T-2": {}})
    assert _cli(monkeypatch, tmp_path, "run-queue", "--project", "proj",
                "--task-id", "T-1", "T-2", "--limit", "500", "--yes") == cli_mod.EXIT_LIMIT

    _task(cfg, "T-3")
    _install(monkeypatch, cfg, {"T-3": {"refuse_push": True}})
    assert _cli(monkeypatch, tmp_path, "run-queue", "--project", "proj",
                "--task-id", "T-3", "--limit", "500", "--yes") == cli_mod.EXIT_NOT_PUSHED
