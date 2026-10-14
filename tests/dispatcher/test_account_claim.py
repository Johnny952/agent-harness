"""Taking an account is atomic across processes (`docs/decisions.md` ADR 71)."""
import fcntl
import os
import threading
import time
from pathlib import Path

import pytest

import dispatcher.dispatcher as dispatcher_mod
import dispatcher.state_machine as sm
from dispatcher.config import AccountConfig
from dispatcher.docker_exec import ClaudeResult
from dispatcher.state_machine import AccountState, get_state

from tests.dispatcher.test_dispatcher import _make_config, fake_git  # noqa: F401

_HEALTHY = (
    "Current session: 10% used · resets later\n"
    "Current week (all models): 10% used · resets later"
)


# --- state_machine ---------------------------------------------------------


def test_claim_takes_a_missing_account(tmp_path: Path) -> None:
    d = str(tmp_path)
    assert sm.claim(d, "cuenta1", "T-1") is True
    assert get_state(d, "cuenta1") == AccountState.BUSY
    assert sm.get_current_task(d, "cuenta1") == "T-1"
    assert sm.get_busy_since(d, "cuenta1") is not None


@pytest.mark.parametrize(
    "held", [AccountState.BUSY, AccountState.PRE_COOLDOWN, AccountState.COOLING_DOWN],
)
def test_claim_refuses_an_account_that_is_not_idle(tmp_path: Path, held) -> None:
    d = str(tmp_path)
    sm.set_state(d, "cuenta1", held, current_task_id="T-0")
    before = (tmp_path / "cuenta1.json").read_text()

    assert sm.claim(d, "cuenta1", "T-1") is False
    assert (tmp_path / "cuenta1.json").read_text() == before


def test_claim_keeps_rate_limit_and_probe(tmp_path: Path) -> None:
    d = str(tmp_path)
    sm.record_rate_limit(d, "cuenta1", at=5.0)
    probe = {k: 1 for k in sm.PROBE_KEYS}
    sm.record_probe(d, "cuenta1", probe)

    assert sm.claim(d, "cuenta1", "T-1") is True
    assert sm.get_rate_limited_at(d, "cuenta1") == 5.0
    assert sm.get_last_probe(d, "cuenta1") == probe


def test_set_state_only_from_mismatch_writes_nothing(tmp_path: Path) -> None:
    d = str(tmp_path)
    sm.set_state(d, "cuenta1", AccountState.BUSY, current_task_id="T-0")

    ok = sm.set_state(d, "cuenta1", AccountState.IDLE, only_from={AccountState.COOLING_DOWN})

    assert ok is False
    assert get_state(d, "cuenta1") == AccountState.BUSY


def test_forget_rate_limit_drops_it_and_keeps_the_probe(tmp_path: Path) -> None:
    d = str(tmp_path)
    sm.record_rate_limit(d, "cuenta1", at=5.0)
    probe = {k: 1 for k in sm.PROBE_KEYS}
    sm.record_probe(d, "cuenta1", probe)

    assert sm.set_state(d, "cuenta1", AccountState.IDLE, forget_rate_limit=True) is True
    assert sm.get_rate_limited_at(d, "cuenta1") is None
    assert sm.get_last_probe(d, "cuenta1") == probe


_WRITERS = {
    "set_state": lambda d: sm.set_state(d, "cuenta1", AccountState.BUSY, "T-1"),
    "claim": lambda d: sm.claim(d, "cuenta1", "T-1"),
    "record_rate_limit": lambda d: sm.record_rate_limit(d, "cuenta1", at=9.0),
    "clear_rate_limit": lambda d: sm.clear_rate_limit(d, "cuenta1"),
    "record_probe": lambda d: sm.record_probe(d, "cuenta1", {k: 1 for k in sm.PROBE_KEYS}),
}


@pytest.mark.parametrize("name", sorted(_WRITERS))
def test_every_writer_waits_on_the_lock(tmp_path: Path, name: str) -> None:
    d = str(tmp_path)
    sm.record_rate_limit(d, "cuenta1", at=1.0)  # so clear_rate_limit has work
    before = (tmp_path / "cuenta1.json").read_text()

    t = threading.Thread(target=_WRITERS[name], args=(d,))
    with sm._locked(d):
        t.start()
        t.join(0.3)
        assert t.is_alive()
        assert (tmp_path / "cuenta1.json").read_text() == before
    t.join(5)

    assert not t.is_alive()
    assert (tmp_path / "cuenta1.json").read_text() != before


def test_lock_file_is_not_an_account_file(tmp_path: Path) -> None:
    d = str(tmp_path)
    sm.claim(d, "cuenta1", "T-1")
    assert (tmp_path / sm.LOCK_NAME).exists()
    assert not sm.LOCK_NAME.endswith(".json")
    accounts = [AccountConfig(name="cuenta1", container="c1")]
    assert sm.list_idle_accounts(d, accounts) == []


# --- dispatch_phase --------------------------------------------------------


def _install(monkeypatch, on_phase):
    """Fake the container side: `/usage` is healthy, a phase calls `on_phase`."""
    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, **kwargs):
        if "usage" in prompt.lower():
            return ClaudeResult(session_id=None, result_text=_HEALTHY, raw={})
        on_phase(container)
        return ClaudeResult(session_id="s", result_text="done", raw={"is_error": False})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/w/{task_id}",
    )
    monkeypatch.setattr(
        dispatcher_mod, "_resume_missing_handoff",
        lambda cfg, container, workdir, role, result, *a: result,
    )


def _two_accounts(tmp_path, n=2):
    accounts = [AccountConfig(name=f"cuenta{i}", container=f"agent-cuenta{i}") for i in range(1, n + 1)]
    return _make_config(tmp_path, accounts=accounts)


def _run_two(cfg):
    results = {}

    def go(task):
        results[task] = dispatcher_mod.dispatch_phase(cfg, task, "proj", "arquitecto", "p")

    threads = [threading.Thread(target=go, args=(t,)) for t in ("T-A", "T-B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)
        assert not t.is_alive()
    return results


def _barrier_after_first_pick(monkeypatch):
    barrier = threading.Barrier(2, timeout=5)
    real = dispatcher_mod.state_machine.list_idle_accounts
    seen = threading.local()

    def list_idle(state_dir, accounts):
        names = real(state_dir, accounts)
        if not getattr(seen, "done", False):
            seen.done = True
            barrier.wait()  # both processes have read the same IDLE list
        return names

    monkeypatch.setattr(dispatcher_mod.state_machine, "list_idle_accounts", list_idle)


def test_racing_for_one_account_exactly_one_wins(tmp_path, monkeypatch, fake_git) -> None:
    cfg = _two_accounts(tmp_path)
    phases = []
    lock = threading.Lock()

    def on_phase(container):
        with lock:
            phases.append(container)

    _install(monkeypatch, on_phase)
    _barrier_after_first_pick(monkeypatch)

    results = _run_two(cfg)

    assert all(r.success for r in results.values())
    assert sorted(phases) == ["agent-cuenta1", "agent-cuenta2"]
    assert {r.account for r in results.values()} == {"cuenta1", "cuenta2"}


def test_loser_with_no_other_account_gets_none(tmp_path, monkeypatch, fake_git) -> None:
    cfg = _two_accounts(tmp_path, n=1)
    phases = []
    release = threading.Event()

    def on_phase(container):
        phases.append(container)
        release.wait(5)

    _install(monkeypatch, on_phase)
    _barrier_after_first_pick(monkeypatch)

    results = {}

    def go(task):
        results[task] = dispatcher_mod.dispatch_phase(cfg, task, "proj", "arquitecto", "p")
        release.set()

    threads = [threading.Thread(target=go, args=(t,)) for t in ("T-A", "T-B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)
        assert not t.is_alive()

    assert phases == ["agent-cuenta1"]
    assert sorted(r.no_account for r in results.values()) == [False, True]


def test_two_accounts_run_in_parallel_and_the_lock_is_free(tmp_path, monkeypatch, fake_git) -> None:
    cfg = _two_accounts(tmp_path)
    barrier = threading.Barrier(2, timeout=5)

    def on_phase(container):
        # The other task may be claiming right now and holds the lock for a
        # moment; a lock still held after a second is one left held across a
        # phase, which is what this asserts against.
        fd = os.open(os.path.join(cfg.state_dir, sm.LOCK_NAME), os.O_RDWR)
        try:
            deadline = time.monotonic() + 1.0
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.005)
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        barrier.wait()  # both phases are inside `claude` at once

    _install(monkeypatch, on_phase)
    results = _run_two(cfg)

    assert len(results) == 2
    assert all(r.success for r in results.values())


def test_a_refused_probe_parks_the_claimed_account(tmp_path, monkeypatch, fake_git) -> None:
    cfg = _make_config(tmp_path)
    seen = []

    def fake_exec_claude(container, workdir, prompt, **kwargs):
        seen.append(get_state(cfg.state_dir, "cuenta1"))
        return ClaudeResult(
            session_id=None,
            result_text=(
                "Current session: 99% used · resets later\n"
                "Current week (all models): 10% used · resets later"
            ),
            raw={},
        )

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)

    result = dispatcher_mod.dispatch_phase(cfg, "T-A", "proj", "arquitecto", "p")

    assert seen[0] == AccountState.BUSY  # claimed before the probe
    assert result.no_account is True
    assert get_state(cfg.state_dir, "cuenta1") in (
        AccountState.PRE_COOLDOWN, AccountState.COOLING_DOWN,
    )


def test_a_probe_that_raises_leaves_the_account_idle(tmp_path, monkeypatch, fake_git) -> None:
    cfg = _make_config(tmp_path)

    def boom(cfg_, account):
        raise RuntimeError("probe blew up")

    monkeypatch.setattr(dispatcher_mod, "check_quota_ok", boom)

    with pytest.raises(RuntimeError):
        dispatcher_mod.dispatch_phase(cfg, "T-A", "proj", "arquitecto", "p")

    assert get_state(cfg.state_dir, "cuenta1") == AccountState.IDLE


def test_recheck_does_not_undo_a_claim_made_during_the_probe(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    sm.set_state(cfg.state_dir, "cuenta1", AccountState.COOLING_DOWN)

    def fake_exec_claude(container, workdir, prompt, **kwargs):
        # another process takes the account while this one is probing it
        sm.set_state(cfg.state_dir, "cuenta1", AccountState.IDLE)
        assert sm.claim(cfg.state_dir, "cuenta1", "T-other") is True
        return ClaudeResult(session_id=None, result_text=_HEALTHY, raw={})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)

    recovered = dispatcher_mod._recheck_cooling_accounts(cfg)

    assert recovered == []
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.BUSY
    assert sm.get_current_task(cfg.state_dir, "cuenta1") == "T-other"
