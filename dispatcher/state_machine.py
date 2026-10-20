from __future__ import annotations

import contextlib
import enum
import fcntl
import json
import os
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from dispatcher.config import AccountConfig


class AccountState(str, enum.Enum):
    IDLE = "IDLE"
    BUSY = "BUSY"
    PRE_COOLDOWN = "PRE_COOLDOWN"
    COOLING_DOWN = "COOLING_DOWN"
    #: The service rejected the account's login (`docs/decisions.md` ADR 76).
    #: Unlike the two quota states this one does not end by waiting: only the
    #: operator logging the container in again fixes it, so nothing re-probes
    #: it and only `release` brings it back.
    AUTH_EXPIRED = "AUTH_EXPIRED"


def _state_path(state_dir: str, account_name: str) -> str:
    return os.path.join(state_dir, f"{account_name}.json")


def _read_state(state_dir: str, account_name: str) -> dict | None:
    path = _state_path(state_dir, account_name)
    if not os.path.exists(path):
        return None
    return json.loads(Path(path).read_text())


#: The quota and login states. They describe the account, not a phase, so they
#: are kept next to the holders and never drop them (`docs/decisions.md` ADR 85).
PARKED_STATES = frozenset({
    AccountState.PRE_COOLDOWN, AccountState.COOLING_DOWN, AccountState.AUTH_EXPIRED,
})


@dataclass(frozen=True)
class Holder:
    task_id: str | None
    busy_since: float | None


def _holders_of(data: dict) -> list[Holder]:
    if "holders" in data:
        holders = [Holder(e["task_id"], e.get("busy_since")) for e in data["holders"]]
    elif data["state"] == AccountState.BUSY.value:
        # The old shape: BUSY with one task is one holder.
        holders = [Holder(None, None)]
    else:
        holders = []
    if holders:
        # The top-level pair mirrors the first holder and wins on read
        # (ADR 85 point 3): tests and hand edits age a phase by editing
        # `busy_since` there, and the reaper must see it.
        holders[0] = Holder(data.get("current_task_id"), data.get("busy_since"))
    return holders


def _parked_of(data: dict) -> AccountState | None:
    if "state" not in data:
        return None
    state = AccountState(data["state"])
    return state if state in PARKED_STATES else None


def _state_of(parked: AccountState | None, holders: list[Holder]) -> AccountState:
    if parked is not None:
        return parked
    return AccountState.BUSY if holders else AccountState.IDLE


def get_state(state_dir: str, account_name: str) -> AccountState:
    data = _read_state(state_dir, account_name)
    if data is None:
        return AccountState.IDLE
    return _state_of(_parked_of(data), _holders_of(data))


def get_holders(state_dir: str, account_name: str) -> list[Holder]:
    """Every phase the account holds, in the order they claimed it."""
    data = _read_state(state_dir, account_name)
    if data is None:
        return []
    return _holders_of(data)


def get_current_task(state_dir: str, account_name: str) -> str | None:
    holders = get_holders(state_dir, account_name)
    return holders[0].task_id if holders else None


def _write_state(state_dir: str, account_name: str, data: dict) -> None:
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    path = _state_path(state_dir, account_name)
    tmp = tempfile.NamedTemporaryFile(mode="w", dir=state_dir, delete=False)
    try:
        tmp.write(json.dumps(data))
    finally:
        tmp.close()
    os.replace(tmp.name, path)


#: The lock file taking an account and every read-modify-write of a state file
#: go through (`docs/decisions.md` ADR 71). It does not end in `.json`, and
#: accounts are read from their own `<name>.json`, so nothing lists it as one.
LOCK_NAME = ".accounts.lock"


@contextlib.contextmanager
def _locked(state_dir: str):
    """Hold the inter-process lock on `state_dir`'s account state files.

    A new descriptor per acquisition: `flock` belongs to the open file
    description, so two threads exclude each other the way two processes do.
    Not reentrant — never call a public writer while holding it. Held only
    around a read-modify-write, never across a phase.
    """
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    fd = os.open(os.path.join(state_dir, LOCK_NAME), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _write_account(
    state_dir: str,
    account_name: str,
    existing: dict,
    parked: AccountState | None,
    holders: list[Holder],
    forget_rate_limit: bool = False,
) -> None:
    # The write replaces the whole document, so anything persisted next to the
    # state has to be carried across by hand. `rate_limited_at` is the first
    # such field, and it has to outlive every transition: an account is refused,
    # parked and re-probed in three separate writes, and the refusal is only
    # worth anything if it is still there on the third.
    data: dict = {
        "state": _state_of(parked, holders).value,
        "holders": [{"task_id": h.task_id, "busy_since": h.busy_since} for h in holders],
        "current_task_id": holders[0].task_id if holders else None,
    }
    # The stamp lives per holder; the top-level copy mirrors the first one for
    # the readers of the old shape.
    if holders:
        data["busy_since"] = holders[0].busy_since
    if existing.get("rate_limited_at") is not None and not forget_rate_limit:
        data["rate_limited_at"] = existing["rate_limited_at"]
    # `last_probe` is carried for the same reason and by the same rule: it is a
    # fact about the account, not about the transition, and the gate writes it
    # one line before the `set_state` that parks the account on it
    # (`docs/decisions.md` ADR 49). Dropping it here would erase every probe
    # that ever parked anything.
    if existing.get("last_probe") is not None:
        data["last_probe"] = existing["last_probe"]
    _write_state(state_dir, account_name, data)


def claim(state_dir: str, account_name: str, task_id: str, capacity: int = 1) -> bool:
    """Admit `task_id` as a holder while fewer than `capacity` hold the account.

    False, writing nothing, if the account is parked on a quota or login state,
    is full, or already holds `task_id`.
    """
    if capacity < 1:
        raise ValueError(f"capacity must be at least 1, not {capacity}")
    with _locked(state_dir):
        existing = _read_state(state_dir, account_name) or {}
        parked = _parked_of(existing)
        holders = _holders_of(existing) if existing else []
        if parked is not None or len(holders) >= capacity:
            return False
        if any(h.task_id == task_id for h in holders):
            return False
        holders.append(Holder(task_id, time.time()))
        _write_account(state_dir, account_name, existing, None, holders)
        return True


def release(state_dir: str, account_name: str, task_id: str) -> bool:
    """Drop `task_id` alone; the other holders and a parked state stay."""
    with _locked(state_dir):
        existing = _read_state(state_dir, account_name) or {}
        holders = _holders_of(existing) if existing else []
        kept = [h for h in holders if h.task_id != task_id]
        if len(kept) == len(holders):
            return False
        _write_account(state_dir, account_name, existing, _parked_of(existing), kept)
        return True


def park(
    state_dir: str, account_name: str, state: AccountState, *, pool: Iterable[str] = (),
) -> bool:
    """Put the account in a quota or login state, keeping its holders.

    True when this park dried `pool` (`docs/decisions.md` ADR 224): the account
    was not parked before, and now every account named in `pool` is. Read
    under the same lock as the write, so of two accounts parked at once only
    the second sees the pool empty. A neighbour whose file cannot be read
    counts as not parked: the park stands, and no dry is claimed on a guess.
    """
    if state not in PARKED_STATES:
        raise ValueError(f"{state.value} is not an account-wide state")
    names = list(pool)
    with _locked(state_dir):
        existing = _read_state(state_dir, account_name) or {}
        was_parked = _parked_of(existing) is not None
        holders = _holders_of(existing) if existing else []
        _write_account(state_dir, account_name, existing, state, holders)
        if was_parked or not names:
            return False
        for name in names:
            if name == account_name:
                continue
            try:
                if _parked_of(_read_state(state_dir, name) or {}) is None:
                    return False
            except (OSError, ValueError, KeyError, TypeError):
                return False
        return True


def unpark(state_dir: str, account_name: str, *, forget_rate_limit: bool = False) -> bool:
    """Leave a quota or login state, keeping its holders; False if not parked."""
    with _locked(state_dir):
        existing = _read_state(state_dir, account_name) or {}
        if _parked_of(existing) is None:
            return False
        _write_account(
            state_dir, account_name, existing, None, _holders_of(existing),
            forget_rate_limit,
        )
        return True


def set_state(
    state_dir: str,
    account_name: str,
    state: AccountState,
    current_task_id: str | None = None,
    *,
    only_from: Iterable[AccountState] | None = None,
    forget_rate_limit: bool = False,
) -> bool:
    """Write the state; with `only_from`, only if the account is in one of them.

    Returns whether it wrote. The check and the write share one lock hold.
    This is a whole-account write: it replaces every holder, and a park through
    it drops them. Use `claim`, `release` and `park` to touch one holder
    (`docs/decisions.md` ADR 85 point 6).
    """
    with _locked(state_dir):
        return _set_state_locked(
            state_dir, account_name, state, current_task_id,
            only_from, forget_rate_limit,
        )


def _set_state_locked(
    state_dir: str,
    account_name: str,
    state: AccountState,
    current_task_id: str | None,
    only_from: Iterable[AccountState] | None,
    forget_rate_limit: bool,
) -> bool:
    existing = _read_state(state_dir, account_name) or {}
    if only_from is not None:
        current = _derived_state(existing)
        if current not in set(only_from):
            return False
    # The stamp now lives per holder: a fresh one on the way into BUSY, none
    # otherwise, which is the clock the reaper wants.
    if state == AccountState.BUSY:
        holders = [Holder(current_task_id, time.time())]
    else:
        holders = []
    parked = state if state in PARKED_STATES else None
    _write_account(state_dir, account_name, existing, parked, holders, forget_rate_limit)
    return True


def _derived_state(data: dict) -> AccountState:
    if not data:
        return AccountState.IDLE
    return _state_of(_parked_of(data), _holders_of(data))


def record_rate_limit(state_dir: str, account_name: str, at: float | None = None) -> None:
    """Remember that the service itself turned this account away, and when.

    The `/usage` probe the quota gate runs is a local slash command: measured
    2026-09-24, it reports `local_command: "usage"`, zero API milliseconds and
    zero cost, because it reads counters this machine wrote. It therefore
    cannot see a refusal, which is a property of the account and not of the
    disk. The one moment that truth is observable is the 429 itself, and this
    is where it is kept so the probe can be overruled by it later.
    """
    with _locked(state_dir):
        data = _read_state(state_dir, account_name) or {
            "state": AccountState.IDLE.value, "current_task_id": None,
        }
        data["rate_limited_at"] = time.time() if at is None else at
        _write_state(state_dir, account_name, data)


def get_rate_limited_at(state_dir: str, account_name: str) -> float | None:
    """When this account was last refused, or None if it never was here."""
    data = _read_state(state_dir, account_name)
    if data is None:
        return None
    return data.get("rate_limited_at")


def clear_rate_limit(state_dir: str, account_name: str) -> None:
    """Forget the refusal, for an account that has since been served.

    Only a real turn is evidence of that — a clean `/usage` is not, for the
    same reason the refusal had to be recorded in the first place.
    """
    with _locked(state_dir):
        data = _read_state(state_dir, account_name)
        if data is None or data.get("rate_limited_at") is None:
            return
        del data["rate_limited_at"]
        _write_state(state_dir, account_name, data)


#: The keys every persisted probe carries. `get_last_probe` checks for them so a
#: record some older or hand-edited file holds in another shape reads as
#: unreadable rather than as a probe with holes in it.
PROBE_KEYS = frozenset({
    "probed_at", "session_pct", "week_pct", "session_reset", "week_reset",
    "exceeds", "session_ceiling_pct", "week_ceiling",
})


def record_probe(state_dir: str, account_name: str, probe: dict) -> None:
    """Keep the last `/usage` probe of this account and what it was held to.

    One record, overwritten by every probe: this is the account's latest
    measurement, not a series (`docs/decisions.md` ADR 49). It lives in the
    state file because that is what `observability/api` already reads, and
    every other writer here carries it across a rewrite — `set_state` by
    hand, the rate-limit pair because they rewrite the document they read.
    """
    with _locked(state_dir):
        data = _read_state(state_dir, account_name) or {
            "state": AccountState.IDLE.value, "current_task_id": None,
        }
        data["last_probe"] = probe
        _write_state(state_dir, account_name, data)


def get_last_probe(state_dir: str, account_name: str) -> dict | None:
    """The last probe recorded for this account, or None if it was never probed.

    Raises ValueError for a record that is there but is not one: a reader that
    serves it (the api's account row) nulls the record and says why, rather
    than serving a half of one as if it were the whole.
    """
    data = _read_state(state_dir, account_name)
    if data is None:
        return None
    probe = data.get("last_probe")
    if probe is None:
        return None
    if not isinstance(probe, dict):
        raise ValueError(f"last_probe is a {type(probe).__name__}, not an object")
    missing = sorted(PROBE_KEYS - probe.keys())
    if missing:
        raise ValueError(f"last_probe is missing {', '.join(missing)}")
    return probe


def get_busy_since(state_dir: str, account_name: str) -> float | None:
    """When this account entered BUSY, or None if it is not BUSY.

    Only a fallback for the account-lock TTL: a phase that holds a card is
    judged by that card's heartbeat, which a running phase refreshes, because
    a wall-clock reading would expire a phase that is legitimately long. This
    is what is left to judge an account that holds no card at all.
    """
    holders = get_holders(state_dir, account_name)
    return holders[0].busy_since if holders else None


def list_idle_accounts(state_dir: str, accounts: list[AccountConfig]) -> list[str]:
    """Every IDLE account, in the order the picker should try them.

    The pool is ordered, not partitioned. The primary is in it like any other
    account and simply ranks last, which is what lets "every secondary is out
    of quota" need no special case at all — it is the ordering running off its
    end. With no primary configured every account sorts equal and this is
    config.yaml's own order, exactly as it was before ranking existed.
    """
    idle = [a for a in accounts if get_state(state_dir, a.name) == AccountState.IDLE]
    return [a.name for a in sorted(idle, key=lambda a: a.is_primary)]


def list_shared_accounts(state_dir: str, accounts: list[AccountConfig]) -> list[str]:
    """Every unparked account that holds a phase and still has a free slot.

    Config order. With every `max_concurrent_phases` at 1 this is always empty,
    so the picker behaves as it did before slots existed (`docs/decisions.md`
    ADR 88).
    """
    shared = []
    for a in accounts:
        data = _read_state(state_dir, a.name)
        if not data or _parked_of(data) is not None:
            continue
        if 0 < len(_holders_of(data)) < a.max_concurrent_phases:
            shared.append(a.name)
    return shared


def stamp_holder(state_dir: str, account_name: str, task_id: str | None) -> bool:
    """Give the holder of `task_id` that has no `busy_since` one, keeping a park.

    False if no holder is in that case. This is how the reaper starts the clock
    on a holder it found without one (an old-shape file).
    """
    with _locked(state_dir):
        existing = _read_state(state_dir, account_name) or {}
        holders = _holders_of(existing) if existing else []
        for i, h in enumerate(holders):
            if h.task_id == task_id and h.busy_since is None:
                holders[i] = Holder(h.task_id, time.time())
                _write_account(
                    state_dir, account_name, existing, _parked_of(existing), holders,
                )
                return True
        return False
