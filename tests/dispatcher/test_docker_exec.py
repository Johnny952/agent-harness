import json
import subprocess

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


def test_create_worktree_builds_branch_name_and_tolerates_existing(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="fatal: already exists")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    path = create_worktree("agent-cuenta1", "/data/projects", "myproj", "task-1", "implementador")

    assert path == "/data/projects/myproj/worktrees/task-1"
    assert "agent/implementador/task-1" in captured["cmd"]
