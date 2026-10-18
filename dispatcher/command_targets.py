"""Where a filed command request would run, and what no request may run.

Step 3 of `docs/plans/command-requests.md` (decisions 3 and 7;
`docs/decisions.md` ADR 118). A request the step-2 check accepted
(`handoff.command_requests`) names a kind of target, never a container; this
module turns that kind into the one container it can mean for the account the
phase ran on, and refuses what the denylist names. Nothing here runs the
command: that is step 4, and it calls `denylist_problems` again on what it is
about to run, so the rules are the same ones at both ends.

Everything but `running_services` is pure. That one is the only docker call,
kept apart so the resolver can be tested on a set and never on a daemon.
"""

from __future__ import annotations

import dataclasses
import logging
import posixpath
import re
import subprocess
from collections.abc import Callable, Iterable

from dispatcher import docker_exec
from dispatcher.config import AccountConfig

logger = logging.getLogger(__name__)

#: The harness's own compose services, and the board service the plans name
#: but no compose file has yet. No request resolves to one by construction —
#: only the phase's own account's two containers and the project's services
#: can come out of `resolve` — and `_never_target` checks it again by name, so
#: a project that calls its own service `api` is refused rather than trusted.
HARNESS_SERVICES = frozenset({
    "dispatcher", "api", "collector", "console", "config-writer", "caddy",
    "registry-mirror", "board", "board-service",
})

#: The labels `docker compose` puts on every container it starts. The working
#: directory is what ties a container to a project: it is the path the agent
#: ran compose from, under `/data/projects/<slug>`, and the project *name* is
#: not — two checkouts can share one, and a project can set its own.
_LABEL_WORKING_DIR = "com.docker.compose.project.working_dir"
_LABEL_SERVICE = "com.docker.compose.service"

#: How long the one `docker ps` may take before the list counts as unavailable.
_SERVICES_TIMEOUT_SECONDS = 30

SERVICES_UNAVAILABLE = "service list unavailable"


def writer_worktree(projects_root: str, slug: str, task_id: str) -> str:
    """The task's writer worktree, the tree every request's `cwd` is relative to."""
    return f"{docker_exec.task_worktrees_dir(projects_root, slug, task_id)}/{docker_exec.WRITER_WORKTREE_NAME}"


def _inside(path: str, root: str) -> bool:
    """Whether `path` is `root` or under it, by whole segment and after normalizing both."""
    path, root = posixpath.normpath(path), posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def _never_target(
    resolved: dict, account: AccountConfig, accounts: Iterable[AccountConfig],
) -> str | None:
    """Why a resolved target is one no request may reach, or None.

    Belt and braces: `resolve` can only produce the account's own names, so
    this fires only on a config that gives two accounts one container, or a
    project service named like a harness one. Either is a reason to refuse,
    not to guess which was meant.
    """
    container = resolved["container"]
    for other in accounts:
        if other.name == account.name:
            continue
        if container in (other.container, other.dind):
            return f"the target {container!r} is account {other.name}'s container"
    if container in HARNESS_SERVICES:
        return f"the target {container!r} is a harness service"
    service = resolved.get("service")
    if service is not None and service in HARNESS_SERVICES:
        return f"the service {service!r} is named like a harness service"
    return None


def resolve(
    request: dict,
    account: AccountConfig,
    worktree: str,
    services: frozenset[str] | None = None,
    accounts: Iterable[AccountConfig] = (),
) -> tuple[dict | None, str | None]:
    """A request's concrete target, as `(resolved, None)` or `(None, reason)`.

    `request` is one the step-2 check accepted, `account` the one the phase
    ran on, `worktree` the task's writer worktree, `services` the project's
    running compose services in that account's dind (None when they could not
    be read) and `accounts` the whole pool, for the never-target check. The
    resolved target is `{"kind", "container", "service"?, "cwd"}`: `cwd` is
    the worktree joined with the request's and normalized, and must stay in
    it. For `service`, `container` is the dind the service runs in and
    `service` its compose name; step 4 reaches it through that dind.
    """
    kind = request["target"]["kind"]
    if kind == "agent":
        container = account.container
    elif not account.dind:
        return None, f"account {account.name} has no dind, so a {kind} target cannot be resolved"
    else:
        container = account.dind
    resolved: dict = {"kind": kind, "container": container}
    if kind == "service":
        name = request["target"]["name"].strip()
        if services is None:
            return None, SERVICES_UNAVAILABLE
        if name not in services:
            return None, f"service {name!r} is not running in {container} for this project"
        resolved["service"] = name
    cwd = posixpath.normpath(posixpath.join(worktree, request["cwd"]))
    if not _inside(cwd, worktree):
        return None, f"denylist rule cwd-outside-worktree: cwd {cwd!r} is outside the task's worktree"
    resolved["cwd"] = cwd
    problem = _never_target(resolved, account, accounts)
    if problem is not None:
        return None, problem
    return resolved, None


# --- the denylist ---------------------------------------------------------


def _base(item: str) -> str:
    """An argv item as a command name: its basename, so `/usr/bin/sudo` is `sudo`."""
    return posixpath.basename(item)


def _positions(argv: list[str], names: Iterable[str]) -> list[int]:
    """Every index whose item, by basename, is one of `names`.

    Every item and not only the first: `env sudo x`, `timeout 9 git push` and
    `docker compose exec app git push` carry the command further in, and the
    rules do not parse launchers to find it. A bare word that happens to be a
    command name (`grep docker`) is refused too; that is the price of a
    denylist that does not try to understand each launcher's options.
    """
    names = frozenset(names)
    return [i for i, item in enumerate(argv) if _base(item) in names]


#: Git's options that come before the subcommand and take a separate value.
_GIT_VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"})
#: Subcommands that reach a remote, or set one up to be reached.
_GIT_REMOTE_SUBCOMMANDS = frozenset({"push", "fetch", "pull", "clone", "ls-remote", "send-pack", "fetch-pack"})
#: What `git remote` may still do: list, and print a url. Everything else adds,
#: changes or contacts one.
_GIT_REMOTE_LOCAL = frozenset({"-v", "--verbose", "get-url"})


def _git_subcommand(argv: list[str], start: int) -> tuple[str | None, list[str]]:
    """The subcommand after the `git` at `start`, skipping its global options, and what follows it."""
    i = start + 1
    while i < len(argv):
        item = argv[i]
        if item in _GIT_VALUE_OPTIONS:
            i += 2
        elif item.startswith("-"):
            i += 1
        else:
            return item, argv[i + 1:]
    return None, []


def _remote_git(argv: list[str], kind: str, cwd: str, worktree: str) -> str | None:
    for i in _positions(argv, ("git",)):
        sub, rest = _git_subcommand(argv, i)
        if sub in _GIT_REMOTE_SUBCOMMANDS:
            return f"`git {sub}` reaches a remote"
        if sub == "remote" and rest and rest[0] not in _GIT_REMOTE_LOCAL:
            return f"`git remote {rest[0]}` adds, changes or contacts a remote"
        if sub == "submodule" and "update" in rest:
            return "`git submodule update` fetches from a remote"
    return None


_DOCKER_BINARIES = frozenset({"docker", "docker-compose", "podman", "nerdctl"})
#: Anywhere in an item, for every kind: a socket path or the variable that
#: points a client at a daemon. Substrings, because they come embedded —
#: `-v /var/run/docker.sock:/s`, `DOCKER_HOST=tcp://x`.
_SOCKET_MARKERS = ("docker.sock", "DOCKER_HOST", "DOCKER_CONTEXT")
#: Docker's global options that point the client at some other daemon.
_DOCKER_HOST_OPTIONS = frozenset({"-H", "--host", "-c", "--context", "--config"})


def _docker_socket(argv: list[str], kind: str, cwd: str, worktree: str) -> str | None:
    for item in argv:
        for marker in _SOCKET_MARKERS:
            if marker in item:
                return f"{marker!r} points a client at a docker daemon"
    positions = _positions(argv, _DOCKER_BINARIES)
    if kind == "agent":
        if positions:
            return f"`{_base(argv[positions[0]])}` from the agent side; file it for the dind"
        return None
    # In the dind, docker is the point (the project's docker commands); what
    # is refused is aiming it at any daemon but the one it runs next to.
    for i in positions:
        j = i + 1
        while j < len(argv) and argv[j].startswith("-"):
            option = argv[j].split("=", 1)[0]
            if option in _DOCKER_HOST_OPTIONS:
                return f"`{_base(argv[i])} {option}` points docker at another daemon"
            j += 1
        if j < len(argv) and argv[j] == "context":
            return f"`{_base(argv[i])} context` switches docker to another daemon"
    return None


#: Path segments that name a credential. Matched by whole segment, so
#: `README.env.md` and `my.ssh.notes` pass and `config/.env` does not.
_CREDENTIAL_SEGMENTS = frozenset({
    ".env", ".claude", ".claude-account", ".ssh", "git-credentials",
    ".git-credentials", ".netrc", "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
})
#: Two-segment paths: a credentials directory is named by its parent.
_CREDENTIAL_PAIRS = frozenset({(".data", "credentials"), ("run", "secrets")})


def _segments(item: str) -> list[str]:
    """An argv item's path segments, splitting on `=` and `:` too.

    So `--file=/root/.ssh/id_rsa` and `-v /root/.claude:/x` are read as the
    paths they carry, not as one opaque word.
    """
    return [part for part in re.split(r"[/=:]", item) if part]


def _credential_path(argv: list[str], kind: str, cwd: str, worktree: str) -> str | None:
    for item in argv:
        parts = _segments(item)
        for n, part in enumerate(parts):
            if part in _CREDENTIAL_SEGMENTS or part.startswith(".env."):
                return f"{item!r} names a credential path ({part})"
            if n and (parts[n - 1], part) in _CREDENTIAL_PAIRS:
                return f"{item!r} names a credential path ({parts[n - 1]}/{part})"
    return None


_PRIVILEGE = frozenset({"sudo", "su", "doas"})


def _privilege(argv: list[str], kind: str, cwd: str, worktree: str) -> str | None:
    positions = _positions(argv, _PRIVILEGE)
    if positions:
        return f"`{_base(argv[positions[0]])}` raises privilege"
    return None


_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "ash", "fish"})
#: A short-option cluster with `c` in it: `-c`, `-lc`, `-ec`.
_SHELL_COMMAND_FLAG = re.compile(r"^-[A-Za-z]*c[A-Za-z]*$")


def _shell_command(argv: list[str], kind: str, cwd: str, worktree: str) -> str | None:
    # The one rule about form rather than content: a `-c` string is a whole
    # command in one argv item, and every other rule reads items, not shell.
    # Refused outright rather than parsed; a request that needs a pipeline
    # files a script in the worktree and asks to run that.
    for i in _positions(argv, _SHELLS):
        j = i + 1
        while j < len(argv) and argv[j].startswith("-"):
            if _SHELL_COMMAND_FLAG.match(argv[j]) or argv[j] == "--command":
                return f"`{_base(argv[i])} {argv[j]}` runs a command string no rule can read"
            j += 1
    return None


def _cwd_outside(argv: list[str], kind: str, cwd: str, worktree: str) -> str | None:
    if not _inside(cwd, worktree):
        return f"cwd {cwd!r} is outside the task's worktree"
    return None


@dataclasses.dataclass(frozen=True)
class Rule:
    """One denylist rule: a name the refusal carries, and the check.

    `check(argv, kind, cwd, worktree)` returns why the command is refused, or
    None. `cwd` is the resolved, absolute one.
    """

    name: str
    check: Callable[[list[str], str, str, str], str | None]


DENYLIST: tuple[Rule, ...] = (
    Rule("remote-git", _remote_git),
    Rule("docker-socket", _docker_socket),
    Rule("credential-path", _credential_path),
    Rule("privilege", _privilege),
    Rule("shell-command", _shell_command),
    Rule("cwd-outside-worktree", _cwd_outside),
)
"""What no verdict overrides (`docs/plans/command-requests.md` decision 7).

Checked when a request is filed and again, by step 4, when it is run. The list
lives here and not in the config, and it changes only under an ADR
(`docs/decisions.md` ADR 118 settles this one). It is a denylist, not a
sandbox: it refuses what C-5 and the account boundary forbid, and the person
reading the argv in Approvals is still the check on everything else.
"""


def denylist_problems(argv: list[str], kind: str, cwd: str, worktree: str) -> list[str]:
    """Every denylist rule the command breaks, each naming its rule. Empty means none.

    `cwd` is absolute, as `resolve` returns it. Exported so step 4 runs the
    same rules on what it is about to execute.
    """
    problems = []
    for rule in DENYLIST:
        why = rule.check(argv, kind, cwd, worktree)
        if why is not None:
            problems.append(f"denylist rule {rule.name}: {why}")
    return problems


# --- the filing check -----------------------------------------------------


def check(
    accepted: list[dict],
    refused: list[dict],
    account: AccountConfig | None,
    accounts: Iterable[AccountConfig],
    worktree: str,
    read_services: Callable[[], frozenset[str] | None],
) -> tuple[list[dict], list[dict]]:
    """The step-2 lists after resolution and the denylist, as new lists.

    A request either fails here and moves to `refused` as
    `{"request": entry, "reason": ...}`, like a step-2 refusal, or stays
    accepted as a copy carrying its `resolved` target. `read_services` is
    called at most once, and only when a `service` request needs it.
    """
    accounts = list(accounts)
    still: list[dict] = []
    out = list(refused)
    services: frozenset[str] | None = None
    services_read = False
    for entry in accepted:
        if account is None:
            out.append({"request": entry, "reason": "the phase ran on no known account, so no target resolves"})
            continue
        if entry["target"]["kind"] == "service" and account.dind and not services_read:
            services, services_read = read_services(), True
        resolved, reason = resolve(entry, account, worktree, services, accounts)
        if resolved is None:
            out.append({"request": entry, "reason": reason})
            continue
        problems = denylist_problems(entry["argv"], resolved["kind"], resolved["cwd"], worktree)
        if problems:
            out.append({"request": entry, "reason": "; ".join(problems)})
            continue
        still.append({**entry, "resolved": resolved})
    return still, out


# --- the one docker call --------------------------------------------------


def project_services(output: str, project_dir: str) -> frozenset[str]:
    """The compose services in `docker ps` output that belong to `project_dir`.

    One line per container: working directory, a tab, service. A container
    without the labels was not started by compose and belongs to no project;
    one started from outside the project's directory is another project's.
    """
    names = set()
    for line in output.splitlines():
        working_dir, _, service = line.partition("\t")
        working_dir, service = working_dir.strip(), service.strip()
        if working_dir and service and _inside(working_dir, project_dir):
            names.add(service)
    return frozenset(names)


def running_services(dind: str, project_dir: str) -> frozenset[str] | None:
    """The project's running compose services in `dind`, or None if they cannot be read.

    A `docker ps` run inside the dind against its own daemon. Any failure is
    None, which the resolver turns into SERVICES_UNAVAILABLE: a service is
    never accepted on a guess.
    """
    template = f'{{{{.Label "{_LABEL_WORKING_DIR}"}}}}\t{{{{.Label "{_LABEL_SERVICE}"}}}}'
    try:
        proc = docker_exec.run_docker_exec(
            dind, "/", ["docker", "ps", "--format", template],
            timeout=_SERVICES_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("the services in %s could not be listed: %s", dind, exc)
        return None
    if proc.returncode != 0:
        logger.warning(
            "the services in %s could not be listed: exit %d", dind, proc.returncode,
        )
        return None
    return project_services(proc.stdout, project_dir)
