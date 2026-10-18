# observability/api/plans.py
"""The reader for a plan's frontmatter, shared by `/api/roadmap` and its test.

Every plan under `docs/plans/` opens with the block
`docs/plans/roadmap-view.md` gives (*What it needs underneath*, 1): exactly
`status`, `approved`, `built` and `supersedes`, between `---` lines before the
title. T-071 wrote the strict check in `tests/test_plan_frontmatter.py`; it
lives here so the api reads a plan by the same rules the test holds every plan
to, and there is one copy of the format, not two. The api image COPYs only
`observability/` and `dispatcher/`, which is why it is under `observability/`
and not under `tests/`.
"""
from __future__ import annotations

import datetime

import yaml

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


def read(text: str, plan_names: set[str]) -> tuple[dict | None, list[str]]:
    """A plan's frontmatter and the problems with it.

    The mapping is returned only when there are no problems: a block that
    breaks one rule is not trusted for the others, so a reader lists such a
    plan with no status rather than with half of one.
    """
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        return None, ["does not start with a '---' frontmatter block"]
    try:
        end = lines.index("---", 1)
    except ValueError:
        return None, ["frontmatter block is not closed by '---'"]
    try:
        data = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        return None, [f"frontmatter does not parse: {exc}"]
    if not isinstance(data, dict):
        return None, ["frontmatter is not a mapping"]
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
    return (None if problems else data), problems


def check(text: str, plan_names: set[str]) -> list[str]:
    """The problems with a plan's frontmatter; empty when it is valid."""
    return read(text, plan_names)[1]


def title(text: str) -> str | None:
    """The plan's first `# ` heading, or None when it has none.

    Read after the frontmatter when there is a closed block, so a YAML comment
    in it is not taken for the title.
    """
    lines = text.split("\n")
    if lines[0] == "---" and "---" in lines[1:]:
        lines = lines[lines.index("---", 1) + 1 :]
    for line in lines:
        if line.startswith("# "):
            return line[2:].strip()
    return None
