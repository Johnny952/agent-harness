import pytest

from dispatcher.quota import exceeds_threshold, parse_usage_output

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
