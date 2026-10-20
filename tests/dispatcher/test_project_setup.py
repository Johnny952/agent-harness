"""A new project's clone, init, keygen and check (ADR 212), against real git.

Like test_push_base.py, the in-container command runs on the host instead, in
throwaway repositories under tmp_path. The remote is an SSH URL, the only kind
a record holds, and the one command that reaches it is handed a
`url.<bare>.insteadOf` rewrite (through GIT_CONFIG_COUNT), so it lands in a
bare repository in the same tmp dir and could reach nothing on the network.
The deploy key is a placeholder file in a tmp keys dir: `_remote_access` only
asks that it is there, and the rewrite means ssh never runs. The write check's
scratch repository goes under tmp_path too, not the host's /tmp.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from dispatcher import actions, context_transfer, docker_exec, project_keys, projects, worker as worker_mod
from dispatcher import dispatcher as core
from tests.dispatcher.test_dispatcher import _make_config

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
needs_ssh_keygen = pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="no ssh-keygen")

_CONTAINER = "agent-cuenta1"
_URL = "git@github.com:acme/myproj.git"
_GONE = "git@github.com:acme/gone.git"
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


def _git(cwd, *args) -> str:
    proc = _run(["git", *args], str(cwd))
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.fixture
def git_host(tmp_path, monkeypatch):
    """A bare repository with one commit on `main`, served as `_URL`.

    Returns the projects root, the keys dir and the argv of every command run.
    """
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(bare), str(seed))
    (seed / "README").write_text("hello\n")
    _git(seed, "add", "README")
    _git(seed, "commit", "-q", "-m", "first")
    _git(seed, "push", "-q", "origin", "HEAD:main")
    rewrite = {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": f"url.{bare.as_uri()}.insteadOf",
        "GIT_CONFIG_VALUE_0": _URL,
        # A remote that does not answer: a bare repository that is not there.
        "GIT_CONFIG_KEY_1": f"url.{(tmp_path / 'gone.git').as_uri()}.insteadOf",
        "GIT_CONFIG_VALUE_1": _GONE,
    }
    commands: list[list[str]] = []

    def local_exec(container, workdir, command, env=None, timeout=None):
        assert container == _CONTAINER
        commands.append(list(command))
        extra = dict(env or {})
        if command[:1] == ["git"] and (
            "clone" in command or "ls-remote" in command or "push" in command
        ):
            extra.update(rewrite)
        return _run(command, workdir, env=extra, timeout=timeout)

    monkeypatch.setattr(docker_exec, "run_docker_exec", local_exec)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(docker_exec, "WRITE_CHECK_SCRATCH", str(scratch))
    # Not tmp_path/projects: that is where the hive root's records are.
    root = tmp_path / "checkouts"
    root.mkdir()
    keys = tmp_path / "keys"
    return root, keys, commands


def _place_key(keys: Path, slug: str = "myproj") -> None:
    # Not a key: `_remote_access` only tests that the file is there.
    (keys / slug).mkdir(parents=True)
    (keys / slug / "id_ed25519").write_text("placeholder\n")


# ---- docker_exec ------------------------------------------------------------


def test_clone_checks_out_the_remote_with_the_projects_key(git_host):
    root, keys, commands = git_host
    _place_key(keys)

    out = docker_exec.clone_project(
        _CONTAINER, str(root), "myproj", _URL, keys_dir=str(keys)
    )

    assert out.ok, out.detail
    assert out.detail == f"cloned {_URL} into {root}/myproj on main"
    assert (root / "myproj" / "README").read_text() == "hello\n"
    clone = next(c for c in commands if "clone" in c)
    assert clone[-2:] == [_URL, "myproj"]


def test_clone_checks_out_the_base_branch_when_the_record_names_one(git_host, tmp_path):
    root, keys, _ = git_host
    _place_key(keys)
    _git(tmp_path / "seed", "push", "-q", "origin", "HEAD:develop")

    out = docker_exec.clone_project(
        _CONTAINER, str(root), "myproj", _URL, "develop", keys_dir=str(keys)
    )

    assert out.ok and out.detail.endswith(" on develop")


def test_clone_without_a_key_is_not_tried(git_host):
    root, keys, commands = git_host

    out = docker_exec.clone_project(
        _CONTAINER, str(root), "myproj", _URL, keys_dir=str(keys)
    )

    assert not out.ok
    assert out.detail.startswith("not cloned: ") and "project-keygen --project myproj" in out.detail
    assert not any("clone" in c for c in commands)
    assert not (root / "myproj").exists()


def test_clone_refuses_a_directory_that_is_already_there(git_host):
    root, keys, commands = git_host
    _place_key(keys)
    (root / "myproj").mkdir()
    (root / "myproj" / "mine.txt").write_text("keep\n")

    out = docker_exec.clone_project(
        _CONTAINER, str(root), "myproj", _URL, keys_dir=str(keys)
    )

    assert not out.ok and out.detail == f"{root}/myproj already exists; it is left as it is"
    assert (root / "myproj" / "mine.txt").read_text() == "keep\n"
    assert not any("clone" in c for c in commands)


def test_a_failed_clone_says_why_and_leaves_nothing(git_host):
    root, keys, _ = git_host
    _place_key(keys)

    out = docker_exec.clone_project(
        _CONTAINER, str(root), "myproj", _URL, "no-such-branch", keys_dir=str(keys)
    )

    assert not out.ok and out.detail.startswith(f"cloning {_URL} failed: ")
    assert out.detail.endswith("one of the repository's deploy keys?)")
    assert not (root / "myproj").exists()


def test_init_makes_an_empty_repository_with_one_empty_commit(git_host):
    root, _, _ = git_host

    out = docker_exec.init_project(
        _CONTAINER, str(root), "fresh", "main",
        author_name="ia-harness", author_email="ia-harness@ia-harness.invalid",
    )

    assert out.ok, out.detail
    assert out.detail == f"made an empty repository at {root}/fresh on main"
    assert _git(root / "fresh", "log", "--format=%an <%ae> %s") == (
        "ia-harness <ia-harness@ia-harness.invalid> Empty repository"
    )
    assert _git(root / "fresh", "ls-files") == ""
    assert _git(root / "fresh", "remote") == ""


def test_init_refuses_a_directory_that_is_already_there(git_host):
    root, _, commands = git_host
    (root / "fresh").mkdir()

    out = docker_exec.init_project(
        _CONTAINER, str(root), "fresh", author_name="a", author_email="a@b.invalid",
    )

    assert not out.ok and "already exists" in out.detail
    assert not any("init" in c for c in commands)


def test_check_lists_the_remotes_heads_without_a_clone(git_host):
    root, keys, _ = git_host
    _place_key(keys)

    out = docker_exec.check_remote(_CONTAINER, str(root), "myproj", _URL, keys_dir=str(keys))

    assert out.ok and out.detail.startswith(f"{_URL} answered with 1 branch(es); ")
    assert list(root.iterdir()) == []


def test_check_tells_write_access_by_a_dry_run_push_that_leaves_no_trace(git_host, tmp_path):
    root, keys, commands = git_host
    _place_key(keys)
    bare = tmp_path / "remote.git"
    refs_before = _git(bare, "for-each-ref")
    objects_before = sorted(p.name for p in (bare / "objects").rglob("*") if p.is_file())

    out = docker_exec.check_remote(_CONTAINER, str(root), "myproj", _URL, keys_dir=str(keys))

    assert out.ok and out.writable is True
    assert out.detail == (
        f"{_URL} answered with 1 branch(es); it accepted a dry-run push of a new branch"
    )
    push = next(c for c in commands if "push" in c)
    assert push[push.index("push"):push.index("--")] == ["push", "--dry-run", "--porcelain"]
    assert push[-2] == _URL
    assert push[-1].startswith(f"HEAD:refs/heads/{docker_exec.WRITE_CHECK_BRANCH}-")
    # Nothing reached the remote: no branch, no object.
    assert _git(bare, "for-each-ref") == refs_before
    assert sorted(p.name for p in (bare / "objects").rglob("*") if p.is_file()) == objects_before
    # And the scratch repository is gone.
    assert list((tmp_path / "scratch").iterdir()) == []


def test_check_of_a_key_the_remote_will_not_take_a_push_from(git_host, tmp_path, monkeypatch):
    root, keys, _ = git_host
    _place_key(keys)
    local_exec = docker_exec.run_docker_exec

    def read_only(container, workdir, command, env=None, timeout=None):
        # What GitHub answers a read-only deploy key at git-receive-pack.
        if command[:1] == ["git"] and "push" in command:
            return subprocess.CompletedProcess(
                command, 128, "",
                "ERROR: The key you are authenticating with has been marked as read only.\n"
                "fatal: Could not read from remote repository.\n",
            )
        return local_exec(container, workdir, command, env=env, timeout=timeout)

    monkeypatch.setattr(docker_exec, "run_docker_exec", read_only)

    out = docker_exec.check_remote(_CONTAINER, str(root), "myproj", _URL, keys_dir=str(keys))

    assert out.ok and out.writable is False
    assert "it refused a dry-run push: ERROR: The key you are authenticating with" in out.detail
    assert list((tmp_path / "scratch").iterdir()) == []


def test_check_reads_a_refusal_real_git_meets_at_receive_pack(git_host, tmp_path, monkeypatch):
    # Real git, whatever version runs the suite (2.39.5 in the agent image,
    # ADR 217): a stand-in ssh refuses where GitHub does. A pre-receive hook
    # would not do, a dry-run push never reaches it.
    root, keys, _ = git_host
    _place_key(keys)
    refuser = tmp_path / "ssh-refuser"
    refuser.write_text(
        "#!/bin/sh\n"
        "echo 'ERROR: The key you are authenticating with has been marked as read only.' >&2\n"
        "exit 1\n"
    )
    refuser.chmod(0o755)
    local_exec = docker_exec.run_docker_exec

    def refusing(container, workdir, command, env=None, timeout=None):
        if command[:1] == ["git"] and "push" in command:
            # No insteadOf rewrite: git goes over ssh to _URL and meets the refuser.
            return _run(
                command, workdir,
                env={**(env or {}), "GIT_SSH_COMMAND": str(refuser)}, timeout=timeout,
            )
        return local_exec(container, workdir, command, env=env, timeout=timeout)

    monkeypatch.setattr(docker_exec, "run_docker_exec", refusing)

    out = docker_exec.check_remote(_CONTAINER, str(root), "myproj", _URL, keys_dir=str(keys))

    assert out.ok and out.writable is False
    assert (
        "it refused a dry-run push: ERROR: The key you are authenticating with "
        "has been marked as read only." in out.detail
    )
    assert "fatal: Could not read from remote repository." in out.detail
    assert list((tmp_path / "scratch").iterdir()) == []


def test_check_that_cannot_make_its_scratch_repository_leaves_write_untested(
    git_host, monkeypatch,
):
    root, keys, _ = git_host
    _place_key(keys)
    monkeypatch.setattr(docker_exec, "WRITE_CHECK_SCRATCH", "/nonexistent-ia-scratch")

    out = docker_exec.check_remote(_CONTAINER, str(root), "myproj", _URL, keys_dir=str(keys))

    assert out.ok and out.writable is None
    assert "write not checked: no scratch directory" in out.detail


def test_check_without_a_key_says_so(git_host):
    root, keys, _ = git_host

    out = docker_exec.check_remote(_CONTAINER, str(root), "myproj", _URL, keys_dir=str(keys))

    assert not out.ok and out.detail.startswith("not checked: ")
    assert out.writable is None


def test_check_of_a_remote_that_does_not_answer(git_host):
    root, keys, commands = git_host
    _place_key(keys)

    out = docker_exec.check_remote(_CONTAINER, str(root), "myproj", _GONE, keys_dir=str(keys))

    assert not out.ok and out.detail.startswith(f"{_GONE} did not answer: ")
    assert out.writable is None
    assert not any("push" in c for c in commands)


# ---- the dispatcher's wrappers ----------------------------------------------


@pytest.fixture
def cfg(tmp_path, git_host):
    root, keys, _ = git_host
    return dataclasses.replace(
        _make_config(tmp_path), projects_root=str(root), project_keys_dir=str(keys),
    )


def test_make_project_checkout_clones_under_the_base_lock(cfg):
    _place_key(Path(cfg.project_keys_dir))

    out = core.make_project_checkout(cfg, "myproj", _URL, None)

    assert out.ok, out.detail
    assert (Path(cfg.projects_root) / "myproj" / ".git").is_dir()


def test_make_project_checkout_with_no_remote_inits(cfg):
    out = core.make_project_checkout(cfg, "fresh", None, None)

    assert out.ok and out.detail.startswith("made an empty repository")


def test_no_account_means_no_container(cfg):
    with pytest.raises(RuntimeError, match="no account"):
        core.check_project_remote(dataclasses.replace(cfg, accounts=[]), "myproj", _URL)


# ---- the worker's handlers --------------------------------------------------


def _hive(cfg) -> str:
    return context_transfer.hive_root(cfg.hive_tasks_dir)


@pytest.fixture
def worker(cfg):
    w = worker_mod.Worker(cfg, account_poll=0)
    yield w
    w.stop()


def _record(cfg, slug, remote=None):
    return projects.create(_hive(cfg), {"slug": slug, "remote": remote})


def _run_action(cfg, worker, kind, project, params=None) -> dict:
    queued = actions.create(cfg.state_dir, kind, project=project, params=params or {})
    worker.tick()
    worker.join()
    path = actions.actions_dir(cfg.state_dir) / "done" / f"{queued['id']}.json"
    return json.loads(path.read_text())


def test_the_worker_clones_the_records_remote(cfg, worker):
    _record(cfg, "myproj", _URL)
    _place_key(Path(cfg.project_keys_dir))

    rec = _run_action(cfg, worker, "clone", "myproj")

    assert rec["status"] == "done", rec
    assert rec["result"]["detail"].startswith(f"cloned {_URL}")
    assert rec["started_at"]


def test_the_worker_refuses_a_clone_with_no_record(cfg, worker):
    rec = _run_action(cfg, worker, "clone", "nobody")

    assert rec["status"] == "failed" and rec["error"].startswith("no project record nobody")


def test_the_worker_refuses_to_clone_a_record_with_no_remote(cfg, worker):
    _record(cfg, "fresh")

    rec = _run_action(cfg, worker, "clone", "fresh")

    assert rec["status"] == "failed" and "init makes an empty repository" in rec["error"]


def test_a_record_with_a_credential_fails_the_clone_and_never_names_it(cfg, worker):
    _record(cfg, "myproj", _URL)
    # Written past `projects.create`, which refuses it (ADR 205): the record no
    # longer reads, so nothing reaches git.
    path = Path(projects.record_path(_hive(cfg), "myproj"))
    data = json.loads(path.read_text())
    data["remote"] = "ssh://git:hunter2@github.com/acme/myproj.git"
    path.write_text(json.dumps(data))
    _place_key(Path(cfg.project_keys_dir))

    rec = _run_action(cfg, worker, "clone", "myproj")

    assert rec["status"] == "failed" and rec["error"].startswith("the record of myproj does not read")
    assert "hunter2" not in json.dumps(rec)
    assert not (Path(cfg.projects_root) / "myproj").exists()


def test_a_failed_clone_is_a_failed_action_with_gits_reason(cfg, worker):
    _record(cfg, "myproj", _URL)  # no key placed

    rec = _run_action(cfg, worker, "clone", "myproj")

    assert rec["status"] == "failed" and rec["error"].startswith("not cloned: ")
    assert rec["result"]["detail"] == rec["error"]


def test_the_worker_inits_a_record_with_no_remote(cfg, worker):
    _record(cfg, "fresh")

    rec = _run_action(cfg, worker, "init", "fresh")

    assert rec["status"] == "done" and rec["result"]["detail"].startswith("made an empty repository")


def test_the_worker_refuses_to_init_a_record_with_a_remote(cfg, worker):
    _record(cfg, "myproj", _URL)

    rec = _run_action(cfg, worker, "init", "myproj")

    assert rec["status"] == "failed" and rec["error"] == "myproj has a remote; clone it instead"


def test_a_check_is_done_either_way_and_says_whether_it_answered(cfg, worker):
    _record(cfg, "myproj", _URL)

    before = _run_action(cfg, worker, "check", "myproj")
    _place_key(Path(cfg.project_keys_dir))
    after = _run_action(cfg, worker, "check", "myproj")

    assert before["status"] == "done" and before["result"]["reachable"] is False
    assert before["result"]["writable"] is None
    assert after["status"] == "done" and after["result"] == {
        "reachable": True,
        "detail": f"{_URL} answered with 1 branch(es); it accepted a dry-run push of a new branch",
        "writable": True,
    }


@needs_ssh_keygen
def test_keygen_makes_the_key_records_it_and_hands_back_only_the_public_half(cfg, worker):
    _record(cfg, "myproj", _URL)

    rec = _run_action(cfg, worker, "keygen", "myproj")

    assert rec["status"] == "done", rec
    key = project_keys.read(cfg.project_keys_dir, "myproj")
    assert rec["result"] == {"public": key.public, "fingerprint": key.fingerprint, "replaced": False}
    assert "PRIVATE" not in json.dumps(rec)
    assert key.path.read_text() not in json.dumps(rec)
    recorded = projects.read(_hive(cfg), "myproj").key
    assert recorded is not None and recorded.fingerprint in key.fingerprint


@needs_ssh_keygen
def test_a_second_keygen_is_refused_unless_replace(cfg, worker):
    _record(cfg, "myproj", _URL)
    first = _run_action(cfg, worker, "keygen", "myproj")

    again = _run_action(cfg, worker, "keygen", "myproj")
    replaced = _run_action(cfg, worker, "keygen", "myproj", {"replace": True})

    assert again["status"] == "failed" and "already has a deploy key" in again["error"]
    assert replaced["status"] == "done" and replaced["result"]["replaced"] is True
    assert replaced["result"]["public"] != first["result"]["public"]


# ---- the queue --------------------------------------------------------------


@pytest.mark.parametrize("kind", ["clone", "init", "keygen", "check"])
def test_a_setup_action_takes_a_project_and_nothing_else(tmp_path, kind):
    state = str(tmp_path)
    with pytest.raises(actions.ActionRefused) as no_project:
        actions.create(state, kind, params={})
    with pytest.raises(actions.ActionRefused) as with_task:
        actions.create(state, kind, project="p", task_id="T-1", params={})
    with pytest.raises(actions.ActionRefused) as with_url:
        actions.create(state, kind, project="p", params={"remote": _URL})

    assert no_project.value.status == with_task.value.status == with_url.value.status == 400
    assert actions.create(state, kind, project="p", params={})["kind"] == kind


def test_keygens_replace_must_be_a_bool(tmp_path):
    with pytest.raises(actions.ActionRefused):
        actions.create(str(tmp_path), "keygen", project="p", params={"replace": "yes"})
    assert actions.create(str(tmp_path), "keygen", project="p", params={"replace": True})


def test_one_setup_action_of_a_kind_per_project(tmp_path):
    state = str(tmp_path)
    actions.create(state, "check", project="p", params={})

    with pytest.raises(actions.ActionRefused) as again:
        actions.create(state, "check", project="p", params={})

    assert again.value.status == 409 and "a check for project p" in str(again.value)
    actions.create(state, "check", project="q", params={})
    actions.create(state, "keygen", project="p", params={})


def test_clone_and_init_hold_the_project_keygen_and_check_do_not(tmp_path):
    recs = {k: actions.create(str(tmp_path), k, project="p", params={})
            for k in ("clone", "init", "keygen", "check")}
    keys = {k: actions.serial_key(r) for k, r in recs.items()}

    assert keys == {"clone": "project:p", "init": "project:p", "keygen": None, "check": None}
    assert not any(actions.KINDS[k].spends_quota for k in recs)
