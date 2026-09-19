import datetime as dt
import time

import pytest

import dispatcher.dispatcher as dispatcher_mod
from dispatcher.config import AccountConfig, Config
from dispatcher.context_transfer import (
    LockHeldError,
    acquire_lock,
    is_lock_expired,
    read_task_file,
    task_file_path,
    write_task_file,
)
from dispatcher.docker_exec import ClaudeResult
from dispatcher.state_machine import AccountState, get_state, set_state


# run_task_cycle refuses to dispatch a task with no description (it would
# spend four phases of quota on roles told nothing but an id), so every
# cycle test has to supply one.
_DESCRIPTION = "Add a /healthz endpoint that returns 200."


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
        phase_timeout_seconds=7200,
    )
    defaults.update(overrides)
    return Config(**defaults)


class _FakeGit:
    """Records the git/ownership calls dispatch_phase makes through docker_exec.

    Those three shell out to a real `docker exec`, so without this every
    dispatch test would go looking for a live container.
    """

    owner = "1000:1000"

    def __init__(self):
        self.commits = []
        self.restored = []

    def commit_worktree(self, container, workdir, message, author_name, author_email):
        self.commits.append(
            dict(
                container=container,
                workdir=workdir,
                message=message,
                author_name=author_name,
                author_email=author_email,
            )
        )
        return True

    def read_owner(self, container, path):
        return self.owner

    def restore_owner(self, container, path, owner):
        self.restored.append((path, owner))


@pytest.fixture(autouse=True)
def fake_git(monkeypatch):
    fake = _FakeGit()
    monkeypatch.setattr(dispatcher_mod.docker_exec, "commit_worktree", fake.commit_worktree)
    monkeypatch.setattr(dispatcher_mod.docker_exec, "read_owner", fake.read_owner)
    monkeypatch.setattr(dispatcher_mod.docker_exec, "restore_owner", fake.restore_owner)
    return fake


def test_dispatch_phase_success(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None):
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


def test_dispatch_phase_passes_configured_timeout_to_phase_exec(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path, phase_timeout_seconds=999)
    captured = {}

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None):
        if "usage" in prompt.lower():
            return ClaudeResult(
                session_id=None,
                result_text=(
                    "Current session: 10% used · resets later\n"
                    "Current week (all models): 10% used · resets later"
                ),
                raw={},
            )
        captured["timeout_seconds"] = timeout_seconds
        return ClaudeResult(session_id="sess-1", result_text="phase done", raw={"is_error": False})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    assert captured["timeout_seconds"] == 999


def test_check_quota_ok_probe_uses_fixed_usage_probe_timeout(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path, phase_timeout_seconds=999)
    captured = {}

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None):
        captured["timeout_seconds"] = timeout_seconds
        return ClaudeResult(
            session_id=None,
            result_text=(
                "Current session: 10% used · resets later\n"
                "Current week (all models): 10% used · resets later"
            ),
            raw={},
        )

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)

    dispatcher_mod.check_quota_ok(cfg, "cuenta1")

    assert captured["timeout_seconds"] == dispatcher_mod._USAGE_PROBE_TIMEOUT_SECONDS
    assert dispatcher_mod._USAGE_PROBE_TIMEOUT_SECONDS == 120


def test_dispatch_phase_all_accounts_over_quota(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None):
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


def test_check_quota_ok_returns_true_and_warns_when_usage_probe_format_drifts(tmp_path, monkeypatch, caplog) -> None:
    cfg = _make_config(tmp_path)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None):
        return ClaudeResult(session_id=None, result_text="not the /usage format we expect at all", raw={})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        ok = dispatcher_mod.check_quota_ok(cfg, "cuenta1")

    assert ok is True
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.IDLE
    assert "cuenta1" in caplog.text


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


def test_dispatch_phase_raises_lock_held_error_without_touching_worktree_when_task_owned_by_other(
    tmp_path, monkeypatch,
) -> None:
    cfg = _make_config(tmp_path)
    acquire_lock(cfg.hive_tasks_dir, "task-1", owner="otro")

    worktree_calls = []

    def spy_create_worktree(container, projects_root, slug, task_id, role):
        worktree_calls.append((container, projects_root, slug, task_id, role))
        return f"{projects_root}/{slug}/worktrees/{task_id}"

    monkeypatch.setattr(dispatcher_mod.docker_exec, "create_worktree", spy_create_worktree)

    with pytest.raises(LockHeldError):
        dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    # A task someone else holds must be refused before the worktree is ever
    # touched — see dispatch_phase's acquire_lock-before-create_worktree
    # ordering (design spec sec. 5, lock ownership enforcement).
    assert worktree_calls == []
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.IDLE
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert task.owner == "otro"


def test_dispatch_phase_takes_over_lock_with_no_heartbeat_via_ttl_pass_through(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    # heartbeat=None can only come from a hand-edited or legacy task file, not
    # a live dispatcher (acquire_lock always stamps a fresh heartbeat) —
    # reap_expired_locks/is_lock_expired both treat heartbeat=None as "not
    # expired", so the ttl_seconds pass-through into acquire_lock is the only
    # path that can ever take this lock over.
    write_task_file(
        task_file_path(cfg.hive_tasks_dir, "task-1"),
        dispatcher_mod.context_transfer.TaskFile(
            task_id="task-1", status="in_progress", owner="otro", depends_on=[], heartbeat=None, body="",
        ),
    )

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None):
        if "usage" in prompt.lower():
            return ClaudeResult(
                session_id=None,
                result_text=(
                    "Current session: 10% used · resets later\n"
                    "Current week (all models): 10% used · resets later"
                ),
                raw={},
            )
        return ClaudeResult(session_id="sess-3", result_text="phase done", raw={"is_error": False})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    assert result.success is True
    assert result.account == "cuenta1"
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert task.owner == "cuenta1"


def test_dispatch_phase_exception_during_busy_window_leaves_account_idle(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None):
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

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None):
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


def test_recheck_cooling_accounts_recovers_to_idle_when_usage_probe_raises(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    set_state(cfg.state_dir, "cuenta1", AccountState.COOLING_DOWN)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None):
        raise ValueError("bad /usage format")

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)

    recovered = dispatcher_mod._recheck_cooling_accounts(cfg)

    assert recovered == ["cuenta1"]
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.IDLE


def test_dispatch_phase_does_not_loop_forever_when_cooling_probe_raises_and_phase_rate_limits(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    set_state(cfg.state_dir, "cuenta1", AccountState.COOLING_DOWN)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None):
        if "usage" in prompt.lower():
            raise ValueError("bad /usage format")
        return ClaudeResult(session_id=None, result_text="rate limit reached", raw={"is_error": True})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    assert result.success is False
    assert result.result_text == "no accounts available"


def test_dispatch_phase_picks_untried_recovered_account_after_recheck(tmp_path, monkeypatch) -> None:
    # cuenta1 rate-limits and joins `tried`; the recheck that follows also
    # recovers cuenta1 (now COOLING_DOWN) alongside cuenta3, which was
    # already cooling from a prior call. Picking idle[0] without excluding
    # `tried` would hand the retry straight back to cuenta1 and immediately
    # fail closed instead of trying cuenta3. cuenta2 stays over-threshold
    # through the recheck, proving the pick isn't just "any recovered
    # account" either.
    cfg = _make_config(
        tmp_path,
        accounts=[
            AccountConfig(name="cuenta1", container="agent-cuenta1"),
            AccountConfig(name="cuenta2", container="agent-cuenta2"),
            AccountConfig(name="cuenta3", container="agent-cuenta3"),
        ],
    )
    set_state(cfg.state_dir, "cuenta2", AccountState.COOLING_DOWN)
    set_state(cfg.state_dir, "cuenta3", AccountState.COOLING_DOWN)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None):
        if "usage" in prompt.lower():
            if container == "agent-cuenta2":
                return ClaudeResult(
                    session_id=None,
                    result_text=(
                        "Current session: 95% used · resets later\n"
                        "Current week (all models): 95% used · resets later"
                    ),
                    raw={},
                )
            return ClaudeResult(
                session_id=None,
                result_text=(
                    "Current session: 5% used · resets later\n"
                    "Current week (all models): 5% used · resets later"
                ),
                raw={},
            )
        if container == "agent-cuenta1":
            return ClaudeResult(session_id="sess-a", result_text="usage limit reached", raw={"is_error": True})
        return ClaudeResult(session_id="sess-c", result_text="phase done", raw={"is_error": False})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    assert result.success is True
    assert result.account == "cuenta3"
    assert get_state(cfg.state_dir, "cuenta2") == AccountState.COOLING_DOWN


def test_dispatch_phase_returns_failure_when_exec_crashes_with_empty_raw(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None):
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
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert len(reap_calls) == 1
    assert ("task-1", "blocked") in kanban.statuses


def test_run_task_cycle_blocks_without_raising_when_task_locked_by_other_owner(
    tmp_path, monkeypatch, caplog,
) -> None:
    cfg = _make_config(tmp_path)
    acquire_lock(cfg.hive_tasks_dir, "task-1", owner="otro")

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None):
        return ClaudeResult(
            session_id=None,
            result_text=(
                "Current session: 10% used · resets later\n"
                "Current week (all models): 10% used · resets later"
            ),
            raw={},
        )

    worktree_calls = []

    def spy_create_worktree(container, projects_root, slug, task_id, role):
        worktree_calls.append((container, projects_root, slug, task_id, role))
        return f"{projects_root}/{slug}/worktrees/{task_id}"

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(dispatcher_mod.docker_exec, "create_worktree", spy_create_worktree)

    kanban = _FakeKanban()
    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert worktree_calls == []
    assert ("task-1", "blocked") in kanban.statuses
    assert "task-1" in caplog.text
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert task.owner == "otro"


def test_run_task_cycle_prompt_references_task_file(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    captured_prompts = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None):
        captured_prompts.append(prompt)
        return dispatcher_mod.DispatchResult(success=False, session_id=None, result_text="stop", account="")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    expected_path = task_file_path(cfg.hive_tasks_dir, "task-1")
    assert captured_prompts
    assert expected_path in captured_prompts[0]


def test_run_task_cycle_embeds_the_description_in_every_role_prompt(tmp_path, monkeypatch) -> None:
    """The roles used to get a role name, a task id and a file path — the
    arquitecto had nothing to plan from. The description goes in the prompt
    whole (never truncated: a cut ask misinforms) for every phase, not just
    the first, so a revisor or auditor can check the work against what was
    actually asked for."""
    cfg = _make_config(tmp_path, max_revision_rounds=1)
    captured = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None):
        captured.append((role, prompt))
        return dispatcher_mod.DispatchResult(
            success=True, session_id=None, result_text="VERDICT: APPROVED", account="cuenta1",
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    description = "Add a /healthz endpoint.\n\nIt must return 200 and no body."
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=description)

    assert [role for role, _ in captured] == ["arquitecto", "implementador", "revisor", "auditor"]
    for role, prompt in captured:
        assert description in prompt, f"{role}'s prompt does not carry the description"

    # The revisor's verdict instruction has to stay the last thing it reads,
    # or a description ending in prose could bury it.
    revisor_prompt = next(prompt for role, prompt in captured if role == "revisor")
    assert revisor_prompt.rstrip().endswith("if it needs another revision round.")


def test_run_task_cycle_seeds_the_description_into_the_task_file(tmp_path, monkeypatch) -> None:
    """Seeded once, then read from the file on a re-run: the roles open the
    task file for handoff context anyway, and a resume (`run-task` with no
    --description after a Ctrl+C) has to find the original ask there."""
    cfg = _make_config(tmp_path, max_revision_rounds=1)
    prompts = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None):
        prompts.append(prompt)
        return dispatcher_mod.DispatchResult(
            success=True, session_id=None, result_text="VERDICT: APPROVED", account="cuenta1",
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert task.description == _DESCRIPTION
    # handoff() appended four phase summaries to the body; the description
    # must not be one of them, or a re-run would stack copies of the ask.
    assert _DESCRIPTION not in task.body

    prompts.clear()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban())

    assert prompts, "the re-run should have dispatched using the stored description"
    assert all(_DESCRIPTION in prompt for prompt in prompts)


def test_run_task_cycle_blocks_without_dispatching_when_no_description_exists(
    tmp_path, monkeypatch, caplog,
) -> None:
    """No description anywhere (no argument, no task file) means the four
    phases can only produce noise at full quota cost, so nothing is
    dispatched at all and the task goes straight to blocked."""
    cfg = _make_config(tmp_path)
    calls = []

    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase",
        lambda *a, **kw: calls.append(a) or dispatcher_mod.DispatchResult(
            success=True, session_id=None, result_text="ok", account="cuenta1",
        ),
    )

    kanban = _FakeKanban()
    with caplog.at_level("ERROR", logger=dispatcher_mod.logger.name):
        dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban)

    assert calls == []
    assert ("task-1", "blocked") in kanban.statuses
    assert "description" in caplog.text


def test_run_task_cycle_blocks_when_the_stored_description_is_blank(tmp_path, monkeypatch) -> None:
    """A task file whose description is whitespace is as useless as none at
    all — the guard is on content, not on the key being present."""
    cfg = _make_config(tmp_path)
    write_task_file(
        task_file_path(cfg.hive_tasks_dir, "task-1"),
        dispatcher_mod.context_transfer.TaskFile(
            task_id="task-1", status="pending", owner=None, depends_on=[],
            heartbeat=None, body="", description="   \n",
        ),
    )
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", lambda *a, **kw: calls.append(a))

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban)

    assert calls == []
    assert ("task-1", "blocked") in kanban.statuses


def test_revisor_approved_matches_verdict_line() -> None:
    assert dispatcher_mod.revisor_approved("looks good\nVERDICT: APPROVED") is True
    assert dispatcher_mod.revisor_approved("needs work\nVERDICT: CHANGES_REQUESTED") is False


def test_revisor_approved_fails_closed_on_missing_or_malformed_verdict() -> None:
    assert dispatcher_mod.revisor_approved("") is False
    assert dispatcher_mod.revisor_approved("no verdict line here") is False
    assert dispatcher_mod.revisor_approved("VERDICT: approved-ish") is False


def test_revisor_approved_true_with_trailing_blank_lines() -> None:
    assert dispatcher_mod.revisor_approved("looks good\nVERDICT: APPROVED\n\n\n") is True


def test_revisor_approved_true_with_markdown_emphasis() -> None:
    assert dispatcher_mod.revisor_approved("looks good\n**VERDICT: APPROVED**") is True


def test_revisor_approved_false_when_later_line_changes_verdict() -> None:
    assert dispatcher_mod.revisor_approved("VERDICT: APPROVED\nactually wait\nVERDICT: CHANGES_REQUESTED") is False


def test_revisor_approved_false_when_approved_is_not_the_verdict() -> None:
    assert dispatcher_mod.revisor_approved("Not VERDICT: APPROVED") is False


def test_revisor_approved_false_when_more_prose_follows_approved() -> None:
    assert dispatcher_mod.revisor_approved("VERDICT: APPROVED, pending a minor nit") is False


def test_revisor_approved_true_with_bold_label_only() -> None:
    assert dispatcher_mod.revisor_approved("looks good\n**VERDICT:** APPROVED") is True


def test_revisor_approved_true_with_trailing_code_fence() -> None:
    assert dispatcher_mod.revisor_approved("looks good\nVERDICT: APPROVED\n```") is True


def test_revisor_approved_true_with_blockquote() -> None:
    assert dispatcher_mod.revisor_approved("looks good\n> VERDICT: APPROVED") is True


def test_revisor_approved_true_with_underscore_emphasis() -> None:
    assert dispatcher_mod.revisor_approved("looks good\n__VERDICT: APPROVED__") is True


def test_revisor_approved_false_with_trailing_period() -> None:
    assert dispatcher_mod.revisor_approved("looks good\nVERDICT: APPROVED.") is False


def test_is_rate_limit_error_true_on_429_status_with_unrelated_text() -> None:
    result = ClaudeResult(session_id=None, result_text="something went wrong", raw={"is_error": True, "api_error_status": 429})
    assert dispatcher_mod.is_rate_limit_error(result) is True


def test_is_rate_limit_error_true_on_429_status_as_string() -> None:
    result = ClaudeResult(session_id=None, result_text="something went wrong", raw={"is_error": True, "api_error_status": "429"})
    assert dispatcher_mod.is_rate_limit_error(result) is True


@pytest.mark.parametrize(
    "phrase",
    ["rate limit", "rate_limit", "usage limit", "hit your limit"],
)
def test_is_rate_limit_error_true_on_known_phrases(phrase) -> None:
    result = ClaudeResult(session_id=None, result_text=f"Error: {phrase} exceeded", raw={"is_error": True})
    assert dispatcher_mod.is_rate_limit_error(result) is True


def test_is_rate_limit_error_false_on_context_limit_reached() -> None:
    result = ClaudeResult(session_id=None, result_text="Context limit reached", raw={"is_error": True})
    assert dispatcher_mod.is_rate_limit_error(result) is False


def test_is_rate_limit_error_false_on_bare_quota_mention() -> None:
    result = ClaudeResult(session_id=None, result_text="over quota for this project", raw={"is_error": True})
    assert dispatcher_mod.is_rate_limit_error(result) is False


def test_is_rate_limit_error_false_when_not_an_error_even_with_429() -> None:
    result = ClaudeResult(session_id=None, result_text="rate limit", raw={"is_error": False, "api_error_status": 429})
    assert dispatcher_mod.is_rate_limit_error(result) is False


def test_truncate_for_handoff_passthrough_when_short() -> None:
    text = "short result"
    assert dispatcher_mod._truncate_for_handoff(text, head=500, tail=1500) == text


def test_truncate_for_handoff_keeps_head_and_tail_with_accurate_count() -> None:
    text = "H" * 500 + "M" * 1000 + "T" * 1500
    truncated = dispatcher_mod._truncate_for_handoff(text, head=500, tail=1500)
    assert truncated.startswith("H" * 500)
    assert truncated.endswith("T" * 1500)
    assert "[… 1000 chars omitted …]" in truncated


def test_truncate_for_handoff_unchanged_at_exact_head_plus_tail_boundary() -> None:
    text = "x" * 2000
    assert dispatcher_mod._truncate_for_handoff(text, head=500, tail=1500) == text


def test_truncate_for_handoff_truncates_one_char_past_boundary() -> None:
    text = "H" * 500 + "x" + "T" * 1500
    truncated = dispatcher_mod._truncate_for_handoff(text, head=500, tail=1500)
    assert truncated != text
    assert truncated.startswith("H" * 500)
    assert truncated.endswith("T" * 1500)
    assert "[… 1 chars omitted …]" in truncated


def test_run_task_cycle_keeps_handoff_tail_for_long_phase_output(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    long_body = "H" * 600 + "M" * 5000 + "T" * 1600

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None):
        if role == "arquitecto":
            return dispatcher_mod.DispatchResult(success=True, session_id=None, result_text=long_body, account="cuenta1")
        if role == "revisor":
            return dispatcher_mod.DispatchResult(
                success=True, session_id=None, result_text="VERDICT: APPROVED", account="cuenta1",
            )
        return dispatcher_mod.DispatchResult(success=True, session_id=None, result_text="ok", account="cuenta1")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert "T" * 1500 in task.body
    assert "M" * 5000 not in task.body


def _dispatch_call_kwargs(cfg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None):
    return dict(role=role, prompt=prompt, model=model, effort=effort, round_num=round_num)


def test_run_task_cycle_approves_on_first_round(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    calls = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None):
        calls.append(_dispatch_call_kwargs(cfg_arg, task_id, slug, role, prompt, resume_session_id, model, effort, round_num))
        if role == "revisor":
            return dispatcher_mod.DispatchResult(
                success=True, session_id=None, result_text="VERDICT: APPROVED", account="cuenta1",
            )
        return dispatcher_mod.DispatchResult(success=True, session_id=None, result_text="ok", account="cuenta1")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    roles = [c["role"] for c in calls]
    assert roles == ["arquitecto", "implementador", "revisor", "auditor"]
    assert all(c["effort"] is None for c in calls)
    assert all(c["model"] == "opus" for c in calls)
    assert ("task-1", "done") in kanban.statuses


def test_run_task_cycle_completes_when_kanban_status_updates_always_raise(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    calls = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None):
        calls.append(role)
        if role == "revisor":
            return dispatcher_mod.DispatchResult(
                success=True, session_id=None, result_text="VERDICT: APPROVED", account="cuenta1",
            )
        return dispatcher_mod.DispatchResult(success=True, session_id=None, result_text="ok", account="cuenta1")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    class _RaisingKanban:
        def update_task_status(self, task_id, status):
            raise RuntimeError("kanban is down")

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _RaisingKanban(), description=_DESCRIPTION)

    assert calls == ["arquitecto", "implementador", "revisor", "auditor"]
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert task.status == "done"


def test_run_task_cycle_escalates_effort_after_configured_round(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path, max_revision_rounds=3, escalate_effort_after_round=2, escalated_effort="high")
    calls = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None):
        calls.append(_dispatch_call_kwargs(cfg_arg, task_id, slug, role, prompt, resume_session_id, model, effort, round_num))
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
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

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

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None):
        calls.append(role)
        if role == "revisor":
            return dispatcher_mod.DispatchResult(
                success=True, session_id=None, result_text="VERDICT: CHANGES_REQUESTED", account="cuenta1",
            )
        return dispatcher_mod.DispatchResult(success=True, session_id=None, result_text="ok", account="cuenta1")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert calls.count("revisor") == 2
    assert "auditor" not in calls
    assert ("task-1", "blocked") in kanban.statuses
    assert ("task-1", "done") not in kanban.statuses


def _phase_exec(result):
    """A fake exec_claude that clears the usage probe and then returns `result`."""

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None):
        if "usage" in prompt.lower():
            return ClaudeResult(
                session_id=None,
                result_text=(
                    "Current session: 10% used · resets later\n"
                    "Current week (all models): 10% used · resets later"
                ),
                raw={},
            )
        return result

    return fake_exec_claude


def _fake_worktree(monkeypatch):
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}/work",
    )


def test_dispatch_phase_commits_the_writing_phase(tmp_path, monkeypatch, fake_git) -> None:
    """Nothing used to commit, so the revisor reviewed an empty diff."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "exec_claude",
        _phase_exec(ClaudeResult(session_id="sess-1", result_text="done", raw={"is_error": False})),
    )
    _fake_worktree(monkeypatch)

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "do it", round_num=2)

    assert len(fake_git.commits) == 1
    commit = fake_git.commits[0]
    assert commit["workdir"].endswith("/worktrees/task-1/work")
    assert commit["author_name"] == "implementador (cuenta1)"
    assert commit["author_email"] == "implementador@ia-harness.invalid"
    # The role, the task, the round and the session that produced it: enough to
    # trace any commit back to the transcript that explains it.
    assert commit["message"].startswith("agent(implementador): task-1 round 2")
    assert "Account: cuenta1" in commit["message"]
    assert "Session: sess-1" in commit["message"]


def test_dispatch_phase_does_not_commit_a_reviewing_phase(tmp_path, monkeypatch, fake_git) -> None:
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "exec_claude",
        _phase_exec(ClaudeResult(session_id="sess-1", result_text="VERDICT: APPROVED", raw={"is_error": False})),
    )
    _fake_worktree(monkeypatch)

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review it")

    assert fake_git.commits == []


def test_dispatch_phase_does_not_commit_a_failed_phase(tmp_path, monkeypatch, fake_git) -> None:
    """A failed phase is retried in the same worktree, resuming the session."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "exec_claude",
        _phase_exec(ClaudeResult(session_id="sess-1", result_text="boom", raw={})),
    )
    _fake_worktree(monkeypatch)

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "do it")

    assert result.success is False
    assert fake_git.commits == []


def test_dispatch_phase_does_not_commit_a_rate_limited_phase(tmp_path, monkeypatch, fake_git) -> None:
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "exec_claude",
        _phase_exec(ClaudeResult(
            session_id="sess-1", result_text="429 rate limit exceeded", raw={"is_error": True},
        )),
    )
    _fake_worktree(monkeypatch)

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "do it")

    # The phase is retried on another account (there is none here), so its
    # half-finished tree must not be committed as if it were the round's work.
    assert result.success is False
    assert fake_git.commits == []


def test_dispatch_phase_hands_the_tree_back_to_its_owner(tmp_path, monkeypatch, fake_git) -> None:
    """The agents run as root against a bind mount owned by the host user."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "exec_claude",
        _phase_exec(ClaudeResult(session_id="sess-1", result_text="done", raw={"is_error": False})),
    )
    _fake_worktree(monkeypatch)

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "do it")

    assert fake_git.restored == [(f"{cfg.projects_root}/myproj", "1000:1000")]


def test_dispatch_phase_hands_the_tree_back_even_when_the_phase_raises(tmp_path, monkeypatch, fake_git) -> None:
    """`git worktree add` alone is enough to leave root-owned files behind."""
    cfg = _make_config(tmp_path)

    def raising_create_worktree(container, projects_root, slug, task_id, role):
        raise RuntimeError("git worktree add failed")

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", _phase_exec(None))
    monkeypatch.setattr(dispatcher_mod.docker_exec, "create_worktree", raising_create_worktree)

    with pytest.raises(RuntimeError):
        dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "do it")

    assert fake_git.restored == [(f"{cfg.projects_root}/myproj", "1000:1000")]
