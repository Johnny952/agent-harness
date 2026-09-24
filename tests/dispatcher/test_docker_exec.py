import json
import logging
import subprocess

import pytest

import dispatcher.docker_exec as docker_exec_mod
from dispatcher.docker_exec import (
    commit_worktree,
    create_worktree,
    exec_claude,
    merge_task_branch,
    read_owner,
    remove_review_worktrees,
    remove_task_worktrees,
    restore_owner,
    run_docker_exec,
    task_worktrees_dir,
)


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


def test_exec_claude_passes_a_turn_budget(monkeypatch) -> None:
    """`--max-turns` is hidden from `--help` but registered in the CLI, and it
    is the only ceiling a bounded phase has: without it the mapping phase
    spends whatever it likes."""
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude("agent-cuenta1", "/wd", "map it", max_turns=40)

    cmd = captured["cmd"]
    assert cmd[cmd.index("--max-turns") + 1] == "40"


def test_exec_claude_omits_the_turn_budget_when_not_given(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude("agent-cuenta1", "/wd", "do it")

    assert "--max-turns" not in captured["cmd"]


@pytest.mark.parametrize("max_turns", [0, -1])
def test_exec_claude_rejects_a_turn_budget_that_buys_nothing(monkeypatch, max_turns: int) -> None:
    """Caught here rather than in the container: `--max-turns 0` is a phase
    that cannot do anything, which is a config mistake, not a cheap run."""
    def fake_run(cmd, capture_output, text, timeout=None):
        raise AssertionError("should not reach docker")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    with pytest.raises(ValueError, match="max_turns must be positive"):
        exec_claude("agent-cuenta1", "/wd", "do it", max_turns=max_turns)


def test_exec_claude_reports_a_spent_turn_budget_as_the_cli_does(monkeypatch) -> None:
    """Hitting the ceiling is not a crash: the CLI returns its normal JSON
    with `subtype: error_max_turns`, and the work done up to there is on disk.
    The raw envelope has to survive so the caller can tell the two apart."""
    payload = {
        "session_id": "sess-9",
        "result": "Reached maximum number of turns (40)",
        "is_error": True,
        "subtype": "error_max_turns",
    }

    def fake_run(cmd, capture_output, text, timeout=None):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    result = exec_claude("agent-cuenta1", "/wd", "map it", max_turns=40)

    assert result.session_id == "sess-9"
    assert result.raw["subtype"] == "error_max_turns"


@pytest.mark.parametrize(
    ("returncode", "expected"), [(0, True), (1, False)], ids=["there", "missing"],
)
def test_path_exists_asks_the_container(monkeypatch, returncode: int, expected: bool) -> None:
    """The repo is not mounted into the agents, so a project file only exists
    from the dispatcher's side through `docker exec`."""
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    assert docker_exec_mod.path_exists("agent-cuenta1", "/data/projects/foo/docs/README.md") is expected
    assert captured["cmd"][-3:] == ["test", "-e", "/data/projects/foo/docs/README.md"]


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


def test_exec_claude_passes_one_plugin_dir_flag_per_skill(monkeypatch) -> None:
    """Role skills are delivered per call, so the set must survive verbatim:
    N directories mean N `--plugin-dir` flags, in order. The CLI takes one
    path per flag — a comma-joined list or a parent directory would silently
    deliver the wrong set."""
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude(
        "agent-cuenta1", "/wd", "do it",
        plugin_dirs=["/opt/ia-harness/skills/a", "/opt/ia-harness/skills/b"],
    )

    cmd = captured["cmd"]
    pairs = [(cmd[i], cmd[i + 1]) for i, part in enumerate(cmd) if part == "--plugin-dir"]
    assert pairs == [
        ("--plugin-dir", "/opt/ia-harness/skills/a"),
        ("--plugin-dir", "/opt/ia-harness/skills/b"),
    ]


def test_exec_claude_puts_plugin_dirs_before_the_prompt(monkeypatch) -> None:
    """Everything after `-p` is the prompt's argument or the flags the CLI
    reads with it; a `--plugin-dir` that landed past it would be parsed as
    something else or ignored."""
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude(
        "agent-cuenta1", "/wd", "do it",
        plugin_dirs=["/opt/ia-harness/skills/a"],
        append_system_prompt="method skills: a",
    )

    cmd = captured["cmd"]
    assert cmd.index("--plugin-dir") < cmd.index("-p")
    assert cmd.index("--append-system-prompt") < cmd.index("-p")
    assert cmd[cmd.index("--append-system-prompt") + 1] == "method skills: a"


def test_exec_claude_omits_skill_flags_when_not_given(monkeypatch) -> None:
    """A role with no skills must produce the command it produced before this
    existed — not an empty flag, which the CLI would read as a path."""
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude("agent-cuenta1", "/wd", "do it", plugin_dirs=[], append_system_prompt=None)

    assert "--plugin-dir" not in captured["cmd"]
    assert "--append-system-prompt" not in captured["cmd"]


def test_exec_claude_passes_the_json_schema_before_the_prompt(monkeypatch) -> None:
    """The schema is what makes the return structured, and it is read as a
    flag: past `-p` it would be part of the prompt's argument list. It travels
    as one compact JSON argv element — pretty-printing it would spend argv on
    whitespace for no reader."""
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    schema = {"type": "object", "properties": {"status": {"type": "string"}}, "required": ["status"]}
    exec_claude("agent-cuenta1", "/wd", "do it", json_schema=schema)

    cmd = captured["cmd"]
    assert cmd.index("--json-schema") < cmd.index("-p")
    serialized = cmd[cmd.index("--json-schema") + 1]
    assert json.loads(serialized) == schema
    assert ", " not in serialized


def test_exec_claude_omits_json_schema_when_not_given(monkeypatch) -> None:
    """A role with no schema keeps the free-text call it had before: an empty
    or `null` argument is rejected by the CLI as invalid JSON and would fail
    every phase of that role."""
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude("agent-cuenta1", "/wd", "do it", json_schema=None)

    assert "--json-schema" not in captured["cmd"]


def test_exec_claude_passes_the_permission_mode_before_the_prompt(monkeypatch) -> None:
    """The flag that decides whether a phase can write anything at all. With
    no mode the CLI runs `-p` under `--permission-prompts host` with no host
    to ask, and denies instead of asking — the phase's own worktree included.
    Read as a flag, so past `-p` it would join the prompt's argument list."""
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude("agent-cuenta1", "/wd", "do it", permission_mode="acceptEdits")

    cmd = captured["cmd"]
    assert cmd.index("--permission-mode") < cmd.index("-p")
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"


def test_exec_claude_omits_the_permission_mode_when_not_given(monkeypatch) -> None:
    """`permission_mode: null` has to reach the CLI as no flag rather than an
    empty one, because that unflagged call is the measured baseline the config
    key exists to keep reproducible."""
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude("agent-cuenta1", "/wd", "do it", permission_mode=None)

    assert "--permission-mode" not in captured["cmd"]


def test_exec_claude_passes_every_add_dir_under_one_flag(monkeypatch) -> None:
    """`--add-dir <directories...>` is variadic: one flag collects every path
    that follows it until the next dash-prefixed token. Repeating the flag
    would work too, but one flag is what the CLI documents, and a
    comma-joined list would be read as a single directory whose name contains
    a comma."""
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude("agent-cuenta1", "/wd", "do it", add_dirs=["/data/.hive", "/data/shared"])

    cmd = captured["cmd"]
    assert cmd.count("--add-dir") == 1
    start = cmd.index("--add-dir")
    assert cmd[start + 1:start + 3] == ["/data/.hive", "/data/shared"]


def test_exec_claude_puts_add_dirs_before_the_prompt(monkeypatch) -> None:
    """Placed ahead of `-p` for both reasons at once: a flag past `-p` is not
    read as a flag, and a variadic that started there would swallow the
    prompt's own arguments until the next dash."""
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude("agent-cuenta1", "/wd", "do it", add_dirs=["/data/.hive"])

    cmd = captured["cmd"]
    assert cmd.index("--add-dir") < cmd.index("-p")
    assert cmd[cmd.index("--add-dir") + 1] == "/data/.hive"


@pytest.mark.parametrize("add_dirs", [None, []])
def test_exec_claude_omits_add_dir_when_there_is_nothing_to_add(monkeypatch, add_dirs) -> None:
    """A bare `--add-dir` with nothing after it would take the next token as a
    directory — which is `-p`'s prompt or a flag the CLI then never sees."""
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude("agent-cuenta1", "/wd", "do it", add_dirs=add_dirs)

    assert "--add-dir" not in captured["cmd"]


def test_exec_claude_passes_every_allowed_tool_under_one_flag(monkeypatch) -> None:
    """`--allowed-tools <tools...>` is variadic like `--add-dir`, and a
    pattern that contains a space stays one argv element: the flag documents
    itself as "comma or space-separated", but the splitting happens inside a
    single argument, so `Bash(node --test*)` handed over whole is not torn in
    two. Measured against a live CLI on 2026-09-23 — that pattern, this
    shape, and node ran."""
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude(
        "agent-cuenta1", "/wd", "do it",
        allowed_tools=["Bash(node --test*)", "Bash(npm test*)"],
    )

    cmd = captured["cmd"]
    assert cmd.count("--allowed-tools") == 1
    start = cmd.index("--allowed-tools")
    assert cmd[start + 1:start + 3] == ["Bash(node --test*)", "Bash(npm test*)"]


def test_exec_claude_puts_allowed_tools_before_the_prompt(monkeypatch) -> None:
    """Same reason as `--add-dir`: a variadic that started past `-p` would eat
    the prompt instead of being read as a flag at all."""
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude("agent-cuenta1", "/wd", "do it", allowed_tools=["Bash(node --test*)"])

    cmd = captured["cmd"]
    assert cmd.index("--allowed-tools") < cmd.index("-p")
    assert cmd[cmd.index("--allowed-tools") + 1] == "Bash(node --test*)"


@pytest.mark.parametrize("allowed_tools", [None, []])
def test_exec_claude_omits_allowed_tools_when_there_is_nothing_to_allow(
    monkeypatch, allowed_tools,
) -> None:
    """Empty is the default, and a bare `--allowed-tools` would take the next
    token as a pattern — silently widening or breaking the command instead of
    sending no flag."""
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude("agent-cuenta1", "/wd", "do it", allowed_tools=allowed_tools)

    assert "--allowed-tools" not in captured["cmd"]


def test_exec_claude_keeps_allowed_tools_and_add_dirs_apart(monkeypatch) -> None:
    """Two variadics in a row: each one has to stop at the other's flag rather
    than absorbing it, which is what makes the order they are emitted in safe."""
    captured = {}

    def fake_run(cmd, capture_output, text, timeout=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    exec_claude(
        "agent-cuenta1", "/wd", "do it",
        add_dirs=["/data/.hive"],
        allowed_tools=["Bash(node --test*)"],
    )

    cmd = captured["cmd"]
    start = cmd.index("--add-dir")
    assert cmd[start + 1] == "/data/.hive"
    assert cmd[start + 2] == "--allowed-tools"
    assert cmd[cmd.index("--allowed-tools") + 2] == "-p"


def test_exec_claude_keeps_the_structured_output_on_raw(monkeypatch) -> None:
    """The validated return arrives as a field of the `--output-format json`
    envelope, beside `result` rather than inside it, and `raw` is what carries
    it to the caller."""

    def fake_run(cmd, capture_output, text, timeout=None):
        payload = {
            "session_id": "s-1",
            "result": "Structured output provided successfully",
            "structured_output": {"status": "complete", "verdict": "APPROVED"},
        }
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", fake_run)

    result = exec_claude("agent-cuenta1", "/wd", "do it", json_schema={"type": "object"})

    assert result.raw["structured_output"] == {"status": "complete", "verdict": "APPROVED"}
    assert result.session_id == "s-1"


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


_CONTAINER = "agent-cuenta1"
_PROJECT = "/data/projects/myproj"
_BRANCH = "agent/task/task-1"


def _in_container(cmd):
    """The command as it runs inside the container, without the docker prefix."""
    return cmd[cmd.index(_CONTAINER) + 1:]


def _fake_docker(calls, respond=None):
    """A docker exec that records every call and answers per command.

    create_worktree and commit_worktree each issue several different commands,
    so a fake that answers all of them identically cannot tell the interesting
    cases apart. `respond` takes the in-container command and returns
    (returncode, stdout, stderr); the default is "everything worked".
    """

    def fake_run(cmd, capture_output, text, timeout=None):
        calls.append(cmd)
        returncode, stdout, stderr = respond(_in_container(cmd)) if respond else (0, "", "")
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    return fake_run


def _branch_missing(args):
    """show-ref exits non-zero, i.e. the task branch does not exist yet."""
    return (1, "", "") if args[:2] == ["git", "show-ref"] else (0, "", "")


def test_task_branch_is_per_task_not_per_role() -> None:
    assert docker_exec_mod.task_branch("task-1") == _BRANCH


def test_create_worktree_gives_every_writer_role_the_same_path_and_branch(monkeypatch) -> None:
    """The implementador has to start from what the arquitecto left behind."""
    calls = []
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls, _branch_missing))

    paths = {
        role: create_worktree(_CONTAINER, "/data/projects", "myproj", "task-1", role)
        for role in ("arquitecto", "implementador")
    }

    assert set(paths.values()) == {f"{_PROJECT}/worktrees/task-1/work"}
    adds = [args for args in map(_in_container, calls) if args[:3] == ["git", "worktree", "add"]]
    assert len(adds) == 2
    assert all(_BRANCH in args for args in adds)


def test_create_worktree_creates_the_task_branch_when_it_is_missing(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls, _branch_missing))

    path = create_worktree(_CONTAINER, "/data/projects", "myproj", "task-1", "arquitecto")

    assert _in_container(calls[-1]) == ["git", "worktree", "add", "-b", _BRANCH, path]


def test_create_worktree_never_resets_an_existing_task_branch(monkeypatch) -> None:
    """Round 2 checks the branch out where it is.

    `-b` would fail and `-B` would reset the branch, throwing away every commit
    the earlier phases made — which is the whole point of having one branch.
    """
    calls = []
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls))

    path = create_worktree(_CONTAINER, "/data/projects", "myproj", "task-1", "implementador")

    assert _in_container(calls[-1]) == ["git", "worktree", "add", path, _BRANCH]


def test_create_worktree_tolerates_the_writer_worktree_already_being_there(monkeypatch) -> None:
    def respond(args):
        if args[:3] == ["git", "worktree", "add"]:
            return (128, "", f"fatal: '{_PROJECT}/worktrees/task-1/work' already exists\n")
        return (0, "", "")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker([], respond))

    path = create_worktree(_CONTAINER, "/data/projects", "myproj", "task-1", "implementador")

    assert path == f"{_PROJECT}/worktrees/task-1/work"


def test_create_worktree_raises_on_unrelated_already_exists_error(monkeypatch) -> None:
    def respond(args):
        return (128, "", "fatal: a branch named 'agent/task/task-9' already exists\n")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker([], respond))

    with pytest.raises(RuntimeError, match="git worktree add failed"):
        create_worktree(_CONTAINER, "/data/projects", "myproj", "task-1", "implementador")


def test_create_worktree_tolerates_the_branch_being_used_by_this_same_worktree(monkeypatch) -> None:
    work = f"{_PROJECT}/worktrees/task-1/work"

    def respond(args):
        if args[:3] == ["git", "worktree", "add"]:
            return (128, "", f"fatal: '{_BRANCH}' is already used by worktree at '{work}'\n")
        return (0, "", "")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker([], respond))

    assert create_worktree(_CONTAINER, "/data/projects", "myproj", "task-1", "implementador") == work


def test_create_worktree_raises_when_another_worktree_holds_the_branch(monkeypatch) -> None:
    """Two tasks fighting over one branch is a real conflict, not a re-run."""

    def respond(args):
        if args[:3] == ["git", "worktree", "add"]:
            return (128, "", f"fatal: '{_BRANCH}' is already used by worktree at '/somewhere/else'\n")
        return (0, "", "")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker([], respond))

    with pytest.raises(RuntimeError, match="git worktree add failed"):
        create_worktree(_CONTAINER, "/data/projects", "myproj", "task-1", "implementador")


def test_create_worktree_rebuilds_a_detached_checkout_for_reviewers(monkeypatch) -> None:
    """A reviewer reads the branch tip, and gets a fresh checkout every round.

    Detached because git refuses to check one branch out twice, and rebuilt
    because a reused checkout is how the revisor ended up reviewing a tree with
    none of the implementador's work in it.
    """
    calls = []
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls))

    path = create_worktree(_CONTAINER, "/data/projects", "myproj", "task-1", "revisor")

    assert path == f"{_PROJECT}/worktrees/task-1/revisor"
    assert [_in_container(cmd) for cmd in calls] == [
        # ensure_worktrees_ignored, with no repository here to exclude anything in.
        ["git", "rev-parse", "--git-common-dir"],
        ["rm", "-rf", path],
        ["git", "worktree", "prune"],
        ["git", "worktree", "add", "--detach", path, _BRANCH],
    ]


def test_create_worktree_shares_one_path_across_the_writers_and_not_the_reviewer(
    monkeypatch,
) -> None:
    """The split the whole scheme rests on: one tree to build in, one to review."""
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker([]))

    paths = {
        role: create_worktree(_CONTAINER, "/data/projects", "myproj", "task-1", role)
        for role in ("arquitecto", "implementador", "auditor", "revisor")
    }

    writers = {paths[role] for role in ("arquitecto", "implementador", "auditor")}
    assert len(writers) == 1
    assert paths["revisor"] not in writers


def test_create_worktree_raises_when_the_review_checkout_cannot_be_rebuilt(monkeypatch) -> None:
    """Never tolerated: a surviving directory here means a stale review."""

    def respond(args):
        if "--detach" in args:
            return (128, "", f"fatal: '{_PROJECT}/worktrees/task-1/revisor' already exists\n")
        return (0, "", "")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker([], respond))

    with pytest.raises(RuntimeError, match="--detach"):
        create_worktree(_CONTAINER, "/data/projects", "myproj", "task-1", "revisor")


@pytest.mark.parametrize(
    "projects_root,slug,task_id,role",
    [
        ("", "myproj", "task-1", "revisor"),
        ("/data/projects", "", "task-1", "revisor"),
        ("/data/projects", "myproj", "", "revisor"),
        ("/data/projects", "myproj", "task-1", ""),
    ],
)
def test_create_worktree_rejects_empty_path_components(monkeypatch, projects_root, slug, task_id, role) -> None:
    """The reviewer path is rm -rf'd, so an empty component must never widen it."""
    calls = []
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls))

    with pytest.raises(ValueError):
        create_worktree(_CONTAINER, projects_root, slug, task_id, role)

    assert calls == []


def test_create_worktree_pins_git_locale(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls))

    create_worktree(_CONTAINER, "/data/projects", "myproj", "task-1", "implementador")

    assert all("LC_ALL=C" in cmd for cmd in calls)


_WORKTREES = f"{_PROJECT}/worktrees/task-1"


def _listing(*names, returncode=0):
    """`ls -1` of the task's worktrees directory answers with these names."""

    def respond(args):
        if args[0] == "ls":
            return (returncode, "".join(f"{name}\n" for name in names), "")
        return (0, "", "")

    return respond


def test_remove_review_worktrees_keeps_the_writers_worktree(monkeypatch) -> None:
    """The reviewing checkouts are disposable; `work` holds the deliverable."""
    calls = []
    monkeypatch.setattr(
        docker_exec_mod.subprocess, "run", _fake_docker(calls, _listing("auditor", "revisor", "work"))
    )

    removed = remove_review_worktrees(_CONTAINER, "/data/projects", "myproj", "task-1")

    assert removed == ["auditor", "revisor"]
    assert [_in_container(cmd) for cmd in calls] == [
        ["ls", "-1", _WORKTREES],
        ["rm", "-rf", f"{_WORKTREES}/auditor"],
        ["rm", "-rf", f"{_WORKTREES}/revisor"],
        ["git", "worktree", "prune"],
    ]


def test_remove_review_worktrees_cleans_up_a_role_nobody_listed(monkeypatch) -> None:
    """create_worktree defines reviewers by negation, so cleanup must too.

    A role added to the config later gets a review worktree without anything
    here learning its name; finding them by listing the directory is what keeps
    the two sides from drifting into a leak.
    """
    calls = []
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls, _listing("work", "qa")))

    assert remove_review_worktrees(_CONTAINER, "/data/projects", "myproj", "task-1") == ["qa"]
    assert ["rm", "-rf", f"{_WORKTREES}/qa"] in [_in_container(cmd) for cmd in calls]


def test_remove_review_worktrees_touches_nothing_when_only_work_is_there(monkeypatch) -> None:
    """No reviewers left means no rm and, with it, no pointless prune."""
    calls = []
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls, _listing("work")))

    assert remove_review_worktrees(_CONTAINER, "/data/projects", "myproj", "task-1") == []
    assert [_in_container(cmd) for cmd in calls] == [["ls", "-1", _WORKTREES]]


def test_remove_review_worktrees_is_quiet_when_the_task_never_ran(monkeypatch) -> None:
    """ls fails on a task with no worktrees directory: nothing to clean, not an error."""
    calls = []
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls, _listing(returncode=2)))

    assert remove_review_worktrees(_CONTAINER, "/data/projects", "myproj", "task-1") == []
    assert [_in_container(cmd) for cmd in calls] == [["ls", "-1", _WORKTREES]]


def test_remove_task_worktrees_takes_the_whole_directory_but_not_the_branch(monkeypatch) -> None:
    """The commits are the work; these are only checkouts of them."""
    calls = []
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls, _listing("revisor", "work")))

    removed = remove_task_worktrees(_CONTAINER, "/data/projects", "myproj", "task-1")

    assert removed == ["revisor", "work"]
    assert [_in_container(cmd) for cmd in calls] == [
        ["ls", "-1", _WORKTREES],
        ["rm", "-rf", _WORKTREES],
        ["git", "worktree", "prune"],
    ]
    assert not any("branch" in cmd for cmd in calls)


def test_remove_task_worktrees_raises_when_the_directory_survives(monkeypatch) -> None:
    """Reporting a cleanup that did not happen would be worse than failing."""

    def respond(args):
        if args[0] == "rm":
            return (1, "", "rm: cannot remove: Device or resource busy\n")
        return _listing("work")(args)

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker([], respond))

    with pytest.raises(RuntimeError, match="could not remove"):
        remove_task_worktrees(_CONTAINER, "/data/projects", "myproj", "task-1")


@pytest.mark.parametrize(
    "remove",
    [remove_review_worktrees, remove_task_worktrees],
    ids=["review", "task"],
)
@pytest.mark.parametrize(
    "projects_root,slug,task_id",
    [("", "myproj", "task-1"), ("/data/projects", "", "task-1"), ("/data/projects", "myproj", "")],
)
def test_removing_worktrees_rejects_empty_path_components(
    monkeypatch, remove, projects_root, slug, task_id
) -> None:
    """Same guard as create_worktree: an empty component must not widen the rm -rf."""
    calls = []
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls))

    with pytest.raises(ValueError):
        remove(_CONTAINER, projects_root, slug, task_id)

    assert calls == []


def test_task_worktrees_dir_matches_the_path_create_worktree_builds() -> None:
    """The two sides agree, or cleanup would walk a directory nobody writes to."""
    built_path = task_worktrees_dir("/data/projects", "myproj", "task-1")

    assert built_path == _WORKTREES


def test_removing_worktrees_pins_git_locale(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls, _listing("revisor", "work")))

    remove_review_worktrees(_CONTAINER, "/data/projects", "myproj", "task-1")

    assert all("LC_ALL=C" in cmd for cmd in calls if _in_container(cmd)[0] != "ls")


def test_commit_worktree_stages_everything_and_attributes_the_role(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls))

    committed = commit_worktree(
        _CONTAINER, f"{_PROJECT}/worktrees/task-1/work",
        message="agent(implementador): task-1",
        author_name="implementador (cuenta1)",
        author_email="implementador@ia-harness.invalid",
    )

    assert committed is True
    assert [_in_container(cmd) for cmd in calls] == [
        ["git", "add", "-A"],
        [
            "git",
            "-c", "user.name=implementador (cuenta1)",
            "-c", "user.email=implementador@ia-harness.invalid",
            "commit", "-m", "agent(implementador): task-1",
        ],
    ]
    assert all("LC_ALL=C" in cmd for cmd in calls)


def test_commit_worktree_returns_false_when_there_was_nothing_to_commit(monkeypatch) -> None:
    """git says so on *stdout*, with a non-zero exit code."""

    def respond(args):
        if "commit" in args:
            return (1, "On branch agent/task/task-1\nnothing to commit, working tree clean\n", "")
        return (0, "", "")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker([], respond))

    assert commit_worktree(_CONTAINER, "/wd", "msg", "name", "mail@example.invalid") is False


def test_commit_worktree_raises_when_the_commit_really_fails(monkeypatch) -> None:
    def respond(args):
        if "commit" in args:
            return (128, "", "fatal: unable to write new index file\n")
        return (0, "", "")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker([], respond))

    with pytest.raises(RuntimeError, match="git commit failed"):
        commit_worktree(_CONTAINER, "/wd", "msg", "name", "mail@example.invalid")


def test_commit_worktree_raises_when_staging_fails(monkeypatch) -> None:
    def respond(args):
        return (128, "", "fatal: not a git repository\n")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker([], respond))

    with pytest.raises(RuntimeError, match="git add failed"):
        commit_worktree(_CONTAINER, "/wd", "msg", "name", "mail@example.invalid")


def test_commit_worktree_stages_only_the_paths_a_role_was_scoped_to(monkeypatch) -> None:
    """The auditor commits after the review, so it commits only its own docs."""
    calls = []
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls))

    committed = commit_worktree(
        _CONTAINER, f"{_PROJECT}/worktrees/task-1/work",
        message="agent(auditor): task-1",
        author_name="auditor (cuenta1)",
        author_email="auditor@ia-harness.invalid",
        paths=["docs"],
    )

    assert committed is True
    assert ["git", "add", "-A", "--", "docs"] in [_in_container(cmd) for cmd in calls]
    assert ["git", "add", "-A"] not in [_in_container(cmd) for cmd in calls]


def test_commit_worktree_checks_the_scope_exists_before_staging_it(monkeypatch) -> None:
    """`git add -- docs` exits 128 on a tree with no docs/, and a phase that
    wrote nothing is the ordinary case rather than a failure."""
    calls = []

    def respond(args):
        return (1, "", "") if args[:2] == ["test", "-e"] else (0, "", "")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls, respond))

    committed = commit_worktree(
        _CONTAINER, "/wd", "msg", "name", "mail@example.invalid", paths=["docs"],
    )

    assert committed is False
    assert [_in_container(cmd)[0] for cmd in calls] == ["test"]


def test_commit_worktree_warns_about_what_a_scoped_commit_leaves_behind(
    monkeypatch, caplog,
) -> None:
    """A phase writing outside its scope is worth seeing, not dropping silently."""

    def respond(args):
        if args[:2] == ["git", "status"]:
            return (0, " M docs/README.md\n M src/app.py\n?? notes.txt\n", "")
        return (0, "", "")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker([], respond))

    with caplog.at_level(logging.WARNING, logger="dispatcher.docker_exec"):
        commit_worktree(
            _CONTAINER, "/wd", "msg", "name", "mail@example.invalid", paths=["docs"],
        )

    assert "src/app.py" in caplog.text
    assert "notes.txt" in caplog.text
    assert "docs/README.md" not in caplog.text


def test_commit_worktree_is_quiet_when_a_scoped_commit_leaves_nothing_behind(
    monkeypatch, caplog,
) -> None:
    def respond(args):
        if args[:2] == ["git", "status"]:
            return (0, " M docs/README.md\n?? docs/learnings/L-001.md\n", "")
        return (0, "", "")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker([], respond))

    with caplog.at_level(logging.WARNING, logger="dispatcher.docker_exec"):
        commit_worktree(
            _CONTAINER, "/wd", "msg", "name", "mail@example.invalid", paths=["docs"],
        )

    assert caplog.text == ""


def test_commit_worktree_commits_anyway_when_the_scope_check_cannot_run(
    monkeypatch, caplog,
) -> None:
    """The warning is an observation about a commit that is otherwise fine."""

    def respond(args):
        return (128, "", "fatal: bad\n") if args[:2] == ["git", "status"] else (0, "", "")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker([], respond))

    with caplog.at_level(logging.WARNING, logger="dispatcher.docker_exec"):
        committed = commit_worktree(
            _CONTAINER, "/wd", "msg", "name", "mail@example.invalid", paths=["docs"],
        )

    assert committed is True
    assert caplog.text == ""


def test_read_owner_returns_the_host_uid_and_gid(monkeypatch) -> None:
    calls = []

    def respond(args):
        return (0, "1000:1000\n", "")

    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls, respond))

    assert read_owner(_CONTAINER, _PROJECT) == "1000:1000"
    assert _in_container(calls[0]) == ["stat", "-c", "%u:%g", _PROJECT]


@pytest.mark.parametrize("returncode,stdout", [(1, ""), (0, "\n")])
def test_read_owner_returns_none_when_stat_gives_nothing_usable(monkeypatch, returncode, stdout) -> None:
    monkeypatch.setattr(
        docker_exec_mod.subprocess, "run", _fake_docker([], lambda args: (returncode, stdout, "")),
    )

    assert read_owner(_CONTAINER, _PROJECT) is None


def test_restore_owner_chowns_the_tree_back(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls))

    restore_owner(_CONTAINER, _PROJECT, "1000:1000")

    assert _in_container(calls[0]) == ["chown", "-R", "1000:1000", _PROJECT]


def test_restore_owner_does_nothing_without_an_owner(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls))

    restore_owner(_CONTAINER, _PROJECT, None)

    assert calls == []


def test_restore_owner_warns_instead_of_raising_when_chown_fails(monkeypatch, caplog) -> None:
    """It runs in a finally, on a path that may already be failing."""
    monkeypatch.setattr(
        docker_exec_mod.subprocess, "run",
        _fake_docker([], lambda args: (1, "", "chown: changing ownership: Read-only file system\n")),
    )

    with caplog.at_level(logging.WARNING):
        restore_owner(_CONTAINER, _PROJECT, "1000:1000")

    assert "could not restore ownership" in caplog.text


def _merge_responder(status_out="", symbolic=("main", 0), merge=(0, "Merge made by the 'ort' strategy.\n", ""), branch_exists=True):
    """A docker exec that answers each of the merge's probes separately."""

    def respond(args):
        if args[:2] == ["git", "show-ref"]:
            return (0 if branch_exists else 1, "", "")
        if args[:2] == ["git", "symbolic-ref"]:
            name, rc = symbolic
            return (rc, f"{name}\n" if name else "", "")
        if args[:2] == ["git", "status"]:
            return (0, status_out, "")
        if "merge" in args and "--abort" in args:
            return (128, "", "fatal: There is no merge to abort\n")
        if "merge" in args:
            return merge
        return (0, "", "")

    return respond


def _merge_call(calls):
    """The actual `git merge` out of a recorded run, or None."""
    for args in map(_in_container, calls):
        if "merge" in args and "--abort" not in args:
            return args
    return None


def test_merge_task_branch_merges_into_the_branch_the_project_is_on(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls, _merge_responder()))

    outcome = merge_task_branch(_CONTAINER, "/data/projects", "myproj", "task-1")

    assert outcome.status == docker_exec_mod.MERGED
    assert outcome.merged and not outcome.refused
    assert outcome.target == "main"
    merge = _merge_call(calls)
    assert merge[-3:] == ["-m", f"Merge {_BRANCH}", _BRANCH]
    assert "--no-ff" in merge and "--no-edit" in merge


def test_merge_task_branch_runs_in_the_project_not_a_worktree(monkeypatch) -> None:
    """The merge has to land in the checkout the human works from."""
    calls = []
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls, _merge_responder()))

    merge_task_branch(_CONTAINER, "/data/projects", "myproj", "task-1")

    assert all(cmd[cmd.index("-w") + 1] == _PROJECT for cmd in calls)


def test_merge_task_branch_reports_an_already_merged_branch_as_up_to_date(monkeypatch) -> None:
    """Re-running the merge is not a failure, and must not read like one."""
    monkeypatch.setattr(
        docker_exec_mod.subprocess, "run",
        _fake_docker([], _merge_responder(merge=(0, "Already up to date.\n", ""))),
    )

    outcome = merge_task_branch(_CONTAINER, "/data/projects", "myproj", "task-1")

    assert outcome.status == docker_exec_mod.UP_TO_DATE
    assert not outcome.merged and not outcome.refused


def test_merge_task_branch_ignores_untracked_files(monkeypatch) -> None:
    """`worktrees/` lives in the repo, so untracked output would block every merge."""
    calls = []
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls, _merge_responder()))

    merge_task_branch(_CONTAINER, "/data/projects", "myproj", "task-1")

    status = next(args for args in map(_in_container, calls) if args[:2] == ["git", "status"])
    assert "--untracked-files=no" in status


def test_merge_task_branch_refuses_a_dirty_tree_without_starting_a_merge(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        docker_exec_mod.subprocess, "run",
        _fake_docker(calls, _merge_responder(status_out=" M README.md\n")),
    )

    outcome = merge_task_branch(_CONTAINER, "/data/projects", "myproj", "task-1")

    assert outcome.refused
    assert "uncommitted changes" in outcome.detail
    assert _merge_call(calls) is None


def test_merge_task_branch_refuses_a_detached_head(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        docker_exec_mod.subprocess, "run",
        _fake_docker(calls, _merge_responder(symbolic=("", 1))),
    )

    outcome = merge_task_branch(_CONTAINER, "/data/projects", "myproj", "task-1")

    assert outcome.refused
    assert outcome.target is None
    assert "detached HEAD" in outcome.detail
    assert _merge_call(calls) is None


def test_merge_task_branch_refuses_to_merge_a_branch_into_itself(monkeypatch) -> None:
    """The writers' worktree can hold the branch, but so can the project checkout."""
    calls = []
    monkeypatch.setattr(
        docker_exec_mod.subprocess, "run",
        _fake_docker(calls, _merge_responder(symbolic=(_BRANCH, 0))),
    )

    outcome = merge_task_branch(_CONTAINER, "/data/projects", "myproj", "task-1")

    assert outcome.refused
    assert _merge_call(calls) is None


def test_merge_task_branch_refuses_when_the_task_branch_does_not_exist(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        docker_exec_mod.subprocess, "run",
        _fake_docker(calls, _merge_responder(branch_exists=False)),
    )

    outcome = merge_task_branch(_CONTAINER, "/data/projects", "myproj", "task-1")

    assert outcome.refused
    assert _BRANCH in outcome.detail
    assert _merge_call(calls) is None


def test_merge_task_branch_aborts_a_conflicted_merge(monkeypatch) -> None:
    """A conflict must not leave the project's checkout mid-merge."""
    calls = []
    conflict = (1, "CONFLICT (content): Merge conflict in app.py\n", "Automatic merge failed\n")
    monkeypatch.setattr(
        docker_exec_mod.subprocess, "run", _fake_docker(calls, _merge_responder(merge=conflict)),
    )

    outcome = merge_task_branch(_CONTAINER, "/data/projects", "myproj", "task-1")

    assert outcome.refused
    assert "rolled back" in outcome.detail
    assert _in_container(calls[-1]) == ["git", "merge", "--abort"]


def test_merge_task_branch_survives_an_abort_that_had_nothing_to_abort(monkeypatch) -> None:
    """git exits non-zero there, and that rc is not the caller's problem."""
    monkeypatch.setattr(
        docker_exec_mod.subprocess, "run",
        _fake_docker([], _merge_responder(merge=(128, "", "fatal: refusing to merge unrelated histories\n"))),
    )

    outcome = merge_task_branch(_CONTAINER, "/data/projects", "myproj", "task-1")

    assert outcome.refused
    assert "unrelated histories" in outcome.detail


def test_merge_task_branch_never_deletes_the_task_branch(monkeypatch) -> None:
    """The branch is the work; the merge only offers it somewhere else."""
    calls = []
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls, _merge_responder()))

    merge_task_branch(_CONTAINER, "/data/projects", "myproj", "task-1")

    assert not any("branch" in args and "-d" in args for args in map(_in_container, calls))
    assert not any("--delete" in args for args in map(_in_container, calls))


@pytest.mark.parametrize("projects_root,slug,task_id", [("", "myproj", "task-1"), ("/data/projects", "", "task-1"), ("/data/projects", "myproj", "")])
def test_merge_task_branch_rejects_empty_path_components(monkeypatch, projects_root, slug, task_id) -> None:
    calls = []
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls))

    with pytest.raises(ValueError):
        merge_task_branch(_CONTAINER, projects_root, slug, task_id)

    assert calls == []


def test_current_branch_returns_none_on_a_detached_head(monkeypatch) -> None:
    monkeypatch.setattr(
        docker_exec_mod.subprocess, "run", _fake_docker([], lambda args: (1, "", "")),
    )

    assert docker_exec_mod.current_branch(_CONTAINER, _PROJECT) is None


def _workdir(cmd):
    """The -w the call ran in, i.e. where the redirect below lands."""
    return cmd[cmd.index("-w") + 1]


def _exclude_responder(common_dir=".git", exclude=(0, ""), append=(0, "", "")):
    """A docker exec that answers the exclude file's probes separately.

    `common_dir` is what `git rev-parse` prints (None for "not a repository"),
    `exclude` is the (returncode, contents) of cat-ing the file.
    """
    def respond(args):
        if args[:2] == ["git", "rev-parse"]:
            return (1, "", "") if common_dir is None else (0, f"{common_dir}\n", "")
        if args[0] == "cat":
            returncode, text = exclude
            return (returncode, text, "")
        if args[0] == "sh":
            return append
        return (0, "", "")
    return respond


def _appends(calls):
    """The shell calls that write to the exclude file."""
    return [cmd for cmd in calls if _in_container(cmd)[0] == "sh"]


def test_ensure_worktrees_ignored_excludes_the_directory_in_the_repositorys_own_git_dir(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls, _exclude_responder()))

    assert docker_exec_mod.ensure_worktrees_ignored(_CONTAINER, _PROJECT) is True

    appended = _appends(calls)
    assert len(appended) == 1
    assert _workdir(appended[0]) == f"{_PROJECT}/.git/info"
    # Redirected into the file rather than passed as a path, so the workdir is
    # the only thing saying which repository this lands in.
    assert _in_container(appended[0])[:2] == ["sh", "-c"]
    assert _in_container(appended[0])[2].endswith(">> exclude")


def test_ensure_worktrees_ignored_anchors_the_entry_to_the_repository_root() -> None:
    """A project with its own src/worktrees/ must go on seeing it."""
    assert docker_exec_mod.WORKTREES_EXCLUDE_ENTRY == "/worktrees/"


def test_ensure_worktrees_ignored_writes_the_anchored_entry(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls, _exclude_responder()))

    docker_exec_mod.ensure_worktrees_ignored(_CONTAINER, _PROJECT)

    script = _in_container(_appends(calls)[0])[2]
    assert "'/worktrees/'" in script
    assert script.startswith("printf ")


def test_ensure_worktrees_ignored_does_nothing_when_the_entry_is_already_there(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        docker_exec_mod.subprocess, "run",
        _fake_docker(calls, _exclude_responder(exclude=(0, "*.log\n/worktrees/\n"))),
    )

    assert docker_exec_mod.ensure_worktrees_ignored(_CONTAINER, _PROJECT) is False
    assert _appends(calls) == []


def test_ensure_worktrees_ignored_accepts_the_entry_someone_wrote_unanchored(monkeypatch) -> None:
    """`worktrees/` by hand already covers our directory: don't pile on."""
    calls = []
    monkeypatch.setattr(
        docker_exec_mod.subprocess, "run",
        _fake_docker(calls, _exclude_responder(exclude=(0, "# stuff\nworktrees/\n"))),
    )

    assert docker_exec_mod.ensure_worktrees_ignored(_CONTAINER, _PROJECT) is False
    assert _appends(calls) == []


def test_ensure_worktrees_ignored_is_not_fooled_by_a_longer_line(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        docker_exec_mod.subprocess, "run",
        _fake_docker(calls, _exclude_responder(exclude=(0, "src/worktrees/keep\n"))),
    )

    assert docker_exec_mod.ensure_worktrees_ignored(_CONTAINER, _PROJECT) is True


def test_ensure_worktrees_ignored_follows_git_to_an_absolute_git_dir(monkeypatch) -> None:
    """A separate .git directory is where the exclude file actually lives."""
    calls = []
    monkeypatch.setattr(
        docker_exec_mod.subprocess, "run",
        _fake_docker(calls, _exclude_responder(common_dir="/srv/repos/myproj.git")),
    )

    docker_exec_mod.ensure_worktrees_ignored(_CONTAINER, _PROJECT)

    assert _workdir(_appends(calls)[0]) == "/srv/repos/myproj.git/info"


def test_ensure_worktrees_ignored_leaves_a_directory_that_is_not_a_repository_alone(monkeypatch) -> None:
    """bootstrap-project makes the directory before anyone clones into it."""
    calls = []
    monkeypatch.setattr(
        docker_exec_mod.subprocess, "run",
        _fake_docker(calls, _exclude_responder(common_dir=None)),
    )

    assert docker_exec_mod.ensure_worktrees_ignored(_CONTAINER, _PROJECT) is False
    assert _appends(calls) == []
    assert not any(_in_container(cmd)[0] == "mkdir" for cmd in calls)


def test_ensure_worktrees_ignored_creates_an_info_directory_that_is_missing(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        docker_exec_mod.subprocess, "run",
        _fake_docker(calls, _exclude_responder(exclude=(1, ""))),
    )

    assert docker_exec_mod.ensure_worktrees_ignored(_CONTAINER, _PROJECT) is True
    mkdirs = [_in_container(cmd) for cmd in calls if _in_container(cmd)[0] == "mkdir"]
    assert mkdirs == [["mkdir", "-p", f"{_PROJECT}/.git/info"]]


def test_ensure_worktrees_ignored_reports_a_write_it_could_not_make(monkeypatch, caplog) -> None:
    calls = []
    monkeypatch.setattr(
        docker_exec_mod.subprocess, "run",
        _fake_docker(calls, _exclude_responder(append=(1, "", "Read-only file system\n"))),
    )

    with caplog.at_level(logging.WARNING):
        assert docker_exec_mod.ensure_worktrees_ignored(_CONTAINER, _PROJECT) is False

    assert "Read-only file system" in caplog.text


def test_create_worktree_excludes_the_directory_before_it_creates_one(monkeypatch) -> None:
    """The noise never appears, rather than being cleaned up afterwards."""
    calls = []
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker(calls, _exclude_responder()))

    create_worktree(_CONTAINER, "/data/projects", "myproj", "task-1", "arquitecto")

    order = [_in_container(cmd) for cmd in calls]
    excluded = next(i for i, args in enumerate(order) if args[0] == "sh")
    added = next(i for i, args in enumerate(order) if args[:3] == ["git", "worktree", "add"])
    assert excluded < added


def test_create_worktree_does_not_fail_a_phase_over_the_exclude_file(monkeypatch) -> None:
    """Hygiene: a repository that won't take the entry still gets its worktree."""
    calls = []
    monkeypatch.setattr(
        docker_exec_mod.subprocess, "run",
        _fake_docker(calls, _exclude_responder(append=(1, "", "denied\n"))),
    )

    path = create_worktree(_CONTAINER, "/data/projects", "myproj", "task-1", "arquitecto")

    assert path == f"{_PROJECT}/worktrees/task-1/work"
