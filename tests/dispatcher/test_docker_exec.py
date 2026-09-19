import json
import logging
import subprocess

import pytest

import dispatcher.docker_exec as docker_exec_mod
from dispatcher.docker_exec import (
    commit_worktree,
    create_worktree,
    exec_claude,
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
        ["rm", "-rf", path],
        ["git", "worktree", "prune"],
        ["git", "worktree", "add", "--detach", path, _BRANCH],
    ]


def test_create_worktree_gives_each_reviewer_its_own_path(monkeypatch) -> None:
    monkeypatch.setattr(docker_exec_mod.subprocess, "run", _fake_docker([]))

    paths = {
        role: create_worktree(_CONTAINER, "/data/projects", "myproj", "task-1", role)
        for role in ("arquitecto", "revisor", "auditor")
    }

    assert len(set(paths.values())) == 3


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
