import time
from pathlib import Path

from dispatcher.config import AccountConfig
from dispatcher.state_machine import (
    AccountState,
    clear_rate_limit,
    get_busy_since,
    get_current_task,
    get_rate_limited_at,
    get_state,
    list_idle_accounts,
    record_rate_limit,
    set_state,
)


def test_get_state_defaults_to_idle(tmp_path: Path) -> None:
    assert get_state(str(tmp_path), "cuenta1") == AccountState.IDLE


def test_set_then_get_state_roundtrip(tmp_path: Path) -> None:
    set_state(str(tmp_path), "cuenta1", AccountState.BUSY, current_task_id="task-1")

    assert get_state(str(tmp_path), "cuenta1") == AccountState.BUSY
    assert get_current_task(str(tmp_path), "cuenta1") == "task-1"


def test_list_idle_accounts_filters_by_state(tmp_path: Path) -> None:
    accounts = [AccountConfig(name="cuenta1", container="c1"), AccountConfig(name="cuenta2", container="c2")]
    set_state(str(tmp_path), "cuenta1", AccountState.BUSY)

    assert list_idle_accounts(str(tmp_path), accounts) == ["cuenta2"]


def test_no_refusal_is_recorded_for_an_account_that_was_never_refused(tmp_path: Path) -> None:
    set_state(str(tmp_path), "cuenta1", AccountState.BUSY)

    assert get_rate_limited_at(str(tmp_path), "cuenta1") is None


def test_a_refusal_can_be_recorded_before_the_account_has_any_state_file(tmp_path: Path) -> None:
    # check_quota_ok can refuse an account on its very first probe, before
    # anything has written a state file for it.
    record_rate_limit(str(tmp_path), "cuenta1", at=1000.0)

    assert get_rate_limited_at(str(tmp_path), "cuenta1") == 1000.0
    assert get_state(str(tmp_path), "cuenta1") == AccountState.IDLE


def test_a_recorded_refusal_survives_every_later_state_change(tmp_path: Path) -> None:
    # set_state rewrites the whole document, so without an explicit carry the
    # refusal would be erased by the very transition that parks the account —
    # and the recheck would then recover it on the local counters that cannot
    # see the refusal at all.
    record_rate_limit(str(tmp_path), "cuenta1", at=1000.0)
    set_state(str(tmp_path), "cuenta1", AccountState.COOLING_DOWN)
    set_state(str(tmp_path), "cuenta1", AccountState.BUSY, current_task_id="task-1")

    assert get_rate_limited_at(str(tmp_path), "cuenta1") == 1000.0
    assert get_current_task(str(tmp_path), "cuenta1") == "task-1"


def test_clearing_a_refusal_leaves_the_state_alone(tmp_path: Path) -> None:
    record_rate_limit(str(tmp_path), "cuenta1", at=1000.0)
    set_state(str(tmp_path), "cuenta1", AccountState.BUSY, current_task_id="task-1")

    clear_rate_limit(str(tmp_path), "cuenta1")

    assert get_rate_limited_at(str(tmp_path), "cuenta1") is None
    assert get_state(str(tmp_path), "cuenta1") == AccountState.BUSY
    assert get_current_task(str(tmp_path), "cuenta1") == "task-1"


def test_clearing_a_refusal_that_was_never_recorded_is_a_no_op(tmp_path: Path) -> None:
    clear_rate_limit(str(tmp_path), "cuenta1")
    set_state(str(tmp_path), "cuenta1", AccountState.BUSY)
    clear_rate_limit(str(tmp_path), "cuenta1")

    assert get_state(str(tmp_path), "cuenta1") == AccountState.BUSY


def test_list_idle_accounts_ranks_the_primary_last(tmp_path: Path) -> None:
    """The pool is ordered, not partitioned. The primary is listed first in
    config.yaml — it is the first container defined — so a picker that took the
    head of the config order would spend the operator's own console before
    either worker had been asked."""
    accounts = [
        AccountConfig(name="cuenta1", container="c1", is_primary=True),
        AccountConfig(name="cuenta2", container="c2"),
        AccountConfig(name="cuenta3", container="c3"),
    ]

    assert list_idle_accounts(str(tmp_path), accounts) == ["cuenta2", "cuenta3", "cuenta1"]


def test_ranking_keeps_the_config_order_among_the_workers(tmp_path: Path) -> None:
    """A stable sort on one flag, not a reordering: the workers stay in the
    order the operator wrote them, which is the only order anything expresses a
    preference about."""
    accounts = [
        AccountConfig(name="cuenta3", container="c3"),
        AccountConfig(name="cuenta1", container="c1", is_primary=True),
        AccountConfig(name="cuenta2", container="c2"),
    ]

    assert list_idle_accounts(str(tmp_path), accounts) == ["cuenta3", "cuenta2", "cuenta1"]


def test_the_primary_is_still_dropped_when_it_is_not_idle(tmp_path: Path) -> None:
    """Ranking last is not the same as being kept out, and it is also not a
    reprieve: a primary that is BUSY is off the list like any other account."""
    accounts = [
        AccountConfig(name="cuenta1", container="c1", is_primary=True),
        AccountConfig(name="cuenta2", container="c2"),
    ]
    set_state(str(tmp_path), "cuenta1", AccountState.BUSY)

    assert list_idle_accounts(str(tmp_path), accounts) == ["cuenta2"]


def test_busy_since_is_stamped_on_the_way_into_busy(tmp_path: Path) -> None:
    before = time.time()
    set_state(str(tmp_path), "cuenta1", AccountState.BUSY, current_task_id="task-1")

    busy_since = get_busy_since(str(tmp_path), "cuenta1")

    assert busy_since is not None
    assert before <= busy_since <= time.time()


def test_busy_since_is_dropped_on_the_way_out(tmp_path: Path) -> None:
    """The stamp belongs to the transition and not to the account, unlike
    rate_limited_at. Carrying it across would leave the reaper reading the
    clock of a phase that ended hours ago the next time the account went
    BUSY."""
    set_state(str(tmp_path), "cuenta1", AccountState.BUSY, current_task_id="task-1")
    set_state(str(tmp_path), "cuenta1", AccountState.IDLE)

    assert get_busy_since(str(tmp_path), "cuenta1") is None


def test_re_entering_busy_restamps_the_clock(tmp_path: Path) -> None:
    set_state(str(tmp_path), "cuenta1", AccountState.BUSY, current_task_id="task-1")
    first = get_busy_since(str(tmp_path), "cuenta1")
    set_state(str(tmp_path), "cuenta1", AccountState.IDLE)
    set_state(str(tmp_path), "cuenta1", AccountState.BUSY, current_task_id="task-2")

    second = get_busy_since(str(tmp_path), "cuenta1")

    assert first is not None and second is not None
    assert second >= first


def test_no_busy_since_for_an_account_with_no_state_file(tmp_path: Path) -> None:
    assert get_busy_since(str(tmp_path), "cuenta1") is None
