from __future__ import annotations

import enum
import json
import os
from pathlib import Path

from dispatcher.config import AccountConfig


class AccountState(str, enum.Enum):
    IDLE = "IDLE"
    BUSY = "BUSY"
    PRE_COOLDOWN = "PRE_COOLDOWN"
    COOLING_DOWN = "COOLING_DOWN"


def _state_path(state_dir: str, account_name: str) -> str:
    return os.path.join(state_dir, f"{account_name}.json")


def get_state(state_dir: str, account_name: str) -> AccountState:
    path = _state_path(state_dir, account_name)
    if not os.path.exists(path):
        return AccountState.IDLE
    data = json.loads(Path(path).read_text())
    return AccountState(data["state"])


def get_current_task(state_dir: str, account_name: str) -> str | None:
    path = _state_path(state_dir, account_name)
    if not os.path.exists(path):
        return None
    data = json.loads(Path(path).read_text())
    return data.get("current_task_id")


def set_state(
    state_dir: str,
    account_name: str,
    state: AccountState,
    current_task_id: str | None = None,
) -> None:
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    path = _state_path(state_dir, account_name)
    Path(path).write_text(json.dumps({"state": state.value, "current_task_id": current_task_id}))


def list_idle_accounts(state_dir: str, accounts: list[AccountConfig]) -> list[str]:
    return [a.name for a in accounts if get_state(state_dir, a.name) == AccountState.IDLE]
