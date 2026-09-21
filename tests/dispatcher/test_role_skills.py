"""The role -> skills table has three copies: skills/README.md (for a human),
ROLE_SKILLS (for the dispatcher) and the skills on disk (for the CLI). These
tests exist so the three cannot drift apart silently — a skill delivered but
not vendored is a `Path not found` warning the CLI swallows, and a skill
vendored but never delivered is dead weight nobody notices.
"""
import re
from pathlib import Path

import pytest

from dispatcher.role_skills import (
    ROLE_SKILLS,
    SKILLS_ROOT,
    plugin_dirs,
    skills_for,
    system_prompt,
)

REPO_ROOT = Path(__file__).parent.parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
DOCKERFILE = REPO_ROOT / "docker" / "agent" / "Dockerfile"


def _readme_table() -> dict[str, tuple[str, ...]]:
    """Parse the `## What goes to which role` table out of skills/README.md.

    Returns the same shape as ROLE_SKILLS: role -> skills in row order, which
    is the order the module documents itself as preserving.
    """
    text = (SKILLS_DIR / "README.md").read_text(encoding="utf-8")
    section = text.split("## What goes to which role", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("|")]
    header = [cell.strip() for cell in rows[0].strip("|").split("|")]
    roles = header[1:]
    table: dict[str, list[str]] = {role: [] for role in roles}
    for row in rows[2:]:  # rows[1] is the |---|:-:| alignment line
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        skill = cells[0].strip("`")
        for role, mark in zip(roles, cells[1:]):
            if mark:
                table[role].append(skill)
    return {role: tuple(skills) for role, skills in table.items()}


def test_role_skills_matches_the_readme_table() -> None:
    """The README is where the choice is argued; this map is where it is
    executed. If they disagree, the prose is a lie about what runs."""
    assert ROLE_SKILLS == _readme_table()


@pytest.mark.parametrize("skill", sorted({s for skills in ROLE_SKILLS.values() for s in skills}))
def test_every_delivered_skill_is_vendored_in_the_expected_layout(skill: str) -> None:
    """`--plugin-dir` points at the plugin; the CLI needs the manifest and the
    doubled `<name>/skills/<name>/SKILL.md` inside it. A directory missing
    either loads as nothing, and the CLI does not fail the phase over it."""
    plugin = SKILLS_DIR / skill
    assert (plugin / ".claude-plugin" / "plugin.json").is_file()
    assert (plugin / "skills" / skill / "SKILL.md").is_file()


def test_no_vendored_skill_is_left_undelivered() -> None:
    """The other direction: a skill nobody receives is cost with no reader.
    If one is vendored on purpose ahead of its role, add the row first."""
    vendored = {p.name for p in SKILLS_DIR.iterdir() if (p / ".claude-plugin").is_dir()}
    delivered = {skill for skills in ROLE_SKILLS.values() for skill in skills}
    assert vendored == delivered


def test_skills_root_is_where_the_dockerfile_copies_them() -> None:
    """SKILLS_ROOT is not configurable: the tree exists only where the image
    put it. This is the assertion the Dockerfile comment promises."""
    copies = re.findall(r"^COPY\s+skills\s+(\S+)\s*$", DOCKERFILE.read_text(encoding="utf-8"), re.M)
    assert copies == [SKILLS_ROOT]


def test_plugin_dirs_are_one_absolute_path_per_skill_in_table_order() -> None:
    assert plugin_dirs("revisor") == [
        f"{SKILLS_ROOT}/systematic-debugging",
        f"{SKILLS_ROOT}/verification-before-completion",
        f"{SKILLS_ROOT}/blocking-review",
    ]


def test_an_unknown_role_gets_no_skills_and_no_system_prompt() -> None:
    """dispatch_phase computes the set from whatever role it was handed, so a
    role outside the four phases must degrade to the old behaviour rather than
    raise — and must not be charged for a prompt naming an empty set."""
    assert skills_for("becario") == ()
    assert plugin_dirs("becario") == []
    assert system_prompt("becario") is None


@pytest.mark.parametrize("role", sorted(ROLE_SKILLS))
def test_system_prompt_names_the_role_and_every_skill_it_got(role: str) -> None:
    """The addendum's whole job is telling the model which of the delivered
    skills belong to this phase; a name missing from it is a skill the model
    has no reason to look for."""
    prompt = system_prompt(role)
    assert prompt is not None
    assert role in prompt
    for skill in ROLE_SKILLS[role]:
        assert skill in prompt


@pytest.mark.parametrize("role", sorted(ROLE_SKILLS))
def test_system_prompt_says_the_project_wins(role: str) -> None:
    """Always-on precedence: these are method skills vendored from upstream and
    know nothing about the repo the role was dispatched against."""
    assert "project wins" in system_prompt(role)
