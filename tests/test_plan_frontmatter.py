"""Every plan in docs/plans/ opens with the frontmatter of
`docs/plans/roadmap-view.md` (*What it needs underneath*, 1): exactly
`status`, `approved`, `built` and `supersedes`, between `---` lines before
the title. The prose *Status* line stays; this block is what a reader parses.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
PLANS_DIR = REPO_ROOT / "docs" / "plans"

KEYS = {"status", "approved", "built", "supersedes"}
STATUSES = {"idea", "design", "approved", "building", "built", "superseded", "dropped"}


def _is_date(value: object) -> bool:
    if isinstance(value, datetime.datetime):
        return False
    if isinstance(value, datetime.date):
        return True
    if isinstance(value, str):
        try:
            datetime.date.fromisoformat(value)
        except ValueError:
            return False
        return True
    return False


def check(text: str, plan_names: set[str]) -> list[str]:
    """The problems with a plan's frontmatter; empty when it is valid."""
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        return ["does not start with a '---' frontmatter block"]
    try:
        end = lines.index("---", 1)
    except ValueError:
        return ["frontmatter block is not closed by '---'"]
    try:
        data = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        return [f"frontmatter does not parse: {exc}"]
    if not isinstance(data, dict):
        return ["frontmatter is not a mapping"]
    problems = []
    if set(data) != KEYS:
        problems.append(f"keys are {sorted(data)}, not {sorted(KEYS)}")
    status = data.get("status")
    if status not in STATUSES:
        problems.append(f"status {status!r} is not one of {sorted(STATUSES)}")
    for key in ("approved", "built"):
        value = data.get(key)
        if value is not None and not _is_date(value):
            problems.append(f"{key} {value!r} is not an ISO date or null")
    if (data.get("built") is not None) != (status == "built"):
        problems.append("built is set if and only if status is built")
    supersedes = data.get("supersedes")
    if not isinstance(supersedes, list):
        problems.append("supersedes is not a list")
    else:
        for name in supersedes:
            if name not in plan_names:
                problems.append(f"supersedes {name!r} is not a plan file")
    return problems


PLANS = sorted(PLANS_DIR.glob("*.md"))
PLAN_NAMES = {p.name for p in PLANS}


def test_there_are_plans():
    assert PLANS


@pytest.mark.parametrize("plan", PLANS, ids=lambda p: p.name)
def test_plan_frontmatter_is_valid(plan: Path):
    assert check(plan.read_text(encoding="utf-8"), PLAN_NAMES) == []


GOOD = "---\nstatus: building\napproved: 2026-10-11\nbuilt: null\nsupersedes: [board.md]\n---\n\n# Title\n"


def test_good_block_passes():
    assert check(GOOD, {"board.md"}) == []


def test_missing_block_fails():
    assert check("# Title\n\n**Status:** design.\n", {"board.md"})


def test_unclosed_block_fails():
    assert check("---\nstatus: idea\n# Title\n", set())


def test_extra_key_fails():
    text = GOOD.replace("built: null\n", "built: null\nowner: me\n")
    assert check(text, {"board.md"})


def test_missing_key_fails():
    assert check(GOOD.replace("approved: 2026-10-11\n", ""), {"board.md"})


def test_unknown_status_fails():
    assert check(GOOD.replace("status: building", "status: done"), {"board.md"})


def test_bad_date_fails():
    assert check(GOOD.replace("2026-10-11", "11/10/2026"), {"board.md"})


def test_built_date_without_built_status_fails():
    assert check(GOOD.replace("built: null", "built: 2026-10-12"), {"board.md"})


def test_built_status_without_date_fails():
    assert check(GOOD.replace("status: building", "status: built"), {"board.md"})


def test_built_status_with_date_passes():
    text = GOOD.replace("status: building", "status: built").replace(
        "built: null", "built: 2026-10-12"
    )
    assert check(text, {"board.md"}) == []


def test_unknown_superseded_plan_fails():
    assert check(GOOD, {"front.md"})
