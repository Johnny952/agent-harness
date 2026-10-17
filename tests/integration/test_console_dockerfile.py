"""Guards docker/console/Dockerfile (docs/decisions.md ADR 97).

Read as text, like test_agent_dockerfile.py: the image is not built here. What
is asserted is what a hand-edit could undo without any other test noticing —
the Node target, the frozen install, the non-root runtime user, and a context
that cannot carry node_modules, .lovable/ or a secret into the image.
"""

from __future__ import annotations

from pathlib import Path

CONSOLE_DIR = Path(__file__).parent.parent.parent / "docker" / "console"


def _lines() -> list[str]:
    text = (CONSOLE_DIR / "Dockerfile").read_text()
    return [line.strip() for line in text.splitlines() if not line.strip().startswith("#")]


def test_the_build_installs_frozen_and_selects_the_node_target_before_building() -> None:
    lines = _lines()
    install = lines.index("RUN bun install --frozen-lockfile")
    preset = lines.index("ENV NITRO_PRESET=node-server")
    build = lines.index("RUN bun run build")

    assert install < build
    assert preset < build


def test_the_runtime_stage_runs_the_built_server_entry_as_a_non_root_user() -> None:
    lines = _lines()
    last_from = max(i for i, line in enumerate(lines) if line.startswith("FROM "))
    runtime = lines[last_from:]

    users = [line for line in runtime if line.startswith("USER ")]
    assert users and users[-1].split()[1] not in {"root", "0"}
    cmds = [line for line in runtime if line.startswith("CMD ")]
    assert len(cmds) == 1 and ".output/server/index.mjs" in cmds[0]


def test_front_is_copied_by_explicit_path_never_as_a_whole() -> None:
    for line in _lines():
        assert not line.startswith(("COPY front/ ", "COPY front/. ")), line


def test_nothing_sets_the_variables_that_make_the_lovable_config_drop_the_preset() -> None:
    text = (CONSOLE_DIR / "Dockerfile").read_text()
    code = "\n".join(_lines())

    assert "LOVABLE_SANDBOX" not in code
    assert "DEV_SERVER__PROJECT_PATH" not in code
    assert "NITRO_PRESET" in text


def test_the_ignore_file_beside_the_dockerfile_keeps_dependencies_and_secrets_out() -> None:
    entries = {
        line.strip()
        for line in (CONSOLE_DIR / "Dockerfile.dockerignore").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }

    assert "*" in entries
    assert "front/node_modules" in entries
    assert "front/.lovable" in entries
    assert any(entry.endswith(".env*") for entry in entries)
