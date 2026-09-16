import json
import subprocess

import pytest

import dispatcher.docker_exec as docker_exec_mod
from dispatcher.docker_exec import create_worktree, exec_claude, run_docker_exec


def test_run_docker_exec_builds_expected_command(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    run_docker_exec("agent-cuenta1", "/data/projects/foo/worktrees/task-1", ["echo", "hi"])

    assert captured["cmd"] == [
        "docker", "exec", "-w", "/data/projects/foo/worktrees/task-1", "agent-cuenta1", "echo", "hi",
    ]


def test_run_docker_exec_forwards_timeout(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["timeout"] = timeout
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    run_docker_exec("agent-cuenta1", "/wd", ["echo", "hi"], timeout=42)

    assert captured["timeout"] == 42


def test_exec_claude_parses_session_id_and_result(monkeypatch) -> None:
    payload = {"session_id": "sess-123", "result": "done", "is_error": False}

    def fake_run(cmd, capture_output, text, timeout=None):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    result = exec_claude("agent-cuenta1", "/data/projects/foo/worktrees/task-1", "do it")

    assert result.session_id == "sess-123"
    assert result.result_text == "done"
    assert result.raw == payload


def test_exec_claude_null_result_does_not_leak_none(monkeypatch) -> None:
    payload = {"session_id": "sess-123", "result": None, "is_error": False}

    def fake_run(cmd, capture_output, text, timeout=None):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    result = exec_claude("agent-cuenta1", "/wd", "do it")

    assert result.result_text == ""
    assert result.raw == payload


def test_exec_claude_passes_resume_flag(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude("agent-cuenta1", "/wd", "continue", resume_session_id="sess-123")

    assert "--resume" in captured["cmd"]
    assert "sess-123" in captured["cmd"]


def test_exec_claude_passes_model_flag(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude("agent-cuenta1", "/wd", "do it", model="opus")

    assert "--model" in captured["cmd"]
    assert "opus" in captured["cmd"]


def test_exec_claude_passes_effort_flag(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude("agent-cuenta1", "/wd", "do it", effort="high")

    cmd = captured["cmd"]
    assert cmd[cmd.index("--effort") + 1] == "high"
    assert not any("CLAUDE_CODE_EFFORT_LEVEL" in part for part in cmd)


def test_exec_claude_omits_model_and_effort_when_not_given(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude("agent-cuenta1", "/wd", "do it")

    assert "--model" not in captured["cmd"]
    assert "--effort" not in captured["cmd"]
    assert "-e" not in captured["cmd"]


def test_exec_claude_with_timeout_seconds_prefixes_in_container_timeout(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        captured["timeout"] = timeout
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude("agent-cuenta1", "/wd", "do it", timeout_seconds=600)

    cmd = captured["cmd"]
    container_index = cmd.index("agent-cuenta1")
    assert cmd[container_index + 1:container_index + 5] == ["timeout", "--kill-after=30", "600", "claude"]
    assert captured["timeout"] == 660


def test_exec_claude_without_timeout_seconds_runs_claude_directly(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        captured["timeout"] = timeout
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude("agent-cuenta1", "/wd", "do it")

    cmd = captured["cmd"]
    container_index = cmd.index("agent-cuenta1")
    assert cmd[container_index + 1] == "claude"
    assert captured["timeout"] is None


def test_exec_claude_timeout_expired_returns_diagnostic(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text, timeout=None):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    result = exec_claude("agent-cuenta1", "/wd", "do it", timeout_seconds=600)

    assert result.raw == {}
    assert result.session_id is None
    assert "timed out after 600s" in result.result_text


def test_exec_claude_non_json_stdout_returns_diagnostic(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text, timeout=None):
        return subprocess.CompletedProcess(cmd, 1, stdout="not json", stderr="boom")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    result = exec_claude("agent-cuenta1", "/wd", "do it")

    assert result.raw == {}
    assert result.session_id is None
    assert "exit 1" in result.result_text
    assert "boom" in result.result_text
    assert "not json" not in result.result_text


def test_exec_claude_json_list_stdout_returns_diagnostic(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text, timeout=None):
        return subprocess.CompletedProcess(cmd, 0, stdout="[1, 2, 3]", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    result = exec_claude("agent-cuenta1", "/wd", "do it")

    assert result.raw == {}
    assert result.session_id is None
    assert "exit 0" in result.result_text


def test_exec_claude_empty_stdout_returns_diagnostic(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text, timeout=None):
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    result = exec_claude("agent-cuenta1", "/wd", "do it")

    assert result.raw == {}
    assert result.session_id is None
    assert "exit 2" in result.result_text


def test_exec_claude_diagnostic_falls_back_to_stdout_tail_when_stderr_empty(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text, timeout=None):
        return subprocess.CompletedProcess(cmd, 1, stdout="garbled output", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    result = exec_claude("agent-cuenta1", "/wd", "do it")

    assert "garbled output" in result.result_text


def test_exec_claude_exit_124_with_timeout_seconds_returns_timed_out_message(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text, timeout=None):
        return subprocess.CompletedProcess(cmd, 124, stdout="", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    result = exec_claude("agent-cuenta1", "/wd", "do it", timeout_seconds=600)

    assert result.raw == {}
    assert result.result_text == "claude timed out after 600s"


def test_exec_claude_exit_124_without_timeout_seconds_returns_generic_diagnostic(monkeypatch) -> None:
    # exit 124 can happen for reasons unrelated to our timeout when we never
    # asked for one; only treat it as "timed out" when timeout_seconds is set.
    def fake_run(cmd, capture_output, text, timeout=None):
        return subprocess.CompletedProcess(cmd, 124, stdout="", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    result = exec_claude("agent-cuenta1", "/wd", "do it")

    assert result.raw == {}
    assert "exit 124" in result.result_text
    assert "timed out" not in result.result_text


def test_exec_claude_exit_137_with_timeout_seconds_returns_timed_out_message(monkeypatch) -> None:
    # --kill-after fires SIGKILL when claude ignores SIGTERM; coreutils timeout
    # then exits 137, not 124.
    def fake_run(cmd, capture_output, text, timeout=None):
        return subprocess.CompletedProcess(cmd, 137, stdout="", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    result = exec_claude("agent-cuenta1", "/wd", "do it", timeout_seconds=600)

    assert result.raw == {}
    assert result.result_text == "claude timed out after 600s"


def test_exec_claude_rejects_non_positive_timeout_seconds(monkeypatch) -> None:
    called = False

    def fake_run(cmd, capture_output, text, timeout=None):
        nonlocal called
        called = True
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        exec_claude("agent-cuenta1", "/wd", "do it", timeout_seconds=0)

    assert called is False


def test_exec_claude_diagnostic_caps_stderr_tail_to_500_chars(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text, timeout=None):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="x" * 600 + "END")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    result = exec_claude("agent-cuenta1", "/wd", "do it")

    assert result.result_text.endswith("END")
    tail = result.result_text.split(": ", 1)[1]
    assert len(tail) <= 500


def test_exec_claude_diagnostic_collapses_multiline_tail(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text, timeout=None):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="line one\nline two\n  line three")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    result = exec_claude("agent-cuenta1", "/wd", "do it")

    assert "\n" not in result.result_text
    assert "line one line two line three" in result.result_text


def test_create_worktree_builds_branch_name_and_tolerates_existing(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 128, stdout="", stderr="fatal: a branch named 'agent/implementador/task-1' already exists\n"
        )

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    path = create_worktree("agent-cuenta1", "/data/projects", "myproj", "task-1", "implementador")

    assert path == "/data/projects/myproj/worktrees/task-1/implementador"
    assert "agent/implementador/task-1" in captured["cmd"]


def test_create_worktree_paths_differ_per_role(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text, timeout=None):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    paths = {
        role: create_worktree("agent-cuenta1", "/data/projects", "myproj", "task-1", role)
        for role in ("arquitecto", "implementador", "revisor", "auditor")
    }

    assert len(set(paths.values())) == 4


def test_create_worktree_tolerates_existing_worktree_directory(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text, timeout=None):
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
    def fake_run(cmd, capture_output, text, timeout=None):
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

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    create_worktree("agent-cuenta1", "/data/projects", "myproj", "task-1", "implementador")

    assert "LC_ALL=C" in captured["cmd"]
