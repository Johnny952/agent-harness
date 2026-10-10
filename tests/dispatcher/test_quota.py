import datetime as dt

import pytest

from dispatcher.quota import (
    PACE_FINAL_PCT,
    PACE_PROBE_MARGIN_PCT,
    PACE_RAMP_PCT,
    PACE_START_PCT,
    exceeds_threshold,
    parse_reset,
    parse_usage_output,
    week_ceiling,
    weekly_ceiling_pct,
)


def _utc(year, month, day, hour, minute=0):
    return dt.datetime(year, month, day, hour, minute, tzinfo=dt.timezone.utc)

USAGE_TEXT = (
    "Current session: 33% used · resets Sep 13, 10:50pm (America/Santiago)\n"
    "Current week (all models): 24% used · resets Sep 18, 11am (America/Santiago)"
)


def test_parse_usage_output() -> None:
    usage = parse_usage_output(USAGE_TEXT)

    assert usage.session_pct == 33
    assert usage.session_reset == "Sep 13, 10:50pm (America/Santiago)"
    assert usage.week_pct == 24
    assert usage.week_reset == "Sep 18, 11am (America/Santiago)"


def test_exceeds_threshold_true_when_either_window_over() -> None:
    usage = parse_usage_output(USAGE_TEXT)

    assert exceeds_threshold(usage, 20) is True
    assert exceeds_threshold(usage, 50) is False


#: What the CLI actually printed for an account with an untouched session, as
#: captured in .data/verify/v3-allowlist-run.log on 2026-09-24. The reset
#: clause is simply absent from the 0% line — it is not empty, it is not
#: "n/a", the template just stops after "used".
FRESH_SESSION_TEXT = (
    "You are currently using your subscription to power your Claude Code usage\n"
    "\n"
    "Current session: 0% used\n"
    "Current week (all models): 35% used \u00b7 resets Sep 25, 2pm (UTC)"
)


def test_parse_usage_output_reads_a_line_with_no_reset_clause() -> None:
    usage = parse_usage_output(FRESH_SESSION_TEXT)

    assert usage.session_pct == 0
    assert usage.session_reset is None
    assert usage.week_pct == 35
    assert usage.week_reset == "Sep 25, 2pm (UTC)"


def test_a_quota_probe_survives_the_account_being_emptiest() -> None:
    """The whole point: an unparseable reset used to lose the percentages too,
    and the dispatcher skipped an account that had 100% of its session left."""
    usage = parse_usage_output(FRESH_SESSION_TEXT)

    assert exceeds_threshold(usage, 80) is False


def test_parse_usage_output_still_needs_both_percentages() -> None:
    """Optional is the reset clause, not the number anything is decided on."""
    with pytest.raises(ValueError):
        parse_usage_output("Current session: 0% used\nCurrent week (all models): unknown")


def test_parse_usage_output_raises_on_unexpected_format() -> None:
    with pytest.raises(ValueError):
        parse_usage_output("something unexpected")


# --- the reset parser (ADR 48) ----------------------------------------------
#
# Every case passes `now`, so none of these depends on the day the suite runs.

#: cuenta1, probed 2026-10-09 21:38 UTC — the recorded line P8 quotes, and the
#: probe that parked it at 84% of a week resetting the next day.
RECORDED_WEEK_86 = "Current week (all models): 86% used · resets Oct 10, 4:59pm (UTC)"
#: cuenta2, probed 2026-10-09 17:37 UTC, just after its own week rolled.
RECORDED_WEEK_0 = "Current week (all models): 0% used · resets Oct 16, 1:59pm (UTC)"


def test_parse_reset_reads_the_recorded_86_percent_clause() -> None:
    usage = parse_usage_output(f"Current session: 7% used\n{RECORDED_WEEK_86}")

    reset = parse_reset(usage.week_reset, now=_utc(2026, 10, 9, 21, 38))

    assert reset == _utc(2026, 10, 10, 16, 59)
    assert reset.tzinfo is not None


def test_parse_reset_reads_the_recorded_fresh_week_clause() -> None:
    """Nearly seven days out, which is the far edge of the window the year is
    chosen inside."""
    usage = parse_usage_output(f"Current session: 0% used\n{RECORDED_WEEK_0}")

    assert parse_reset(usage.week_reset, now=_utc(2026, 10, 9, 17, 37)) == _utc(
        2026, 10, 16, 13, 59
    )


def test_parse_reset_reads_an_hour_only_clause() -> None:
    """`11am` carries no minutes, which is the second format P8 names."""
    assert parse_reset("Sep 18, 11am (UTC)", now=_utc(2026, 9, 14, 9)) == _utc(
        2026, 9, 18, 11
    )


def test_parse_reset_crosses_a_year_end() -> None:
    """The clause has no year, so the probe's own year would read Jan 2 as ten
    months past. The year taken is the one that puts the reset ahead."""
    assert parse_reset("Jan 2, 1:59pm (UTC)", now=_utc(2026, 12, 30, 10)) == _utc(
        2027, 1, 2, 13, 59
    )


def test_parse_reset_does_not_read_a_clause_backwards_over_a_year_start() -> None:
    """The other direction is not symmetric, and deliberately so: a `Dec 31`
    clause probed on Jan 1 names a moment already past, whichever year is
    tried, so it reads as unparseable and the week falls back to the reserve.
    Only the forward year, `now.year + 1`, can ever land inside the window."""
    assert parse_reset("Dec 31, 1:59pm (UTC)", now=_utc(2027, 1, 1, 10)) is None


def test_parse_reset_on_a_missing_clause_is_none() -> None:
    """The CLI omits the clause from a 0% line, so None is the ordinary case
    and not a drift."""
    assert parse_reset(None, now=_utc(2026, 10, 9, 21, 38)) is None


@pytest.mark.parametrize(
    "clause",
    [
        "",
        "later",  # what tests wrote before any of this was parsed
        "3pm",
        "Oct 10, 4:59pm",  # no parenthesised zone
        "Flub 41, 4:59pm (UTC)",
        "Oct 10, 4:59 (UTC)",  # no am/pm
        "Mars/Phobos",
    ],
)
def test_parse_reset_answers_none_on_anything_it_cannot_read(clause) -> None:
    assert parse_reset(clause, now=_utc(2026, 10, 9, 21, 38)) is None


def test_parse_reset_answers_none_on_a_zone_it_cannot_resolve() -> None:
    """A well-formed clause in an unknown zone has no moment, and guessing one
    would pace a real week against a fiction."""
    assert parse_reset("Oct 10, 4:59pm (Mars/Phobos)", now=_utc(2026, 10, 9, 21, 38)) is None


def test_parse_reset_answers_none_on_a_reset_already_past() -> None:
    """Strictly safe: the fallback is `reserve_pct`, so a stale clause delays
    work rather than releasing a week the old code would have parked."""
    assert parse_reset("Oct 10, 4:59pm (UTC)", now=_utc(2026, 10, 11, 9)) is None


# --- the ramp ----------------------------------------------------------------


@pytest.mark.parametrize(
    "days_left,expected",
    [
        (7, 10),
        (6, 10),
        (5, 24.1667),
        (4, 38.3333),
        (3, 52.5),
        (2, 66.6667),
        (1.5, 73.75),
        (1.0001, 80.8319),
        (1, 95),
        (0.5, 95),
    ],
)
def test_the_weekly_ceiling_follows_the_ramp(days_left, expected) -> None:
    """P8's own list. Its 24/38/53/67 for five to two days left are these
    numbers rounded, and its "just over one day left gives about 81%" is the
    1.0001 row — which also pins the step up to 95 at exactly one day."""
    assert weekly_ceiling_pct(days_left) == pytest.approx(expected, abs=1e-4)


def test_the_ramp_constants_agree_with_each_other() -> None:
    """The ramp is named in five constants rather than one expression, so the
    one relation between them is worth a test."""
    assert PACE_RAMP_PCT == PACE_FINAL_PCT - PACE_START_PCT


# --- week_ceiling ------------------------------------------------------------


def test_week_ceiling_takes_the_probe_margin_off_the_ramp() -> None:
    ceiling = week_ceiling("Oct 11, 4:59pm (UTC)", now=_utc(2026, 10, 9, 16, 59), reserve_pct=60)

    assert ceiling.days_left == pytest.approx(2.0)
    assert ceiling.pct == pytest.approx(66.6667 - PACE_PROBE_MARGIN_PCT, abs=1e-4)
    assert ceiling.reset == _utc(2026, 10, 11, 16, 59)
    assert ceiling.fallback_reason is None


def test_week_ceiling_falls_back_to_the_reserve_with_no_reset_clause() -> None:
    ceiling = week_ceiling(None, now=_utc(2026, 10, 9, 21, 38), reserve_pct=60)

    assert ceiling.pct == 60
    assert ceiling.days_left is None
    assert ceiling.reset is None
    assert "no reset clause" in ceiling.fallback_reason


def test_week_ceiling_falls_back_and_quotes_a_clause_it_could_not_read() -> None:
    """The reason carries the clause, because the next question an operator
    asks is what the CLI actually printed."""
    ceiling = week_ceiling("later", now=_utc(2026, 10, 9, 21, 38), reserve_pct=60)

    assert ceiling.pct == 60
    assert ceiling.days_left is None
    assert "'later'" in ceiling.fallback_reason and "did not parse" in ceiling.fallback_reason


def test_week_ceiling_paces_the_recorded_week_that_was_parked_unspent() -> None:
    """The measurement P8 was written from: cuenta1 at 86% of a week resetting
    in under a day was parked on the 60% reserve, and under ADR 48 the same
    probe is inside the ceiling."""
    usage = parse_usage_output(f"Current session: 7% used\n{RECORDED_WEEK_86}")

    ceiling = week_ceiling(usage.week_reset, now=_utc(2026, 10, 9, 21, 38), reserve_pct=60)

    assert ceiling.pct == pytest.approx(90.0)
    assert usage.week_pct < ceiling.pct
    assert exceeds_threshold(usage, 60) is True
