from pathlib import Path

from dispatcher.config import AccountConfig
from dispatcher.state_machine import (
    AccountState,
    get_current_task,
    get_state,
    list_idle_accounts,
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
