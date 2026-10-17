import json
import threading
import time
from pathlib import Path

import pytest

from dispatcher.config import AccountConfig
from dispatcher.state_machine import (
    AccountState,
    Holder,
    claim,
    clear_rate_limit,
    get_busy_since,
    get_current_task,
    get_holders,
    get_last_probe,
    get_rate_limited_at,
    get_state,
    list_idle_accounts,
    park,
    record_probe,
    record_rate_limit,
    release,
    set_state,
    unpark,
)

PARKED = [AccountState.PRE_COOLDOWN, AccountState.COOLING_DOWN, AccountState.AUTH_EXPIRED]


def test_two_claims_at_capacity_two_are_admitted_and_a_third_is_refused(tmp_path: Path) -> None:
    d = str(tmp_path)

    assert claim(d, "a", "T-1", capacity=2)
    assert claim(d, "a", "T-2", capacity=2)
    assert not claim(d, "a", "T-3", capacity=2)

    holders = get_holders(d, "a")
    assert [h.task_id for h in holders] == ["T-1", "T-2"]
    assert all(h.busy_since is not None for h in holders)
    assert get_state(d, "a") == AccountState.BUSY


def test_capacity_one_refuses_a_second_claim_and_writes_nothing(tmp_path: Path) -> None:
    d = str(tmp_path)
    assert claim(d, "a", "T-1")
    before = (tmp_path / "a.json").read_text()

    assert not claim(d, "a", "T-2")

    assert (tmp_path / "a.json").read_text() == before


def test_the_same_task_cannot_hold_twice(tmp_path: Path) -> None:
    d = str(tmp_path)
    assert claim(d, "a", "T-1", capacity=2)
    assert not claim(d, "a", "T-1", capacity=2)


def test_a_capacity_below_one_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        claim(str(tmp_path), "a", "T-1", capacity=0)


def test_release_drops_only_its_holder(tmp_path: Path) -> None:
    d = str(tmp_path)
    claim(d, "a", "T-1", capacity=2)
    claim(d, "a", "T-2", capacity=2)

    assert release(d, "a", "T-1")

    assert [h.task_id for h in get_holders(d, "a")] == ["T-2"]
    assert get_state(d, "a") == AccountState.BUSY
    assert get_current_task(d, "a") == "T-2"
    assert release(d, "a", "T-2")
    assert get_state(d, "a") == AccountState.IDLE
    assert not release(d, "a", "T-2")


@pytest.mark.parametrize("parked", PARKED)
def test_a_quota_state_keeps_the_holders(tmp_path: Path, parked: AccountState) -> None:
    d = str(tmp_path)
    claim(d, "a", "T-1", capacity=2)
    claim(d, "a", "T-2", capacity=2)

    park(d, "a", parked)

    assert get_state(d, "a") == parked
    assert [h.task_id for h in get_holders(d, "a")] == ["T-1", "T-2"]
    assert not claim(d, "a", "T-3", capacity=3)
    assert release(d, "a", "T-1")
    assert get_state(d, "a") == parked
    assert unpark(d, "a")
    assert get_state(d, "a") == AccountState.BUSY
    assert release(d, "a", "T-2")
    assert get_state(d, "a") == AccountState.IDLE


def test_unpark_of_an_account_that_is_not_parked_does_nothing(tmp_path: Path) -> None:
    d = str(tmp_path)
    claim(d, "a", "T-1")
    assert not unpark(d, "a")


def test_an_old_shape_file_reads_as_one_holder(tmp_path: Path) -> None:
    d = str(tmp_path)
    (tmp_path / "a.json").write_text(json.dumps(
        {"state": "BUSY", "current_task_id": "T-1", "busy_since": 5.0}))

    assert get_holders(d, "a") == [Holder("T-1", 5.0)]
    assert get_state(d, "a") == AccountState.BUSY
    assert get_busy_since(d, "a") == 5.0
    assert claim(d, "a", "T-2", capacity=2)
    assert get_holders(d, "a")[0] == Holder("T-1", 5.0)
    assert len(get_holders(d, "a")) == 2


def test_an_old_shape_parked_file_has_no_holders(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text(json.dumps(
        {"state": "COOLING_DOWN", "current_task_id": None}))

    assert get_holders(str(tmp_path), "a") == []
    assert get_state(str(tmp_path), "a") == AccountState.COOLING_DOWN


def test_the_refusal_and_the_probe_survive_the_per_holder_writers(tmp_path: Path) -> None:
    d = str(tmp_path)
    record_rate_limit(d, "a", at=7.0)
    probe = {"probed_at": 1}
    record_probe(d, "a", probe)

    claim(d, "a", "T-1", capacity=2)
    park(d, "a", AccountState.COOLING_DOWN)
    unpark(d, "a")
    release(d, "a", "T-1")

    data = json.loads((tmp_path / "a.json").read_text())
    assert data["rate_limited_at"] == 7.0
    assert data["last_probe"] == probe


def test_set_state_idle_still_clears_every_holder(tmp_path: Path) -> None:
    d = str(tmp_path)
    claim(d, "a", "T-1", capacity=2)
    claim(d, "a", "T-2", capacity=2)

    set_state(d, "a", AccountState.IDLE)

    assert get_holders(d, "a") == []


def test_park_refuses_a_state_that_is_not_account_wide(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        park(str(tmp_path), "a", AccountState.BUSY)
    with pytest.raises(ValueError):
        park(str(tmp_path), "a", AccountState.IDLE)


def test_two_threads_claiming_the_last_slot_have_one_winner(tmp_path: Path) -> None:
    d = str(tmp_path)
    claim(d, "a", "T-0", capacity=2)
    barrier = threading.Barrier(2, timeout=5)
    results: list[bool] = []

    def go(task: str) -> None:
        barrier.wait()
        results.append(claim(d, "a", task, capacity=2))

    threads = [threading.Thread(target=go, args=(t,)) for t in ("T-1", "T-2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [False, True]
    assert len(get_holders(d, "a")) == 2


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


# --- last_probe (docs/decisions.md ADR 49) ---------------------------------

_PROBE = {
    "probed_at": 1791547200.0,
    "session_pct": 12,
    "week_pct": 30,
    "session_reset": "4:59pm (UTC)",
    "week_reset": None,
    "exceeds": False,
    "session_ceiling_pct": 90,
    "week_ceiling": {
        "paced": False, "pct": 90, "days_left": None, "reset": None, "fallback_reason": None,
    },
}


def test_no_probe_is_recorded_for_an_account_that_was_never_probed(tmp_path: Path) -> None:
    assert get_last_probe(str(tmp_path), "cuenta1") is None
    set_state(str(tmp_path), "cuenta1", AccountState.IDLE)
    assert get_last_probe(str(tmp_path), "cuenta1") is None


def test_a_probe_round_trips_through_the_state_file(tmp_path: Path) -> None:
    # The gate can probe an account before anything wrote its state file.
    record_probe(str(tmp_path), "cuenta1", _PROBE)

    assert get_last_probe(str(tmp_path), "cuenta1") == _PROBE
    assert get_state(str(tmp_path), "cuenta1") == AccountState.IDLE


def test_a_later_probe_replaces_the_earlier_one(tmp_path: Path) -> None:
    record_probe(str(tmp_path), "cuenta1", _PROBE)
    later = {**_PROBE, "probed_at": 1791550800.0, "week_pct": 31}

    record_probe(str(tmp_path), "cuenta1", later)

    assert get_last_probe(str(tmp_path), "cuenta1") == later


def test_recording_a_probe_leaves_the_state_and_the_refusal_alone(tmp_path: Path) -> None:
    record_rate_limit(str(tmp_path), "cuenta1", at=1000.0)
    set_state(str(tmp_path), "cuenta1", AccountState.BUSY, current_task_id="task-1")

    record_probe(str(tmp_path), "cuenta1", _PROBE)

    assert get_state(str(tmp_path), "cuenta1") == AccountState.BUSY
    assert get_current_task(str(tmp_path), "cuenta1") == "task-1"
    assert get_rate_limited_at(str(tmp_path), "cuenta1") == 1000.0


def test_a_recorded_probe_survives_every_later_state_change(tmp_path: Path) -> None:
    # The gate records the probe and then parks the account with `set_state`,
    # which rewrites the whole document: without the carry the probe that
    # parked the account would be gone the moment it did.
    record_probe(str(tmp_path), "cuenta1", _PROBE)
    set_state(str(tmp_path), "cuenta1", AccountState.PRE_COOLDOWN)
    set_state(str(tmp_path), "cuenta1", AccountState.COOLING_DOWN)
    set_state(str(tmp_path), "cuenta1", AccountState.BUSY, current_task_id="task-1")
    set_state(str(tmp_path), "cuenta1", AccountState.IDLE)

    assert get_last_probe(str(tmp_path), "cuenta1") == _PROBE


def test_a_recorded_probe_survives_the_rate_limit_writers(tmp_path: Path) -> None:
    record_probe(str(tmp_path), "cuenta1", _PROBE)

    record_rate_limit(str(tmp_path), "cuenta1", at=1000.0)
    assert get_last_probe(str(tmp_path), "cuenta1") == _PROBE

    clear_rate_limit(str(tmp_path), "cuenta1")
    assert get_last_probe(str(tmp_path), "cuenta1") == _PROBE
    assert get_rate_limited_at(str(tmp_path), "cuenta1") is None


@pytest.mark.parametrize(
    "record",
    [["not", "an", "object"], {"session_pct": 12}],
    ids=["not-an-object", "missing-keys"],
)
def test_a_record_in_the_wrong_shape_reads_as_unreadable(tmp_path: Path, record) -> None:
    set_state(str(tmp_path), "cuenta1", AccountState.IDLE)
    path = tmp_path / "cuenta1.json"
    data = json.loads(path.read_text())
    data["last_probe"] = record
    path.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="last_probe"):
        get_last_probe(str(tmp_path), "cuenta1")
