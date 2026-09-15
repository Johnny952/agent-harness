import datetime as dt
import time

import pytest

import dispatcher.dispatcher as dispatcher_mod
from dispatcher.config import AccountConfig, Config
from dispatcher.context_transfer import acquire_lock, is_lock_expired, read_task_file, task_file_path, write_task_file
from dispatcher.docker_exec import ClaudeResult
from dispatcher.state_machine import AccountState, get_state, set_state


class _FakeKanban:
    def __init__(self):
        self.statuses = []

    def update_task_status(self, task_id, status):
        self.statuses.append((task_id, status))


def _make_config(tmp_path, **overrides):
    defaults = dict(
        accounts=[AccountConfig(name="cuenta1", container="agent-cuenta1")],
        quota_threshold_pct=90,
        heartbeat_ttl_seconds=120,
        heartbeat_interval_seconds=1,
        projects_root=str(tmp_path / "projects"),
        hive_tasks_dir=str(tmp_path / "hive"),
        state_dir=str(tmp_path / "state"),
        vibe_kanban_mcp_url="http://127.0.0.1:9100/sse",
        collector_url="http://127.0.0.1:8787",
        default_model="opus",
        max_revision_rounds=3,
        escalate_effort_after_round=2,
        escalated_effort="high",
    )
    defaults.update(overrides)
    return Config(**defaults)


def test_dispatch_phase_success(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None):
        if "usage" in prompt.lower():
            return ClaudeResult(
                session_id=None,
                result_text=(
                    "Current session: 10% used · resets later\n"
                    "Current week (all models): 10% used · resets later"
                ),
                raw={},
            )
        return ClaudeResult(session_id="sess-1", result_text="phase done", raw={"is_error": False})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    assert result.success is True
    assert result.account == "cuenta1"
    assert result.session_id == "sess-1"
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.IDLE


def test_dispatch_phase_all_accounts_over_quota(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None):
        return ClaudeResult(
            session_id=None,
            result_text=(
                "Current session: 95% used · resets later\n"
                "Current week (all models): 20% used · resets later"
            ),
            raw={},
        )

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    assert result.success is False
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.PRE_COOLDOWN


def test_reap_expired_locks_releases_stale_owner(tmp_path) -> None:
    cfg = _make_config(tmp_path)
    acquire_lock(cfg.hive_tasks_dir, "task-1", owner="cuenta1")
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert not is_lock_expired(task, cfg.heartbeat_ttl_seconds)

    import datetime as dt
    from dispatcher.context_transfer import write_task_file
    task.heartbeat = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=999)).isoformat()
    write_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"), task)

    reaped = dispatcher_mod.reap_expired_locks(cfg)

    assert reaped == ["task-1"]
    refreshed = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert refreshed.owner is None


def test_dispatch_phase_exception_during_busy_window_leaves_account_idle(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None):
        return ClaudeResult(
            session_id=None,
            result_text=(
                "Current session: 10% used · resets later\n"
                "Current week (all models): 10% used · resets later"
            ),
            raw={},
        )

    def raising_create_worktree(container, projects_root, slug, task_id, role):
        raise RuntimeError("boom")

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(dispatcher_mod.docker_exec, "create_worktree", raising_create_worktree)

    with pytest.raises(RuntimeError):
        dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    assert get_state(cfg.state_dir, "cuenta1") == AccountState.IDLE


def test_dispatch_phase_recovers_account_from_cooling_down_via_recheck(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    set_state(cfg.state_dir, "cuenta1", AccountState.COOLING_DOWN)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None):
        if "usage" in prompt.lower():
            return ClaudeResult(
                session_id=None,
                result_text=(
                    "Current session: 5% used · resets later\n"
                    "Current week (all models): 5% used · resets later"
                ),
                raw={},
            )
        return ClaudeResult(session_id="sess-2", result_text="phase done", raw={"is_error": False})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    assert result.success is True
    assert result.account == "cuenta1"
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.IDLE


def test_dispatch_phase_returns_failure_when_exec_crashes_with_empty_raw(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None):
        if "usage" in prompt.lower():
            return ClaudeResult(
                session_id=None,
                result_text=(
                    "Current session: 10% used · resets later\n"
                    "Current week (all models): 10% used · resets later"
                ),
                raw={},
            )
        return ClaudeResult(session_id=None, result_text="", raw={})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    assert result.success is False
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.IDLE


def test_heartbeat_loop_survives_transient_refresh_exception(tmp_path, monkeypatch) -> None:
    calls = []

    def flaky_refresh_heartbeat(hive_dir, task_id):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("transient")

    monkeypatch.setattr(dispatcher_mod.context_transfer, "refresh_heartbeat", flaky_refresh_heartbeat)

    with dispatcher_mod._HeartbeatLoop(str(tmp_path), "task-1", interval_seconds=0.05) as loop:
        time.sleep(0.3)
        assert loop._thread.is_alive()

    assert len(calls) >= 2


def test_run_task_cycle_reaps_expired_locks_before_dispatching(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    acquire_lock(cfg.hive_tasks_dir, "task-1", owner="cuenta1")
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    task.heartbeat = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=999)).isoformat()
    write_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"), task)

    reap_calls = []
    original_reap = dispatcher_mod.reap_expired_locks

    def spying_reap(cfg_arg):
        reap_calls.append(cfg_arg)
        return original_reap(cfg_arg)

    monkeypatch.setattr(dispatcher_mod, "reap_expired_locks", spying_reap)
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase",
        lambda *a, **kw: dispatcher_mod.DispatchResult(success=False, session_id=None, result_text="stop", account=""),
    )

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban)

    assert len(reap_calls) == 1
    assert ("task-1", "blocked") in kanban.statuses


def test_run_task_cycle_prompt_references_task_file(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    captured_prompts = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None):
        captured_prompts.append(prompt)
        return dispatcher_mod.DispatchResult(success=False, session_id=None, result_text="stop", account="")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban)

    expected_path = task_file_path(cfg.hive_tasks_dir, "task-1")
    assert captured_prompts
    assert expected_path in captured_prompts[0]


def test_revisor_approved_matches_verdict_line() -> None:
    assert dispatcher_mod.revisor_approved("looks good\nVERDICT: APPROVED") is True
    assert dispatcher_mod.revisor_approved("needs work\nVERDICT: CHANGES_REQUESTED") is False


def test_revisor_approved_fails_closed_on_missing_or_malformed_verdict() -> None:
    assert dispatcher_mod.revisor_approved("") is False
    assert dispatcher_mod.revisor_approved("no verdict line here") is False
    assert dispatcher_mod.revisor_approved("VERDICT: approved-ish") is False


def _dispatch_call_kwargs(cfg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None):
    return dict(role=role, prompt=prompt, model=model, effort=effort)


def test_run_task_cycle_approves_on_first_round(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    calls = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None):
        calls.append(_dispatch_call_kwargs(cfg_arg, task_id, slug, role, prompt, resume_session_id, model, effort))
        if role == "revisor":
            return dispatcher_mod.DispatchResult(
                success=True, session_id=None, result_text="VERDICT: APPROVED", account="cuenta1",
            )
        return dispatcher_mod.DispatchResult(success=True, session_id=None, result_text="ok", account="cuenta1")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban)

    roles = [c["role"] for c in calls]
    assert roles == ["arquitecto", "implementador", "revisor", "auditor"]
    assert all(c["effort"] is None for c in calls)
    assert all(c["model"] == "opus" for c in calls)
    assert ("task-1", "done") in kanban.statuses


def test_run_task_cycle_escalates_effort_after_configured_round(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path, max_revision_rounds=3, escalate_effort_after_round=2, escalated_effort="high")
    calls = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None):
        calls.append(_dispatch_call_kwargs(cfg_arg, task_id, slug, role, prompt, resume_session_id, model, effort))
        if role == "revisor" and "round 3" in prompt:
            return dispatcher_mod.DispatchResult(
                success=True, session_id=None, result_text="VERDICT: APPROVED", account="cuenta1",
            )
        if role == "revisor":
            return dispatcher_mod.DispatchResult(
                success=True, session_id=None, result_text="VERDICT: CHANGES_REQUESTED", account="cuenta1",
            )
        return dispatcher_mod.DispatchResult(success=True, session_id=None, result_text="ok", account="cuenta1")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban)

    early_rounds = [
        c for c in calls
        if c["role"] in ("implementador", "revisor") and ("round 1" in c["prompt"] or "round 2" in c["prompt"])
    ]
    round3 = [c for c in calls if c["role"] in ("implementador", "revisor") and "round 3" in c["prompt"]]
    assert early_rounds and all(c["effort"] is None for c in early_rounds)
    assert round3, "expected round 3 to run"
    assert all(c["effort"] == "high" for c in round3)
    assert ("task-1", "done") in kanban.statuses


def test_run_task_cycle_blocks_when_revision_rounds_exhausted(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path, max_revision_rounds=2)
    calls = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None):
        calls.append(role)
        if role == "revisor":
            return dispatcher_mod.DispatchResult(
                success=True, session_id=None, result_text="VERDICT: CHANGES_REQUESTED", account="cuenta1",
            )
        return dispatcher_mod.DispatchResult(success=True, session_id=None, result_text="ok", account="cuenta1")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban)

    assert calls.count("revisor") == 2
    assert "auditor" not in calls
    assert ("task-1", "blocked") in kanban.statuses
    assert ("task-1", "done") not in kanban.statuses
