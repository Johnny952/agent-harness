"""The in-container process probe of the account reaper (ADR 429)."""
import os
import subprocess

import pytest

import dispatcher.docker_exec as docker_exec_mod
import dispatcher.dispatcher as dispatcher_mod
from dispatcher.context_transfer import read_task_file, task_file_path, write_task_file
from dispatcher.state_machine import AccountState, get_state, set_state
from dispatcher.tasks import acquire_lock

from tests.dispatcher.test_dispatcher import (
    _ORPHAN_WINDOW_PAST,
    _age_busy_since,
    _age_heartbeat,
    _make_config,
)

# Taken at import, before the autouse stub in conftest.py replaces it.
_ORIGINAL = dispatcher_mod._task_process_seen


def _fake_run(monkeypatch, *, stdout="", returncode=0, raises=None):
    calls = []

    def fake(container, workdir, command, env=None, timeout=None):
        calls.append((container, workdir, command, timeout))
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(docker_exec_mod, "run_docker_exec", fake)
    return calls


def _run_script(task_dir):
    return subprocess.run(
        ["sh", "-c", docker_exec_mod.PROCESS_PROBE_SCRIPT, "ia-harness-probe", str(task_dir)],
        cwd="/", capture_output=True, text=True,
    )


@pytest.mark.skipif(not os.path.isdir("/proc/self"), reason="needs /proc")
def test_the_script_sees_a_process_working_under_the_task_dir_and_not_otherwise(tmp_path) -> None:
    task_dir = tmp_path / "worktrees" / "T-1"
    (task_dir / "work").mkdir(parents=True)
    other = tmp_path / "worktrees" / "T-10"
    other.mkdir()

    assert _run_script(task_dir).stdout.strip() == "gone"
    with subprocess.Popen(["sleep", "30"], cwd=other) as proc:
        try:
            assert _run_script(task_dir).stdout.strip() == "gone"
        finally:
            proc.kill()
    with subprocess.Popen(["sleep", "30"], cwd=task_dir / "work") as proc:
        try:
            assert _run_script(task_dir).stdout.strip() == "seen"
        finally:
            proc.kill()


def test_probe_sends_the_script_with_the_task_dir_as_an_argument(monkeypatch) -> None:
    calls = _fake_run(monkeypatch, stdout="seen\n")

    assert docker_exec_mod.task_process_seen("agent-c1", "/p/s/worktrees/T-1") is True

    container, workdir, command, timeout = calls[0]
    assert (container, workdir) == ("agent-c1", "/")
    assert command == [
        "sh", "-c", docker_exec_mod.PROCESS_PROBE_SCRIPT, "ia-harness-probe", "/p/s/worktrees/T-1",
    ]
    assert timeout == docker_exec_mod.PROCESS_PROBE_TIMEOUT_SECONDS


@pytest.mark.parametrize("kwargs,expected", [
    (dict(stdout="seen\n"), True),
    (dict(stdout="gone\n"), False),
    (dict(stdout="other\n"), None),
    (dict(stdout=""), None),
    (dict(stdout="gone\n", returncode=1), None),
    (dict(raises=OSError("no docker")), None),
    (dict(raises=subprocess.TimeoutExpired("docker", 30)), None),
])
def test_probe_answers(monkeypatch, kwargs, expected) -> None:
    _fake_run(monkeypatch, **kwargs)

    assert docker_exec_mod.task_process_seen("c", "/d") is expected


def _set_project(cfg, project):
    path = task_file_path(cfg.hive_tasks_dir, "task-1")
    task = read_task_file(path)
    task.project = project
    write_task_file(path, task)


def _stale_holder(tmp_path, *, project="myproj", heartbeat_age=999, busy_age=999):
    cfg = _make_config(tmp_path)
    acquire_lock(cfg.hive_tasks_dir, "task-1", owner="cuenta1")
    _set_project(cfg, project)
    _age_heartbeat(cfg, "task-1", heartbeat_age)
    set_state(cfg.state_dir, "cuenta1", AccountState.BUSY, current_task_id="task-1")
    _age_busy_since(cfg, "cuenta1", busy_age)
    return cfg


def _answer(monkeypatch, value):
    calls = []

    def fake(cfg, account, task):
        calls.append((account, task.task_id))
        return value

    monkeypatch.setattr(dispatcher_mod, "_task_process_seen", fake)
    return calls


def test_the_reaper_releases_a_holder_whose_process_is_gone(tmp_path, monkeypatch) -> None:
    cfg = _stale_holder(tmp_path)
    calls = _answer(monkeypatch, False)

    assert dispatcher_mod.reap_stale_busy_accounts(cfg) == ["cuenta1"]
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.IDLE
    assert calls == [("cuenta1", "task-1")]


@pytest.mark.parametrize("value", [True, None])
def test_the_reaper_keeps_a_holder_that_is_seen_or_unknown(tmp_path, monkeypatch, value) -> None:
    cfg = _stale_holder(tmp_path)
    _answer(monkeypatch, value)

    assert dispatcher_mod.reap_stale_busy_accounts(cfg) == []
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.BUSY


def test_the_reaper_does_not_probe_a_live_heartbeat(tmp_path, monkeypatch) -> None:
    cfg = _stale_holder(tmp_path, heartbeat_age=0)
    calls = _answer(monkeypatch, False)

    assert dispatcher_mod.reap_stale_busy_accounts(cfg) == []
    assert calls == []


def test_the_reaper_does_not_probe_past_the_window(tmp_path, monkeypatch) -> None:
    cfg = _stale_holder(
        tmp_path, heartbeat_age=_ORPHAN_WINDOW_PAST(_make_config(tmp_path)),
        busy_age=_ORPHAN_WINDOW_PAST(_make_config(tmp_path)),
    )
    calls = _answer(monkeypatch, True)

    assert dispatcher_mod.reap_stale_busy_accounts(cfg) == ["cuenta1"]
    assert calls == []


def test_the_helper_probes_the_accounts_container_and_the_task_dir(tmp_path, monkeypatch) -> None:
    cfg = _stale_holder(tmp_path)
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    seen = []
    monkeypatch.setattr(
        docker_exec_mod, "task_process_seen", lambda c, d: seen.append((c, d)) or False,
    )

    assert _ORIGINAL(cfg, "cuenta1", task) is False
    assert seen == [("agent-cuenta1", f"{cfg.projects_root}/myproj/worktrees/task-1")]


def test_the_helper_does_not_probe_a_card_without_a_project(tmp_path, monkeypatch) -> None:
    cfg = _stale_holder(tmp_path, project=None)
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    seen = []
    monkeypatch.setattr(
        docker_exec_mod, "task_process_seen", lambda c, d: seen.append((c, d)) or False,
    )

    assert _ORIGINAL(cfg, "cuenta1", task) is None
    assert seen == []

