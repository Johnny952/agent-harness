"""Exercises the real docker/agent/entrypoint.sh inside the built agent image.

Guards the V0.4 fix (per-account CLAUDE_CONFIG_DIR): tests/integration/
test_agent_dockerfile.py and test_compose_invariants.py check the Dockerfile
and compose files statically, but only running the actual script inside the
actual image can catch a shell bug in the guards or the symlink loop -- the
class of bug that let the original defect (a login landing in the shared
claude_shared volume) go unnoticed. Requires a built image and a working
docker daemon, so it's skipped by default; see IA_HARNESS_AGENT_IMAGE below.

Each `docker run` is throwaway (--rm, tmpfs-only, no named volumes) and never
touches the real claude_shared / claude_creds_<account> volumes.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from hooks import install_settings

IMAGE = os.environ.get("IA_HARNESS_AGENT_IMAGE")

pytestmark = pytest.mark.skipif(
    not IMAGE or not shutil.which("docker"),
    reason="set IA_HARNESS_AGENT_IMAGE to a built agent image and have docker on PATH",
)

# Mirrors docker/agent/entrypoint.sh's $shared_names.
SHARED_DIRS = (
    "projects todos file-history session-env plans skills agents commands "
    "plugins output-styles"
)
SHARED_NAMES = SHARED_DIRS + " settings.json CLAUDE.md"

ENTRYPOINT = "/usr/local/bin/entrypoint.sh true"


def _run(
    script: str,
    tmpfs: tuple[str, ...] = ("/root/.claude", "/root/.claude-account"),
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    """Run `script` with `sh -c` as the entrypoint of a throwaway container.

    `tmpfs` lists the paths mounted as (empty, writable) tmpfs before the
    image's own filesystem is seen, standing in for the real per-account and
    shared named volumes without touching them.
    """
    cmd = ["docker", "run", "--rm"]
    for path in tmpfs:
        cmd += ["--tmpfs", path]
    cmd += ["--entrypoint", "sh", IMAGE, "-c", script]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def test_happy_path_symlinks_every_shared_name_and_registers_hook() -> None:
    script = (
        f"{ENTRYPOINT}; echo EXIT:$?; "
        f"for name in {SHARED_NAMES}; do "
        'link="/root/.claude-account/$name"; '
        'if [ -L "$link" ]; then echo "LINK:$name:$(readlink "$link")"; '
        'else echo "NOLINK:$name"; fi; '
        "done; "
        "cat /root/.claude/settings.json"
    )
    result = _run(script)

    assert "EXIT:0" in result.stdout, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    for name in SHARED_NAMES.split():
        assert f"LINK:{name}:/root/.claude/{name}" in result.stdout, result.stdout
    assert install_settings.HOOK_COMMAND in result.stdout, result.stdout


def test_running_entrypoint_twice_is_idempotent() -> None:
    script = f"{ENTRYPOINT}; echo FIRST:$?; {ENTRYPOINT}; echo SECOND:$?"
    result = _run(script)

    assert "FIRST:0" in result.stdout, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "SECOND:0" in result.stdout, f"stdout={result.stdout!r} stderr={result.stderr!r}"


def test_empty_claude_config_dir_fails() -> None:
    result = _run(f"CLAUDE_CONFIG_DIR= {ENTRYPOINT}")

    assert result.returncode != 0
    assert "unset or empty" in result.stderr


def test_relative_claude_config_dir_fails() -> None:
    result = _run(f"CLAUDE_CONFIG_DIR=relative/path {ENTRYPOINT}")

    assert result.returncode != 0
    assert "absolute path" in result.stderr


def test_config_dir_under_shared_volume_fails() -> None:
    result = _run(f"CLAUDE_CONFIG_DIR=/root/.claude/acct {ENTRYPOINT}")

    assert result.returncode != 0
    assert "must not be /root/.claude or a path under it" in result.stderr


def test_missing_account_tmpfs_is_not_a_mountpoint_and_fails() -> None:
    result = _run(ENTRYPOINT, tmpfs=("/root/.claude",))

    assert result.returncode != 0
    assert "mount point" in result.stderr


def test_preexisting_real_projects_dir_fails_and_names_path() -> None:
    script = f"mkdir -p /root/.claude-account/projects; {ENTRYPOINT}"
    result = _run(script)

    assert result.returncode != 0
    assert "/root/.claude-account/projects" in result.stderr


def test_settings_json_symlink_to_wrong_target_fails_and_names_path() -> None:
    script = (
        "ln -s /root/.claude/CLAUDE.md /root/.claude-account/settings.json; "
        f"{ENTRYPOINT}"
    )
    result = _run(script)

    assert result.returncode != 0
    assert "/root/.claude-account/settings.json" in result.stderr


def test_securestorage_config_dir_set_fails() -> None:
    result = _run(f"CLAUDE_SECURESTORAGE_CONFIG_DIR=/tmp/elsewhere {ENTRYPOINT}")

    assert result.returncode != 0
    assert "CLAUDE_SECURESTORAGE_CONFIG_DIR" in result.stderr


def test_leftover_empty_credentials_file_warns_but_succeeds() -> None:
    script = f"touch /root/.claude/.credentials.json; {ENTRYPOINT}"
    result = _run(script)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "/root/.claude/.credentials.json" in result.stderr
