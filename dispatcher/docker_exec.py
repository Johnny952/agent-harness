from __future__ import annotations

import dataclasses
import json
import logging
import re
import shlex
import subprocess
import tempfile
import uuid
from collections.abc import Sequence

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
    return subprocess.run(
        _docker_exec_argv(container, workdir, command, env),
        capture_output=True, text=True, timeout=timeout,
    )


def _docker_exec_argv(
    container: str, workdir: str, command: list[str], env: dict[str, str] | None = None,
) -> list[str]:
    full_command = ["docker", "exec", "-w", workdir]
    for key, value in (env or {}).items():
        full_command += ["-e", f"{key}={value}"]
    return [*full_command, container, *command]


def open_docker_exec(container: str, workdir: str, command: list[str]) -> subprocess.Popen:
    """`run_docker_exec`'s argv, started and not waited for: stdout is a pipe
    the caller reads line by line, stderr goes to a temporary file (read it
    from `proc.stderr` after `wait()`), so neither pipe can fill and block."""
    return subprocess.Popen(
        _docker_exec_argv(container, workdir, command),
        stdout=subprocess.PIPE, stderr=tempfile.TemporaryFile(mode="w+"),
        text=True, bufsize=1,
    )


def kill_in_container(container: str, pid: int) -> None:
    """SIGTERM a process inside the container (killing the host `docker exec`
    client does not stop it). Best effort: it may already have exited."""
    try:
        run_docker_exec(container, "/", ["kill", "-TERM", str(pid)], timeout=30)
    except (OSError, subprocess.SubprocessError):
        logger.warning("could not signal pid %s in %s", pid, container)


def _output_tail(proc: subprocess.CompletedProcess, limit: int = 500) -> str:
    tail = (proc.stderr.strip() or proc.stdout.strip())[-limit:]
    # Collapse to one line so a multi-line stack trace doesn't blow up the
    # single-line diagnostic (and downstream logs/messages that assume one).
    return " ".join(tail.split())


#: How long past its own timeout a `claude` call may still hold the host: the
#: in-container `timeout --kill-after=30` fits inside it, and the host-side
#: backstop on `docker exec` fires at the end of it.
HOST_BACKSTOP_SECONDS = 60


def exec_claude(
    container: str,
    workdir: str,
    prompt: str,
    resume_session_id: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    timeout_seconds: int | None = None,
    max_turns: int | None = None,
    plugin_dirs: Sequence[str] | None = None,
    append_system_prompt: str | None = None,
    json_schema: dict | None = None,
    permission_mode: str | None = None,
    add_dirs: Sequence[str] | None = None,
    allowed_tools: Sequence[str] | None = None,
    mcp_config: str | None = None,
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
    # Who is allowed to answer a permission prompt. Under `-p` the CLI
    # defaults `--permission-prompts` to "host", and there is no SDK host
    # here and no --permission-prompt-tool, so with no mode every tool call
    # that would prompt is denied — inside the phase's own worktree
    # included. Every flag on this command belongs to one `claude` process,
    # so a --resume retry has to carry it again; it inherits nothing.
    if permission_mode:
        command += ["--permission-mode", permission_mode]
    # A ceiling on agent turns, for a phase that is bounded by how much it may
    # spend rather than by what it must finish. Hitting it is not a crash: the
    # CLI returns its normal JSON with `is_error` and `subtype:
    # "error_max_turns"`, and the work done up to there is on disk. The flag
    # is hidden from `--help` but registered in the CLI (2.1.273); if a future
    # version drops it, the exec fails as an unknown option and only the
    # phases that opt into a budget are affected.
    if max_turns is not None:
        if max_turns <= 0:
            raise ValueError("max_turns must be positive")
        command += ["--max-turns", str(max_turns)]
    # Session-scoped skill delivery: one directory per plugin, repeatable.
    # Nothing is installed in the container by this, so two roles running in
    # the same agent never see each other's set. A directory that is not
    # there is not fatal — the CLI prints `Path not found` and runs the
    # phase anyway — so an image built before the skills were baked in
    # degrades to no skills instead of failing every dispatch.
    for plugin_dir in plugin_dirs or ():
        command += ["--plugin-dir", plugin_dir]
    if append_system_prompt:
        command += ["--append-system-prompt", append_system_prompt]
    # Structured return. The CLI validates the model's answer against this and
    # puts it on the result envelope as `structured_output`; the `result` text
    # may then be a placeholder, so callers read the payload, not the prose.
    # Compact separators because this travels as one argv element.
    if json_schema is not None:
        command += ["--json-schema", json.dumps(json_schema, separators=(",", ":"))]
    # The MCP servers this one process may reach, as inline JSON: today only
    # the board service, for the roles that have its tools (ADR 128). Inline
    # rather than a file, so there is nothing in the container to write,
    # share between two roles, or go stale. A --resume restates it like every
    # other flag here. Declared `--mcp-config <configs...>`, variadic like the
    # two below, so it too sits ahead of the `-p` block.
    if mcp_config:
        command += ["--mcp-config", mcp_config]
    # Directories the file tools may touch besides the working directory.
    # Declared `--add-dir <directories...>`, so one flag carries them all and
    # the variadic stops at the next dash-prefixed token — which is why this
    # sits ahead of the `-p` block and not after it.
    if add_dirs:
        command += ["--add-dir", *add_dirs]
    # Tool patterns allowed outright, on top of whatever the permission mode
    # already grants. `acceptEdits` covers the file tools and the CLI's own
    # read-only Bash set, but not running a program: every `node --test` in
    # the T-002 run was refused, so a phase cannot prove its own work. This
    # is how the harness hands that back — narrowly, and from outside, since
    # a phase writing its own .claude/settings.local.json is refused too.
    # Variadic like --add-dir, so it sits ahead of the `-p` block for the
    # same reason: the list stops at the next dash-prefixed token.
    if allowed_tools:
        command += ["--allowed-tools", *allowed_tools]
    command += ["-p", prompt, "--output-format", "json"]
    if timeout_seconds is not None:
        # Killing the host `docker exec` client does not kill the process inside
        # the container, so the real timeout has to run in-container via
        # coreutils `timeout` (present in the node:20-slim agent image). The
        # host-side timeout passed to run_docker_exec below is only a backstop
        # in case `docker exec` itself hangs.
        command = ["timeout", "--kill-after=30", str(timeout_seconds), *command]
    host_timeout = timeout_seconds + HOST_BACKSTOP_SECONDS if timeout_seconds is not None else None

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


#: The first line a turn prints is the pid of the in-container `timeout` (the
#: `sh` prints it and `exec`s the rest), which is what `kill_in_container` needs.
TURN_PID_WRAPPER = ["sh", "-c", 'echo "$$"; exec "$@"', "ia-harness-turn"]


def turn_command(
    prompt: str,
    *,
    resume_session_id: str | None,
    model: str,
    effort: str,
    permission_mode: str,
    allowed_tools: Sequence[str],
    disallowed_tools: Sequence[str],
    timeout_seconds: int,
    mcp_config: str | None = None,
) -> list[str]:
    """The argv of one conversation turn (docs/decisions.md ADR 173, 3): a
    streamed `claude -p`, no-code by flags. Every flag is restated on a
    `--resume`; nothing is inherited from the session. `mcp_config` is the
    board server for the thread role (ADR 187, 8)."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    command = ["claude"]
    if resume_session_id:
        command += ["--resume", resume_session_id]
    command += ["--model", model, "--effort", effort]
    command += ["--permission-mode", permission_mode, "--permission-prompts", "none"]
    # Variadic like exec_claude's lists: they sit ahead of `-p`.
    if mcp_config:
        command += ["--mcp-config", mcp_config]
    command += ["--allowed-tools", *allowed_tools]
    command += ["--disallowed-tools", *disallowed_tools]
    # The person's text goes last, after `--`, so a message that starts with
    # `-` is a prompt and never a flag that could lift the no-code set.
    command += ["-p", "--output-format", "stream-json", "--verbose", "--", prompt]
    return [
        *TURN_PID_WRAPPER, "timeout", "--kill-after=30", str(timeout_seconds), *command,
    ]


# git translates its messages, and the tolerated-error checks below match the
# English ones, so every git call here pins the locale rather than depending on
# whatever the image happens to have.
_GIT_ENV = {"LC_ALL": "C"}

# "cartografo" is spelled out rather than imported from project_docs, which
# imports this module: the mapping phase writes the project's docs, so it
# needs the same shared checkout the other writers get.
WRITER_ROLES = frozenset({"cartografo", "arquitecto", "implementador", "auditor"})
"""Roles that change the tree, and so share one worktree on the task branch.

Everyone else reviews what they produced and gets a throwaway detached
checkout instead — see create_worktree.

The auditor is here because of what it writes, not where it runs. Its own
prompt opens "you are the only phase that writes the indexes"
(project_docs._AUDITOR), and a review worktree is detached and deleted with
everything in it: measured on 2026-09-24, the phase filed the learnings and
debt entries it was asked for and the branch that landed had none of them.
A role that reviews belongs outside this set; a role that has to leave
something behind belongs in it, whenever it happens to run.

It runs after the revisor has approved, though, so a commit taking the whole
tree would land code nobody reviewed. Its commit is held to the docs instead
— see project_docs.commit_scope and the `paths` argument of commit_worktree.
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


WORKTREES_EXCLUDE_ENTRY = "/worktrees/"
"""What the project's checkout is taught to ignore, anchored to the repo root.

The leading slash is deliberate: only the directory create_worktree makes at
the top of the project is ours, and a project carrying its own src/worktrees/
should go on seeing it.
"""

_EXCLUDE_COMMENT = "# ia-harness: worktrees of dispatched tasks live here"


def _git_common_dir(container: str, project_dir: str) -> str | None:
    """The repository's shared .git directory, absolute, or None if there is none."""
    proc = run_docker_exec(
        container, project_dir, ["git", "rev-parse", "--git-common-dir"], env=_GIT_ENV
    )
    path = proc.stdout.strip()
    if proc.returncode != 0 or not path:
        return None
    # Asked from the top of a checkout, git answers relatively: ".git".
    return path if path.startswith("/") else f"{project_dir}/{path}"


def _excludes_worktrees(exclude_text: str) -> bool:
    """Is the worktrees directory already excluded, in either spelling?"""
    wanted = {WORKTREES_EXCLUDE_ENTRY, WORKTREES_EXCLUDE_ENTRY.lstrip("/")}
    return any(line.strip() in wanted for line in exclude_text.splitlines())


def ensure_worktrees_ignored(container: str, project_dir: str) -> bool:
    """Keep a task's worktrees out of the project's own `git status`.

    Every task is checked out under <project>/worktrees/, which lives inside
    the repository being worked on, so a project that has ever run one reports
    an untracked `worktrees/` for good: noise for whoever owns the checkout,
    and one `git add -A` away from committing a worktree as an embedded
    repository. The entry goes in .git/info/exclude rather than .gitignore
    because it describes where this harness keeps its scratch checkouts — a
    fact about this clone, not something the project should carry in its
    history.

    Idempotent, and best-effort: a directory nobody has cloned into yet
    (bootstrap-project makes it before the repo exists) simply gets nothing.
    Returns True when the entry was added.
    """
    git_dir = _git_common_dir(container, project_dir)
    if git_dir is None:
        return False
    info_dir = f"{git_dir}/info"
    existing = run_docker_exec(container, project_dir, ["cat", f"{info_dir}/exclude"])
    if existing.returncode == 0 and _excludes_worktrees(existing.stdout):
        return False
    # git ships info/ with every repository, but a repo can reach us without
    # it, and then the append below would be the thing that fails.
    run_docker_exec(container, project_dir, ["mkdir", "-p", info_dir])
    # The only shell in this module, and it interpolates nothing of the
    # caller's: both literals are module constants, and the directory arrives
    # as the workdir, which docker passes as an argv element rather than to sh.
    appended = run_docker_exec(
        container,
        info_dir,
        ["sh", "-c", f"printf '%s\\n' '{_EXCLUDE_COMMENT}' '{WORKTREES_EXCLUDE_ENTRY}' >> exclude"],
    )
    if appended.returncode != 0:
        logger.warning(
            "could not exclude %s in %s: %s",
            WORKTREES_EXCLUDE_ENTRY, info_dir, _output_tail(appended),
        )
        return False
    return True


def create_worktree(
    container: str, projects_root: str, slug: str, task_id: str, role: str,
    *, at_base: bool = False,
) -> str:
    """Create (or re-use) the worktree a role works in for one task.

    A task has one branch, agent/task/<task-id>, and the roles that write to it
    (WRITER_ROLES) share one worktree checked out on it, so the implementador
    starts from what the arquitecto left rather than from a pristine HEAD.

    The reviewing roles get their own path detached at that branch's tip, and
    it is rebuilt from scratch on every call: git will not check one branch out
    in two worktrees at once, and a reused checkout is exactly how the revisor
    ended up reviewing a tree with none of the implementador's work in it.

    `at_base` asks for the reader's checkout whatever the role: detached at the
    project clone's HEAD (the base), with no branch made and nothing to share.
    A draft run uses it for the arquitecto (`docs/decisions.md` ADR 94).
    """
    # The reviewing path rm -rf's a path built from these, so an empty component
    # must never be allowed to widen it.
    _require_non_empty(projects_root=projects_root, slug=slug, task_id=task_id, role=role)

    project_dir = f"{projects_root}/{slug}"
    # Before the first worktree exists, so the directory is ignored from the
    # moment it shows up instead of after someone trips over it.
    ensure_worktrees_ignored(container, project_dir)
    if at_base:
        return _add_review_worktree(container, project_dir, task_id, role, "HEAD")
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


MERGED = "merged"
UP_TO_DATE = "up-to-date"
REFUSED = "refused"


@dataclasses.dataclass(frozen=True)
class MergeOutcome:
    """What became of a task branch offered to the project's own branch.

    Three outcomes, not two: a branch already in the target is neither a
    success worth announcing nor a problem worth an exit code, and telling
    them apart is the difference between a CLI that reads honestly and one
    that cries wolf on a re-run.
    """

    status: str
    target: str | None
    detail: str

    @property
    def merged(self) -> bool:
        return self.status == MERGED

    @property
    def refused(self) -> bool:
        return self.status == REFUSED


def current_branch(container: str, project_dir: str) -> str | None:
    """The branch the project's main checkout is on, or None when detached."""
    proc = run_docker_exec(
        container, project_dir,
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        env=_GIT_ENV,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _has_uncommitted_changes(container: str, project_dir: str) -> bool:
    """Whether the main checkout has tracked changes a merge could tangle with.

    Untracked files are deliberately not counted: `worktrees/` lives inside the
    repository, so a project that has ever run a task always has some, and
    treating that as dirty would refuse every merge forever. The case untracked
    files actually matter in — a merge that wants to write over one — git
    refuses on its own, and that refusal comes back as the merge failing.
    """
    proc = run_docker_exec(
        container, project_dir,
        ["git", "status", "--porcelain", "--untracked-files=no"],
        env=_GIT_ENV,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git status failed: {_output_tail(proc)}")
    return bool(proc.stdout.strip())


def merge_task_branch(
    container: str,
    projects_root: str,
    slug: str,
    task_id: str,
    author_name: str = "ia-harness dispatcher",
    author_email: str = "dispatcher@ia-harness.invalid",
) -> MergeOutcome:
    """Merge a task's branch into whatever branch the project is sitting on.

    The target is read rather than configured: the branch checked out in
    `projects_root/<slug>` is the one whoever set the project up chose to work
    from, and a merge into a branch nobody is looking at helps no one.

    Every refusal leaves the repository exactly as it was found. The task
    branch is never touched, deleted, or rewritten by any path through here —
    it stays as the record of the work and as the way back if the merge turns
    out to be wrong. (The pre-merge check that runs before this, `premerge_base`
    and `adopt_premerge`, may add one merge commit on top of it; it never
    rewrites what was there.)
    """
    _require_non_empty(projects_root=projects_root, slug=slug, task_id=task_id)
    project_dir = f"{projects_root}/{slug}"
    branch = task_branch(task_id)

    if not _branch_exists(container, project_dir, branch):
        return MergeOutcome(REFUSED, None, f"there is no branch {branch} to merge")

    target = current_branch(container, project_dir)
    if target is None:
        return MergeOutcome(
            REFUSED, None,
            f"{project_dir} is on a detached HEAD, so there is no branch to merge into",
        )
    if target == branch:
        return MergeOutcome(
            REFUSED, target,
            f"{project_dir} is checked out on {branch} itself",
        )
    if _has_uncommitted_changes(container, project_dir):
        return MergeOutcome(
            REFUSED, target,
            f"{project_dir} has uncommitted changes; commit or stash them first",
        )

    proc = run_docker_exec(
        container, project_dir,
        [
            "git",
            "-c", f"user.name={author_name}",
            "-c", f"user.email={author_email}",
            "merge", "--no-ff", "--no-edit", "-m", f"Merge {branch}", branch,
        ],
        env=_GIT_ENV,
    )
    if proc.returncode != 0:
        # Conflicts leave the tree mid-merge; the earlier refusals (a dirty
        # tree included) never start one at all, so the abort is allowed to
        # fail — "there is no merge to abort" is the expected answer there.
        run_docker_exec(container, project_dir, ["git", "merge", "--abort"], env=_GIT_ENV)
        return MergeOutcome(
            REFUSED, target,
            f"merging {branch} into {target} failed, and was rolled back: {_output_tail(proc)}",
        )
    if "Already up to date" in proc.stdout:
        return MergeOutcome(UP_TO_DATE, target, f"{target} already contains {branch}")
    return MergeOutcome(MERGED, target, f"merged {branch} into {target}")


def branch_changed_paths(
    container: str, projects_root: str, slug: str, task_id: str,
) -> list[str] | None:
    """The repo paths a task branch changes against the branch the project is on.

    What the automatic approve compares with the task's surface and the
    protected list (`dispatcher/auto_approve.py`). A three-dot diff, so it is
    the branch's own changes since it last met the target: after the pre-merge
    check merged the base in, that is the task's work and its conflict
    resolutions, not what the base gained meanwhile. Renames are split into a
    deletion and an addition so both paths are checked. None when it cannot
    be read — no branch, a detached project, a git error — and the caller
    treats that as a failed condition, never as an empty change.
    """
    _require_non_empty(projects_root=projects_root, slug=slug, task_id=task_id)
    project_dir = f"{projects_root}/{slug}"
    branch = task_branch(task_id)
    target = current_branch(container, project_dir)
    if target is None or target == branch:
        return None
    proc = run_docker_exec(
        container, project_dir,
        ["git", "diff", "--name-only", "--no-renames", "-z", f"{target}...{branch}"],
        env=_GIT_ENV,
    )
    if proc.returncode != 0:
        return None
    return [path for path in proc.stdout.split("\0") if path]


def head_commit(container: str, project_dir: str) -> str | None:
    """The commit the project's checkout is at, or None when it cannot be read."""
    proc = run_docker_exec(container, project_dir, ["git", "rev-parse", "HEAD"], env=_GIT_ENV)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


PREMERGE_CLEAN = "clean"
PREMERGE_CONFLICT = "conflict"

#: The detached checkout the pre-merge check builds its merge in. Not a writer,
#: so `remove_review_worktrees` clears a leftover one with the reviewers'.
PREMERGE_WORKTREE_NAME = "premerge"


@dataclasses.dataclass(frozen=True)
class PreMergeOutcome:
    """What merging the base into a task branch came to, before anything moved.

    `clean` is a merge commit sitting in `workdir`, a detached checkout, with
    the task branch still at `tip`: the caller gates it there and only then
    adopts it (`adopt_premerge`) or drops it (`drop_premerge_worktree`).
    `up-to-date` means the base is already in the branch, so there is nothing
    to merge and nothing new to gate. `conflict` was aborted and its checkout
    dropped; `conflicts` names the files (empty when git refused without
    any, as on unrelated histories, and `detail` then says why). `refused`
    never started a merge.
    """

    status: str
    base: str | None
    base_commit: str | None
    tip: str | None
    detail: str
    merged_tip: str | None = None
    conflicts: tuple[str, ...] = ()
    workdir: str | None = None

    @property
    def clean(self) -> bool:
        return self.status == PREMERGE_CLEAN

    @property
    def conflicted(self) -> bool:
        return self.status == PREMERGE_CONFLICT

    @property
    def up_to_date(self) -> bool:
        return self.status == UP_TO_DATE

    @property
    def refused(self) -> bool:
        return self.status == REFUSED


def _rev_parse(container: str, workdir: str, rev: str) -> str | None:
    """`rev` as a full commit sha, or None when it does not name one."""
    proc = run_docker_exec(
        container, workdir, ["git", "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"],
        env=_GIT_ENV,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def premerge_worktree_path(projects_root: str, slug: str, task_id: str) -> str:
    return f"{task_worktrees_dir(projects_root, slug, task_id)}/{PREMERGE_WORKTREE_NAME}"


def premerge_base(
    container: str,
    projects_root: str,
    slug: str,
    task_id: str,
    author_name: str = "ia-harness dispatcher",
    author_email: str = "dispatcher@ia-harness.invalid",
) -> PreMergeOutcome:
    """Merge the base into a task branch, in a checkout of its own.

    The base is the branch the project checkout is on (`current_branch`), the
    same one `merge_task_branch` lands the task in. A real merge, never a
    rebase, and never required to fast-forward: the task's commits stay what
    they were, and the merge commit is one more on top.

    The merge runs in a detached worktree at the branch's tip rather than in
    the writers' worktree, so nothing that fails here can move the branch or
    touch what a phase left uncommitted there. On a conflict it is aborted
    and the checkout dropped: the branch is still at its tip, and the base was
    only ever read.
    """
    _require_non_empty(projects_root=projects_root, slug=slug, task_id=task_id)
    project_dir = f"{projects_root}/{slug}"
    branch = task_branch(task_id)

    if not _branch_exists(container, project_dir, branch):
        return PreMergeOutcome(REFUSED, None, None, None, f"there is no branch {branch} to check")
    base = current_branch(container, project_dir)
    if base is None:
        return PreMergeOutcome(
            REFUSED, None, None, None,
            f"{project_dir} is on a detached HEAD, so there is no base to merge in",
        )
    if base == branch:
        return PreMergeOutcome(
            REFUSED, base, None, None, f"{project_dir} is checked out on {branch} itself",
        )
    base_commit = _rev_parse(container, project_dir, base)
    tip = _rev_parse(container, project_dir, branch)
    if base_commit is None or tip is None:
        return PreMergeOutcome(
            REFUSED, base, base_commit, tip, f"could not resolve {base} or {branch} to a commit",
        )

    proc = run_docker_exec(
        container, project_dir,
        ["git", "merge-base", "--is-ancestor", base_commit, tip],
        env=_GIT_ENV,
    )
    if proc.returncode == 0:
        return PreMergeOutcome(
            UP_TO_DATE, base, base_commit, tip,
            f"{branch} already contains {base} at {base_commit[:7]}",
            merged_tip=tip,
        )

    workdir = _add_review_worktree(container, project_dir, task_id, PREMERGE_WORKTREE_NAME, tip)
    proc = run_docker_exec(
        container, workdir,
        [
            "git",
            "-c", f"user.name={author_name}",
            "-c", f"user.email={author_email}",
            "merge", "--no-edit", "-m", f"Merge {base} ({base_commit[:7]}) into {branch}",
            base_commit,
        ],
        env=_GIT_ENV,
    )
    if proc.returncode != 0:
        listed = run_docker_exec(
            container, workdir, ["git", "diff", "--name-only", "--diff-filter=U"], env=_GIT_ENV,
        )
        conflicts = tuple(
            line.strip() for line in listed.stdout.splitlines() if line.strip()
        ) if listed.returncode == 0 else ()
        # Allowed to fail: a merge git refused outright never started one.
        run_docker_exec(container, workdir, ["git", "merge", "--abort"], env=_GIT_ENV)
        _drop_worktrees(container, project_dir, [workdir])
        named = ", ".join(conflicts) if conflicts else f"no file named ({_output_tail(proc)})"
        return PreMergeOutcome(
            PREMERGE_CONFLICT, base, base_commit, tip,
            f"merging {base} at {base_commit[:7]} into {branch} conflicts: {named}",
            conflicts=conflicts,
        )

    merged_tip = _rev_parse(container, workdir, "HEAD")
    if merged_tip is None:
        _drop_worktrees(container, project_dir, [workdir])
        raise RuntimeError(f"the pre-merge of {base} into {branch} left no HEAD in {workdir}")
    return PreMergeOutcome(
        PREMERGE_CLEAN, base, base_commit, tip,
        f"merged {base} at {base_commit[:7]} into {branch} cleanly",
        merged_tip=merged_tip, workdir=workdir,
    )


def adopt_premerge(
    container: str, projects_root: str, slug: str, task_id: str, outcome: PreMergeOutcome,
) -> None:
    """Move the task branch onto a clean pre-merge's commit.

    Where the writers' worktree has the branch checked out, it fast-forwards
    there, so that checkout's files follow the branch instead of going stale
    under it; anything a phase left uncommitted is carried, or git refuses
    and this raises. Otherwise `update-ref` moves the ref, and only if it is
    still at the tip the merge was built on.
    """
    if not outcome.clean or outcome.merged_tip is None or outcome.tip is None:
        raise ValueError(f"nothing to adopt from a {outcome.status} pre-merge")
    project_dir = f"{projects_root}/{slug}"
    branch = task_branch(task_id)
    writer = f"{task_worktrees_dir(projects_root, slug, task_id)}/{WRITER_WORKTREE_NAME}"
    if path_exists(container, writer) and current_branch(container, writer) == branch:
        proc = run_docker_exec(
            container, writer,
            ["git", "merge", "--ff-only", "--quiet", outcome.merged_tip],
            env=_GIT_ENV,
        )
    else:
        proc = run_docker_exec(
            container, project_dir,
            [
                "git", "update-ref", "-m", f"pre-merge of {outcome.base}",
                f"refs/heads/{branch}", outcome.merged_tip, outcome.tip,
            ],
            env=_GIT_ENV,
        )
    if proc.returncode != 0:
        raise RuntimeError(
            f"could not move {branch} onto the pre-merge {outcome.merged_tip[:7]}: "
            f"{_output_tail(proc)}"
        )


def drop_premerge_worktree(container: str, projects_root: str, slug: str, task_id: str) -> None:
    """Remove the pre-merge's detached checkout; the commits it made stay in git."""
    _require_non_empty(projects_root=projects_root, slug=slug, task_id=task_id)
    _drop_worktrees(
        container, f"{projects_root}/{slug}", [premerge_worktree_path(projects_root, slug, task_id)]
    )


GIT_CREDENTIALS_PATH = "/run/secrets/git-credentials"
"""Where a container is handed a credential for the project's remote.

A read-only bind mount from the host, in git's `store` format: one
`https://<user>:<token>@<host>` line. A mount rather than an environment
variable because run_docker_exec passes env as `-e KEY=value` on the
`docker exec` argv, where any `ps` on the host reads it. A mount rather than
something baked into the image for the reason the Claude credentials are one
too — containers are built to be thrown away, and a file on the host outlives
them and is shared by every agent without logging each one in.
"""

PROJECT_KEYS_DIR = "/run/secrets/projects"
"""Where a container is handed each project's SSH deploy key.

One subdirectory per project slug, holding `id_ed25519` and its `.pub`
(project_key_path). Read-only in the agents and writable only in the
dispatcher's container, where `project-keygen` makes a pair
(dispatcher/project_keys.py): a deploy key opens one repository, where the
token at GIT_CREDENTIALS_PATH usually opens every repository of its owner
(docs/plans/projects.md, Decisions 2).
"""

PROJECT_KEY_NAME = "id_ed25519"


def project_key_path(keys_dir: str, slug: str) -> str:
    """The private half of `slug`'s deploy key, under `keys_dir`."""
    return f"{keys_dir}/{slug}/{PROJECT_KEY_NAME}"


def ssh_command(key_path: str) -> str:
    """The GIT_SSH_COMMAND that fetches with exactly one key and never asks.

    IdentitiesOnly so ssh offers this key and no other it finds; BatchMode so
    a refused key fails instead of prompting on a terminal nobody watches; and
    StrictHostKeyChecking=yes with the per-user known_hosts pointed at
    /dev/null, so the only host keys trusted are the ones the agent image pins
    in /etc/ssh/ssh_known_hosts (docker/agent/github_known_hosts) and none is
    ever accepted on first use. It goes on the `docker exec` argv as env, which
    `ps` reads, and that is fine: it names a path, never a secret.
    """
    return (
        f"ssh -i {shlex.quote(key_path)} -o IdentitiesOnly=yes -o BatchMode=yes "
        f"-o StrictHostKeyChecking=yes -o UserKnownHostsFile=/dev/null"
    )


# `git@github.com:owner/repo.git`, git's scp-like form, or an ssh:// URL.
_SCP_LIKE_REMOTE = re.compile(r"^[\w.-]+@[\w.-]+:(?!//)")
_GITHUB_HTTPS = "https://github.com/"
_GITHUB_SSH = "git@github.com:"


def is_ssh_remote(url: str) -> bool:
    return url.startswith("ssh://") or bool(_SCP_LIKE_REMOTE.match(url))


UPDATED = "updated"
UNCHANGED = "unchanged"
DIVERGED = "diverged"
UNAVAILABLE = "unavailable"


@dataclasses.dataclass(frozen=True)
class UpdateOutcome:
    """What became of an attempt to bring the project's branch up to date.

    Four outcomes, because three different things get called "it did not
    update" and only one of them should stop a task:

    * UPDATED — the branch was behind and was fast-forwarded.
    * UNCHANGED — nothing to do. Either the branch is level with the remote,
      or it is *ahead*, which is the ordinary state after `merge-task` landed
      a branch and nobody has pushed yet. Being ahead is not a problem and
      must not read like one.
    * DIVERGED — ahead *and* behind. Reconciling that is a merge, and a merge
      is a decision; the charter's C-1 says a phase that needs a decision the
      task never gave it ends blocked and says so rather than inventing one.
      This is the only blocking outcome.
    * UNAVAILABLE — the update could not be attempted: no remote, an SSH remote
      with no deploy key for its project, a remote that is neither SSH nor
      https://, or a fetch that failed, a private remote with no credential
      mounted for it among the reasons it can fail.
      The base is then as stale as it was before any of this existed, which is
      the behaviour every run had until now, so it is a warning and not a stop.
    """

    status: str
    detail: str

    @property
    def updated(self) -> bool:
        return self.status == UPDATED

    @property
    def blocking(self) -> bool:
        return self.status == DIVERGED


def _rev_parse_short(container: str, project_dir: str, rev: str) -> str:
    """`rev` as a short sha, or the string itself when it cannot be resolved."""
    proc = run_docker_exec(
        container, project_dir, ["git", "rev-parse", "--short", rev], env=_GIT_ENV
    )
    if proc.returncode != 0:
        return rev
    return proc.stdout.strip() or rev


def _ahead_behind(container: str, project_dir: str, other: str) -> tuple[int, int] | None:
    """How many commits HEAD has that `other` does not, and the other way round."""
    proc = run_docker_exec(
        container, project_dir,
        ["git", "rev-list", "--left-right", "--count", f"HEAD...{other}"],
        env=_GIT_ENV,
    )
    if proc.returncode != 0:
        return None
    fields = proc.stdout.split()
    if len(fields) != 2:
        return None
    try:
        return int(fields[0]), int(fields[1])
    except ValueError:
        return None


@dataclasses.dataclass(frozen=True)
class _RemoteAccess:
    """How one git command reaches a project's remote: its `-c` args, env, hint.

    `missing` is the sentence added to a failure when the command fails, naming
    what was or was not handed to git, so a refusal reads with its likely cause.
    """

    git_args: list[str]
    env: dict[str, str]
    missing: str


def _remote_access(
    container: str,
    slug: str,
    remote: str,
    remote_url: str,
    credentials_path: str,
    keys_dir: str,
) -> _RemoteAccess | str:
    """What a fetch or a push to `remote_url` is handed, or why it cannot be tried.

    One answer for both directions, so the base is pushed with the same
    credential it is fetched with and a project never has one for reading and
    another for writing (ADR 78). The string, when that is what comes back, is
    the reason the command was not attempted, without the caller's own tail.

    A project with a deploy key (project_key_path) goes over SSH with it, and
    the https:// token is not consulted: an SSH remote as it is, and an
    `https://github.com/` clone through `url.<ssh>.insteadOf` for this one
    command, so the operator's remote is never rewritten. A project with no key
    keeps the token, so the token works until every project has moved.
    """
    ssh_remote = is_ssh_remote(remote_url)
    key_path = project_key_path(keys_dir, slug)
    # Probed only for a remote it could serve, and with `test -f` for the same
    # reason as the credential below. A key for a host other than github.com is
    # used too; the image pins no host key for it, so that command fails on the
    # host check and says so, rather than trusting the first answer.
    has_key = (ssh_remote or remote_url.startswith(_GITHUB_HTTPS)) and (
        run_docker_exec(container, "/", ["test", "-f", key_path]).returncode == 0
    )
    # Without GIT_TERMINAL_PROMPT a credential git cannot find turns into a
    # prompt on a terminal nobody is watching, and the call hangs until the
    # phase timeout instead of failing in a second with a reason.
    env = {**_GIT_ENV, "GIT_TERMINAL_PROMPT": "0"}
    if has_key:
        env["GIT_SSH_COMMAND"] = ssh_command(key_path)
        # `-c` and not `remote set-url`: rewriting someone's remote is a change
        # to their clone, and this lasts for the one command.
        git_args = (
            [] if ssh_remote
            else ["-c", f"url.{_GITHUB_SSH}.insteadOf={_GITHUB_HTTPS}"]
        )
        missing = (
            f" (with the deploy key at {key_path}; is its public half one of "
            f"the repository's deploy keys?)"
        )
        return _RemoteAccess(git_args, env, missing)
    if ssh_remote:
        return (
            f"{remote} is an SSH remote and there is no deploy key at {key_path} "
            f"(`project-keygen --project {slug}` makes one)"
        )
    if not remote_url.startswith("https://"):
        # Said rather than fixed: rewriting someone's remote is a change to
        # their clone, and the credential format mounted here can only answer
        # for HTTPS.
        return (
            f"{remote} is neither an SSH nor an https:// remote, the only kinds "
            f"a deploy key or the mounted credential can serve"
        )
    # `test -f` and not path_exists's `test -e`, because the thing most
    # likely to be at this path is a directory: a bind mount whose host
    # source does not exist gets one created for it, so the operator who
    # brought the agents up before writing the credential file has an empty
    # directory here, and a directory is not something git's store helper
    # can read.
    has_credential = (
        run_docker_exec(container, "/", ["test", "-f", credentials_path]).returncode == 0
    )
    # A missing credential is not a reason to skip the command, only a reason
    # not to name a helper. A public repo over https needs no credential to
    # fetch, and this runs against whatever project the operator put under
    # projects_root. A private remote refuses in a second instead, and comes
    # back through the caller's failure in git's own words with the path that
    # was looked for added.
    git_args = (
        ["-c", f"credential.helper=store --file={credentials_path}"]
        if has_credential
        else []
    )
    missing = "" if has_credential else f" (no credential file at {credentials_path})"
    return _RemoteAccess(git_args, env, missing)


def update_project_branch(
    container: str,
    projects_root: str,
    slug: str,
    remote: str = "origin",
    credentials_path: str = GIT_CREDENTIALS_PATH,
    timeout: float | None = None,
    keys_dir: str = PROJECT_KEYS_DIR,
) -> UpdateOutcome:
    """Fast-forward the project's own checkout onto its remote, or say why not.

    Called once per task, before the first worktree exists. That is the only
    moment it is safe: a new task's branch is cut from this checkout's HEAD
    (_add_writer_worktree's `-b` form takes no commit-ish), so moving HEAD here
    is what gives the task a fresh base, while the four roles of a task already
    under way share one worktree on one branch and moving the ground under them
    mid-cycle is how the revisor once came to review a tree with none of the
    implementador's work in it.

    Nothing here pushes, and a public remote needs no credential to be read:
    the mounted file is named to git only when it is there, and a fetch that
    then fails is reported rather than prevented. The deploy key or token it
    fetches with is the one push_base pushes with (_remote_access).

    The fetch is `--quiet` and the comparison is against FETCH_HEAD rather than
    refs/remotes/<remote>/<branch>: what was just fetched is what we want to
    measure, whatever the clone's refspec happens to be configured to track.
    """
    _require_non_empty(projects_root=projects_root, slug=slug, remote=remote)
    project_dir = f"{projects_root}/{slug}"

    branch = current_branch(container, project_dir)
    if branch is None:
        return UpdateOutcome(
            UNAVAILABLE, f"{project_dir} is not on a branch; left as it is"
        )
    url = run_docker_exec(
        container, project_dir, ["git", "remote", "get-url", remote], env=_GIT_ENV
    )
    if url.returncode != 0:
        return UpdateOutcome(
            UNAVAILABLE, f"{project_dir} has no remote named {remote}; {branch} left as it is"
        )
    access = _remote_access(
        container, slug, remote, url.stdout.strip(), credentials_path, keys_dir
    )
    if isinstance(access, str):
        return UpdateOutcome(UNAVAILABLE, f"{access}; {branch} left as it is")
    git_args, env, missing = access.git_args, access.env, access.missing
    fetched = run_docker_exec(
        container, project_dir,
        ["git", *git_args, "fetch", "--quiet", remote, branch],
        env=env,
        timeout=timeout,
    )
    if fetched.returncode != 0:
        why = f"{_output_tail(fetched)}{missing}"
        return UpdateOutcome(
            UNAVAILABLE,
            f"fetching {branch} from {remote} failed; {branch} left as it is: {why}",
        )

    counts = _ahead_behind(container, project_dir, "FETCH_HEAD")
    if counts is None:
        return UpdateOutcome(
            UNAVAILABLE,
            f"could not compare {branch} with {remote}/{branch}; {branch} left as it is",
        )
    ahead, behind = counts
    remote_head = _rev_parse_short(container, project_dir, "FETCH_HEAD")
    if behind == 0:
        if ahead == 0:
            return UpdateOutcome(UNCHANGED, f"{branch} is level with {remote}/{branch} at {remote_head}")
        # The ordinary state of a clone that merge-task has landed work into.
        return UpdateOutcome(
            UNCHANGED,
            f"{branch} is {ahead} commit(s) ahead of {remote}/{branch} and behind it "
            f"by none; nothing to update",
        )
    if ahead:
        return UpdateOutcome(
            DIVERGED,
            f"{branch} is {ahead} commit(s) ahead of {remote}/{branch} and {behind} behind: "
            f"reconciling them is a merge nobody asked for, so this task does not start",
        )

    if _has_uncommitted_changes(container, project_dir):
        return UpdateOutcome(
            UNAVAILABLE,
            f"{project_dir} has uncommitted changes, so {branch} cannot be "
            f"fast-forwarded {behind} commit(s); left as it is",
        )
    before = _rev_parse_short(container, project_dir, "HEAD")
    merged = run_docker_exec(
        container, project_dir,
        ["git", "merge", "--ff-only", "--quiet", "FETCH_HEAD"],
        env=_GIT_ENV,
    )
    if merged.returncode != 0:
        return UpdateOutcome(
            UNAVAILABLE,
            f"fast-forwarding {branch} failed; left as it is: {_output_tail(merged)}",
        )
    return UpdateOutcome(
        UPDATED,
        f"fast-forwarded {branch} {behind} commit(s) from {before} to {remote_head}",
    )


@dataclasses.dataclass(frozen=True)
class SetupOutcome:
    """What became of a new project's clone, init or remote check (ADR 212).

    `ok` is whether it did what it was asked; `detail` is the one line that
    says what happened, in git's words when git refused. `writable` is the
    check's alone (ADR NNN): whether the remote took a dry-run push, and
    `None` when that was not tried, which is every clone and init and a check
    whose read already failed.
    """

    ok: bool
    detail: str
    writable: bool | None = None


def _new_project_dir(container: str, projects_root: str, slug: str) -> str | SetupOutcome:
    """`<projects_root>/<slug>`, or the refusal when something is already there.

    A clone or an init never lands on top of a directory, whatever is in it:
    an existing directory is someone's checkout, or what is left of one, and
    neither is this action's to replace.
    """
    _require_non_empty(projects_root=projects_root, slug=slug)
    project_dir = f"{projects_root}/{slug}"
    if path_exists(container, project_dir):
        return SetupOutcome(False, f"{project_dir} already exists; it is left as it is")
    return project_dir


def clone_project(
    container: str,
    projects_root: str,
    slug: str,
    remote_url: str,
    base_branch: str | None = None,
    *,
    credentials_path: str = GIT_CREDENTIALS_PATH,
    keys_dir: str = PROJECT_KEYS_DIR,
    timeout: float | None = None,
) -> SetupOutcome:
    """Clone `remote_url` into `<projects_root>/<slug>`, with the project's key.

    The same access as every fetch and push (`_remote_access`): an SSH remote
    goes with the project's deploy key or is not tried, so a clone that fails
    here fails the way the first task's fetch would. `base_branch` is checked
    out when given; otherwise the remote's default branch is.
    """
    project_dir = _new_project_dir(container, projects_root, slug)
    if isinstance(project_dir, SetupOutcome):
        return project_dir
    access = _remote_access(
        container, slug, remote_url, remote_url, credentials_path, keys_dir
    )
    if isinstance(access, str):
        return SetupOutcome(False, f"not cloned: {access}")
    branch_args = ["--branch", base_branch] if base_branch else []
    cloned = run_docker_exec(
        container, projects_root,
        ["git", *access.git_args, "clone", "--quiet", *branch_args, "--", remote_url, slug],
        env=access.env,
        timeout=timeout,
    )
    if cloned.returncode != 0:
        # git removes the directory a failed clone made, so nothing is left
        # half-made to refuse the next try.
        return SetupOutcome(
            False, f"cloning {remote_url} failed: {_output_tail(cloned)}{access.missing}"
        )
    branch = current_branch(container, project_dir)
    on = f" on {branch}" if branch else ""
    return SetupOutcome(True, f"cloned {remote_url} into {project_dir}{on}")


def init_project(
    container: str,
    projects_root: str,
    slug: str,
    base_branch: str | None = None,
    *,
    author_name: str,
    author_email: str,
) -> SetupOutcome:
    """An empty repository at `<projects_root>/<slug>`, with one empty commit.

    The commit is there because a task's branch is cut from the checkout's
    HEAD (`_add_writer_worktree`'s `-b` form), and an unborn HEAD has nothing
    to cut from. It holds no file, so the project's first task writes the
    first one. The branch is `base_branch`, or git's own default when none.
    """
    project_dir = _new_project_dir(container, projects_root, slug)
    if isinstance(project_dir, SetupOutcome):
        return project_dir
    branch_args = ["--initial-branch", base_branch] if base_branch else []
    made = run_docker_exec(
        container, projects_root,
        ["git", "init", "--quiet", *branch_args, "--", slug],
        env=_GIT_ENV,
    )
    if made.returncode != 0:
        return SetupOutcome(False, f"git init failed: {_output_tail(made)}")
    # The author in the environment, which git ranks above any user.name a
    # config in the container might set, so the commit is always this one's.
    author = {
        "GIT_AUTHOR_NAME": author_name, "GIT_AUTHOR_EMAIL": author_email,
        "GIT_COMMITTER_NAME": author_name, "GIT_COMMITTER_EMAIL": author_email,
    }
    committed = run_docker_exec(
        container, project_dir,
        ["git", "commit", "--quiet", "--allow-empty", "-m", "Empty repository"],
        env={**_GIT_ENV, **author},
    )
    if committed.returncode != 0:
        return SetupOutcome(
            False,
            f"{project_dir} was made but its first commit failed: {_output_tail(committed)}",
        )
    branch = current_branch(container, project_dir)
    on = f" on {branch}" if branch else ""
    return SetupOutcome(True, f"made an empty repository at {project_dir}{on}")


def check_remote(
    container: str,
    projects_root: str,
    slug: str,
    remote_url: str,
    *,
    credentials_path: str = GIT_CREDENTIALS_PATH,
    keys_dir: str = PROJECT_KEYS_DIR,
    scratch_root: str | None = None,
    author_name: str = "ia-harness",
    author_email: str = "ia-harness@ia-harness.invalid",
    timeout: float | None = None,
) -> SetupOutcome:
    """Whether `remote_url` answers the project's key, and whether it may push.

    Run in `projects_root`, so it needs no clone: it is the step between
    pasting the deploy key on GitHub and cloning with it. The read is `git
    ls-remote --heads`; the write is `_probe_push`, tried only once the read
    answered (ADR NNN), so `writable` is `None` whenever `ok` is false.
    """
    _require_non_empty(projects_root=projects_root, slug=slug)
    access = _remote_access(
        container, slug, remote_url, remote_url, credentials_path, keys_dir
    )
    if isinstance(access, str):
        return SetupOutcome(False, f"not checked: {access}")
    listed = run_docker_exec(
        container, projects_root,
        ["git", *access.git_args, "ls-remote", "--heads", "--", remote_url],
        env=access.env,
        timeout=timeout,
    )
    if listed.returncode != 0:
        return SetupOutcome(
            False, f"{remote_url} did not answer: {_output_tail(listed)}{access.missing}"
        )
    heads = len([line for line in listed.stdout.splitlines() if line.strip()])
    read = f"{remote_url} answered with {heads} branch(es)"
    writable, said = _probe_push(
        container, remote_url, access, scratch_root or WRITE_CHECK_SCRATCH,
        author_name=author_name, author_email=author_email, timeout=timeout,
    )
    return SetupOutcome(True, f"{read}; {said}", writable)


#: The branch a write check names in its dry-run push. Never created: a
#: dry-run sends no pack and updates no ref, so the name only has to be one
#: nobody has, which is why each check adds a random suffix.
WRITE_CHECK_BRANCH = "ia-harness/write-check"

#: Where, in the container, the write check makes its throwaway repository
#: when the caller names no `scratch_root`: outside `projects_root`, so a probe
#: left behind by a killed check is never mistaken for a project.
WRITE_CHECK_SCRATCH = "/tmp"


def _probe_push(
    container: str,
    remote_url: str,
    access: _RemoteAccess,
    scratch_root: str,
    *,
    author_name: str,
    author_email: str,
    timeout: float | None,
) -> tuple[bool | None, str]:
    """`git push --dry-run` of an empty commit to a branch the remote does not have.

    What it proves, measured against git over SSH (ADR NNN): a dry-run push
    still connects to `git-receive-pack`, and that is where GitHub refuses a
    read-only deploy key ("The key you are authenticating with has been marked
    as read only"), before any ref is looked at. Accepted, the remote said it
    would take the new branch; nothing was sent, since a dry-run uploads no
    pack and updates no ref. Over https:// the same command asks for the
    receive-pack service, which GitHub grants only to a token that may write.
    It does not see branch protection: GitHub checks that per ref, on a real
    update, and this one names a new branch.

    The commit lives in a throwaway repository under `scratch_root`, never the
    project's directory, which a check runs before there is one; it is removed
    either way. `None` with the reason when the probe could not be set up.
    """
    made = run_docker_exec(
        container, "/", ["mktemp", "-d", f"{scratch_root}/ia-write-check.XXXXXX"],
        env=_GIT_ENV,
    )
    scratch = made.stdout.strip()
    if made.returncode != 0 or not scratch:
        return None, f"write not checked: no scratch directory: {_output_tail(made)}"
    try:
        # The author in the environment, as init_project does, so no config in
        # the container decides whether the commit can be made.
        author = {
            "GIT_AUTHOR_NAME": author_name, "GIT_AUTHOR_EMAIL": author_email,
            "GIT_COMMITTER_NAME": author_name, "GIT_COMMITTER_EMAIL": author_email,
        }
        for step in (
            ["git", "init", "--quiet"],
            ["git", "commit", "--quiet", "--allow-empty", "-m", "ia-harness write check"],
        ):
            proc = run_docker_exec(container, scratch, step, env={**_GIT_ENV, **author})
            if proc.returncode != 0:
                return None, f"write not checked: git {step[1]} failed: {_output_tail(proc)}"
        branch = f"{WRITE_CHECK_BRANCH}-{uuid.uuid4().hex[:12]}"
        pushed = run_docker_exec(
            container, scratch,
            ["git", *access.git_args, "push", "--dry-run", "--porcelain", "--",
             remote_url, f"HEAD:refs/heads/{branch}"],
            env=access.env,
            timeout=timeout,
        )
        if pushed.returncode != 0:
            return False, f"it refused a dry-run push: {_output_tail(pushed)}"
        return True, "it accepted a dry-run push of a new branch"
    finally:
        run_docker_exec(container, "/", ["rm", "-rf", "--", scratch], env=_GIT_ENV)


PUSHED = "pushed"
NO_UPSTREAM = "no-upstream"
PUSH_REFUSED = "push-refused"


@dataclasses.dataclass(frozen=True)
class PushOutcome:
    """What became of pushing the project's own branch to its upstream.

    Three outcomes, and only one of them is something to stop on:

    * PUSHED — the upstream now has the branch, including the case where it
      already had every commit and git had nothing to send.
    * NO_UPSTREAM — there is nowhere to push: no remote, or a branch that
      tracks nothing. A clone the operator keeps local is a choice, not a
      fault, so this is said and never treated as a failure.
    * PUSH_REFUSED — a push was due and did not land: the upstream moved and
      the push is not a fast-forward, the remote could not be reached with the
      credential there is, or git failed in any other way. The local branch
      keeps the merge either way; this is the outcome the queue (A-5) stops on.
    """

    status: str
    target: str | None
    detail: str

    @property
    def pushed(self) -> bool:
        return self.status == PUSHED

    @property
    def refused(self) -> bool:
        return self.status == PUSH_REFUSED


def _git_config(container: str, project_dir: str, key: str) -> str | None:
    proc = run_docker_exec(
        container, project_dir, ["git", "config", "--get", key], env=_GIT_ENV
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def push_base(
    container: str,
    projects_root: str,
    slug: str,
    credentials_path: str = GIT_CREDENTIALS_PATH,
    timeout: float | None = None,
    keys_dir: str = PROJECT_KEYS_DIR,
) -> PushOutcome:
    """Push the branch the project's checkout sits on to its upstream, or say why not.

    Called after a task branch has landed in that branch, by the automatic
    approve and by `merge-task --push` (docs/plans/autonomy.md, A-4). The
    upstream is the branch's own tracking configuration, `branch.<b>.remote`
    and `branch.<b>.merge`, and nothing else: no remote is guessed and no
    branch name is assumed to match on the other side.

    Never forced. The refspec has no `+` and neither `--force` nor
    `--force-with-lease` is passed, so a remote that moved refuses the push
    and the refusal is returned as it is. Nothing here resets, rebases or
    otherwise rewrites the local branch afterwards: the merge stays where it
    landed, and reconciling it with what the remote gained is a decision for a
    person (charter C-5, ADR 78).

    The credential is the one the fetch uses (_remote_access): the project's
    deploy key when there is one, the mounted https:// token otherwise. A push
    needs write scope where the fetch did not, and a credential without it
    comes back as a refusal in git's own words.
    """
    _require_non_empty(projects_root=projects_root, slug=slug)
    project_dir = f"{projects_root}/{slug}"

    branch = current_branch(container, project_dir)
    if branch is None:
        return PushOutcome(
            NO_UPSTREAM, None, f"{project_dir} is not on a branch; nothing pushed"
        )
    remote = _git_config(container, project_dir, f"branch.{branch}.remote")
    merge_ref = _git_config(container, project_dir, f"branch.{branch}.merge")
    if remote is None or merge_ref is None:
        return PushOutcome(
            NO_UPSTREAM, branch, f"{branch} tracks no upstream branch; nothing pushed"
        )
    if remote == ".":
        # `git branch --track` onto another local branch: a push there would
        # move a branch of this same clone, which is not what "the upstream"
        # means for a base.
        return PushOutcome(
            NO_UPSTREAM, branch,
            f"{branch} tracks {merge_ref} of this same clone, not a remote; nothing pushed",
        )
    url = run_docker_exec(
        container, project_dir,
        ["git", "remote", "get-url", "--push", remote],
        env=_GIT_ENV,
    )
    if url.returncode != 0:
        return PushOutcome(
            NO_UPSTREAM, branch,
            f"{branch} tracks {remote}, and {project_dir} has no remote by that name; "
            f"nothing pushed",
        )
    upstream = f"{remote}/{merge_ref.removeprefix('refs/heads/')}"
    access = _remote_access(
        container, slug, remote, url.stdout.strip(), credentials_path, keys_dir
    )
    if isinstance(access, str):
        return PushOutcome(
            PUSH_REFUSED, branch, f"{access}; {branch} not pushed to {upstream}"
        )
    pushed = run_docker_exec(
        container, project_dir,
        [
            "git", *access.git_args, "push", "--quiet", remote,
            f"refs/heads/{branch}:{merge_ref}",
        ],
        env=access.env,
        timeout=timeout,
    )
    if pushed.returncode != 0:
        return PushOutcome(
            PUSH_REFUSED, branch,
            f"pushing {branch} to {upstream} was refused; {branch} keeps the merge "
            f"locally and is not reset: {_output_tail(pushed)}{access.missing}",
        )
    head = _rev_parse_short(container, project_dir, "HEAD")
    return PushOutcome(PUSHED, branch, f"pushed {branch} to {upstream} at {head}")


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
    paths: Sequence[str] | None = None,
    excludes: Sequence[str] | None = None,
) -> bool:
    """Commit a worktree's work. False when there was nothing to commit.

    The identity is passed per invocation so each commit names the role and
    account that produced it; `-c` outranks the image's --system fallback (see
    docker/agent/Dockerfile) without writing any repo-local config.

    `paths` holds the commit to the part of the tree the role's duties cover,
    for a phase that shares the writers' worktree but runs after the review —
    see project_docs.commit_scope. Anything changed outside them stays in the
    worktree and is named in a warning: a phase writing where it was not asked
    to is worth seeing, and dropping it silently is the bug this whole
    argument exists to keep from coming back somewhere else.

    `excludes` subtracts paths from whatever `paths` allowed, for the files no
    role commits whoever it is — see project_docs.commit_excludes. It is a
    subtraction rather than a refusal on purpose: a phase that edited the
    charter still did the rest of its work, and throwing the branch away to
    punish one file would cost more than the file does. The edit stays in the
    worktree, named in a warning of its own.
    """
    excluded = list(excludes or ())
    exclude_specs = [f":(exclude){path}" for path in excluded]
    if paths is None:
        # `git add -A` takes no pathspec, and the exclusions need one to be
        # subtracted from. `.` is the pathspec `-A` already implies: every
        # caller's workdir is a worktree root, which is where this runs.
        scope = ["."] if exclude_specs else []
    else:
        # A pathspec matching nothing is a hard error in git, not an empty
        # commit: `git add -A -- docs` exits 128 on a tree with no docs/, and
        # a role that wrote nothing is the ordinary case, not a failure.
        present = [path for path in paths if path_exists(container, f"{workdir}/{path}")]
        if not present:
            return False
        _warn_changes_outside(container, workdir, present)
        scope = present
    _warn_excluded_changes(container, workdir, excluded)
    add_cmd = ["git", "add", "-A"]
    if scope or exclude_specs:
        add_cmd += ["--", *scope, *exclude_specs]
    add = run_docker_exec(container, workdir, add_cmd, env=_GIT_ENV)
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


def dirty_paths(container: str, workdir: str) -> list[str]:
    """What this checkout holds that its HEAD does not, as sorted paths.

    Best-effort on purpose: every caller asks this about a phase that has
    already returned, to say something extra about what it left behind, so a
    `git status` that will not run answers "nothing" rather than raising.
    """
    proc = run_docker_exec(
        container, workdir, ["git", "status", "--porcelain"], env=_GIT_ENV,
    )
    if proc.returncode != 0:
        return []
    # Porcelain v1 is two status columns, a space, then the path.
    return sorted(
        {line[3:].strip() for line in proc.stdout.splitlines() if line.strip()}
    )


def _under(path: str, roots: Sequence[str]) -> bool:
    """Whether `path` is one of `roots` or sits below one of them."""
    return any(
        path == root.rstrip("/") or path.startswith(f"{root.rstrip('/')}/")
        for root in roots
    )


@dataclasses.dataclass(frozen=True)
class LeftBehind:
    """What a scoped commit left in the worktree, uncommitted."""

    outside: list[str]
    excluded: list[str]

    @property
    def paths(self) -> list[str]:
        return [*self.outside, *self.excluded]


def left_behind(
    container: str,
    workdir: str,
    excludes: Sequence[str] | None = None,
) -> LeftBehind:
    """The paths still dirty after a commit, split into excluded and outside.

    Taken after the commit, so everything dirty is by definition what the
    commit did not carry. Best-effort like `dirty_paths`.
    """
    roots = list(excludes or ())
    excluded: list[str] = []
    outside: list[str] = []
    for path in dirty_paths(container, workdir):
        (excluded if _under(path, roots) else outside).append(path)
    return LeftBehind(outside=outside, excluded=excluded)


def diff_of(container: str, workdir: str, paths: Sequence[str]) -> str:
    """A `git apply`-able patch of exactly `paths`, untracked files included.

    Raises RuntimeError when git cannot produce it: the caller records
    best-effort and treats that as a warning.
    """
    if not paths:
        return ""
    tracked = run_docker_exec(
        container, workdir,
        ["git", "diff", "--binary", "HEAD", "--", *paths], env=_GIT_ENV,
    )
    if tracked.returncode != 0:
        raise RuntimeError(f"git diff failed: {tracked.stderr}")
    listing = run_docker_exec(
        container, workdir,
        ["git", "ls-files", "--others", "--exclude-standard", "-z", "--", *paths],
        env=_GIT_ENV,
    )
    if listing.returncode != 0:
        raise RuntimeError(f"git ls-files failed: {listing.stderr}")
    chunks = [tracked.stdout]
    for name in (n for n in listing.stdout.split("\0") if n):
        proc = run_docker_exec(
            container, workdir,
            ["git", "diff", "--binary", "--no-index", "--", "/dev/null", name],
            env=_GIT_ENV,
        )
        # `--no-index` exits 1 when the files differ, which is the whole point.
        if proc.returncode not in (0, 1):
            raise RuntimeError(f"git diff of {name} failed: {proc.stderr}")
        chunks.append(proc.stdout)
    return "".join(chunks)


def _warn_changes_outside(container: str, workdir: str, scope: Sequence[str]) -> None:
    """Name what a scoped commit is about to leave behind, and leave it.

    Best-effort on purpose: this is an observation about a commit that is
    otherwise fine, so a `git status` that will not run must not be the thing
    that stops it.
    """
    prefixes = tuple(f"{path.rstrip('/')}/" for path in scope)
    bare = {path.rstrip("/") for path in scope}
    outside = [
        path
        for path in dirty_paths(container, workdir)
        if path not in bare and not path.startswith(prefixes)
    ]
    if outside:
        logger.warning(
            "%s: left uncommitted, outside this phase's scope (%s): %s",
            workdir, ", ".join(scope), ", ".join(outside[:10]),
        )


def _warn_excluded_changes(container: str, workdir: str, excludes: Sequence[str]) -> None:
    """Name an excluded path this phase changed, and leave the change behind.

    Louder than `_warn_changes_outside`, and worth keeping separate from it:
    that one reports an untidy phase, this one reports a rule broken. A phase
    that edited the charter was told not to, so the commit does not carry it
    and a human finds the edit in the worktree.
    """
    if not excludes:
        return
    touched = sorted(set(dirty_paths(container, workdir)) & set(excludes))
    if touched:
        logger.warning(
            "%s: NOT committed, and no role may change it: %s",
            workdir, ", ".join(touched),
        )


def _nothing_to_commit(stdout: str, stderr: str) -> bool:
    """git reports a clean tree on *stdout* with exit 1, not on stderr."""
    combined = f"{stdout}\n{stderr}"
    return any(
        marker in combined
        for marker in (
            "nothing to commit",
            "nothing added to commit",
            "no changes added to commit",
        )
    )


def path_exists(container: str, path: str) -> bool:
    """Whether `path` is there, asked of the container rather than the host.

    The repo is not mounted into the agents, so a project file only exists
    from the dispatcher's point of view through `docker exec`. Costs no quota:
    no model runs.
    """
    proc = run_docker_exec(container, "/", ["test", "-e", path])
    return proc.returncode == 0


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
