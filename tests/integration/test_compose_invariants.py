"""Static invariants the two compose files must hold (final-review fix round).

These catch a class of regression unit tests can't: a Critical fix silently
undone by a later hand-edit to docker-compose.yml or
docker-compose.agents.yml (e.g. a dind service losing `runtime: sysbox-runc`,
or an agent service gaining `privileged: true`). Parses both files with
yaml.safe_load and asserts on the resulting structure rather than grepping
text, so formatting changes don't break the test.
"""

from __future__ import annotations

from pathlib import Path

import yaml

COMPOSE_DIR = Path(__file__).parent.parent.parent / "docker" / "compose"


def _load(name: str) -> dict:
    with open(COMPOSE_DIR / name) as f:
        return yaml.safe_load(f)


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
