"""Every plan in docs/plans/ opens with the frontmatter of
`docs/plans/roadmap-view.md` (*What it needs underneath*, 1): exactly
`status`, `approved`, `built` and `supersedes`, between `---` lines before
the title. The prose *Status* line stays; this block is what a reader parses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# The reader is shared with `/api/roadmap` (T-072), so the api reads a plan by
# the rules this test holds every plan to.
from observability.api.plans import check

REPO_ROOT = Path(__file__).parent.parent
PLANS_DIR = REPO_ROOT / "docs" / "plans"

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
