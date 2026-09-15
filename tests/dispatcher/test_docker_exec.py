import json
import subprocess

import pytest

import dispatcher.docker_exec as docker_exec_mod
from dispatcher.docker_exec import create_worktree, exec_claude, run_docker_exec


def test_run_docker_exec_builds_expected_command(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    run_docker_exec("agent-cuenta1", "/data/projects/foo/worktrees/task-1", ["echo", "hi"])

    assert captured["cmd"] == [
        "docker", "exec", "-w", "/data/projects/foo/worktrees/task-1", "agent-cuenta1", "echo", "hi",
    ]


def test_exec_claude_parses_session_id_and_result(monkeypatch) -> None:
    payload = {"session_id": "sess-123", "result": "done", "is_error": False}

    def fake_run(cmd, capture_output, text):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    result = exec_claude("agent-cuenta1", "/data/projects/foo/worktrees/task-1", "do it")

    assert result.session_id == "sess-123"
    assert result.result_text == "done"
    assert result.raw == payload


def test_exec_claude_passes_resume_flag(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude("agent-cuenta1", "/wd", "continue", resume_session_id="sess-123")

    assert "--resume" in captured["cmd"]
    assert "sess-123" in captured["cmd"]


def test_exec_claude_passes_model_flag(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude("agent-cuenta1", "/wd", "do it", model="opus")

    assert "--model" in captured["cmd"]
    assert "opus" in captured["cmd"]


def test_exec_claude_sets_effort_env_var(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude("agent-cuenta1", "/wd", "do it", effort="high")

    assert "-e" in captured["cmd"]
    assert "CLAUDE_CODE_EFFORT_LEVEL=high" in captured["cmd"]


def test_exec_claude_omits_model_and_effort_when_not_given(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude("agent-cuenta1", "/wd", "do it")

    assert "--model" not in captured["cmd"]
    assert "-e" not in captured["cmd"]


def test_create_worktree_builds_branch_name_and_tolerates_existing(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 128, stdout="", stderr="fatal: a branch named 'agent/implementador/task-1' already exists\n"
        )

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    path = create_worktree("agent-cuenta1", "/data/projects", "myproj", "task-1", "implementador")

    assert path == "/data/projects/myproj/worktrees/task-1/implementador"
    assert "agent/implementador/task-1" in captured["cmd"]


def test_create_worktree_paths_differ_per_role(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    paths = {
        role: create_worktree("agent-cuenta1", "/data/projects", "myproj", "task-1", role)
        for role in ("arquitecto", "implementador", "revisor", "auditor")
    }

    assert len(set(paths.values())) == 4


def test_create_worktree_tolerates_existing_worktree_directory(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text):
        return subprocess.CompletedProcess(
            cmd,
            128,
            stdout="",
            stderr="fatal: '/data/projects/myproj/worktrees/task-1/revisor' already exists\n",
        )

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    path = create_worktree("agent-cuenta1", "/data/projects", "myproj", "task-1", "revisor")

    assert path == "/data/projects/myproj/worktrees/task-1/revisor"


def test_create_worktree_raises_on_unrelated_already_exists_error(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text):
        return subprocess.CompletedProcess(
            cmd,
            128,
            stdout="",
            stderr="fatal: a branch named 'agent/otro/task-9' already exists\n",
        )

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="git worktree add failed"):
        create_worktree("agent-cuenta1", "/data/projects", "myproj", "task-1", "implementador")


def test_create_worktree_pins_git_locale(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    create_worktree("agent-cuenta1", "/data/projects", "myproj", "task-1", "implementador")

    assert "LC_ALL=C" in captured["cmd"]
