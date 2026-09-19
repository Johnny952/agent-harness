from __future__ import annotations

import dataclasses
import json
import logging
import subprocess

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class ClaudeResult:
    session_id: str | None
    result_text: str
    raw: dict


def run_docker_exec(
    container: str,
    workdir: str,
    command: list[str],
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    full_command = ["docker", "exec", "-w", workdir]
    for key, value in (env or {}).items():
        full_command += ["-e", f"{key}={value}"]
    full_command += [container, *command]
    return subprocess.run(full_command, capture_output=True, text=True, timeout=timeout)


def _output_tail(proc: subprocess.CompletedProcess, limit: int = 500) -> str:
    tail = (proc.stderr.strip() or proc.stdout.strip())[-limit:]
    # Collapse to one line so a multi-line stack trace doesn't blow up the
    # single-line diagnostic (and downstream logs/messages that assume one).
    return " ".join(tail.split())


def exec_claude(
    container: str,
    workdir: str,
    prompt: str,
    resume_session_id: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    timeout_seconds: int | None = None,
) -> ClaudeResult:
    if timeout_seconds is not None and timeout_seconds <= 0:
        # coreutils `timeout 0` disables the in-container timeout entirely, so
        # claude would keep running there after the host backstop gives up.
        raise ValueError("timeout_seconds must be positive")

    command = ["claude"]
    if resume_session_id:
        command += ["--resume", resume_session_id]
    if model:
        command += ["--model", model]
    if effort:
        command += ["--effort", effort]
    command += ["-p", prompt, "--output-format", "json"]
    if timeout_seconds is not None:
        # Killing the host `docker exec` client does not kill the process inside
        # the container, so the real timeout has to run in-container via
        # coreutils `timeout` (present in the node:20-slim agent image). The
        # host-side timeout passed to run_docker_exec below is only a backstop
        # in case `docker exec` itself hangs.
        command = ["timeout", "--kill-after=30", str(timeout_seconds), *command]
    host_timeout = timeout_seconds + 60 if timeout_seconds is not None else None

    try:
        proc = run_docker_exec(container, workdir, command, timeout=host_timeout)
    except subprocess.TimeoutExpired:
        return ClaudeResult(
            session_id=None,
            result_text=f"claude timed out after {timeout_seconds}s (host backstop)",
            raw={},
        )

    parsed = None
    if proc.stdout.strip():
        try:
            candidate = json.loads(proc.stdout)
        except json.JSONDecodeError:
            candidate = None
        if isinstance(candidate, dict):
            parsed = candidate

    if parsed is None:
        # --kill-after sends SIGKILL when claude ignores SIGTERM, so coreutils
        # timeout can exit 137 (killed) as well as 124 (timed out normally).
        if proc.returncode in (124, 137) and timeout_seconds is not None:
            diagnostic = f"claude timed out after {timeout_seconds}s"
        else:
            diagnostic = f"claude produced no JSON result (exit {proc.returncode}): {_output_tail(proc)}"
        return ClaudeResult(session_id=None, result_text=diagnostic, raw={})

    return ClaudeResult(session_id=parsed.get("session_id"), result_text=parsed.get("result") or "", raw=parsed)


# git translates its messages, and the tolerated-error checks below match the
# English ones, so every git call here pins the locale rather than depending on
# whatever the image happens to have.
_GIT_ENV = {"LC_ALL": "C"}

WRITER_ROLES = frozenset({"arquitecto", "implementador"})
"""Roles that change the tree, and so share one worktree on the task branch.

Everyone else reviews what they produced and gets a throwaway detached
checkout instead — see create_worktree.
"""

WRITER_WORKTREE_NAME = "work"
"""Directory name the writing roles share, inside a task's worktrees directory.

Cleanup keeps this one and removes its siblings, so it has to stay the same
string _add_writer_worktree builds its path from.
"""


def task_branch(task_id: str) -> str:
    """The single branch a task's work accumulates on."""
    return f"agent/task/{task_id}"


def _require_non_empty(**components: str) -> None:
    """Guard the components of any path this module rm -rf's."""
    empty = sorted(name for name, value in components.items() if not value)
    if empty:
        raise ValueError(f"{', '.join(empty)} must be non-empty")


def task_worktrees_dir(projects_root: str, slug: str, task_id: str) -> str:
    """The directory holding every worktree of one task."""
    _require_non_empty(projects_root=projects_root, slug=slug, task_id=task_id)
    return f"{projects_root}/{slug}/worktrees/{task_id}"


def create_worktree(container: str, projects_root: str, slug: str, task_id: str, role: str) -> str:
    """Create (or re-use) the worktree a role works in for one task.

    A task has one branch, agent/task/<task-id>, and the roles that write to it
    (WRITER_ROLES) share one worktree checked out on it, so the implementador
    starts from what the arquitecto left rather than from a pristine HEAD.

    The reviewing roles get their own path detached at that branch's tip, and
    it is rebuilt from scratch on every call: git will not check one branch out
    in two worktrees at once, and a reused checkout is exactly how the revisor
    ended up reviewing a tree with none of the implementador's work in it.
    """
    # The reviewing path rm -rf's a path built from these, so an empty component
    # must never be allowed to widen it.
    _require_non_empty(projects_root=projects_root, slug=slug, task_id=task_id, role=role)

    project_dir = f"{projects_root}/{slug}"
    branch = task_branch(task_id)
    if role in WRITER_ROLES:
        return _add_writer_worktree(container, project_dir, task_id, branch)
    return _add_review_worktree(container, project_dir, task_id, role, branch)


def _add_writer_worktree(container: str, project_dir: str, task_id: str, branch: str) -> str:
    """Check the task branch out at a shared, persistent path."""
    worktree_path = f"{project_dir}/worktrees/{task_id}/{WRITER_WORKTREE_NAME}"
    if _branch_exists(container, project_dir, branch):
        # Round 2+ and resumes: check the branch out where it is, never `-B`,
        # which would reset it and throw away the commits made so far.
        command = ["git", "worktree", "add", worktree_path, branch]
    else:
        command = ["git", "worktree", "add", "-b", branch, worktree_path]

    proc = run_docker_exec(container, project_dir, command, env=_GIT_ENV)
    if proc.returncode != 0 and not _worktree_already_exists(proc.stderr, branch, worktree_path):
        raise RuntimeError(f"git worktree add failed: {proc.stderr}")
    return worktree_path


def _add_review_worktree(container: str, project_dir: str, task_id: str, role: str, branch: str) -> str:
    """Rebuild a detached checkout of the task branch's current tip."""
    worktree_path = f"{project_dir}/worktrees/{task_id}/{role}"
    _drop_worktrees(container, project_dir, [worktree_path])

    proc = run_docker_exec(
        container, project_dir,
        ["git", "worktree", "add", "--detach", worktree_path, branch],
        env=_GIT_ENV,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git worktree add --detach failed: {proc.stderr}")
    return worktree_path


def _drop_worktrees(container: str, project_dir: str, paths: list[str]) -> None:
    """Delete worktree directories and drop git's bookkeeping for them.

    rm -rf rather than `git worktree remove`, so a leftover directory that git
    never registered (a crash between mkdir and bookkeeping) is cleared too;
    the single `prune` afterwards drops the admin entries the way remove would
    have. Paths are removed one by one instead of with a glob, so nothing here
    needs a shell.
    """
    if not paths:
        return
    for path in paths:
        run_docker_exec(container, project_dir, ["rm", "-rf", path], env=_GIT_ENV)
    run_docker_exec(container, project_dir, ["git", "worktree", "prune"], env=_GIT_ENV)


def list_task_worktrees(container: str, projects_root: str, slug: str, task_id: str) -> list[str]:
    """The names of the worktrees a task currently has on disk."""
    worktrees_dir = task_worktrees_dir(projects_root, slug, task_id)
    proc = run_docker_exec(container, f"{projects_root}/{slug}", ["ls", "-1", worktrees_dir])
    if proc.returncode != 0:
        # No directory at all: a task that never ran a phase, or one already
        # cleaned up. Either way there is nothing to remove.
        return []
    return [name.strip() for name in proc.stdout.splitlines() if name.strip()]


def remove_review_worktrees(container: str, projects_root: str, slug: str, task_id: str) -> list[str]:
    """Delete a finished task's reviewing checkouts, keeping the writers' one.

    A reviewing role's checkout is detached and holds nothing of its own —
    create_worktree rebuilds it from scratch on every call for that very
    reason — so once the task is over there is nothing in it to lose. The
    writers' worktree stays: it is checked out on the task branch, it holds
    whatever a failed phase left uncommitted for a retry to resume into, and
    until something merges the branch it is where a human reads the result.
    remove_task_worktrees is the deliberate, hand-invoked version that takes it.

    The reviewers are found by listing the directory rather than by naming the
    roles, because create_worktree defines them by negation: any role added
    later that is not in WRITER_ROLES gets a review worktree, and gets cleaned
    up here without this function having to learn its name.
    """
    worktrees_dir = task_worktrees_dir(projects_root, slug, task_id)
    doomed = [
        name for name in list_task_worktrees(container, projects_root, slug, task_id)
        if name != WRITER_WORKTREE_NAME
    ]
    _drop_worktrees(container, f"{projects_root}/{slug}", [f"{worktrees_dir}/{name}" for name in doomed])
    return doomed


def remove_task_worktrees(container: str, projects_root: str, slug: str, task_id: str) -> list[str]:
    """Delete every worktree of a task, the writers' one included.

    The branch is left alone: the commits are the work, and these are only
    checkouts of them. `git worktree add <path> agent/task/<task-id>` brings
    any of it back.
    """
    worktrees_dir = task_worktrees_dir(projects_root, slug, task_id)
    removed = list_task_worktrees(container, projects_root, slug, task_id)
    project_dir = f"{projects_root}/{slug}"
    proc = run_docker_exec(container, project_dir, ["rm", "-rf", worktrees_dir], env=_GIT_ENV)
    if proc.returncode != 0:
        raise RuntimeError(f"could not remove {worktrees_dir}: {_output_tail(proc)}")
    run_docker_exec(container, project_dir, ["git", "worktree", "prune"], env=_GIT_ENV)
    return removed


def _branch_exists(container: str, project_dir: str, branch: str) -> bool:
    proc = run_docker_exec(
        container, project_dir,
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        env=_GIT_ENV,
    )
    return proc.returncode == 0


def _worktree_already_exists(stderr: str, branch: str, worktree_path: str) -> bool:
    """True only for git's "this exact branch/path is already there" errors."""
    return (
        f"a branch named '{branch}' already exists" in stderr
        or f"'{worktree_path}' already exists" in stderr
        or f"'{worktree_path}' is already registered" in stderr
        # "already used by worktree at <path>" is only benign when the worktree
        # git names is the one being asked for; any other path means two tasks
        # are fighting over the branch.
        or (f"'{branch}' is already used by worktree at" in stderr and worktree_path in stderr)
    )


def commit_worktree(
    container: str,
    workdir: str,
    message: str,
    author_name: str,
    author_email: str,
) -> bool:
    """Commit everything in a worktree. False when there was nothing to commit.

    The identity is passed per invocation so each commit names the role and
    account that produced it; `-c` outranks the image's --system fallback (see
    docker/agent/Dockerfile) without writing any repo-local config.
    """
    add = run_docker_exec(container, workdir, ["git", "add", "-A"], env=_GIT_ENV)
    if add.returncode != 0:
        raise RuntimeError(f"git add failed: {add.stderr}")

    proc = run_docker_exec(
        container, workdir,
        [
            "git",
            "-c", f"user.name={author_name}",
            "-c", f"user.email={author_email}",
            "commit", "-m", message,
        ],
        env=_GIT_ENV,
    )
    if proc.returncode == 0:
        return True
    if _nothing_to_commit(proc.stdout, proc.stderr):
        # A phase that only read, or that re-ran after its work was already
        # committed. Not an error, and not worth an empty commit.
        return False
    raise RuntimeError(f"git commit failed: {proc.stderr or proc.stdout}")


def _nothing_to_commit(stdout: str, stderr: str) -> bool:
    """git reports a clean tree on *stdout* with exit 1, not on stderr."""
    combined = f"{stdout}\n{stderr}"
    return "nothing to commit" in combined or "nothing added to commit" in combined


def read_owner(container: str, path: str) -> str | None:
    """The uid:gid owning `path` on the host, or None if it cannot be read."""
    proc = run_docker_exec(container, path, ["stat", "-c", "%u:%g", path])
    owner = proc.stdout.strip()
    if proc.returncode != 0 or not owner:
        return None
    return owner


def restore_owner(container: str, path: str, owner: str | None) -> None:
    """Give `path` back to `owner` after the container (root) has written to it.

    /data/projects is a bind mount owned by the host user, but the agents run
    as root, so everything they create is root-owned and undeletable by the
    person who owns the checkout. Best-effort by design: this is hygiene, and
    it runs on the way out of a phase that may already be failing, so it must
    never be the thing that raises.
    """
    if not owner:
        return
    proc = run_docker_exec(container, path, ["chown", "-R", owner, path])
    if proc.returncode != 0:
        logger.warning("could not restore ownership of %s to %s: %s", path, owner, _output_tail(proc))
