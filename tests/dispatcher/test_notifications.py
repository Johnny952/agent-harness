"""The notification record and its writers (ADR 221, `docs/plans/notifications.md` step 1).

The record is `dispatcher/notifications.py`; its writers are the board
module's state changes (`tasks`) and the dispatcher's own (the pool, the
cycle's end). Every test reads the file back through `parse_line`, the rule
the api's reader uses, so what is asserted is what the inbox will see.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import dispatcher.dispatcher as dispatcher_mod
from dispatcher import handoff, notifications, tasks
from dispatcher.config import AccountConfig
from dispatcher.context_transfer import read_task_file, task_file_path, write_task_file
from dispatcher.docker_exec import ClaudeResult
from dispatcher.state_machine import AccountState, get_state, set_state
from tests.dispatcher.test_dispatcher import _make_config, _split_ctx
from tests.dispatcher.test_tasks import _epic, _hive, _legacy


def _records(hive: str) -> list[dict]:
    path = Path(notifications.notifications_path(hive))
    if not path.exists():
        return []
    out = []
    for line in path.read_bytes().splitlines():
        record, problem = notifications.parse_line(line)
        assert problem is None, problem
        out.append(record)
    return out


def _kinds(hive: str) -> list[tuple[str, str]]:
    return [(r["kind"], r.get("task") or r.get("account")) for r in _records(hive)]


# -- the record ---------------------------------------------------------------


def test_append_writes_one_line_beside_the_board(tmp_path: Path) -> None:
    hive = _hive(tmp_path)

    record = notifications.append(
        hive, "awaiting_review", task="task-1", title="task-1  is\nwaiting", url="/tasks/task-1"
    )

    assert notifications.notifications_path(hive) == str(tmp_path / "hive" / "notifications.jsonl")
    assert set(record) == {"id", "kind", "at", "task", "title", "url"}
    assert record["id"].startswith("n-") and record["id"].count("-") == 2
    assert record["title"] == "task-1 is waiting"
    assert _records(hive) == [record]


def test_a_long_title_is_cut_to_one_lock_screen_line(tmp_path: Path) -> None:
    hive = _hive(tmp_path)

    record = notifications.append(hive, "merged", task="t", title="x" * 500, url="/tasks/t")

    assert len(record["title"]) == notifications.MAX_TITLE_CHARS
    assert record["title"].endswith("…")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "made_up", "task": "t"},
        {"kind": "merged"},
        {"kind": "merged", "task": "t", "account": "cuenta1"},
    ],
)
def test_a_caller_that_breaks_the_shape_raises(tmp_path: Path, kwargs) -> None:
    with pytest.raises(ValueError):
        notifications.append(_hive(tmp_path), title="t", url="/", **kwargs)


def test_a_write_that_fails_is_logged_and_never_raises(tmp_path: Path, caplog) -> None:
    hive = _hive(tmp_path)
    os.mkdir(notifications.notifications_path(hive))  # a directory where the file goes

    with caplog.at_level("WARNING", logger=notifications.logger.name):
        assert notifications.append(hive, "pool", account="cuenta1", title="t", url="/pool") is None

    assert "not written" in caplog.text


def test_task_url_quotes_the_id_and_task_title_names_it() -> None:
    assert notifications.task_url("a/b c") == "/tasks/a%2Fb%20c"
    assert notifications.task_title("T-1", "is blocked") == "T-1 is blocked"
    assert notifications.task_title("T-1", "is blocked", "  Fix\nit ") == "T-1 is blocked: Fix it"


@pytest.mark.parametrize(
    "line, problem",
    [
        (b"{nope", "not a JSON line"),
        (b"[1]", "not a JSON object"),
        (b'{"id": "n", "kind": "merged", "at": "a", "title": "t"}', "no string url"),
        (b'{"id": "n", "kind": "x", "at": "a", "title": "t", "url": "/", "task": "t"}', "kind 'x'"),
        (b'{"id": "n", "kind": "merged", "at": "a", "title": "t", "url": "/"}', "exactly one"),
        (b'{"id": "n", "kind": "merged", "at": "a", "title": "t", "url": "/", "task": 1}',
         "exactly one"),
    ],
)
def test_parse_line_names_what_is_wrong(line: bytes, problem: str) -> None:
    record, found = notifications.parse_line(line)
    assert record is None
    assert problem in found


def test_parse_line_keeps_a_key_a_later_step_adds() -> None:
    line = b'{"id": "n", "kind": "pool", "at": "a", "title": "t", "url": "/pool", ' \
           b'"account": "cuenta1", "read": true}'
    record, problem = notifications.parse_line(line)
    assert problem is None and record["read"] is True


# -- the board module's writers ----------------------------------------------


@pytest.mark.parametrize(
    "reason, failed, kind",
    [
        ("a decision nobody made", False, "blocked"),
        (handoff.COMMAND_REQUEST, False, "command_request"),
        (None, True, "phase_failed"),
    ],
)
def test_mark_blocked_tells_the_kind_of_stop(tmp_path: Path, reason, failed, kind) -> None:
    hive = _hive(tmp_path)
    _legacy(hive, "")

    tasks.mark_blocked(hive, "task-1", reason=reason, failed=failed)

    [record] = _records(hive)
    assert (record["kind"], record["task"], record["url"]) == (kind, "task-1", "/tasks/task-1")
    assert record["title"].startswith("task-1 ")


def test_a_move_into_conflict_is_told_once(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _legacy(hive, "")

    tasks.handoff(hive, "task-1", new_status="pending", body="## arquitecto")
    assert _records(hive) == []
    tasks.handoff(hive, "task-1", new_status="conflict", body="## approve")
    tasks.handoff(hive, "task-1", new_status="conflict", body="## approve again")

    assert _kinds(hive) == [("conflict", "task-1")]


def test_a_merge_is_told_the_first_time_only(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _legacy(hive, "")

    tasks.mark_merged(hive, "task-1", "main")
    tasks.mark_merged(hive, "task-1", "main")

    [record] = _records(hive)
    assert record["kind"] == "merged"
    assert record["title"].startswith("task-1 merged into main")


def test_an_epic_closed_by_its_last_merge_is_told_as_merged(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    _epic(hive)

    tasks.mark_merged(hive, "task-1-S1", "main")
    tasks.close_epic(hive, "task-1-S1")
    tasks.mark_merged(hive, "task-1-S2", "main")
    assert tasks.close_epic(hive, "task-1-S2") == "task-1"
    tasks.close_epic(hive, "task-1-S2")

    assert _kinds(hive) == [
        ("merged", "task-1-S1"), ("merged", "task-1-S2"), ("merged", "task-1"),
    ]
    assert "closed: every sub-task is merged or dropped" in _records(hive)[-1]["title"]


# -- the dispatcher's writers --------------------------------------------------


def _owned_by_nobody(cfg, status: str, merged: bool = False) -> None:
    os.makedirs(cfg.hive_tasks_dir, exist_ok=True)
    _legacy(cfg.hive_tasks_dir, "")
    path = task_file_path(cfg.hive_tasks_dir, "task-1")
    task = read_task_file(path)
    task.status, task.owner = status, None
    write_task_file(path, task)
    if merged:
        tasks.mark_merged(cfg.hive_tasks_dir, "task-1", "main")


def test_a_cycle_left_done_and_unmerged_waits_for_review(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    _owned_by_nobody(cfg, "done")

    dispatcher_mod._notify_awaiting_review(cfg, "task-1")

    assert _kinds(cfg.hive_tasks_dir) == [("awaiting_review", "task-1")]


@pytest.mark.parametrize("status, merged", [("done", True), ("pending", False), ("blocked", False)])
def test_a_cycle_that_merged_or_did_not_finish_is_not_waiting(tmp_path: Path, status, merged) -> None:
    cfg = _make_config(tmp_path)
    _owned_by_nobody(cfg, status, merged)

    dispatcher_mod._notify_awaiting_review(cfg, "task-1")

    assert [k for k, _ in _kinds(cfg.hive_tasks_dir)] == (["merged"] if merged else [])


def test_no_task_file_is_no_review_and_no_raise(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    dispatcher_mod._notify_awaiting_review(cfg, "task-404")
    assert _records(cfg.hive_tasks_dir) == []


@pytest.mark.parametrize("failed, kind", [(False, "blocked"), (True, "phase_failed")])
def test_a_cycle_that_stopped_blocked_tells_whether_its_phase_failed(
    tmp_path: Path, failed, kind,
) -> None:
    cfg = _make_config(tmp_path)
    _owned_by_nobody(cfg, "in_progress")
    ctx = _split_ctx(cfg)
    ctx.took_lock, ctx.phase_failed = True, failed

    dispatcher_mod._mark_blocked(ctx, reason="stopped")

    assert _kinds(cfg.hive_tasks_dir) == [(kind, "task-1")]


def test_an_account_recovered_from_cooling_is_told(tmp_path: Path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    set_state(cfg.state_dir, "cuenta1", AccountState.COOLING_DOWN)

    def fake_exec_claude(*args, **kwargs):
        raise ValueError("bad /usage format")

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)

    assert dispatcher_mod._recheck_cooling_accounts(cfg) == ["cuenta1"]
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.IDLE
    [record] = _records(cfg.hive_tasks_dir)
    assert (record["kind"], record["account"], record["url"]) == ("pool", "cuenta1", "/pool")
    assert record["title"] == "account cuenta1 is available again"


# -- pool dry (ADR 224) -------------------------------------------------------


def _two_accounts(tmp_path: Path):
    return _make_config(tmp_path, accounts=[
        AccountConfig(name="cuenta1", container="agent-cuenta1"),
        AccountConfig(name="cuenta2", container="agent-cuenta2"),
    ])


def _probe(monkeypatch, result: ClaudeResult) -> None:
    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", lambda *a, **k: result)


_REFUSED = ClaudeResult(session_id=None, result_text="usage limit reached", raw={"is_error": True})
_DRY = "the pool is dry: account cuenta1 went {} and no account is left"


def test_the_park_that_leaves_no_account_tells_the_pool_is_dry(tmp_path: Path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    _probe(monkeypatch, _REFUSED)

    assert dispatcher_mod.check_quota_ok(cfg, "cuenta1") is False

    [record] = _records(cfg.hive_tasks_dir)
    assert (record["kind"], record["account"], record["url"]) == ("pool", "cuenta1", "/pool")
    assert record["title"] == _DRY.format("COOLING_DOWN")


def test_the_quota_ceiling_park_is_a_dry_too(tmp_path: Path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    _probe(monkeypatch, ClaudeResult(session_id=None, result_text="usage", raw={"is_error": False}))
    monkeypatch.setattr(
        dispatcher_mod.quota, "parse_usage_output",
        lambda text: SimpleNamespace(session_pct=99, week_pct=50),
    )
    monkeypatch.setattr(
        dispatcher_mod, "_quota_decision",
        lambda cfg, account, usage: SimpleNamespace(exceeds=True, limit_phrase="the ceiling"),
    )

    assert dispatcher_mod.check_quota_ok(cfg, "cuenta1") is False

    assert [r["title"] for r in _records(cfg.hive_tasks_dir)] == [_DRY.format("PRE_COOLDOWN")]


def test_a_park_that_leaves_an_account_is_not_dry(tmp_path: Path, monkeypatch) -> None:
    cfg = _two_accounts(tmp_path)
    _probe(monkeypatch, _REFUSED)

    dispatcher_mod.check_quota_ok(cfg, "cuenta1")
    assert _records(cfg.hive_tasks_dir) == []

    # A login refused is parked like a quota, and empties the pool the same way.
    dispatcher_mod._park_auth_expired(cfg, "cuenta2", "a phase")
    assert _kinds(cfg.hive_tasks_dir) == [("pool", "cuenta2")]


def test_a_dry_pool_is_told_once_and_again_after_it_came_back(tmp_path: Path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    dispatcher_mod._park(cfg, "cuenta1", AccountState.PRE_COOLDOWN)
    dispatcher_mod._park(cfg, "cuenta1", AccountState.COOLING_DOWN)  # still dry: no second line

    def fake_exec_claude(*args, **kwargs):
        raise ValueError("bad /usage format")

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    assert dispatcher_mod._recheck_cooling_accounts(cfg) == ["cuenta1"]
    dispatcher_mod._park(cfg, "cuenta1", AccountState.PRE_COOLDOWN)

    assert [r["title"] for r in _records(cfg.hive_tasks_dir)] == [
        _DRY.format("PRE_COOLDOWN"),
        "account cuenta1 is available again",
        _DRY.format("PRE_COOLDOWN"),
    ]


def test_a_dry_record_lost_to_the_disk_leaves_the_park_standing(
    tmp_path: Path, monkeypatch, caplog,
) -> None:
    cfg = _make_config(tmp_path)
    os.makedirs(notifications.notifications_path(cfg.hive_tasks_dir))  # a directory where the file goes
    _probe(monkeypatch, _REFUSED)

    with caplog.at_level("WARNING", logger=notifications.logger.name):
        assert dispatcher_mod.check_quota_ok(cfg, "cuenta1") is False

    assert get_state(cfg.state_dir, "cuenta1") == AccountState.COOLING_DOWN
    assert "not written" in caplog.text
