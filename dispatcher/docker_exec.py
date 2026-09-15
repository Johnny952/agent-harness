from __future__ import annotations

import dataclasses
import json
import subprocess


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
) -> subprocess.CompletedProcess:
    full_command = ["docker", "exec", "-w", workdir]
    for key, value in (env or {}).items():
        full_command += ["-e", f"{key}={value}"]
    full_command += [container, *command]
    return subprocess.run(full_command, capture_output=True, text=True)


def exec_claude(
    container: str,
    workdir: str,
    prompt: str,
    resume_session_id: str | None = None,
) -> ClaudeResult:
    command = ["claude"]
    if resume_session_id:
        command += ["--resume", resume_session_id]
    command += ["-p", prompt, "--output-format", "json"]
    proc = run_docker_exec(container, workdir, command)
    raw = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return ClaudeResult(session_id=raw.get("session_id"), result_text=raw.get("result", ""), raw=raw)


def create_worktree(container: str, projects_root: str, slug: str, task_id: str, role: str) -> str:
    """Create (or re-use) the worktree a role works in for one task.

    The path is scoped by role as well as task_id: every role of a task gets
    its own branch (agent/<role>/<task-id>), and git refuses to check two
    branches out in the same worktree directory, so a task_id-only path made
    every role after the first collide.

    Re-running the same role is tolerated — that is the resume case, where the
    branch and directory are already there — but only for that exact branch or
    path, so unrelated failures that happen to contain "already exists" still
    raise.
    """
    project_dir = f"{projects_root}/{slug}"
    worktree_path = f"{project_dir}/worktrees/{task_id}/{role}"
    branch = f"agent/{role}/{task_id}"
    proc = run_docker_exec(
        container,
        project_dir,
        ["git", "worktree", "add", "-b", branch, worktree_path],
        # git translates its messages; the tolerated-error check below matches
        # the English ones, so pin the locale rather than depend on the image's.
        env={"LC_ALL": "C"},
    )
    if proc.returncode != 0 and not _worktree_already_exists(proc.stderr, branch, worktree_path):
        raise RuntimeError(f"git worktree add failed: {proc.stderr}")
    return worktree_path


def _worktree_already_exists(stderr: str, branch: str, worktree_path: str) -> bool:
    """True only for git's "this exact branch/path is already there" errors."""
    return (
        f"a branch named '{branch}' already exists" in stderr
        or f"'{worktree_path}' already exists" in stderr
        or f"'{worktree_path}' is already registered" in stderr
    )
