from __future__ import annotations

import dataclasses
import json
import logging
import subprocess
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
    max_turns: int | None = None,
    plugin_dirs: Sequence[str] | None = None,
    append_system_prompt: str | None = None,
    json_schema: dict | None = None,
    permission_mode: str | None = None,
    add_dirs: Sequence[str] | None = None,
    allowed_tools: Sequence[str] | None = None,
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
    # Before the first worktree exists, so the directory is ignored from the
    # moment it shows up instead of after someone trips over it.
    ensure_worktrees_ignored(container, project_dir)
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
    out to be wrong.
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
      the mounted credential cannot serve, or a fetch that failed, a private
      remote with no credential mounted for it among the reasons it can fail.
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


def update_project_branch(
    container: str,
    projects_root: str,
    slug: str,
    remote: str = "origin",
    credentials_path: str = GIT_CREDENTIALS_PATH,
    timeout: float | None = None,
) -> UpdateOutcome:
    """Fast-forward the project's own checkout onto its remote, or say why not.

    Called once per task, before the first worktree exists. That is the only
    moment it is safe: a new task's branch is cut from this checkout's HEAD
    (_add_writer_worktree's `-b` form takes no commit-ish), so moving HEAD here
    is what gives the task a fresh base, while the four roles of a task already
    under way share one worktree on one branch and moving the ground under them
    mid-cycle is how the revisor once came to review a tree with none of the
    implementador's work in it.

    Nothing here pushes, so the credential it asks for only ever needs to read,
    and a public remote needs none: the mounted file is named to git only when
    it is there, and a fetch that then fails is reported rather than prevented.

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
    if not url.stdout.strip().startswith("https://"):
        # Said rather than fixed: rewriting someone's remote is a change to
        # their clone, and the credential format mounted here can only answer
        # for HTTPS. The agent image ships no ssh at all, so an SSH remote
        # cannot be fetched from in here by any means.
        return UpdateOutcome(
            UNAVAILABLE,
            f"{remote} is not an https:// remote, which is the only kind the "
            f"mounted credential can serve; {branch} left as it is",
        )

    # `test -f` and not path_exists's `test -e`, because the thing most likely to
    # be at this path is a directory: a bind mount whose host source does not
    # exist gets one created for it, so the operator who brought the agents up
    # before writing the credential file has an empty directory here, and a
    # directory is not something git's store helper can read.
    has_credential = (
        run_docker_exec(container, "/", ["test", "-f", credentials_path]).returncode == 0
    )
    # A missing credential is not a reason to skip the fetch, only a reason not
    # to name a helper. A public repo over https needs no credential at all, and
    # this runs against whatever project the operator put under projects_root. A
    # private remote refuses in a second instead, and comes back through the
    # failure below in git's own words with the path that was looked for added.
    helper = (
        ["-c", f"credential.helper=store --file={credentials_path}"]
        if has_credential
        else []
    )
    fetched = run_docker_exec(
        container, project_dir,
        ["git", *helper, "fetch", "--quiet", remote, branch],
        # Without this a credential git cannot find turns into a prompt on a
        # terminal nobody is watching, and the call hangs until the phase
        # timeout instead of failing in a second with a reason.
        env={**_GIT_ENV, "GIT_TERMINAL_PROMPT": "0"},
        timeout=timeout,
    )
    if fetched.returncode != 0:
        why = _output_tail(fetched)
        if not has_credential:
            why = f"{why} (no credential file at {credentials_path})"
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
    return "nothing to commit" in combined or "nothing added to commit" in combined


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
