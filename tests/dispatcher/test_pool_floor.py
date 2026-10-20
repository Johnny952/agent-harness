"""The pool's floor (docs/plans/board-service.md, docs/plans/projects.md step 3).

A `start` is not claimed while the primary account's free share, 100 minus the
tighter of `/usage`'s two windows, is under the start's floor (5% unless the
start names one). `/usage` is stubbed through `docker_exec.exec_claude`; nothing
here runs the real one.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

import dispatcher.dispatcher as dispatcher_mod
from dispatcher import actions, state_machine, worker as worker_mod
from dispatcher.config import AccountConfig
from dispatcher.docker_exec import ClaudeResult
from dispatcher.state_machine import AccountState, get_state
from tests.dispatcher.test_dispatcher import _make_config
from tests.dispatcher.test_worker import (  # noqa: F401 (fixtures)
    accounts, clock, cycle, start, where,
)


def _usage(session: int, week: int) -> str:
    return (
        f"Current session: {session}% used · resets later\n"
        f"Current week (all models): {week}% used · resets later"
    )


class Usage:
    """A stubbed `/usage`: what each container answers, and who was asked."""

    def __init__(self, monkeypatch):
        self.answers: dict[str, object] = {}
        self.asked: list[str] = []
        monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", self)

    def __call__(self, container, workdir, prompt, timeout_seconds=None, **kwargs):
        assert prompt == "/usage"
        self.asked.append(container)
        answer = self.answers[container]
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, ClaudeResult):
            return answer
        return ClaudeResult(session_id=None, result_text=answer, raw={})


@pytest.fixture
def usage(monkeypatch):
    return Usage(monkeypatch)


@pytest.fixture
def cfg(tmp_path):
    # The primary is cuenta2 here, so a reader that assumed cuenta1 would fail.
    return _make_config(
        tmp_path,
        accounts=[
            AccountConfig(name="cuenta1", container="agent-cuenta1"),
            AccountConfig(name="cuenta2", container="agent-cuenta2", is_primary=True),
        ],
        primary_account="cuenta2",
    )


# ---- the reading ------------------------------------------------------------


def test_the_free_share_is_the_primarys_tighter_window(cfg, usage):
    usage.answers["agent-cuenta2"] = _usage(session=30, week=88)
    assert dispatcher_mod.primary_free_pct(cfg) == 12
    usage.answers["agent-cuenta2"] = _usage(session=97, week=10)
    assert dispatcher_mod.primary_free_pct(cfg) == 3
    assert usage.asked == ["agent-cuenta2", "agent-cuenta2"]


def test_no_primary_means_no_reading(tmp_path, usage):
    cfg = _make_config(tmp_path)  # no primary_account
    assert dispatcher_mod.primary_free_pct(cfg) is None
    assert usage.asked == []


def test_an_unreadable_usage_is_no_reading_and_logged(cfg, usage, caplog):
    usage.answers["agent-cuenta2"] = "not the /usage format at all"
    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        assert dispatcher_mod.primary_free_pct(cfg) is None
    assert "cuenta2" in caplog.text
    usage.answers["agent-cuenta2"] = RuntimeError("docker is gone")
    assert dispatcher_mod.primary_free_pct(cfg) is None


def test_an_auth_refusal_is_no_reading_and_parks_nothing(cfg, usage):
    usage.answers["agent-cuenta2"] = ClaudeResult(
        session_id=None, result_text="Invalid API key", raw={"is_error": True, "api_error_status": 401},
    )
    assert dispatcher_mod.primary_free_pct(cfg) is None
    assert get_state(cfg.state_dir, "cuenta2") == AccountState.IDLE


def test_a_rate_limit_refusal_reads_as_nothing_free(cfg, usage):
    usage.answers["agent-cuenta2"] = ClaudeResult(
        session_id=None, result_text="rate limit reached", raw={"is_error": True},
    )
    assert dispatcher_mod.primary_free_pct(cfg) == 0
    # The floor reads; the picker is what parks.
    assert get_state(cfg.state_dir, "cuenta2") == AccountState.IDLE
    assert state_machine.get_rate_limited_at(cfg.state_dir, "cuenta2") is None


def test_the_check_quota_gate_still_reads_through_the_same_probe(cfg, usage):
    usage.answers["agent-cuenta2"] = _usage(session=10, week=10)
    assert dispatcher_mod.check_quota_ok(cfg, "cuenta2") is True
    usage.answers["agent-cuenta1"] = ClaudeResult(
        session_id=None, result_text="rate limit reached", raw={"is_error": True},
    )
    assert dispatcher_mod.check_quota_ok(cfg, "cuenta1") is False
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.COOLING_DOWN


# ---- the worker -------------------------------------------------------------


@pytest.fixture
def free(monkeypatch):
    """The primary's free share as the worker reads it; None is no reading."""
    state = {"pct": 100, "reads": 0}

    def read(cfg):
        state["reads"] += 1
        return state["pct"]

    monkeypatch.setattr(dispatcher_mod, "primary_free_pct", read)
    return state


def test_a_start_waits_for_the_pool_under_the_default_floor(cfg, cycle, accounts, clock, free, caplog):
    w = worker_mod.Worker(cfg, account_poll=60)
    rec = start(cfg, "T-1")
    free["pct"] = 4
    with caplog.at_level("INFO", logger=worker_mod.logger.name):
        w.tick()
    state, got = where(cfg, rec["id"])
    assert state == "pending" and got["waiting"] == "pool"
    assert cycle.calls == []
    assert "4%" in caplog.text and "5%" in caplog.text
    reads = free["reads"]
    w.tick()
    assert free["reads"] == reads  # paced: no second read within account_poll
    free["pct"] = 5
    clock["t"] += timedelta(seconds=61)
    w.tick()
    w.join()
    state, got = where(cfg, rec["id"])
    assert state == "done" and got["waiting"] is None
    assert cycle.calls == [("T-1", "proj")]


def test_a_start_names_its_own_floor(cfg, cycle, accounts, clock, free):
    from tests.dispatcher.test_queue import _task

    w = worker_mod.Worker(cfg, account_poll=0)
    _task(cfg, "T-1")
    _task(cfg, "T-2")
    free["pct"] = 30
    high = actions.create(cfg.state_dir, "start", project="proj", task_id="T-1",
                          params={"limit": None, "floor": 40})
    zero = actions.create(cfg.state_dir, "start", project="other", task_id="T-2",
                          params={"limit": None, "floor": 0})
    w.tick()
    w.join()
    assert where(cfg, high["id"])[1]["waiting"] == "pool"
    assert where(cfg, zero["id"])[1]["status"] == "done"


def test_no_reading_lets_the_start_through(cfg, cycle, accounts, clock, free):
    w = worker_mod.Worker(cfg, account_poll=0)
    rec = start(cfg, "T-1")
    free["pct"] = None
    w.tick()
    w.join()
    assert where(cfg, rec["id"])[1]["status"] == "done"


def test_the_account_is_looked_at_before_the_pool(cfg, cycle, accounts, clock, free):
    w = worker_mod.Worker(cfg, account_poll=0)
    rec = start(cfg, "T-1")
    accounts["open"] = False
    free["pct"] = 0
    w.tick()
    assert where(cfg, rec["id"])[1]["waiting"] == "account"
    assert free["reads"] == 0


def test_a_verdict_is_not_held_by_the_floor(cfg, accounts, clock, free, monkeypatch):
    from dispatcher import command_run
    from tests.dispatcher.test_worker import Verdict, verdict

    fake = Verdict()
    monkeypatch.setattr(command_run, "run_command_request", fake)
    w = worker_mod.Worker(cfg, account_poll=0)
    free["pct"] = 0
    rec = verdict(cfg)
    w.tick()
    w.join()
    assert where(cfg, rec["id"])[1]["status"] == "done"
    assert free["reads"] == 0


# ---- the record -------------------------------------------------------------


@pytest.mark.parametrize("floor", [0, 5, 100, None])
def test_a_start_takes_a_floor_from_0_to_100(cfg, floor):
    rec = actions.create(cfg.state_dir, "start", project="proj", task_id="T-1",
                         params={"limit": None, "floor": floor})
    assert rec["params"]["floor"] == floor


@pytest.mark.parametrize("floor", [-1, 101, 5.5, "5", True])
def test_a_floor_out_of_range_is_refused(cfg, floor):
    with pytest.raises(actions.ActionRefused) as err:
        actions.create(cfg.state_dir, "start", project="proj", task_id="T-1",
                       params={"limit": None, "floor": floor})
    assert err.value.status == 400
    assert "params.floor" in str(err.value)


def test_the_default_floor_is_5():
    assert actions.DEFAULT_POOL_FLOOR_PCT == 5
    assert actions.start_floor({"params": {"limit": None}}) == 5
    assert actions.start_floor({"params": {"limit": None, "floor": None}}) == 5
    assert actions.start_floor({"params": {"limit": None, "floor": 20}}) == 20


def test_an_epic_run_keeps_its_floor_and_its_starts_carry_it(cfg):
    records, run = actions.start_epic_run(
        cfg.state_dir, "T-9", "proj", ["T-1"], floor=20,
        offset=lambda tid: 0, spent=lambda r: 0,
    )
    assert run["floor"] == 20
    assert records[0]["params"] == {"limit": None, "epic": "T-9", "floor": 20}
    assert actions.read_epic_run(cfg.state_dir, "T-9")["floor"] == 20
    out = actions.follow_epic_run(
        cfg.state_dir, "T-9", lambda: ["T-2"], offset=lambda tid: 0, spent=lambda r: 0,
    )
    assert out["queued"][0]["params"] == {"limit": None, "epic": "T-9", "floor": 20}


def test_an_epic_run_without_a_floor_queues_starts_as_before(cfg):
    records, run = actions.start_epic_run(
        cfg.state_dir, "T-9", "proj", ["T-1"], offset=lambda tid: 0, spent=lambda r: 0,
    )
    assert run["floor"] is None
    assert records[0]["params"] == {"limit": None, "epic": "T-9"}


def test_an_epic_run_refuses_a_bad_floor(cfg):
    with pytest.raises(actions.ActionRefused) as err:
        actions.start_epic_run(
            cfg.state_dir, "T-9", "proj", ["T-1"], floor=101,
            offset=lambda tid: 0, spent=lambda r: 0,
        )
    assert err.value.status == 400
