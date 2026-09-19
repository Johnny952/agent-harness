"""Guards the pinned Claude Code CLI version in docker/agent/Dockerfile.

The dispatcher parses this CLI's `--output-format json` shape, `/usage`
text, and rate-limit messages, so an unpinned `npm install` that silently
picks up a new version on rebuild can break parsing without anyone noticing.
Reads the Dockerfile as text (path resolved relative to the repo root, the
same way tests/integration/test_compose_invariants.py locates the compose
files) rather than building the image.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCKERFILE_PATH = Path(__file__).parent.parent.parent / "docker" / "agent" / "Dockerfile"

VERSION = r"\d+\.\d+\.\d+"


def _read() -> str:
    return DOCKERFILE_PATH.read_text()


def _assert_claude_code_pinned(text: str) -> None:
    """Every `@anthropic-ai/claude-code` install spec found anywhere in the
    Dockerfile must be pinned to a literal x.y.z version, or to an ARG that
    itself defaults to one and is declared where the final build stage
    (after the last FROM) can see it."""
    joined = text.replace("\\\n", " ")  # join line continuations
    specs = re.findall(r"@anthropic-ai/claude-code(\S*)", joined)
    assert specs, "expected an `npm install -g @anthropic-ai/claude-code` line"

    for spec in specs:
        arg_ref = re.fullmatch(r"@\$\{?(\w+)\}?", spec)
        assert arg_ref or re.fullmatch(rf"@{VERSION}", spec), f"unpinned: {spec!r}"
        if arg_ref:
            arg_name = arg_ref.group(1)
            final_stage = joined[joined.rfind("\nFROM "):]
            assert re.search(
                rf"^ARG {re.escape(arg_name)}={VERSION}\s*$", final_stage, re.MULTILINE
            ), f"ARG {arg_name} has no version default visible in the final build stage"


def test_real_dockerfile_pins_claude_code_version() -> None:
    _assert_claude_code_pinned(_read())


def test_final_stage_sets_claude_config_dir() -> None:
    """CLAUDE_CONFIG_DIR must be image env (a Dockerfile ENV), not something
    the entrypoint exports: `docker exec` sessions (the dispatcher's
    `claude -p`, an operator's `/login`) inherit container env but never
    variables an entrypoint sets at runtime (see docker/agent/entrypoint.sh).
    """
    joined = _read().replace("\\\n", " ")
    final_stage = joined[joined.rfind("\nFROM "):]
    assert re.search(
        r"^ENV CLAUDE_CONFIG_DIR=/root/\.claude-account\s*$", final_stage, re.MULTILINE
    ), "expected `ENV CLAUDE_CONFIG_DIR=/root/.claude-account` in the final build stage"


def test_final_stage_disables_cli_autoupdater() -> None:
    """The pin only holds if the CLI's background auto-updater is off: it
    otherwise runs `npm install -g` inside the running container, replacing
    the pinned version until the next recreate, and an update cut off
    mid-install leaves no `claude` on PATH (found in the 2026-09-19 V0.4
    re-run, see docs/ROADMAP.md). Image env for the same `docker exec` reason
    as CLAUDE_CONFIG_DIR above.
    """
    joined = _read().replace("\\\n", " ")
    final_stage = joined[joined.rfind("\nFROM "):]
    assert re.search(
        r"^ENV DISABLE_AUTOUPDATER=1\s*$", final_stage, re.MULTILINE
    ), "expected `ENV DISABLE_AUTOUPDATER=1` in the final build stage"


def test_final_stage_marks_the_project_tree_safe_for_git() -> None:
    """`/data/projects` is a host-owned bind mount, so every git command in
    the container fails with `fatal: detected dubious ownership` until
    safe.directory covers it (V0.9 reproduced this on `git worktree add`,
    the first thing a role needs). The value must be the literal `*`: the
    image ships git 2.39.5, where safe.directory matches only an exact path
    or `*`, and the tempting `/data/projects/*` needs git >= 2.46.
    """
    joined = _read().replace("\\\n", " ")
    final_stage = joined[joined.rfind("\nFROM "):]
    assert re.search(
        r"git config --system safe\.directory (['\"])\*\1", final_stage
    ), "expected `git config --system safe.directory '*'` in the final build stage"


def test_final_stage_sets_a_fallback_git_identity() -> None:
    """Without one, a commit dies with `Author identity unknown` even after
    safe.directory is fixed. The dispatcher overrides this per commit to
    attribute work to the role and account, so the image value only has to
    exist — but it must not be a routable address, since it ends up in the
    author field of every commit made outside that path.
    """
    joined = _read().replace("\\\n", " ")
    final_stage = joined[joined.rfind("\nFROM "):]

    assert re.search(r"git config --system user\.name ", final_stage), \
        "expected a `git config --system user.name` line in the final build stage"

    email = re.search(r"git config --system user\.email (['\"])(.+?)\1", final_stage)
    assert email, "expected a `git config --system user.email` line in the final build stage"
    assert email.group(2).endswith((".invalid", ".example", ".test", ".localhost")), (
        f"{email.group(2)!r} may resolve to a real mailbox; use a TLD reserved by "
        "RFC 2606/6761 so the fallback identity can never reach anyone"
    )


def _at_latest(text: str) -> str:
    return text.replace(
        "RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}",
        "RUN npm install -g @anthropic-ai/claude-code@latest",
        1,
    )


def _arg_default_latest(text: str) -> str:
    return text.replace(
        "ARG CLAUDE_CODE_VERSION=2.1.273", "ARG CLAUDE_CODE_VERSION=latest", 1
    )


def _dollar_var_with_no_arg_declared(text: str) -> str:
    without_arg = text.replace("ARG CLAUDE_CODE_VERSION=2.1.273\n", "", 1)
    return without_arg.replace(
        "@${CLAUDE_CODE_VERSION}", "@$CLAUDE_CODE_VERSION", 1
    )


def _arg_above_first_from(text: str) -> str:
    # Valid Dockerfile syntax (a global ARG before the first FROM), but not
    # visible in the final stage unless re-declared there.
    without_arg = text.replace("ARG CLAUDE_CODE_VERSION=2.1.273\n", "", 1)
    first_from = without_arg.index("FROM ")
    return (
        without_arg[:first_from]
        + "ARG CLAUDE_CODE_VERSION=2.1.273\n"
        + without_arg[first_from:]
    )


def _extra_unpinned_install(text: str) -> str:
    return text.replace(
        "RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}\n",
        "RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}\n"
        "RUN npm i -g @anthropic-ai/claude-code\n",
        1,
    )


BAD_VARIANTS = {
    "@latest": _at_latest,
    "ARG defaults to latest": _arg_default_latest,
    "$VAR with no ARG declared": _dollar_var_with_no_arg_declared,
    "ARG above first FROM": _arg_above_first_from,
    "extra unpinned npm i": _extra_unpinned_install,
}


@pytest.mark.parametrize("name", sorted(BAD_VARIANTS))
def test_rejects_unpinned_variants(name: str) -> None:
    mutate = BAD_VARIANTS[name]
    mutated = mutate(_read())
    assert mutated != _read(), f"fixture assumption changed: {name!r} mutation was a no-op"

    with pytest.raises(AssertionError):
        _assert_claude_code_pinned(mutated)
