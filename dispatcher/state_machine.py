from __future__ import annotations

import enum
import json
import os
import tempfile
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


def set_state(
    state_dir: str,
    account_name: str,
    state: AccountState,
    current_task_id: str | None = None,
) -> None:
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    path = _state_path(state_dir, account_name)
    tmp = tempfile.NamedTemporaryFile(mode="w", dir=state_dir, delete=False)
    try:
        tmp.write(json.dumps({"state": state.value, "current_task_id": current_task_id}))
    finally:
        tmp.close()
    os.replace(tmp.name, path)


def list_idle_accounts(state_dir: str, accounts: list[AccountConfig]) -> list[str]:
    return [a.name for a in accounts if get_state(state_dir, a.name) == AccountState.IDLE]
