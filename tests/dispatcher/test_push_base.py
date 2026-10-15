"""The base push against real git repositories (A-4, ADR 78).

Like test_premerge.py, the in-container command runs on the host instead, in
throwaway repositories under tmp_path, so what is asserted is what git did.
The remote is a bare repository in the same tmp dir and nothing else: the
project's `origin` is an https://github.com/ URL, the shape push_base serves
with the mounted token, and only the push command itself is handed a
one-command `url.<bare>.insteadOf` rewrite (through GIT_CONFIG_COUNT), so the
push lands in the bare repository and could reach nothing on the network.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from dispatcher import docker_exec as docker_exec_mod

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

_CONTAINER = "agent-cuenta1"
_URL = "https://github.com/acme/myproj.git"
_ISOLATED = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


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
    """A project on `main` tracking `origin/main`, origin being a bare repo here.

    Returns the paths, the bare repository's rewrite env, and the argv of every
    command push_base ran.
    """
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
    _commit(project, "app.txt", "one\n", "start")
    _git(project, "remote", "add", "origin", _URL)
    _git(project, "push", "-q", "origin", "main", env=rewrite)
    _git(project, "config", "branch.main.remote", "origin")
    _git(project, "config", "branch.main.merge", "refs/heads/main")
    return dict(
        projects=str(projects), project=project, bare=bare, rewrite=rewrite,
        commands=commands, tmp=tmp_path,
    )


def _push(remote):
    # Credential paths that do not exist: the test never reads a real token
    # or key, and the bare remote needs neither.
    return docker_exec_mod.push_base(
        _CONTAINER, remote["projects"], "myproj",
        credentials_path=str(remote["tmp"] / "no-credentials"),
        keys_dir=str(remote["tmp"] / "no-keys"),
    )


def _remote_tip(remote) -> str:
    return _git(remote["bare"], "rev-parse", "refs/heads/main")


def _assert_never_forced(remote) -> None:
    for command in remote["commands"]:
        if "push" in command:
            assert "--force" not in command
            assert "--force-with-lease" not in command
            assert not any(arg.startswith("+") for arg in command)


def test_a_merged_base_is_pushed_to_its_upstream(remote) -> None:
    head = _commit(remote["project"], "app.txt", "two\n", "merged task")

    outcome = _push(remote)

    assert outcome.pushed and not outcome.refused
    assert outcome.status == docker_exec_mod.PUSHED
    assert outcome.target == "main"
    assert _remote_tip(remote) == head
    assert outcome.detail == f"pushed main to origin/main at {head[:7]}"
    _assert_never_forced(remote)


def test_a_push_with_nothing_new_is_still_pushed(remote) -> None:
    head = _git(remote["project"], "rev-parse", "HEAD")

    outcome = _push(remote)

    assert outcome.pushed
    assert _remote_tip(remote) == head


def test_a_push_the_remote_moved_past_is_refused_and_nothing_is_reset(remote) -> None:
    # Someone else pushed to main after the project last fetched.
    other = remote["tmp"] / "other"
    _git(remote["tmp"], "clone", "-q", str(remote["bare"]), str(other))
    theirs = _commit(other, "theirs.txt", "theirs\n", "their work")
    _git(other, "push", "-q", "origin", "main")
    ours = _commit(remote["project"], "app.txt", "two\n", "merged task")

    outcome = _push(remote)

    assert outcome.refused and not outcome.pushed
    assert outcome.status == docker_exec_mod.PUSH_REFUSED
    assert outcome.target == "main"
    assert "pushing main to origin/main was refused" in outcome.detail
    assert "is not reset" in outcome.detail
    # The remote kept their commit, and the local base kept the merge.
    assert _remote_tip(remote) == theirs
    assert _git(remote["project"], "rev-parse", "HEAD") == ours
    assert _git(remote["project"], "status", "--porcelain") == ""
    _assert_never_forced(remote)


def test_a_project_with_no_remote_is_not_pushed_and_not_an_error(remote) -> None:
    _git(remote["project"], "remote", "remove", "origin")
    before = _remote_tip(remote)

    outcome = _push(remote)

    assert outcome.status == docker_exec_mod.NO_UPSTREAM
    assert not outcome.pushed and not outcome.refused
    assert outcome.detail == "main tracks no upstream branch; nothing pushed"
    assert _remote_tip(remote) == before
    assert not any("push" in command for command in remote["commands"])


def test_a_branch_tracking_a_remote_that_is_gone_is_not_pushed(remote) -> None:
    _git(remote["project"], "remote", "rename", "origin", "elsewhere")
    # `remote rename` carries the tracking config along; point it back.
    _git(remote["project"], "config", "branch.main.remote", "origin")

    outcome = _push(remote)

    assert outcome.status == docker_exec_mod.NO_UPSTREAM
    assert "has no remote by that name" in outcome.detail


def test_a_detached_checkout_is_not_pushed(remote) -> None:
    _git(remote["project"], "checkout", "-q", "--detach")

    outcome = _push(remote)

    assert outcome.status == docker_exec_mod.NO_UPSTREAM
    assert outcome.target is None


def test_a_remote_neither_ssh_nor_https_is_refused_without_trying(remote) -> None:
    _git(remote["project"], "remote", "set-url", "origin", str(remote["bare"]))

    outcome = _push(remote)

    assert outcome.refused
    assert "neither an SSH nor an https:// remote" in outcome.detail
    assert not any("push" in command for command in remote["commands"])
