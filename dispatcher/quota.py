from __future__ import annotations

import dataclasses
import re

# The `/usage` command returns free text, not a documented structured API
# (design spec sec. 4a) — parse defensively and fail loudly on drift rather
# than silently misreading a changed template.
_SESSION_RE = re.compile(r"Current session:\s*(\d+)%\s*used\s*·\s*resets\s*(.+)")
_WEEK_RE = re.compile(r"Current week[^:]*:\s*(\d+)%\s*used\s*·\s*resets\s*(.+)")


@dataclasses.dataclass
class UsageInfo:
    session_pct: int
    session_reset: str
    week_pct: int
    week_reset: str


def parse_usage_output(text: str) -> UsageInfo:
    session_match = _SESSION_RE.search(text)
    week_match = _WEEK_RE.search(text)
    if not session_match or not week_match:
        raise ValueError(f"Unexpected /usage output format: {text!r}")
    return UsageInfo(
        session_pct=int(session_match.group(1)),
        session_reset=session_match.group(2).strip(),
        week_pct=int(week_match.group(1)),
        week_reset=week_match.group(2).strip(),
    )


def exceeds_threshold(usage: UsageInfo, threshold_pct: int) -> bool:
    return usage.session_pct >= threshold_pct or usage.week_pct >= threshold_pct
