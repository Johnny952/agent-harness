from __future__ import annotations

import dataclasses
import json
import subprocess


@dataclasses.dataclass
class ClaudeResult:
    session_id: str | None
    result_text: str
    raw: dict


def run_docker_exec(container: str, workdir: str, command: list[str]) -> subprocess.CompletedProcess:
    full_command = ["docker", "exec", "-w", workdir, container, *command]
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
    project_dir = f"{projects_root}/{slug}"
    worktree_path = f"{project_dir}/worktrees/{task_id}"
    branch = f"agent/{role}/{task_id}"
    proc = run_docker_exec(container, project_dir, ["git", "worktree", "add", "-b", branch, worktree_path])
    if proc.returncode != 0 and "already exists" not in proc.stderr:
        raise RuntimeError(f"git worktree add failed: {proc.stderr}")
    return worktree_path
