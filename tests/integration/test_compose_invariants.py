"""Static invariants the two compose files must hold (final-review fix round).

These catch a class of regression unit tests can't: a Critical fix silently
undone by a later hand-edit to docker-compose.yml or
docker-compose.agents.yml (e.g. a dind service losing `runtime: sysbox-runc`,
or an agent service gaining `privileged: true`). Parses both files with
yaml.safe_load and asserts on the resulting structure rather than grepping
text, so formatting changes don't break the test.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

COMPOSE_DIR = Path(__file__).parent.parent.parent / "docker" / "compose"
DOCKERFILE_PATH = Path(__file__).parent.parent.parent / "docker" / "agent" / "Dockerfile"


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
