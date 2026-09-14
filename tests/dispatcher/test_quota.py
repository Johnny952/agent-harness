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


def test_parse_usage_output_raises_on_unexpected_format() -> None:
    with pytest.raises(ValueError):
        parse_usage_output("something unexpected")
