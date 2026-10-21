"""The task-branch push after a committing phase (ADR 271, T-127).

Two halves. `docker_exec.push_task_branch` runs against real git
repositories the way test_push_base.py runs the base push: the in-container
command runs on the host, the project's `origin` is an https://github.com/
URL, and only the push itself is rewritten onto a bare repository in tmp_path.
The hook in `dispatch_phase` runs against the same fakes test_dispatcher.py
uses, with the tip reads and the push itself faked here.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import dispatcher.dispatcher as dispatcher_mod
from dispatcher import docker_exec as docker_exec_mod
from dispatcher.context_transfer import read_task_file, task_file_path
from dispatcher.docker_exec import ClaudeResult
from tests.dispatcher.test_dispatcher import (  # noqa: F401 - fixtures used by name
    _fake_worktree,
    _make_config,
    _phase_exec,
    _phases_may_end_in_prose,
    _split_ctx,
    fake_git,
)

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

_CONTAINER = "agent-cuenta1"
_URL = "https://github.com/acme/myproj.git"
_BRANCH = "agent/task/T-1"
_ISOLATED = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


# --- docker_exec.push_task_branch, against real git ---------------------------


def _run(command, cwd, env=None, timeout=None):
    return subprocess.run(
        command, cwd=cwd, env={**os.environ, **_ISOLATED, **(env or {})},
        capture_output=True, text=True, timeout=timeout,
    )


def _git(cwd, *args, env=None) -> str:
    proc = _run(["git", *args], str(cwd), env=env)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _commit(cwd, name, text, message) -> str:
    (Path(cwd) / name).write_text(text)
    _git(cwd, "add", name)
    _git(cwd, "commit", "-q", "-m", message)
    return _git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def remote(tmp_path, monkeypatch):
    """A project on `main` tracking `origin/main`, with a task branch beside it."""
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    rewrite = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"url.{bare.as_uri()}.insteadOf",
        "GIT_CONFIG_VALUE_0": _URL,
    }
    commands: list[list[str]] = []

    def local_exec(container, workdir, command, env=None, timeout=None):
        assert container == _CONTAINER
        commands.append(list(command))
        extra = dict(env or {})
        if command[:1] == ["git"] and "push" in command:
            extra.update(rewrite)
        return _run(command, workdir, env=extra, timeout=timeout)

    monkeypatch.setattr(docker_exec_mod, "run_docker_exec", local_exec)
    projects = tmp_path / "projects"
    project = projects / "myproj"
    project.mkdir(parents=True)
    _git(project, "init", "-q", "-b", "main")
    base = _commit(project, "app.txt", "one\n", "start")
    _git(project, "remote", "add", "origin", _URL)
    _git(project, "push", "-q", "origin", "main", env=rewrite)
    _git(project, "config", "branch.main.remote", "origin")
    _git(project, "config", "branch.main.merge", "refs/heads/main")
    # The task branch the way a writer worktree leaves it: a commit of its
    # own, while the clone itself stays on the base.
    _git(project, "branch", _BRANCH)
    _git(project, "checkout", "-q", _BRANCH)
    task_tip = _commit(project, "app.txt", "two\n", "agent(implementador): T-1 round 1")
    _git(project, "checkout", "-q", "main")
    # The base moves locally too, so a push that took it along would show.
    _commit(project, "base.txt", "local\n", "unpushed base work")
    return dict(
        projects=str(projects), project=project, bare=bare, rewrite=rewrite,
        commands=commands, tmp=tmp_path, base=base, task_tip=task_tip,
    )


def _push(remote):
    # Credential paths that do not exist: the bare remote needs neither.
    return docker_exec_mod.push_task_branch(
        _CONTAINER, remote["projects"], "myproj", "T-1",
        credentials_path=str(remote["tmp"] / "no-credentials"),
        keys_dir=str(remote["tmp"] / "no-keys"),
    )


def _remote_ref(remote, ref) -> str | None:
    proc = _run(["git", "rev-parse", "--verify", "--quiet", ref], str(remote["bare"]))
    return proc.stdout.strip() if proc.returncode == 0 else None


def _assert_never_forced(remote) -> None:
    for command in remote["commands"]:
        if "push" in command:
            assert "--force" not in command
            assert "--force-with-lease" not in command
            assert "-f" not in command
            assert not any(arg.startswith("+") for arg in command)


@needs_git
def test_the_task_branch_lands_on_the_remote_under_its_own_name(remote) -> None:
    outcome = _push(remote)

    assert outcome.pushed and not outcome.refused
    assert outcome.target == _BRANCH
    assert _remote_ref(remote, f"refs/heads/{_BRANCH}") == remote["task_tip"]
    assert outcome.detail == f"pushed {_BRANCH} to origin at {remote['task_tip'][:7]}"
    _assert_never_forced(remote)


@needs_git
def test_the_base_is_never_pushed_with_the_task_branch(remote) -> None:
    _push(remote)

    # The base gained a commit locally; the remote's main is where it was.
    assert _remote_ref(remote, "refs/heads/main") == remote["base"]
    pushes = [command for command in remote["commands"] if "push" in command]
    assert len(pushes) == 1
    assert pushes[0][-1] == f"refs/heads/{_BRANCH}:refs/heads/{_BRANCH}"


@needs_git
def test_a_task_branch_the_remote_moved_past_is_refused_and_kept(remote) -> None:
    # Someone else pushed a different commit to the same branch name.
    other = remote["tmp"] / "other"
    _git(remote["tmp"], "clone", "-q", str(remote["bare"]), str(other))
    _git(other, "checkout", "-q", "-b", _BRANCH)
    theirs = _commit(other, "theirs.txt", "x\n", "theirs")
    _git(other, "push", "-q", "origin", _BRANCH)

    outcome = _push(remote)

    assert outcome.refused and not outcome.pushed
    assert outcome.status == docker_exec_mod.PUSH_REFUSED
    assert _remote_ref(remote, f"refs/heads/{_BRANCH}") == theirs
    assert _git(remote["project"], "rev-parse", _BRANCH) == remote["task_tip"]
    _assert_never_forced(remote)


@needs_git
def test_a_base_with_no_remote_pushes_nothing(remote) -> None:
    _git(remote["project"], "config", "--unset", "branch.main.remote")

    outcome = _push(remote)

    assert outcome.status == docker_exec_mod.NO_UPSTREAM
    assert not any("push" in command for command in remote["commands"])


# --- the hook in dispatch_phase -----------------------------------------------


class _Branch:
    """The task branch's tip as the hook reads it, and the pushes it asks for."""

    def __init__(self, tips, outcome=None, error=None):
        self.tips = list(tips)
        self.reads = []
        self.pushes = []
        self.outcome = outcome or docker_exec_mod.PushOutcome(
            docker_exec_mod.PUSHED, _BRANCH, f"pushed {_BRANCH} to origin at bbbbbbb"
        )
        self.error = error

    def resolve_commit(self, container, workdir, rev):
        self.reads.append((workdir, rev))
        return self.tips.pop(0) if self.tips else None

    def push_task_branch(self, container, projects_root, slug, task_id, **kwargs):
        self.pushes.append((container, projects_root, slug, task_id, kwargs))
        if self.error is not None:
            raise self.error
        return self.outcome


@pytest.fixture
def phase(tmp_path, monkeypatch, fake_git):
    """dispatch_phase for an implementador round on task T-1, with `branch` faked."""
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "exec_claude",
        _phase_exec(ClaudeResult(session_id="sess-1", result_text="done", raw={"is_error": False})),
    )
    _fake_worktree(monkeypatch)

    def run(branch, at_base=False, **overrides):
        monkeypatch.setattr(dispatcher_mod.docker_exec, "resolve_commit", branch.resolve_commit)
        monkeypatch.setattr(
            dispatcher_mod.docker_exec, "push_task_branch", branch.push_task_branch
        )
        cfg = _make_config(tmp_path, **overrides)
        return dispatcher_mod.dispatch_phase(
            cfg, "T-1", "myproj", "implementador", "do it", round_num=1, at_base=at_base,
        )

    return run


def test_the_task_branch_is_not_pushed_by_default(phase) -> None:
    branch = _Branch(["a" * 40, "b" * 40])

    result = phase(branch)

    assert result.success
    assert branch.pushes == []
    assert branch.reads == []
    assert result.branch_push == ""


def test_a_project_set_to_false_is_not_pushed(phase) -> None:
    branch = _Branch(["a" * 40, "b" * 40])

    phase(branch, push_task_branch={"myproj": False})

    assert branch.pushes == []


def test_a_committing_phase_pushes_the_task_branch(phase, fake_git) -> None:
    branch = _Branch(["a" * 40, "b" * 40])

    result = phase(branch, push_task_branch={"myproj": True})

    assert result.success
    assert len(fake_git.commits) == 1
    assert len(branch.pushes) == 1
    container, projects_root, slug, task_id, kwargs = branch.pushes[0]
    assert (container, slug, task_id) == ("agent-cuenta1", "myproj", "T-1")
    assert projects_root.endswith("/projects")
    # The tip is read on the project clone, the branch the worktree shares.
    assert branch.reads == [
        (f"{projects_root}/myproj", f"refs/heads/{_BRANCH}"),
        (f"{projects_root}/myproj", f"refs/heads/{_BRANCH}"),
    ]
    assert result.branch_push == ""


def test_the_push_is_inside_the_ownership_window(phase, fake_git, monkeypatch) -> None:
    """The push writes a remote-tracking ref under .git/ as root."""
    branch = _Branch(["a" * 40, "b" * 40])
    seen = []
    real_push = branch.push_task_branch

    def recording_push(*args, **kwargs):
        seen.append(list(fake_git.restored))
        return real_push(*args, **kwargs)

    branch.push_task_branch = recording_push

    phase(branch, push_task_branch={"myproj": True})

    assert seen == [[]]
    assert len(fake_git.restored) == 1


def test_a_phase_that_left_no_new_commit_pushes_nothing(phase) -> None:
    branch = _Branch(["a" * 40, "a" * 40])

    result = phase(branch, push_task_branch={"myproj": True})

    assert result.success
    assert branch.pushes == []
    assert result.branch_push == ""


def test_a_phase_at_the_base_pushes_nothing(phase, monkeypatch) -> None:
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role, at_base=False: (
            f"{projects_root}/{slug}"
        ),
    )
    branch = _Branch(["a" * 40, "b" * 40])

    phase(branch, at_base=True, push_task_branch={"myproj": True})

    assert branch.pushes == []
    assert branch.reads == []


def test_a_refused_push_is_noted_and_the_phase_still_succeeds(phase, caplog) -> None:
    branch = _Branch(
        ["a" * 40, "b" * 40],
        outcome=docker_exec_mod.PushOutcome(
            docker_exec_mod.PUSH_REFUSED, _BRANCH,
            f"pushing {_BRANCH} to origin was refused; the branch is kept locally as it is: "
            "! [rejected] (non-fast-forward)",
        ),
    )

    result = phase(branch, push_task_branch={"myproj": True})

    assert result.success
    assert result.branch_push.startswith(f"{_BRANCH} was not pushed after this phase: ")
    assert "non-fast-forward" in result.branch_push
    assert "was not pushed after the implementador phase" in caplog.text


def test_a_push_that_raises_is_noted_and_the_phase_still_succeeds(phase) -> None:
    branch = _Branch(["a" * 40, "b" * 40], error=RuntimeError("docker daemon went away"))

    result = phase(branch, push_task_branch={"myproj": True})

    assert result.success
    assert "the push could not run: docker daemon went away" in result.branch_push


def test_a_tip_that_cannot_be_read_never_fails_the_phase(phase, monkeypatch) -> None:
    branch = _Branch([])

    def broken(container, workdir, rev):
        raise RuntimeError("no container")

    branch.resolve_commit = broken

    result = phase(branch, push_task_branch={"myproj": True})

    assert result.success
    assert branch.pushes == []


def test_the_note_goes_into_the_handoff(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path, push_task_branch={"myproj": True})
    note = f"{_BRANCH} was not pushed after this phase: the remote said no"

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, **kwargs):
        return dispatcher_mod.DispatchResult(
            success=True, session_id=None, result_text="ok", account="cuenta1",
            branch_push=note,
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    dispatcher_mod.run_phase(_split_ctx(cfg), "implementador", round_num=1)

    body = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1")).body
    assert f"## Task-branch push\n\n{note}" in body
