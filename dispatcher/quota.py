from __future__ import annotations

import dataclasses
import datetime as dt
import re
import zoneinfo

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
    well-formed week line under it).

    Nothing schedules off these timestamps: recovery is still a re-probe, see
    dispatcher._recheck_cooling_accounts, and no wake is ever booked for a
    reset. Since `docs/decisions.md` ADR 48 one of them is nonetheless *read*
    — `week_ceiling` turns `week_reset` into the primary's paced weekly
    ceiling on every probe, and `session_reset` is still read by nothing. A
    missing one is therefore worth strictly less than the percentage beside
    it, and never worth the percentage itself: an unparseable week reset falls
    back to `reserve_pct` rather than costing the reading.
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
    """One number against both windows. Still the whole rule for a worker, and
    for the primary when `pace_primary_week` is off — see `week_ceiling` for
    the paced weekly half ADR 48 added."""
    return usage.session_pct >= threshold_pct or usage.week_pct >= threshold_pct


# `docs/decisions.md` ADR 48's ramp, named rather than spelled into one
# expression. None of these is configuration: the ramp's shape is a decision,
# not a dial, and the margin is a placeholder for a measurement
# (`docs/plans/token-economy.md` P5) rather than an operator's choice. The one
# key the entry adds is the switch, `Config.pace_primary_week`.
PACE_START_PCT = 10
"""The weekly ceiling just after a reset, with the whole week still to run."""
PACE_FINAL_PCT = 95
"""The weekly ceiling with a day or less left; the last 5 points are the operator's."""
PACE_RAMP_PCT = 85
"""`PACE_FINAL_PCT - PACE_START_PCT`, spread linearly over the ramp."""
PACE_RAMP_DAYS = 6
"""Days the ramp runs over: the week's first six, the seventh being the step to 95."""
PACE_PROBE_MARGIN_PCT = 5
"""Held back for the phase that runs after the probe.

The probe runs *between* phases, so a phase can overshoot the ceiling by its
own whole cost (P8 *Failure mode*). Five points is the guess P8 asks for until
P5 records a measured phase cost.
"""

#: The two shapes the week line's reset clause has been observed in, before the
#: parenthesised zone: `Oct 10, 4:59pm` and `Sep 18, 11am`. The year is appended
#: by `parse_reset` rather than defaulted, because strptime's own default of
#: 1900 is not a leap year and would lose a `Feb 29` clause outright.
_RESET_FORMATS = ("%b %d, %I:%M%p", "%b %d, %I%p")

#: `<when> (<zone>)`, non-greedy so a zone with no parentheses of its own wins.
_RESET_RE = re.compile(r"^(?P<when>.+?)\s*\((?P<zone>[^)]+)\)$")

#: A reset is believed only inside the window a week can actually reset in.
#: That is what picks the year for a clause that carries none, and it is why a
#: reset already in the past reads as unparseable: strictly safe, since the
#: fallback is `reserve_pct`, the number the week was held to before ADR 48.
_RESET_WINDOW = dt.timedelta(days=7)


def _resolve_zone(name: str) -> dt.tzinfo | None:
    """The clause's zone as a tzinfo, or None when this machine cannot say.

    UTC is special-cased rather than looked up: every recorded week line this
    harness has seen reads `(UTC)`, and a container image without a tz database
    would otherwise lose the whole ramp to a missing `zoneinfo` key.
    """
    cleaned = name.strip()
    if cleaned.upper() in ("UTC", "GMT", "Z"):
        return dt.timezone.utc
    try:
        return zoneinfo.ZoneInfo(cleaned)
    except (KeyError, ValueError, OSError):
        # ZoneInfoNotFoundError is a KeyError; OSError covers a tz database
        # that is present but unreadable. Per
        # docs/learnings/a-lookup-that-never-raises-catches-valueerror.md this
        # catches ValueError rather than the narrower subclass.
        return None


def parse_reset(clause: str | None, now: dt.datetime) -> dt.datetime | None:
    """A week line's reset clause as an aware datetime, or None.

    The clause is free text and carries no year, so `now` decides it: the year
    taken is the one that puts the reset inside the next seven days. `now` is a
    parameter and not a clock read, so nothing here depends on the day the
    tests run (ADR 48).

    None on everything else — a missing clause (the CLI omits it from a 0%
    line), a shape outside `_RESET_FORMATS`, a clause with no parenthesised
    zone, a zone this machine cannot resolve, and a reset that is already past.
    """
    if not clause:
        return None
    match = _RESET_RE.match(clause.strip())
    if not match:
        return None
    zone = _resolve_zone(match.group("zone"))
    if zone is None:
        return None
    when = match.group("when")
    # now.year first, so the ordinary case costs one strptime; +1 carries a
    # `Jan 2` clause probed in December. There is deliberately no now.year - 1
    # candidate: the window below is forward-only, and a last-year date is
    # always behind `now`, so it could never be chosen.
    for year in (now.year, now.year + 1):
        for fmt in _RESET_FORMATS:
            try:
                naive = dt.datetime.strptime(f"{when} {year}", f"{fmt} %Y")
            except ValueError:
                continue
            reset = naive.replace(tzinfo=zone)
            if dt.timedelta(0) <= reset - now <= _RESET_WINDOW:
                return reset
    return None


def weekly_ceiling_pct(days_left: float) -> float:
    """ADR 48's ramp, before the probe margin: what a week may be spent to.

    10% with the whole week left, rising linearly to 95% over the first six
    days, and 95% flat through the last one. P8's table of 10/24/38/53/67 for
    six to two days left is this function rounded.
    """
    if days_left <= 1:
        return float(PACE_FINAL_PCT)
    if days_left > PACE_RAMP_DAYS:
        return float(PACE_START_PCT)
    return PACE_START_PCT + PACE_RAMP_PCT * (PACE_RAMP_DAYS - days_left) / PACE_RAMP_DAYS


@dataclasses.dataclass(frozen=True)
class WeekCeiling:
    """What a paced week is held to on one probe, and what it was read from.

    Nothing caches one of these: `week_ceiling` recomputes from the clause it
    is handed, every probe, which is what keeps the ceiling from stepping at
    midnight and what lets a parked primary be released by the ceiling rising
    (ADR 48).
    """

    #: The number `week_pct` is compared with. The margin is already off it.
    pct: float
    #: Days from the probe to the reset, or None when no reset was read.
    days_left: float | None
    reset: dt.datetime | None
    #: Why `pct` is `reserve_pct` instead of the ramp, or None when it is the ramp.
    fallback_reason: str | None


def week_ceiling(
    week_reset: str | None, now: dt.datetime, reserve_pct: int,
) -> WeekCeiling:
    """The primary's weekly ceiling for this probe, with its reason if it fell back.

    The fallback is exactly the number the week was held to before ADR 48, so
    it can delay work but can never wave through a week that `reserve_pct`
    would have parked.
    """
    reset = parse_reset(week_reset, now)
    if reset is None:
        reason = (
            "the week line carried no reset clause"
            if not week_reset
            else f"the reset clause {week_reset!r} did not parse"
        )
        return WeekCeiling(
            pct=float(reserve_pct), days_left=None, reset=None, fallback_reason=reason,
        )
    days_left = (reset - now).total_seconds() / 86400
    return WeekCeiling(
        pct=weekly_ceiling_pct(days_left) - PACE_PROBE_MARGIN_PCT,
        days_left=days_left,
        reset=reset,
        fallback_reason=None,
    )
