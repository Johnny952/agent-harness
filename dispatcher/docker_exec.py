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
