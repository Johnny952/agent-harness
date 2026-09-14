import dispatcher.dispatcher as dispatcher_mod
from dispatcher.config import AccountConfig, Config
from dispatcher.context_transfer import acquire_lock, is_lock_expired, read_task_file, task_file_path
from dispatcher.docker_exec import ClaudeResult
from dispatcher.state_machine import AccountState, get_state


def _make_config(tmp_path):
    return Config(
        accounts=[AccountConfig(name="cuenta1", container="agent-cuenta1")],
        quota_threshold_pct=90,
        heartbeat_ttl_seconds=120,
        heartbeat_interval_seconds=1,
        projects_root=str(tmp_path / "projects"),
        hive_tasks_dir=str(tmp_path / "hive"),
        state_dir=str(tmp_path / "state"),
        vibe_kanban_mcp_url="http://127.0.0.1:9100/sse",
        collector_url="http://127.0.0.1:8787",
    )


def test_dispatch_phase_success(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None):
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

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None):
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
