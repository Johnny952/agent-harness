"""A filed command request is resolved to one container and checked against
the denylist (`docs/plans/command-requests.md`, decisions 3 and 7;
`docs/decisions.md` ADR 118). No test here reaches docker: the one reader of
the dind's services is stubbed or fed its output."""

import subprocess

import pytest

import dispatcher.dispatcher as dispatcher_mod
from dispatcher import command_targets, context_transfer, handoff
from dispatcher.command_targets import denylist_problems, resolve
from dispatcher.config import AccountConfig
from tests.dispatcher.test_command_requests import _implementador_return, _request
from tests.dispatcher.test_dispatcher import (  # noqa: F401 - fixtures used by name
    _DESCRIPTION,
    _make_config,
    _phases_may_end_in_prose,
    _recording_dispatch_phase,
    fake_git,
)

WORKTREE = "/data/projects/myproj/worktrees/T-1/work"
ONE = AccountConfig(name="cuenta1", container="agent-cuenta1", dind="dind-cuenta1")
TWO = AccountConfig(name="cuenta2", container="agent-cuenta2", dind="dind-cuenta2")
NO_DIND = AccountConfig(name="cuenta1", container="agent-cuenta1")
POOL = (ONE, TWO)


def _resolve(request_, account=ONE, services=frozenset({"db", "app"}), accounts=POOL):
    return resolve(request_, account, WORKTREE, services, accounts)


# --- resolution -----------------------------------------------------------


def test_agent_resolves_to_the_accounts_agent_container_in_the_worktree() -> None:
    resolved, reason = _resolve(_request(kind="agent"))
    assert reason is None
    assert resolved == {"kind": "agent", "container": "agent-cuenta1", "cwd": WORKTREE}


def test_dind_resolves_to_the_accounts_dind() -> None:
    resolved, reason = _resolve(_request(kind="dind", argv=("docker", "ps")))
    assert reason is None
    assert resolved == {"kind": "dind", "container": "dind-cuenta1", "cwd": WORKTREE}


def test_a_running_service_resolves_to_it_inside_the_dind() -> None:
    resolved, reason = _resolve(_request(kind="service", name="db", argv=("psql",)))
    assert reason is None
    assert resolved == {"kind": "service", "container": "dind-cuenta1", "service": "db", "cwd": WORKTREE}


def test_the_resolver_follows_the_account_the_phase_ran_on() -> None:
    resolved, _ = _resolve(_request(kind="dind"), account=TWO)
    assert resolved["container"] == "dind-cuenta2"


@pytest.mark.parametrize(
    "cwd, expected",
    [
        ("backend/api", f"{WORKTREE}/backend/api"),
        ("./backend//api/", f"{WORKTREE}/backend/api"),
        ("backend/./api", f"{WORKTREE}/backend/api"),
        ("", WORKTREE),
    ],
)
def test_agent_cwd_is_joined_and_normalized(cwd, expected) -> None:
    resolved, reason = _resolve(_request(cwd=cwd))
    assert reason is None
    assert resolved["cwd"] == expected


@pytest.mark.parametrize(
    "cwd",
    [
        "src/../../other-task",  # `..` that escapes only once joined
        "../work-evil",           # a sibling sharing the worktree's prefix
        "/etc",                   # absolute: join drops the worktree
        f"{WORKTREE}-evil",       # absolute, and only a string prefix of it
    ],
)
def test_a_cwd_that_leaves_the_worktree_is_refused(cwd) -> None:
    resolved, reason = _resolve(_request(cwd=cwd))
    assert resolved is None
    assert "cwd-outside-worktree" in reason


def test_an_absolute_cwd_inside_the_worktree_resolves() -> None:
    """Step 2 refuses an absolute `cwd`; resolution alone only asks where it lands."""
    resolved, _ = _resolve(_request(cwd=f"{WORKTREE}/src"))
    assert resolved["cwd"] == f"{WORKTREE}/src"


@pytest.mark.parametrize("kind, name", [("dind", ""), ("service", "db")])
def test_an_account_with_no_dind_refuses_dind_and_service(kind, name) -> None:
    resolved, reason = _resolve(_request(kind=kind, name=name), account=NO_DIND, accounts=(NO_DIND,))
    assert resolved is None
    assert "has no dind" in reason


def test_an_account_with_no_dind_still_resolves_agent() -> None:
    resolved, _ = _resolve(_request(kind="agent"), account=NO_DIND, accounts=(NO_DIND,))
    assert resolved["container"] == "agent-cuenta1"


def test_a_service_that_is_not_running_is_refused() -> None:
    resolved, reason = _resolve(_request(kind="service", name="redis"))
    assert resolved is None
    assert "'redis' is not running" in reason


def test_an_unreadable_service_list_is_refused_never_guessed() -> None:
    resolved, reason = _resolve(_request(kind="service", name="db"), services=None)
    assert resolved is None
    assert reason == "service list unavailable"


def test_never_another_accounts_container() -> None:
    """A config that hands two accounts one dind: refused, not resolved to it."""
    shared = AccountConfig(name="cuenta1", container="agent-cuenta1", dind="dind-cuenta2")
    resolved, reason = _resolve(_request(kind="dind"), account=shared, accounts=(shared, TWO))
    assert resolved is None
    assert "account cuenta2's container" in reason


@pytest.mark.parametrize("name", sorted(command_targets.HARNESS_SERVICES))
def test_never_a_harness_service_by_name(name) -> None:
    resolved, reason = _resolve(_request(kind="service", name=name), services=frozenset({name}))
    assert resolved is None
    assert "harness service" in reason


def test_never_a_harness_container() -> None:
    misnamed = AccountConfig(name="cuenta1", container="dispatcher", dind="dind-cuenta1")
    resolved, reason = _resolve(_request(kind="agent"), account=misnamed, accounts=(misnamed,))
    assert resolved is None
    assert "harness service" in reason


@pytest.mark.parametrize("kind, name", [("agent", ""), ("dind", ""), ("service", "db"), ("service", "app")])
def test_by_construction_only_the_accounts_own_names_come_out(kind, name) -> None:
    others = {TWO.container, TWO.dind} | command_targets.HARNESS_SERVICES
    resolved, _ = _resolve(_request(kind=kind, name=name))
    assert resolved["container"] in {ONE.container, ONE.dind}
    assert resolved["container"] not in others


# --- the services reader --------------------------------------------------


def test_only_the_projects_compose_services_count() -> None:
    output = "\n".join([
        "/data/projects/myproj\tdb",
        "/data/projects/myproj/worktrees/T-1/work\tapp",
        "/data/projects/myproj-other\tcache",  # a prefix, another project
        "/data/projects/other\tredis",
        "\tloose",                             # no compose labels at all
        "",
    ])
    assert command_targets.project_services(output, "/data/projects/myproj") == {"db", "app"}


def test_the_reader_lists_inside_the_dind(monkeypatch) -> None:
    seen = []

    def fake_exec(container, workdir, command, env=None, timeout=None):
        seen.append((container, command))
        return subprocess.CompletedProcess(command, 0, stdout="/data/projects/myproj\tdb\n", stderr="")

    monkeypatch.setattr(command_targets.docker_exec, "run_docker_exec", fake_exec)
    assert command_targets.running_services("dind-cuenta1", "/data/projects/myproj") == {"db"}
    assert seen[0][0] == "dind-cuenta1"
    assert seen[0][1][:2] == ["docker", "ps"]


@pytest.mark.parametrize(
    "outcome",
    [
        subprocess.CompletedProcess([], 1, stdout="", stderr="no daemon"),
        subprocess.TimeoutExpired(["docker"], 30),
        FileNotFoundError("docker"),
    ],
)
def test_a_reader_that_fails_says_unavailable(monkeypatch, outcome) -> None:
    def fake_exec(*args, **kwargs):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(command_targets.docker_exec, "run_docker_exec", fake_exec)
    assert command_targets.running_services("dind-cuenta1", "/data/projects/myproj") is None


# --- the denylist ---------------------------------------------------------


def _problems(argv, kind="agent", cwd=WORKTREE):
    return denylist_problems(list(argv), kind, cwd, WORKTREE)


@pytest.mark.parametrize(
    "rule, argv, kind",
    [
        ("remote-git", ("git", "push"), "agent"),
        ("remote-git", ("git", "-C", "x", "push"), "agent"),
        ("remote-git", ("git", "-c", "k=v", "push", "origin"), "agent"),
        ("remote-git", ("git", "--git-dir=.git", "fetch"), "agent"),
        ("remote-git", ("git", "pull"), "agent"),
        ("remote-git", ("git", "clone", "https://x"), "agent"),
        ("remote-git", ("git", "ls-remote", "origin"), "agent"),
        ("remote-git", ("git", "remote", "add", "o", "https://x"), "agent"),
        ("remote-git", ("git", "remote", "set-url", "origin", "https://x"), "agent"),
        ("remote-git", ("git", "submodule", "update", "--init"), "agent"),
        ("remote-git", ("/usr/bin/git", "push"), "agent"),
        ("remote-git", ("timeout", "60", "git", "push"), "agent"),
        ("remote-git", ("docker", "compose", "exec", "app", "git", "push"), "dind"),
        ("docker-socket", ("docker", "ps"), "agent"),
        ("docker-socket", ("docker-compose", "up"), "agent"),
        ("docker-socket", ("podman", "ps"), "agent"),
        ("docker-socket", ("/usr/local/bin/nerdctl", "ps"), "agent"),
        ("docker-socket", ("curl", "--unix-socket", "/var/run/docker.sock", "http://x"), "agent"),
        ("docker-socket", ("env", "DOCKER_HOST=tcp://dind-cuenta2:2375", "make"), "agent"),
        ("docker-socket", ("docker", "-H", "tcp://dind-cuenta2:2375", "ps"), "dind"),
        ("docker-socket", ("docker", "--host=unix:///x.sock", "ps"), "dind"),
        ("docker-socket", ("docker", "--context", "other", "ps"), "dind"),
        ("docker-socket", ("docker", "context", "use", "other"), "dind"),
        ("docker-socket", ("docker", "run", "-v", "/var/run/docker.sock:/s", "img"), "dind"),
        ("credential-path", ("cat", "/root/.claude-account/x"), "agent"),
        ("credential-path", ("cat", ".env"), "agent"),
        ("credential-path", ("cat", "config/.env.production"), "agent"),
        ("credential-path", ("ls", "/root/.claude"), "agent"),
        ("credential-path", ("cat", "/run/secrets/git-credentials"), "agent"),
        ("credential-path", ("cat", "/root/.ssh/id_ed25519"), "agent"),
        ("credential-path", ("ssh-keygen", "-y", "-f", "id_rsa"), "agent"),
        ("credential-path", ("ls", ".data/credentials"), "agent"),
        ("credential-path", ("tool", "--file=/root/.ssh/known_hosts"), "agent"),
        ("credential-path", ("docker", "run", "-v", "/root/.claude:/x", "img"), "dind"),
        ("privilege", ("sudo", "apt-get", "install", "x"), "agent"),
        ("privilege", ("/usr/bin/sudo", "ls"), "agent"),
        ("privilege", ("su", "-"), "agent"),
        ("privilege", ("doas", "ls"), "agent"),
        ("privilege", ("env", "sudo", "ls"), "agent"),
        ("shell-command", ("sh", "-c", "uv sync"), "agent"),
        ("shell-command", ("bash", "-lc", "uv sync"), "agent"),
        ("shell-command", ("/bin/zsh", "-c", "ls"), "agent"),
        ("shell-command", ("bash", "-l", "-c", "ls"), "agent"),
        ("shell-command", ("docker", "compose", "exec", "app", "sh", "-c", "ls"), "dind"),
        ("cwd-outside-worktree", ("ls",), "agent"),
    ],
)
def test_each_rule_refuses_and_names_itself(rule, argv, kind) -> None:
    cwd = "/data/projects/myproj" if rule == "cwd-outside-worktree" else WORKTREE
    problems = _problems(argv, kind, cwd)
    assert any(p.startswith(f"denylist rule {rule}:") for p in problems), problems


@pytest.mark.parametrize(
    "argv, kind",
    [
        (("git", "status"), "agent"),
        (("git", "log", "--grep", "push"), "agent"),
        (("git", "-C", "push", "status"), "agent"),  # `push` is -C's value here
        (("git", "remote", "-v"), "agent"),
        (("git", "remote"), "agent"),
        (("git", "remote", "get-url", "origin"), "agent"),
        (("git", "submodule", "status"), "agent"),
        (("ls", "docker/"), "agent"),
        (("cat", "docker/Dockerfile"), "agent"),
        (("docker", "compose", "up", "-d"), "dind"),
        (("docker", "compose", "-f", "compose.yml", "ps"), "dind"),
        (("docker", "compose", "exec", "db", "psql", "-c", "select 1"), "service"),
        (("cat", "README.env.md"), "agent"),
        (("cat", "my.env"), "agent"),
        (("cat", "docs/ssh.md"), "agent"),
        (("cat", "id_rsa.pub"), "agent"),
        (("ls", "credentials"), "agent"),
        (("cat", "src/claude.py"), "agent"),
        (("cat", "sudoers.md"), "agent"),
        (("pseudo",), "agent"),
        (("bash", "scripts/check.sh"), "agent"),
        (("sh", "./configure", "--prefix=/x"), "agent"),
        (("python", "-c", "print(1)"), "agent"),
        (("uv", "sync"), "agent"),
    ],
)
def test_near_misses_pass(argv, kind) -> None:
    assert _problems(argv, kind) == []


def test_every_broken_rule_is_named() -> None:
    problems = _problems(("sudo", "git", "push"))
    assert [p.split(":", 1)[0] for p in problems] == [
        "denylist rule remote-git", "denylist rule privilege",
    ]


def test_the_denylist_is_a_fixed_list_in_code() -> None:
    assert isinstance(command_targets.DENYLIST, tuple)
    assert {rule.name for rule in command_targets.DENYLIST} == {
        "remote-git", "docker-socket", "credential-path", "privilege",
        "shell-command", "cwd-outside-worktree",
    }
    assert "ADR" in command_targets.DENYLIST.__doc__ or "ADR" in command_targets.__doc__


# --- the filing check -----------------------------------------------------


def _never_read():
    raise AssertionError("the services were read for no service request")


def test_check_moves_a_refused_request_and_resolves_the_rest() -> None:
    good, pushes = _request(), _request(argv=("git", "push"))
    earlier = {"request": _request(cwd="/abs"), "reason": "cwd '/abs' is absolute"}
    accepted, refused = command_targets.check([good, pushes], [earlier], ONE, POOL, WORKTREE, _never_read)
    assert accepted == [{**good, "resolved": {"kind": "agent", "container": "agent-cuenta1", "cwd": WORKTREE}}]
    assert refused[0] == earlier
    assert refused[1]["request"] == pushes
    assert "denylist rule remote-git" in refused[1]["reason"]


def test_check_reads_the_services_once_and_only_for_a_service() -> None:
    reads = []

    def read():
        reads.append(1)
        return frozenset({"db"})

    filed = [_request(kind="service", name="db", argv=("psql",)), _request(kind="service", name="db", argv=("pg_dump",))]
    accepted, _ = command_targets.check(filed, [], ONE, POOL, WORKTREE, read)
    assert len(accepted) == 2
    assert reads == [1]


def test_check_with_no_account_refuses_every_request() -> None:
    accepted, refused = command_targets.check([_request()], [], None, POOL, WORKTREE, _never_read)
    assert accepted == []
    assert "no known account" in refused[0]["reason"]


# --- the filing path ------------------------------------------------------


def _run(tmp_path, monkeypatch, requests, accounts=(ONE,), services=frozenset({"db"})):
    cfg = _make_config(tmp_path, accounts=list(accounts))
    calls = []
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase",
        _recording_dispatch_phase(calls, handoffs={"implementador": _implementador_return(requests)}),
    )
    monkeypatch.setattr(command_targets, "running_services", lambda dind, project_dir: services)
    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)
    envelope = context_transfer.read_handoff_envelope(cfg.hive_tasks_dir, "task-1", "implementador")
    return cfg, outcome, calls, envelope["command_requests"]


def test_a_request_the_denylist_refuses_lands_in_refused_and_does_not_block(tmp_path, monkeypatch) -> None:
    cfg, outcome, calls, recorded = _run(tmp_path, monkeypatch, [_request(argv=("git", "push"))])
    assert outcome != dispatcher_mod.CycleOutcome.BLOCKED
    assert "revisor" in [c["role"] for c in calls]
    assert recorded["accepted"] == []
    assert recorded["refused"][0]["request"] == _request(argv=("git", "push"))
    assert "denylist rule remote-git" in recorded["refused"][0]["reason"]


def test_a_request_resolution_refuses_does_not_block(tmp_path, monkeypatch) -> None:
    _, outcome, _, recorded = _run(
        tmp_path, monkeypatch, [_request(kind="service", name="db")], services=None,
    )
    assert outcome != dispatcher_mod.CycleOutcome.BLOCKED
    assert recorded["refused"][0]["reason"] == "service list unavailable"


def test_an_accepted_request_carries_its_target_and_blocks(tmp_path, monkeypatch) -> None:
    cfg, outcome, calls, recorded = _run(
        tmp_path, monkeypatch,
        [_request(kind="service", name="db", argv=("psql",), cwd="db"), _request(argv=("sudo", "ls"))],
    )
    assert outcome == dispatcher_mod.CycleOutcome.BLOCKED
    assert [c["role"] for c in calls] == ["arquitecto", "implementador"]
    worktree = f"{cfg.projects_root}/myproj/worktrees/task-1/work"
    assert recorded["accepted"] == [{
        **_request(kind="service", name="db", argv=("psql",), cwd="db"),
        "resolved": {"kind": "service", "container": "dind-cuenta1", "service": "db", "cwd": f"{worktree}/db"},
    }]
    assert "denylist rule privilege" in recorded["refused"][0]["reason"]
    task = context_transfer.read_task_file(context_transfer.task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert task.blocked_reason == handoff.COMMAND_REQUEST
