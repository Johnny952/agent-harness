"""scripts/configure.sh, driven the way an operator drives it: answers on stdin,
every output redirected into tmp_path with --config-out/--env-out/--agents-out
so nothing in the checkout is touched.

The tokens the script writes are real random values, so they are asserted by
shape alone and compared with a bare `ok = a == b; assert ok`: a failing
`assert a == b` would print both sides, and that is a token in the test log.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.integration import test_compose_invariants as invariants

REPO_ROOT = Path(__file__).parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "configure.sh"
SHIPPED_AGENTS = REPO_ROOT / "docker" / "compose" / "docker-compose.agents.yml"
SHIPPED_BASE = REPO_ROOT / "docker" / "compose" / "docker-compose.yml"
SHIPPED_COOLIFY = REPO_ROOT / "docker" / "compose" / "docker-compose.coolify.yml"

TOKEN = re.compile(r"^[0-9a-f]{64}$")


def _answers(accounts: str, *, overwrite_agents: str | None = None) -> str:
    """stdin for one run: the account list, the three tuning prompts left at
    their defaults, an answer to the agents file's overwrite prompt when one
    is asked, then credentials (yes, admin, the password twice)."""
    lines = [accounts, "", "", ""]
    if overwrite_agents is not None:
        lines.append(overwrite_agents)
    lines += ["y", "admin", "pw", "pw"]
    return "\n".join(lines) + "\n"


def _run(tmp_path: Path, stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "--config-out",
            str(tmp_path / "config.yaml"),
            "--env-out",
            str(tmp_path / ".env"),
            "--agents-out",
            str(tmp_path / "docker-compose.agents.yml"),
            "--coolify-out",
            str(tmp_path / "docker-compose.coolify.yml"),
        ],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _render(*names: str) -> str:
    """The agents file for a pool, from the script's own function: sourced, the
    script stops after its definitions."""
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; shift; render_agents_compose "$@"', "_", str(SCRIPT), *names],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return result.stdout


def _render_coolify(*names: str) -> str:
    """The Coolify file for a pool, rendered from the shipped one the way the
    wizard renders it."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; shift; render_coolify_compose "$@"',
            "_",
            str(SCRIPT),
            str(SHIPPED_COOLIFY),
            *names,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return result.stdout


def _env(tmp_path: Path) -> dict[str, str]:
    pairs = (
        line.partition("=")
        for line in (tmp_path / ".env").read_text().splitlines()
        if line and not line.startswith("#")
    )
    return {key: value for key, _, value in pairs}


def _accounts(tmp_path: Path) -> list[str]:
    config = yaml.safe_load((tmp_path / "config.yaml").read_text())
    return [account["name"] for account in config["accounts"]]


def _assert_tokens(env: dict[str, str], names: list[str]) -> None:
    """One 64-hex write token per account, and the collector's map binding
    agent-<name> to exactly that value, every entry spelt out literally."""
    keys = {f"COLLECTOR_WRITE_TOKEN_{name.upper().replace('-', '_')}" for name in names}
    assert {key for key in env if key.startswith("COLLECTOR_WRITE_TOKEN_")} == keys
    for key in keys | {"API_TOKEN", "COLLECTOR_READ_TOKEN"}:
        ok = bool(TOKEN.fullmatch(env[key]))
        assert ok, f"{key} is not 64 hex characters"

    raw = env["COLLECTOR_WRITE_TOKENS"]
    entries = [entry.partition("=") for entry in raw.split(",")]
    assert [source_app for source_app, _, _ in entries] == [f"agent-{name}" for name in names]
    for name, (source_app, _, token) in zip(names, entries):
        ok = token == env[f"COLLECTOR_WRITE_TOKEN_{name.upper().replace('-', '_')}"]
        assert ok, f"COLLECTOR_WRITE_TOKENS binds {source_app} to another value than its own key"
    assert "${" not in raw, "the map must spell its tokens out, not as substitutions"
    # Every token distinct: a shared one is an agent able to post as another.
    tokens = [env[key] for key in keys | {"API_TOKEN", "COLLECTOR_READ_TOKEN"}]
    assert len(set(tokens)) == len(tokens)


# --- the rendered agents file ------------------------------------------------


def test_the_default_pool_renders_the_shipped_agents_file() -> None:
    """The renderer is the shipped file with the names as parameters, so a
    change to one that is not made to the other fails here first."""
    ok = yaml.safe_load(_render("cuenta1", "cuenta2")) == yaml.safe_load(SHIPPED_AGENTS.read_text())
    assert ok, "render_agents_compose cuenta1 cuenta2 no longer parses to docker-compose.agents.yml"


def _compose_dir_for(tmp_path: Path, names: list[str]) -> Path:
    """A compose directory for a rendered pool: the agents file rendered, and
    docker-compose.yml with the collector's fallback map rewritten for that
    pool -- the substitution form of what configure.sh writes into .env as
    COLLECTOR_WRITE_TOKENS -- so the deployment-wide token checks see the
    pool the agents file holds."""
    compose_dir = tmp_path / "compose"
    compose_dir.mkdir()
    (compose_dir / "docker-compose.agents.yml").write_text(_render(*names))
    fallback = ",".join(
        f"agent-{name}=${{COLLECTOR_WRITE_TOKEN_{name.upper().replace('-', '_')}}}" for name in names
    )
    base, count = re.subn(
        r"\$\{COLLECTOR_WRITE_TOKENS:-.*\}$",
        lambda _: f"${{COLLECTOR_WRITE_TOKENS:-{fallback}}}",
        SHIPPED_BASE.read_text(),
        flags=re.MULTILINE,
    )
    assert count == 1, "docker-compose.yml's COLLECTOR_WRITE_TOKENS line changed shape"
    (compose_dir / "docker-compose.yml").write_text(base)
    (compose_dir / "docker-compose.coolify.yml").write_text(_render_coolify(*names))
    return compose_dir


@pytest.mark.parametrize("names", [["solo"], ["a", "b-c", "d_e"]], ids=["one", "three"])
def test_a_rendered_pool_holds_the_compose_invariants(tmp_path, monkeypatch, names) -> None:
    """The checks tests/integration/test_compose_invariants.py holds the shipped
    files to, run on a rendered pool by pointing that module at a directory
    holding it, rather than copying them here to drift."""
    monkeypatch.setattr(invariants, "COMPOSE_DIR", _compose_dir_for(tmp_path, names))

    invariants.test_both_compose_files_share_the_same_external_network_name()
    invariants.test_every_agent_service_mounts_the_shared_hive_directory()
    invariants.test_every_dind_sidecar_uses_sysbox_runc_and_nothing_is_privileged()
    invariants.test_every_agent_service_isolates_its_config_home("docker-compose.agents.yml")
    invariants.test_every_agent_service_has_collector_url_and_source_app()
    invariants.test_every_agent_mounts_the_deploy_keys_read_only()
    for check in (
        invariants.test_no_agent_service_gets_the_read_token,
        invariants.test_only_the_collector_gets_the_read_token,
        invariants.test_each_agent_sends_the_write_token_bound_to_its_source_app,
        invariants.test_a_write_token_reaches_only_its_agent_and_the_collector,
        invariants.test_the_collector_tokens_are_spelt_bare_with_no_default,
    ):
        check("split")
        check("coolify")
    invariants.test_every_agent_service_isolates_its_config_home("docker-compose.coolify.yml")
    invariants.test_dispatcher_is_gated_behind_its_own_profile("docker-compose.coolify.yml")


@pytest.mark.parametrize("names", [["solo"], ["a", "b-c", "d_e"]], ids=["one", "three"])
def test_a_rendered_coolify_pool_holds_the_agents_file_invariants(
    tmp_path, monkeypatch, names
) -> None:
    """The checks the invariants module holds only docker-compose.agents.yml to
    -- the hive mount, sysbox sidecars, collector env -- run on the Coolify
    render's agents, by handing it to them under that name. Not the read-only
    deploy keys: the shipped Coolify file mounts no /run/secrets/projects at
    all, a gap of its own and not the renderer's."""
    compose_dir = tmp_path / "compose"
    compose_dir.mkdir()
    (compose_dir / "docker-compose.agents.yml").write_text(_render_coolify(*names))
    shutil.copy(SHIPPED_BASE, compose_dir)
    monkeypatch.setattr(invariants, "COMPOSE_DIR", compose_dir)

    invariants.test_every_agent_service_mounts_the_shared_hive_directory()
    invariants.test_every_dind_sidecar_uses_sysbox_runc_and_nothing_is_privileged()
    invariants.test_every_agent_service_has_collector_url_and_source_app()


def test_the_default_pool_renders_the_shipped_coolify_file() -> None:
    """Byte for byte: the render copies the shipped file's head, comments
    included, so the only freedom it has is in the agent pairs."""
    ok = _render_coolify("cuenta1", "cuenta2") == SHIPPED_COOLIFY.read_text()
    assert ok, "render_coolify_compose cuenta1 cuenta2 no longer reproduces docker-compose.coolify.yml"


def test_a_rendered_coolify_pool_has_its_pairs_volumes_and_token_map() -> None:
    compose = yaml.safe_load(_render_coolify("a", "b-c", "d_e"))
    shipped = yaml.safe_load(SHIPPED_COOLIFY.read_text())

    pool = ["agent-a", "dind-a", "agent-b-c", "dind-b-c", "agent-d_e", "dind-d_e"]
    head = [name for name in shipped["services"] if not name.startswith(("agent-", "dind-"))]
    assert list(compose["services"]) == head + pool
    for name in head:
        if name != "collector":
            assert compose["services"][name] == shipped["services"][name], name
    token_map = (
        "COLLECTOR_WRITE_TOKENS=agent-a=${COLLECTOR_WRITE_TOKEN_A},"
        "agent-b-c=${COLLECTOR_WRITE_TOKEN_B_C},agent-d_e=${COLLECTOR_WRITE_TOKEN_D_E}"
    )
    assert token_map in compose["services"]["collector"]["environment"]
    assert set(compose["volumes"]) == {
        "observability_data",
        "registry_mirror_data",
        "claude_shared",
        "claude_creds_a",
        "claude_creds_b-c",
        "claude_creds_d_e",
        "dind_a_data",
        "dind_b-c_data",
        "dind_d_e_data",
    }
    assert compose["networks"] == shipped["networks"]
    agent = compose["services"]["agent-b-c"]
    assert agent["build"] == shipped["services"]["agent-cuenta1"]["build"]
    assert "COLLECTOR_TOKEN=${COLLECTOR_WRITE_TOKEN_B_C}" in agent["environment"]


def test_a_rendered_pool_has_one_pair_and_its_volumes_per_account() -> None:
    compose = yaml.safe_load(_render("a", "b-c", "d_e"))

    assert list(compose["services"]) == [
        "agent-a", "dind-a", "agent-b-c", "dind-b-c", "agent-d_e", "dind-d_e",
    ]
    assert compose["volumes"] == {
        "claude_shared": {"external": True},
        "claude_creds_a": {"external": True},
        "claude_creds_b-c": {"external": True},
        "claude_creds_d_e": {"external": True},
        "dind_a_data": None,
        "dind_b-c_data": None,
        "dind_d_e_data": None,
    }
    assert compose["networks"] == {"ia_harness_net": {"external": True, "name": "ia_harness_net"}}
    agent = compose["services"]["agent-b-c"]
    assert agent["depends_on"] == ["dind-b-c"]
    assert "DOCKER_HOST=tcp://dind-b-c:2375" in agent["environment"]
    assert "COLLECTOR_TOKEN=${COLLECTOR_WRITE_TOKEN_B_C}" in agent["environment"]


# --- the wizard, end to end --------------------------------------------------


def test_one_account_writes_its_config_agents_file_and_tokens(tmp_path) -> None:
    result = _run(tmp_path, _answers("solo"))

    assert result.returncode == 0, result.stderr
    assert _accounts(tmp_path) == ["solo"]
    ok = (tmp_path / "docker-compose.agents.yml").read_text() == _render("solo")
    assert ok
    ok = (tmp_path / "docker-compose.coolify.yml").read_text() == _render_coolify("solo")
    assert ok
    _assert_tokens(_env(tmp_path), ["solo"])
    assert "scripts/setup_volumes.sh solo\n" in result.stdout
    assert "does not edit that file" not in result.stdout


def test_three_accounts_write_three_pairs_and_three_tokens(tmp_path) -> None:
    result = _run(tmp_path, _answers("a, b-c ,d_e"))

    assert result.returncode == 0, result.stderr
    assert _accounts(tmp_path) == ["a", "b-c", "d_e"]
    services = yaml.safe_load((tmp_path / "docker-compose.agents.yml").read_text())["services"]
    assert sorted(services) == sorted(
        ["agent-a", "dind-a", "agent-b-c", "dind-b-c", "agent-d_e", "dind-d_e"]
    )
    _assert_tokens(_env(tmp_path), ["a", "b-c", "d_e"])
    assert "scripts/setup_volumes.sh a b-c d_e\n" in result.stdout


def test_the_default_pool_leaves_the_agents_file_alone(tmp_path) -> None:
    """cuenta1,cuenta2 is the shipped file already: nothing is written, not even
    over a missing path, and no overwrite prompt is asked."""
    result = _run(tmp_path, _answers("cuenta1,cuenta2"))

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "docker-compose.agents.yml").exists()
    assert not (tmp_path / "docker-compose.coolify.yml").exists()
    assert "left it untouched" in result.stdout
    assert _accounts(tmp_path) == ["cuenta1", "cuenta2"]
    _assert_tokens(_env(tmp_path), ["cuenta1", "cuenta2"])


def test_an_existing_agents_file_is_kept_unless_the_operator_says_overwrite(tmp_path) -> None:
    agents = tmp_path / "docker-compose.agents.yml"
    agents.write_text("# hand-edited\n")

    result = _run(tmp_path, _answers("solo", overwrite_agents="n"))
    assert result.returncode == 0, result.stderr
    assert agents.read_text() == "# hand-edited\n"

    (tmp_path / ".env").unlink()
    (tmp_path / "config.yaml").unlink()
    (tmp_path / "docker-compose.coolify.yml").unlink()
    result = _run(tmp_path, _answers("solo", overwrite_agents="y"))
    assert result.returncode == 0, result.stderr
    ok = agents.read_text() == _render("solo")
    assert ok


def test_names_that_share_a_token_key_are_refused(tmp_path) -> None:
    """`a-b` and `a_b` are two containers but one COLLECTOR_WRITE_TOKEN_A_B, so
    the wizard asks again instead of writing a .env where one overwrites the
    other."""
    result = _run(tmp_path, "a-b,a_b\n" + _answers("solo"))

    assert result.returncode == 0, result.stderr
    assert "'a-b' and 'a_b' would share the .env key COLLECTOR_WRITE_TOKEN_A_B" in result.stderr
    assert _accounts(tmp_path) == ["solo"]
    _assert_tokens(_env(tmp_path), ["solo"])
