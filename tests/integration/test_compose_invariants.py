"""Static invariants the two compose files must hold (final-review fix round).

These catch a class of regression unit tests can't: a Critical fix silently
undone by a later hand-edit to docker-compose.yml or
docker-compose.agents.yml (e.g. a dind service losing `runtime: sysbox-runc`,
or an agent service gaining `privileged: true`). Parses both files with
yaml.safe_load and asserts on the resulting structure rather than grepping
text, so formatting changes don't break the test.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

COMPOSE_DIR = Path(__file__).parent.parent.parent / "docker" / "compose"
DOCKERFILE_PATH = Path(__file__).parent.parent.parent / "docker" / "agent" / "Dockerfile"

# The only api mount that may be read-write, by container path. The collector
# keeps events.db in WAL mode and SQLite creates the `-shm` sidecar beside the
# file even for a read-only open, which a `:ro` mount refuses -- so /api/events
# answered [] plus a warning that read like a missing database on every request
# (docs/debt/T-009-D4.md, confirmed against the running stack 2026-09-26 and
# fixed the same day). `observability/api/app.py` still opens the database with
# `mode=ro` in the URI and never calls `init_db`, so the write permission buys
# the sidecar, not the rows. Named here rather than softening the assertion
# below: every other mount must still be `:ro`, and a second writable mount --
# /state above all, which is root-owned dispatcher state -- is a regression this
# test exists to catch.
API_WRITABLE_MOUNTS = {"/events"}


def _load(name: str) -> dict:
    with open(COMPOSE_DIR / name) as f:
        return yaml.safe_load(f)


def _claude_config_dir() -> str:
    """The per-account config home, read from the Dockerfile's ENV so this
    test and the image can't drift apart. The Dockerfile has two build
    stages (see docker/agent/Dockerfile's `FROM docker:27-cli AS docker-cli`
    and the final `FROM node:20-slim`); only the final stage's ENV is the
    one the built image actually gets, so this searches from the last FROM
    onward, matching test_agent_dockerfile.py's final-stage slice."""
    text = DOCKERFILE_PATH.read_text()
    final_stage = text[text.rfind("\nFROM "):]
    match = re.search(r"^ENV CLAUDE_CONFIG_DIR=(\S+)\s*$", final_stage, re.MULTILINE)
    assert match, "expected an ENV CLAUDE_CONFIG_DIR=... line in docker/agent/Dockerfile's final stage"
    return match.group(1)


def _agent_service_volumes(compose: dict) -> dict[str, list[tuple[str, str]]]:
    """Map each agent-* service name to its (source, target) volume pairs.

    Compose volume entries can be either short syntax (a "source:target"
    string) or long syntax (a mapping with "source"/"target" keys); both
    are covered so a long-syntax entry can't silently escape the checks
    below.
    """
    result: dict[str, list[tuple[str, str]]] = {}
    for name, service in compose["services"].items():
        if not name.startswith("agent-"):
            continue
        pairs = []
        for v in service.get("volumes", []):
            if isinstance(v, str):
                parts = v.split(":")
                if len(parts) >= 2:
                    pairs.append((parts[0], parts[1]))
            elif isinstance(v, dict):
                pairs.append((v.get("source", ""), v.get("target", "")))
        result[name] = pairs
    return result


def test_both_compose_files_share_the_same_external_network_name() -> None:
    base = _load("docker-compose.yml")
    agents = _load("docker-compose.agents.yml")

    base_net = base["networks"]["ia_harness_net"]["name"]
    agents_net = agents["networks"]["ia_harness_net"]["name"]

    assert base_net == agents_net == "ia_harness_net"
    assert agents["networks"]["ia_harness_net"]["external"] is True


def test_every_agent_service_mounts_the_shared_hive_directory() -> None:
    agents = _load("docker-compose.agents.yml")

    agent_services = [
        name for name in agents["services"] if name.startswith("agent-")
    ]
    assert agent_services, "expected at least one agent-* service"

    for name in agent_services:
        volumes = agents["services"][name].get("volumes", [])
        targets = [v.split(":")[1] for v in volumes if isinstance(v, str)]
        assert "/data/.hive" in targets, f"{name} is missing a /data/.hive volume"


def test_every_dind_sidecar_uses_sysbox_runc_and_nothing_is_privileged() -> None:
    base = _load("docker-compose.yml")
    agents = _load("docker-compose.agents.yml")

    dind_services = [
        name for name in agents["services"] if name.startswith("dind-")
    ]
    assert dind_services, "expected at least one dind-* service"

    for name in dind_services:
        assert agents["services"][name].get("runtime") == "sysbox-runc", (
            f"{name} is missing runtime: sysbox-runc"
        )

    for compose in (base, agents):
        for name, service in compose["services"].items():
            assert service.get("privileged") is not True, (
                f"{name} must not run privileged"
            )


@pytest.mark.parametrize(
    "compose_file", ["docker-compose.yml", "docker-compose.coolify.yml"]
)
def test_dispatcher_is_gated_behind_its_own_profile(compose_file: str) -> None:
    """The dispatcher is single-shot (dispatcher.run_task_cycle runs one task
    and exits) and its `command` is a placeholder example, so an unprofiled
    service turns a plain `docker compose up -d` into a real `run-task
    --task-id CHANGE_ME` against a logged-in account. A profiled service is
    skipped unless named explicitly or its profile is enabled, which still
    leaves `docker compose run --rm dispatcher ...` working. Asserted on both
    files that define the service, since only the Coolify one carried the
    profile originally."""
    compose = _load(compose_file)
    dispatcher = compose["services"]["dispatcher"]

    assert dispatcher.get("profiles") == ["dispatcher"], (
        f"dispatcher in {compose_file} must be gated behind profiles: "
        f'["dispatcher"], found {dispatcher.get("profiles")!r} -- without it '
        "`docker compose up` runs its placeholder command as a real task"
    )

    assert dispatcher.get("restart", "no") == "no", (
        f"dispatcher in {compose_file} must not be restarted: it exits after "
        f'one task, so any policy but "no" re-runs that task forever'
    )


@pytest.mark.parametrize(
    "compose_file", ["docker-compose.agents.yml", "docker-compose.coolify.yml"]
)
def test_every_agent_service_isolates_its_config_home(compose_file: str) -> None:
    """Guards the V0.4 fix (per-account CLAUDE_CONFIG_DIR): claude_shared stays
    a shared, read/write allowlist mounted at /root/.claude, each agent's
    login volume mounts at the path the Dockerfile's CLAUDE_CONFIG_DIR names
    (read from the Dockerfile so the two can't drift), no two agents share a
    creds volume, and nothing mounts strictly under /root/.claude/ (a creds
    volume nested there is never used: the CLI writes .credentials.json at
    the config-home root, which is how V0.4 put a login in the shared
    volume)."""
    compose = _load(compose_file)
    config_dir = _claude_config_dir()
    agent_volumes = _agent_service_volumes(compose)
    assert agent_volumes, f"expected at least one agent-* service in {compose_file}"

    creds_volume_by_service: dict[str, str] = {}

    for name, pairs in agent_volumes.items():
        shared_mounts = [source for source, target in pairs if target == "/root/.claude"]
        assert shared_mounts == ["claude_shared"], (
            f"{name} in {compose_file} must mount claude_shared at /root/.claude, "
            f"found {shared_mounts!r}"
        )

        creds_mounts = [
            (source, target) for source, target in pairs if source.startswith("claude_creds_")
        ]
        assert len(creds_mounts) == 1, (
            f"{name} in {compose_file} must mount exactly one claude_creds_* volume, "
            f"found {creds_mounts!r}"
        )
        creds_source, creds_target = creds_mounts[0]
        assert creds_target == config_dir, (
            f"{name} in {compose_file} mounts {creds_source} at {creds_target!r}, "
            f"expected {config_dir!r} (docker/agent/Dockerfile's CLAUDE_CONFIG_DIR)"
        )
        creds_volume_by_service[name] = creds_source

        for _source, target in pairs:
            normalized = target.rstrip("/")
            assert not normalized.startswith("/root/.claude/"), (
                f"{name} in {compose_file} mounts {target!r}, which lies strictly "
                "under /root/.claude/ -- a creds volume nested there is never "
                "used, because the CLI writes .credentials.json at the "
                "config-home root (the V0.4 defect)"
            )

    seen: dict[str, str] = {}
    for name, vol in creds_volume_by_service.items():
        assert vol not in seen, (
            f"{compose_file}: {name} and {seen[vol]} both mount creds volume {vol!r}"
        )
        seen[vol] = name


def test_agent_service_volumes_reads_long_syntax_entries() -> None:
    """A long-syntax volume entry (a mapping with source/target keys) must be
    picked up the same way a short-syntax "source:target" string is -- F6
    flagged this as silently skipped before the fix, which would have let a
    long-syntax creds volume nested under /root/.claude/ escape the checks in
    test_every_agent_service_isolates_its_config_home. A small in-test dict
    fixture is enough; neither real compose file uses long syntax today."""
    compose = {
        "services": {
            "agent-cuenta1": {
                "volumes": [
                    "claude_shared:/root/.claude",
                    {
                        "type": "volume",
                        "source": "claude_creds_cuenta1",
                        "target": "/root/.claude/credentials",
                    },
                ]
            }
        }
    }
    volumes = _agent_service_volumes(compose)
    assert volumes["agent-cuenta1"] == [
        ("claude_shared", "/root/.claude"),
        ("claude_creds_cuenta1", "/root/.claude/credentials"),
    ]


def test_every_agent_service_has_collector_url_and_source_app() -> None:
    agents = _load("docker-compose.agents.yml")

    agent_services = [
        name for name in agents["services"] if name.startswith("agent-")
    ]
    assert agent_services, "expected at least one agent-* service"

    for name in agent_services:
        env = agents["services"][name].get("environment", [])
        env_keys = [e.split("=")[0] for e in env if isinstance(e, str)]
        assert "COLLECTOR_URL" in env_keys, f"{name} is missing COLLECTOR_URL"
        assert "SOURCE_APP" in env_keys, f"{name} is missing SOURCE_APP"


@pytest.mark.parametrize(
    "compose_file", ["docker-compose.yml", "docker-compose.coolify.yml"]
)
def test_the_read_api_gets_no_socket_and_only_the_events_volume_is_writable(
    compose_file: str,
) -> None:
    """The read API (docs/plans/board.md Phase 1) is a web process holding the
    dispatcher's own directories, so two properties are load-bearing: it never
    gets /var/run/docker.sock — `dispatcher/docker_exec.py` travels into the
    image as an import and is never called, and a socket there would be root on
    the host for anyone who reached the page — and every mount is `:ro` except
    the one in API_WRITABLE_MOUNTS, which is what lets it be handed root-owned
    state at all. Asserted statically because both are one hand-edit away from
    being undone, and on both files because the Coolify one is a whole deploy
    path: a service missing from it is a deployment that silently has no
    /api/*, which is how Phase 2 would come to be built against something that
    never starts.

    Named `..._mounts_everything_read_only` until T-009-D4 was fixed; the
    exception is one mount wide and enumerated above, so a new writable mount
    still fails here."""
    api = _load(compose_file)["services"]["api"]

    for volume in api["volumes"]:
        assert isinstance(volume, str), "long-syntax mounts would escape this check"
        source, target, *options = volume.split(":")
        assert "docker.sock" not in source, (
            f"the api service in {compose_file} must not mount the docker socket: "
            "it reads files and calls nothing out of process"
        )
        if target in API_WRITABLE_MOUNTS:
            assert options == [], (
                f"{target} is the WAL-sidecar exception (see API_WRITABLE_MOUNTS) "
                f"and takes no mount options, found {volume!r}"
            )
            continue
        assert options == ["ro"], f"{target} must be mounted :ro, found {volume!r}"
    # Loopback-only like every other published port in these files.
    assert api["ports"] == ["127.0.0.1:8789:8789"]


def _environment(service: dict) -> dict[str, str]:
    """A service's environment as a mapping, from either compose syntax.

    Both files write the list form (`- KEY=value`) throughout; the mapping form
    is read too so a hand-edit that switches one service over cannot slip the
    assertions below.
    """
    env = service.get("environment", [])
    if isinstance(env, dict):
        return {str(key): str(value) for key, value in env.items()}
    pairs = [entry.split("=", 1) for entry in env if isinstance(entry, str)]
    return {pair[0]: (pair[1] if len(pair) == 2 else "") for pair in pairs}


def _substitutions_of(variable: str, services: dict) -> set[str]:
    """Every way one `.env` variable is spelt across a file's services.

    Read off the values and not the keys, because a service may take a variable
    under another name — `FOO=${BAR}` reads `BAR` whatever the service calls it
    inside the container — and it is the substitution that decides whether
    compose warns, not the name it lands under. No service in either file does
    that today; the retired Flask board did, and the next one to need it should
    not have to rewrite this helper. A set, so a file that spells the
    same variable two ways fails on the comparison rather than on whichever
    service happened to be read last.
    """
    found = {
        value
        for service in services.values()
        for value in _environment(service).values()
        if variable in value
    }
    assert found, f"no service reads {variable}"
    return found


@pytest.mark.parametrize(
    "compose_file", ["docker-compose.yml", "docker-compose.coolify.yml"]
)
def test_the_api_takes_the_token_a_service_authenticates_with(compose_file: str) -> None:
    """The api reads `API_TOKEN` from `docker/compose/.env` (docs/decisions.md
    ADR 7), and the holder is the console's server half
    (`front/src/lib/api/forward.ts`), the `console` service in these files
    (docs/decisions.md ADR 97). A holder carrying a token the api was never
    given shows its Error state in every region, which is a deployment that
    looks broken rather than misconfigured, so the two take it spelt the same."""
    services = _load(compose_file)["services"]

    assert "API_TOKEN" in _environment(services["api"])
    assert (
        _environment(services["console"])["API_TOKEN"]
        == _environment(services["api"])["API_TOKEN"]
    )


@pytest.mark.parametrize(
    "compose_file", ["docker-compose.yml", "docker-compose.coolify.yml"]
)
def test_the_console_reads_only_through_the_api(compose_file: str) -> None:
    """The console (docs/decisions.md ADR 15, ADR 97) reads through the api and
    nothing else: no volume, no docker socket, no `COLLECTOR_*` variable, so a
    compromised page holds one bearer and not the collector's read or write
    tokens. Loopback-only publishing, like every service here."""
    text = (COMPOSE_DIR / compose_file).read_text()
    console = _load(compose_file)["services"]["console"]
    api = _load(compose_file)["services"]["api"]

    assert console.get("volumes", []) == []
    assert "docker.sock" not in yaml.safe_dump(console)
    assert "api" in console["depends_on"]
    # console_net only (docs/decisions.md ADR 109): an agent on ia_harness_net
    # must not resolve the console, which forwards with its own bearer.
    assert console["networks"] == ["console_net"]
    assert console["build"] == {
        "context": "../..",
        "dockerfile": "docker/console/Dockerfile",
    }
    assert console["restart"] == "unless-stopped"
    assert console["ports"] == ["127.0.0.1:8790:8790"]
    env = _environment(console)
    assert env["API_BASE_URL"] == "http://api:8789"
    assert env["API_TOKEN"] == "${API_TOKEN}"
    assert env["API_TOKEN"] == _environment(api)["API_TOKEN"]
    assert not any(key.startswith("COLLECTOR_") for key in env)
    assert not any(name.startswith("COLLECTOR_") for name in _variables_read_by(console))
    assert "console:" in text


CADDYFILE = Path(__file__).parent.parent.parent / "docker" / "caddy" / "Caddyfile"
CADDY_PORTS = ["80:80", "443:443", "443:443/udp"]
CADDY_VOLUMES = [
    "../caddy/Caddyfile:/etc/caddy/Caddyfile:ro",
    "caddy_data:/data",
    "caddy_config:/config",
]
OTHER_COMPOSE_FILES = ["docker-compose.agents.yml", "docker-compose.coolify.yml"]


def test_caddy_is_in_the_plain_file_only() -> None:
    """Coolify's own proxy ends TLS there; a second would fight it for 80 and
    443 (docs/decisions.md ADR 100)."""
    assert "caddy" in _load("docker-compose.yml")["services"]
    for name in OTHER_COMPOSE_FILES:
        assert "caddy" not in _load(name).get("services", {})


def test_caddy_is_the_only_service_publishing_beyond_loopback() -> None:
    for name in ["docker-compose.yml", *OTHER_COMPOSE_FILES]:
        for service_name, service in _load(name).get("services", {}).items():
            ports = service.get("ports", [])
            if name == "docker-compose.yml" and service_name == "caddy":
                assert ports == CADDY_PORTS
                continue
            for port in ports:
                assert isinstance(port, str) and port.startswith("127.0.0.1:"), (
                    f"{name}: {service_name} publishes {port!r} beyond loopback"
                )


def test_caddy_gets_no_socket_and_no_state() -> None:
    services = _load("docker-compose.yml")
    caddy = services["services"]["caddy"]

    # Exact list: pins the Caddyfile mount to :ro and allows no other mount.
    assert caddy["volumes"] == CADDY_VOLUMES
    assert "docker.sock" not in yaml.safe_dump(caddy)
    assert re.fullmatch(r"caddy:\d+\.\d+\.\d+", caddy["image"])
    assert "build" not in caddy
    assert _environment(caddy) == {
        "HARNESS_DOMAIN": "${HARNESS_DOMAIN:-http://localhost}",
        "ACME_EMAIL": "${ACME_EMAIL:-}",
        "CONSOLE_AUTH_USER": "${CONSOLE_AUTH_USER:-console}",
        "CONSOLE_AUTH_HASH": "${CONSOLE_AUTH_HASH}",
    }
    assert "console" in caddy["depends_on"]
    assert caddy["networks"] == ["console_net"]  # ADR 109
    assert "caddy_data" in services["volumes"]
    assert "caddy_config" in services["volumes"]


def test_the_caddyfile_takes_its_site_from_harness_domain() -> None:
    text = CADDYFILE.read_text()
    placeholder = re.search(r"^\{\$HARNESS_DOMAIN:([^}]*)\}", text, re.MULTILINE)
    assert placeholder, "the site address must be {$HARNESS_DOMAIN:<fallback>}"
    compose_default = re.fullmatch(
        r"\$\{HARNESS_DOMAIN:-([^}]*)\}",
        _environment(_load("docker-compose.yml")["services"]["caddy"])["HARNESS_DOMAIN"],
    )
    assert compose_default
    assert placeholder.group(1) == compose_default.group(1) == "http://localhost"

    assert "reverse_proxy console:8790" in text
    for exposed in ("8789", "8787", "api:"):
        assert exposed not in text
    assert 'email "{$ACME_EMAIL}"' in text

    # HSTS rides on a named matcher that is HTTPS and not localhost.
    hsts = re.search(r"header (@\w+) Strict-Transport-Security", text)
    assert hsts
    matcher = re.search(re.escape(hsts.group(1)) + r" \{([^}]*)\}", text)
    assert matcher
    assert "protocol https" in matcher.group(1)
    assert "not host localhost" in matcher.group(1)
    assert text.count("Strict-Transport-Security") == 1


def test_the_caddyfile_asks_for_basic_auth_before_the_proxy() -> None:
    """ADR 103: the console has no auth of its own, so Caddy asks on every
    request. The hash has no fallback: with none, the account line is the user
    alone, which `basic_auth` refuses, and Caddy will not load."""
    text = CADDYFILE.read_text()
    assert text.count("basic_auth") == 1
    assert text.count("reverse_proxy") == 1
    directive = re.search(
        r"basic_auth\s*\{\s*\{\$CONSOLE_AUTH_USER:([^}]*)\}\s+\{\$CONSOLE_AUTH_HASH\}\s*\}",
        text,
    )
    assert directive, "basic_auth takes no matcher and the hash has no default"

    compose_default = re.fullmatch(
        r"\$\{CONSOLE_AUTH_USER:-([^}]*)\}",
        _environment(_load("docker-compose.yml")["services"]["caddy"])["CONSOLE_AUTH_USER"],
    )
    assert compose_default
    assert directive.group(1) == compose_default.group(1) == "console"

    site = text.index("{$HARNESS_DOMAIN:")
    assert text.count("{$HARNESS_DOMAIN:") == 1
    assert site < directive.start() < text.index("reverse_proxy")


def test_only_caddy_reads_the_console_credential() -> None:
    for compose_file in ["docker-compose.yml", *OTHER_COMPOSE_FILES]:
        for name, service in _load(compose_file).get("services", {}).items():
            read = _variables_read_by(service)
            holder = compose_file == "docker-compose.yml" and name == "caddy"
            for variable in ("CONSOLE_AUTH_USER", "CONSOLE_AUTH_HASH"):
                assert (variable in read) == holder, f"{compose_file}: {name} {variable}"
            if holder:
                # Bare: compose warns on a missing hash and does not refuse.
                assert read["CONSOLE_AUTH_HASH"] == {""}
                assert "DASHBOARD_PASSWORD_HASH" not in read


def test_configure_writes_the_console_credential_commented() -> None:
    text = (CADDYFILE.parent.parent.parent / "scripts" / "configure.sh").read_text()
    image = _load("docker-compose.yml")["services"]["caddy"]["image"]

    assert "# CONSOLE_AUTH_USER=" in text
    assert "# CONSOLE_AUTH_HASH='" in text
    assert f"{image} caddy hash-password" in text
    # Only commented examples: an uncommented line would write a fake hash.
    assert not re.search(r"echo \"CONSOLE_AUTH_", text)


def test_compose_passes_a_quoted_bcrypt_hash_through_intact(tmp_path: Path) -> None:
    if not shutil.which("docker"):
        pytest.skip("docker is not installed")
    version = subprocess.run(
        ["docker", "compose", "version"], capture_output=True, text=True
    )
    if version.returncode != 0:
        pytest.skip("docker compose is not available")

    bcrypt = "$2a$14$Zkx19XLiW6VYouLHR5NmfOFU0z2GTNmpkT/5qqR7hx4IjWJPDhjvG"
    env = {k: v for k, v in os.environ.items() if not k.startswith("CONSOLE_AUTH_")}
    compose = str(COMPOSE_DIR / "docker-compose.yml")

    def render(env_text: str) -> subprocess.CompletedProcess:
        env_file = tmp_path / "case.env"
        env_file.write_text(env_text)
        return subprocess.run(
            ["docker", "compose", "-f", compose, "--env-file", str(env_file),
             "config", "--format", "json"],
            capture_output=True, text=True, env=env,
        )

    result = render(f"CONSOLE_AUTH_HASH='{bcrypt}'\n")
    assert result.returncode == 0, result.stderr
    caddy_env = json.loads(result.stdout)["services"]["caddy"]["environment"]
    # `config` re-escapes its output so it reads back the same: every `$` is
    # printed as `$$`, and the container gets the bare hash.
    assert caddy_env["CONSOLE_AUTH_HASH"] == bcrypt.replace("$", "$$")
    assert caddy_env["CONSOLE_AUTH_USER"] == "console"

    # No hash: compose does not refuse; Caddy does, at its own start.
    result = render("\n")
    assert result.returncode == 0, result.stderr
    assert {"api", "console"} <= set(json.loads(result.stdout)["services"])


@pytest.mark.parametrize(
    "compose_file", ["docker-compose.yml", "docker-compose.coolify.yml"]
)
@pytest.mark.parametrize("variable", ["CONSOLE_PROJECT"])
def test_an_optional_variable_carries_an_empty_default(
    compose_file: str, variable: str
) -> None:
    """A variable the services are documented to run without is spelt `${VAR:-}`
    and not `${VAR}`, in every file that reads it.

    `${VAR}` with nothing in `.env` makes compose warn on every command, and a
    warning an operator is told to ignore is one they will also ignore on the
    day it means something. The two required credentials below keep the bare
    form for the same reason read the other way.
    """
    services = _load(compose_file)["services"]

    assert _substitutions_of(variable, services) == {f"${{{variable}:-}}"}


@pytest.mark.parametrize(
    "compose_file", ["docker-compose.yml", "docker-compose.coolify.yml"]
)
@pytest.mark.parametrize(
    "variable",
    ["DASHBOARD_USERNAME", "DASHBOARD_PASSWORD_HASH", "API_TOKEN", "CONFIG_WRITER_TOKEN"],
)
def test_a_required_credential_stays_bare_so_compose_says_it_is_missing(
    compose_file: str, variable: str
) -> None:
    """The other half of the rule above: there is no sensible empty default for
    a credential, so the warning is the feature. A service started with an empty
    password hash is a service nobody can log in to, reported as a runtime
    mystery instead of a line compose printed before it started. `API_TOKEN`
    joined them with the `console` service, which refuses to boot without it
    (docs/decisions.md ADR 97)."""
    services = _load(compose_file)["services"]

    assert _substitutions_of(variable, services) == {f"${{{variable}}}"}


@pytest.mark.parametrize(
    "compose_file", ["docker-compose.yml", "docker-compose.coolify.yml"]
)
def test_the_dashboard_service_is_gone_from_both_files(compose_file: str) -> None:
    """T-010 deleted `observability/dashboard/`, so a compose file still naming it
    is a build that fails at `up`. `/events` on the board supersedes its three
    columns."""
    assert "dashboard" not in _load(compose_file)["services"]


@pytest.mark.parametrize(
    "compose_file", ["docker-compose.yml", "docker-compose.coolify.yml"]
)
def test_the_flask_board_service_is_gone_from_both_files(compose_file: str) -> None:
    """Charter C-8 retires `observability/board/` in the task that closes the last
    of the four screens it duplicated — tasks, task detail, debt and tail — and
    those four now come from `front/` against the real api (docs/decisions.md
    ADR 32). A compose file still naming the service is a build that fails at
    `up`, the same failure the dashboard's row above pins."""
    assert "board" not in _load(compose_file)["services"]


@pytest.mark.parametrize(
    "compose_file", ["docker-compose.yml", "docker-compose.coolify.yml"]
)
def test_the_vibe_kanban_service_and_its_volume_are_gone_from_both_files(
    compose_file: str,
) -> None:
    """The board seam is removed (docs/decisions.md ADR 59): no service, no
    `kanban` profile on any service, and no declaration of its volume."""
    compose = _load(compose_file)
    assert "vibe-kanban" not in compose["services"]
    assert "vibe_kanban_data" not in compose["volumes"]
    for name, service in compose["services"].items():
        assert "kanban" not in service.get("profiles", []), name
        assert "vibe-kanban" not in service.get("depends_on", []), name
    assert "kanban" not in (COMPOSE_DIR / compose_file).read_text().lower()


def _short_volumes(service: dict) -> list[str]:
    return [v for v in service.get("volumes", []) if isinstance(v, str)]


@pytest.mark.parametrize(
    "compose_file", ["docker-compose.agents.yml", "docker-compose.coolify.yml"]
)
def test_every_agent_mounts_the_deploy_keys_read_only(compose_file: str) -> None:
    # A phase runs in an agent with the model's hands on the shell, so it may
    # use a project's key to fetch but never make, replace or delete one
    # (docs/plans/projects.md, Decisions 2; charter C-5).
    services = _load(compose_file)["services"]
    agents = [name for name in services if name.startswith("agent-")]
    assert agents
    for name in agents:
        mounts = [v for v in _short_volumes(services[name]) if ":/run/secrets/projects" in v]
        assert mounts == ["../../.data/credentials/projects:/run/secrets/projects:ro"], name


@pytest.mark.parametrize(
    "compose_file", ["docker-compose.yml", "docker-compose.coolify.yml"]
)
def test_the_dispatcher_mounts_the_deploy_keys_writable_at_the_agents_path(
    compose_file: str,
) -> None:
    # `project-keygen` runs in the dispatcher and writes the pair there; the
    # same container path lets one `project_keys_dir` name both sides.
    dispatcher = _load(compose_file)["services"]["dispatcher"]
    mounts = [v for v in _short_volumes(dispatcher) if ":/run/secrets/projects" in v]
    assert mounts == ["../../.data/credentials/projects:/run/secrets/projects"]


# --- the collector's tokens (docs/decisions.md ADR 72) ----------------------
#
# Asserted per *deployment*, not per file: the split path is two files that run
# as one stack, and the read token being absent from docker-compose.agents.yml
# proves nothing if docker-compose.yml handed it to a service defined there.
# Every service of every file in a deployment is read as one mapping.

DEPLOYMENTS = {
    "split": ("docker-compose.yml", "docker-compose.agents.yml"),
    "coolify": ("docker-compose.coolify.yml",),
}

#: `${NAME}`, `${NAME:-x}`, `${NAME:?x}`... as (name, the rest), so a default
#: can be told apart from the bare form. `_substitutions_of` above matches whole
#: values, which the collector's map — two substitutions in one value — is not.
#: Used whole (`fullmatch`) only on values with no nesting; `_substitutions`
#: below is what walks a value whose default holds substitutions of its own.
_SUBSTITUTION = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)([^}]*)\}")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _substitutions(value: str) -> list[tuple[str, str, int, int]]:
    """Every `${NAME...}` in a value as (name, the rest, start, end), nested
    ones included. The collector's `COLLECTOR_WRITE_TOKENS` defaults to the
    per-account map, `${COLLECTOR_WRITE_TOKENS:-agent-a=${A},...}`, so a regex
    that stops at the first `}` would cut the outer one short and miss every
    variable after the first: braces are counted instead, and each default is
    walked in turn."""
    found: list[tuple[str, str, int, int]] = []
    i = 0
    while (start := value.find("${", i)) != -1:
        match = _NAME.match(value, start + 2)
        if not match:
            i = start + 2
            continue
        depth, j = 1, match.end()
        while j < len(value) and depth:
            if value.startswith("${", j):
                depth, j = depth + 1, j + 2
                continue
            depth -= value[j] == "}"
            j += 1
        if depth:
            break
        rest = value[match.end():j - 1]
        found.append((match.group(), rest, start, j))
        found.extend(
            (name, suffix, match.end() + s0, match.end() + e0)
            for name, suffix, s0, e0 in _substitutions(rest)
        )
        i = j
    return found


def _deployment_services(deployment: str) -> dict:
    services: dict = {}
    for name in DEPLOYMENTS[deployment]:
        for service, body in _load(name)["services"].items():
            assert service not in services, f"{service} is defined twice in {deployment}"
            services[service] = body
    return services


def _variables_read_by(service: dict) -> dict[str, set[str]]:
    """Each `.env` variable a service's environment substitutes, with every
    suffix it is spelt with (`""` for the bare form)."""
    found: dict[str, set[str]] = {}
    for value in _environment(service).values():
        for name, suffix, _, _ in _substitutions(value):
            found.setdefault(name, set()).add(suffix)
    return found


def _write_token_variables(services: dict) -> set[str]:
    return {
        name
        for service in services.values()
        for name in _variables_read_by(service)
        if name.startswith("COLLECTOR_WRITE_TOKEN_")
    }


def _write_token_map(collector: dict) -> dict[str, str]:
    """`COLLECTOR_WRITE_TOKENS` as `{source_app: the substitution it holds}`,
    split the way observability/collector/server.py splits it.

    A value that is one `${COLLECTOR_WRITE_TOKENS:-...}` is read through to
    its default: scripts/configure.sh writes the whole map into .env, and the
    default is what a .env without that line resolves to — the per-account
    variables, which is what these tests hold the agents to."""
    raw = _environment(collector)["COLLECTOR_WRITE_TOKENS"]
    outer = _substitutions(raw)
    if outer and outer[0][0] == "COLLECTOR_WRITE_TOKENS" and (outer[0][2], outer[0][3]) == (0, len(raw)):
        raw = outer[0][1].removeprefix(":-")
    entries = [entry.strip().partition("=") for entry in raw.split(",") if entry.strip()]
    return {source_app.strip(): token.strip() for source_app, _, token in entries}


@pytest.mark.parametrize("deployment", sorted(DEPLOYMENTS))
def test_no_agent_service_gets_the_read_token(deployment: str) -> None:
    """The point of splitting read from write: code in an agent container can
    read its own environment, so the token that reads every agent's events
    must not be in any of them. Checked on keys as well as values, so an agent
    that took the variable under another name still fails."""
    services = _deployment_services(deployment)
    agents = [name for name in services if name.startswith("agent-")]
    assert agents

    for name in agents:
        env = _environment(services[name])
        assert "COLLECTOR_READ_TOKEN" not in env, name
        assert not any("COLLECTOR_READ_TOKEN" in value for value in env.values()), name


@pytest.mark.parametrize("deployment", sorted(DEPLOYMENTS))
def test_only_the_collector_gets_the_read_token(deployment: str) -> None:
    services = _deployment_services(deployment)

    readers = {
        name
        for name, service in services.items()
        if "COLLECTOR_READ_TOKEN" in _variables_read_by(service)
    }

    assert readers == {"collector"}
    assert _environment(services["collector"])["COLLECTOR_READ_TOKEN"] == "${COLLECTOR_READ_TOKEN}"


@pytest.mark.parametrize("deployment", sorted(DEPLOYMENTS))
def test_each_agent_sends_the_write_token_bound_to_its_source_app(deployment: str) -> None:
    """Every agent sends `COLLECTOR_TOKEN`, and the collector's map binds the
    variable behind it to that agent's `SOURCE_APP`. A mismatch here is an agent
    whose events land under another agent's name, or are refused outright."""
    services = _deployment_services(deployment)
    bound = _write_token_map(services["collector"])
    agents = [name for name in services if name.startswith("agent-")]
    assert agents

    for name in agents:
        env = _environment(services[name])
        source_app = env.get("SOURCE_APP")
        token = env.get("COLLECTOR_TOKEN", "")
        assert _SUBSTITUTION.fullmatch(token), (
            f"{name} must take COLLECTOR_TOKEN from .env, found {token!r}"
        )
        assert bound.get(source_app) == token, (
            f"the collector binds {source_app} to {bound.get(source_app)!r}"
            f" but {name} sends {token!r}"
        )
    # And nothing in the map that is not an agent of this deployment.
    assert set(bound) == {_environment(services[name]).get("SOURCE_APP") for name in agents}


@pytest.mark.parametrize("deployment", sorted(DEPLOYMENTS))
def test_a_write_token_reaches_only_its_agent_and_the_collector(deployment: str) -> None:
    services = _deployment_services(deployment)
    variables = _write_token_variables(services)
    assert variables

    for variable in variables:
        holders = {
            name for name, service in services.items() if variable in _variables_read_by(service)
        }
        agents = {name for name in holders if name.startswith("agent-")}
        assert len(agents) == 1, f"{variable} reaches {sorted(agents)}"
        assert holders == agents | {"collector"}, f"{variable} reaches {sorted(holders)}"


@pytest.mark.parametrize("deployment", sorted(DEPLOYMENTS))
def test_the_collector_tokens_are_spelt_bare_with_no_default(deployment: str) -> None:
    """Required, like the two DASHBOARD_ credentials above: a default baked in
    here is a token every clone of the repo knows, and an empty one is a
    collector that refuses to start for a reason compose could have printed
    first."""
    services = _deployment_services(deployment)
    names = {"COLLECTOR_READ_TOKEN"} | _write_token_variables(services)

    for service_name, service in services.items():
        for name, suffixes in _variables_read_by(service).items():
            if name in names:
                assert suffixes == {""}, f"{service_name} spells ${{{name}}} with {suffixes}"


# --- the config writer and console_net (docs/decisions.md ADR 75, 109, 110) ----

BOTH_FILES = ["docker-compose.yml", "docker-compose.coolify.yml"]


def _on_console_net(services: dict) -> set[str]:
    return {name for name, service in services.items() if "console_net" in service.get("networks", [])}


@pytest.mark.parametrize("compose_file", BOTH_FILES)
def test_the_writer_has_no_socket_and_config_d_is_its_only_writable_mount(
    compose_file: str,
) -> None:
    """One read-write mount, the directory the overrides live in; config.yaml
    is `:ro`; nothing else: no .hive, state, projects or credentials."""
    writer = _load(compose_file)["services"]["config-writer"]

    assert writer["volumes"] == [
        "../../config.yaml:/app/config.yaml:ro",
        "../../config.d:/app/config.d",
    ]
    assert "docker.sock" not in yaml.safe_dump(writer)
    assert writer["build"] == {"context": "../..", "dockerfile": "config_writer/Dockerfile"}
    assert "profiles" not in writer


@pytest.mark.parametrize("compose_file", BOTH_FILES)
def test_the_writer_publishes_only_loopback_8791(compose_file: str) -> None:
    writer = _load(compose_file)["services"]["config-writer"]

    assert writer["ports"] == ["127.0.0.1:8791:8791"]


@pytest.mark.parametrize("compose_file", BOTH_FILES)
def test_console_net_holds_the_console_side_and_no_agent_network(compose_file: str) -> None:
    """ADR 109: the writer, the console and caddy are on `console_net` and not on
    `ia_harness_net`; the api is on both because it needs the collector."""
    compose = _load(compose_file)
    services = compose["services"]

    expected = {"api", "console", "config-writer"}
    if compose_file == "docker-compose.yml":
        expected.add("caddy")
    assert _on_console_net(services) == expected
    for name in expected - {"api"}:
        assert services[name]["networks"] == ["console_net"], name
    assert sorted(services["api"]["networks"]) == ["console_net", "ia_harness_net"]
    assert "console_net" in compose["networks"]
    assert "name" not in (compose["networks"]["console_net"] or {})
    assert "config_writer_net" not in compose["networks"]


@pytest.mark.parametrize("compose_file", BOTH_FILES)
def test_the_dispatcher_and_the_api_mount_config_d_read_only(compose_file: str) -> None:
    services = _load(compose_file)["services"]

    for name in ("dispatcher", "api"):
        assert "../../config.d:/app/config.d:ro" in services[name]["volumes"], name
    # Only the writer may write it.
    for name, service in services.items():
        for volume in service.get("volumes", []):
            if isinstance(volume, str) and "config.d" in volume and name != "config-writer":
                assert volume.endswith(":ro"), f"{name} mounts {volume!r}"


@pytest.mark.parametrize("deployment", sorted(DEPLOYMENTS))
def test_the_writer_token_reaches_only_the_writer_and_the_console(deployment: str) -> None:
    services = _deployment_services(deployment)

    holders = {
        name
        for name, service in services.items()
        if "CONFIG_WRITER_TOKEN" in _variables_read_by(service)
    }
    assert holders == {"config-writer", "console"}
    for name, service in services.items():
        env = _environment(service)
        assert "CONFIG_WRITER_TOKEN" not in env or name in holders, name


@pytest.mark.parametrize("compose_file", BOTH_FILES)
def test_the_console_is_told_where_the_writer_is(compose_file: str) -> None:
    env = _environment(_load(compose_file)["services"]["console"])

    assert env["CONFIG_WRITER_URL"] == "http://config-writer:8791"
    assert env["CONFIG_WRITER_TOKEN"] == "${CONFIG_WRITER_TOKEN}"


def test_no_agent_or_sidecar_joins_console_net() -> None:
    """The agents file never names it, and neither do the agent and dind
    services of the Coolify file, which is one resource with them all."""
    agents = _load("docker-compose.agents.yml")
    assert "console_net" not in (agents.get("networks") or {})
    assert "console_net" not in yaml.safe_dump(agents)
    for name, service in agents["services"].items():
        assert "console_net" not in service.get("networks", []), name

    coolify = _load("docker-compose.coolify.yml")["services"]
    for name, service in coolify.items():
        if name.startswith(("agent-", "dind-")):
            assert "console_net" not in service.get("networks", []), name
    for name in ("collector", "registry-mirror", "dispatcher"):
        assert "console_net" not in coolify[name]["networks"], name
