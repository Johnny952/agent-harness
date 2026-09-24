from __future__ import annotations

import dataclasses
import re

# The `/usage` command returns free text, not a documented structured API
# (design spec sec. 4a) — parse defensively and fail loudly on drift rather
# than silently misreading a changed template. The percentages are what the
# harness actually decides on, so they stay required; the `· resets <when>`
# clause is optional per line, because the CLI omits it from a line reading 0%.
_SESSION_RE = re.compile(r"Current session:\s*(\d+)%\s*used(?:\s*·\s*resets\s*(.+))?")
_WEEK_RE = re.compile(r"Current week[^:]*:\s*(\d+)%\s*used(?:\s*·\s*resets\s*(.+))?")


@dataclasses.dataclass
class UsageInfo:
    """One account's usage, as its own CLI reports it.

    The resets are None as often as the account is fresh: the CLI writes
    `Current session: 0% used` with no reset clause at all, and requiring one
    cost the harness its whole reading exactly when the account was emptiest
    (measured 2026-09-24, where the probe failed on a 0% session with a
    well-formed week line under it). Nothing schedules off these timestamps —
    recovery is a re-probe, see dispatcher._recheck_cooling_accounts — so a
    missing one is worth strictly less than the percentage beside it.
    """

    session_pct: int
    session_reset: str | None
    week_pct: int
    week_reset: str | None


def parse_usage_output(text: str) -> UsageInfo:
    session_match = _SESSION_RE.search(text)
    week_match = _WEEK_RE.search(text)
    if not session_match or not week_match:
        raise ValueError(f"Unexpected /usage output format: {text!r}")
    return UsageInfo(
        session_pct=int(session_match.group(1)),
        session_reset=_reset_clause(session_match),
        week_pct=int(week_match.group(1)),
        week_reset=_reset_clause(week_match),
    )


def _reset_clause(match: re.Match[str]) -> str | None:
    clause = match.group(2)
    return clause.strip() if clause else None


def exceeds_threshold(usage: UsageInfo, threshold_pct: int) -> bool:
    return usage.session_pct >= threshold_pct or usage.week_pct >= threshold_pct
