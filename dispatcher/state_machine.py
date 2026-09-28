from __future__ import annotations

import enum
import json
import os
import tempfile
import time
from pathlib import Path

from dispatcher.config import AccountConfig


class AccountState(str, enum.Enum):
    IDLE = "IDLE"
    BUSY = "BUSY"
    PRE_COOLDOWN = "PRE_COOLDOWN"
    COOLING_DOWN = "COOLING_DOWN"


def _state_path(state_dir: str, account_name: str) -> str:
    return os.path.join(state_dir, f"{account_name}.json")


def _read_state(state_dir: str, account_name: str) -> dict | None:
    path = _state_path(state_dir, account_name)
    if not os.path.exists(path):
        return None
    return json.loads(Path(path).read_text())


def get_state(state_dir: str, account_name: str) -> AccountState:
    data = _read_state(state_dir, account_name)
    if data is None:
        return AccountState.IDLE
    return AccountState(data["state"])


def get_current_task(state_dir: str, account_name: str) -> str | None:
    data = _read_state(state_dir, account_name)
    if data is None:
        return None
    return data.get("current_task_id")


def _write_state(state_dir: str, account_name: str, data: dict) -> None:
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    path = _state_path(state_dir, account_name)
    tmp = tempfile.NamedTemporaryFile(mode="w", dir=state_dir, delete=False)
    try:
        tmp.write(json.dumps(data))
    finally:
        tmp.close()
    os.replace(tmp.name, path)


def set_state(
    state_dir: str,
    account_name: str,
    state: AccountState,
    current_task_id: str | None = None,
) -> None:
    # The write replaces the whole document, so anything persisted next to the
    # state has to be carried across by hand. `rate_limited_at` is the one such
    # field, and it has to outlive every transition: an account is refused,
    # parked and re-probed in three separate writes, and the refusal is only
    # worth anything if it is still there on the third.
    existing = _read_state(state_dir, account_name) or {}
    data = {"state": state.value, "current_task_id": current_task_id}
    if existing.get("rate_limited_at") is not None:
        data["rate_limited_at"] = existing["rate_limited_at"]
    # `busy_since` is the opposite case: it belongs to this transition and not
    # to the account, so it is stamped on the way into BUSY and dropped on the
    # way out rather than carried. Nothing re-enters BUSY without a new phase
    # behind it, so a fresh stamp each time is the clock the reaper wants.
    if state == AccountState.BUSY:
        data["busy_since"] = time.time()
    _write_state(state_dir, account_name, data)


def record_rate_limit(state_dir: str, account_name: str, at: float | None = None) -> None:
    """Remember that the service itself turned this account away, and when.

    The `/usage` probe the quota gate runs is a local slash command: measured
    2026-09-24, it reports `local_command: "usage"`, zero API milliseconds and
    zero cost, because it reads counters this machine wrote. It therefore
    cannot see a refusal, which is a property of the account and not of the
    disk. The one moment that truth is observable is the 429 itself, and this
    is where it is kept so the probe can be overruled by it later.
    """
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
    data = _read_state(state_dir, account_name)
    if data is None or data.get("rate_limited_at") is None:
        return
    del data["rate_limited_at"]
    _write_state(state_dir, account_name, data)


def get_busy_since(state_dir: str, account_name: str) -> float | None:
    """When this account entered BUSY, or None if it is not BUSY.

    Only a fallback for the account-lock TTL: a phase that holds a card is
    judged by that card's heartbeat, which a running phase refreshes, because
    a wall-clock reading would expire a phase that is legitimately long. This
    is what is left to judge an account that holds no card at all.
    """
    data = _read_state(state_dir, account_name)
    if data is None:
        return None
    return data.get("busy_since")


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
