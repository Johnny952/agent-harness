import datetime as dt
import time

import pytest

import dispatcher.operator as operator_mod
from dispatcher import context_transfer
from dispatcher.config import AccountConfig, Config
from dispatcher.docker_exec import ClaudeResult
from dispatcher.state_machine import (
    AccountState,
    get_rate_limited_at,
    get_state,
    record_rate_limit,
    set_state,
)


def _make_config(tmp_path, **overrides):
    defaults = dict(
        accounts=[
            AccountConfig(name="cuenta1", container="agent-cuenta1"),
            AccountConfig(name="cuenta2", container="agent-cuenta2"),
        ],
        quota_threshold_pct=90,
        quota_cooldown_seconds=1800,
        heartbeat_ttl_seconds=120,
        heartbeat_interval_seconds=1,
        projects_root=str(tmp_path / "projects"),
        hive_tasks_dir=str(tmp_path / "hive"),
        state_dir=str(tmp_path / "state"),
        vibe_kanban=None,
        local_board=None,
        collector_url="http://127.0.0.1:8787",
        default_model="opus",
        permission_mode="acceptEdits",
        allowed_tools=[],
        max_revision_rounds=3,
        escalate_effort_after_round=2,
        escalated_effort="high",
        phase_timeout_seconds=7200,
        merge_on_done=False,
        mapping_enabled=False,
        mapping_model="sonnet",
        mapping_max_turns=40,
        gates_enabled=False,
        gates_test_timeout_seconds=900,
    )
    defaults.update(overrides)
    return Config(**defaults)


def _write_task(cfg, task_id, status="in_progress", owner=None, heartbeat_age=None):
    """A card on disk, optionally held, optionally with a stale heartbeat."""
    heartbeat = None
    if heartbeat_age is not None:
        stamp = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=heartbeat_age)
        heartbeat = stamp.isoformat()
    task = context_transfer.TaskFile(
        task_id=task_id,
        status=status,
        owner=owner,
        depends_on=[],
        heartbeat=heartbeat,
        body="",
    )
    context_transfer.write_task_file(
        context_transfer.task_file_path(cfg.hive_tasks_dir, task_id), task
    )
    return task


class _FakeExec:
    """Stands in for docker_exec.exec_claude, one canned answer per container."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, container, workdir, prompt, **kwargs):
        self.calls.append((container, workdir, prompt, kwargs))
        answer = self.answers[container]
        if isinstance(answer, Exception):
            raise answer
        return answer


def _usage_result(text):
    return ClaudeResult(session_id=None, result_text=text, raw={})


def _refusal_result():
    # The shape is_rate_limit_error actually looks at: the flag and the status,
    # not the wording.
    return ClaudeResult(
        session_id=None,
        result_text="Claude AI usage limit reached",
        raw={"is_error": True, "api_error_status": 429},
    )


_CLEAN_USAGE = "Current session: 12% used · resets 3pm\nCurrent week (all models): 30% used"


# --- status -----------------------------------------------------------------


def test_account_reports_read_the_state_without_touching_it(tmp_path):
    cfg = _make_config(tmp_path)
    set_state(cfg.state_dir, "cuenta1", AccountState.BUSY, "T-008")

    reports = operator_mod.account_reports(cfg)

    assert [r.name for r in reports] == ["cuenta1", "cuenta2"]
    assert reports[0].state is AccountState.BUSY
    assert reports[0].current_task_id == "T-008"
    # An account with no file at all reads IDLE and stays without one: this is
    # the whole difference from check_quota_ok, which parks as it answers.
    assert reports[1].state is AccountState.IDLE
    assert get_state(cfg.state_dir, "cuenta2") is AccountState.IDLE
    assert sorted(p.name for p in (tmp_path / "state").iterdir()) == ["cuenta1.json"]


def test_cooldown_remaining_counts_down_and_then_stops(tmp_path):
    cfg = _make_config(tmp_path, quota_cooldown_seconds=1800)
    now = time.time()
    record_rate_limit(cfg.state_dir, "cuenta1", at=now - 300)
    record_rate_limit(cfg.state_dir, "cuenta2", at=now - 4000)

    by_name = {r.name: r for r in operator_mod.account_reports(cfg, now=now)}

    assert by_name["cuenta1"].cooldown_remaining == pytest.approx(1500)
    # Elapsed, not negative: an old refusal is history, not a debt.
    assert by_name["cuenta2"].cooldown_remaining == 0.0
    assert by_name["cuenta2"].rate_limited_at == pytest.approx(now - 4000)


def test_probe_reports_usage_without_recording_anything(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path)
    fake = _FakeExec({
        "agent-cuenta1": _usage_result(_CLEAN_USAGE),
        "agent-cuenta2": _usage_result("Current session: 97% used\nCurrent week: 99% used"),
    })
    monkeypatch.setattr(operator_mod.docker_exec, "exec_claude", fake)

    by_name = {r.name: r for r in operator_mod.account_reports(cfg, probe=True)}

    assert by_name["cuenta1"].usage.session_pct == 12
    assert by_name["cuenta1"].usage.week_pct == 30
    assert by_name["cuenta1"].probe_error is None
    assert by_name["cuenta2"].usage.session_pct == 97
    # Over the threshold and still IDLE: reporting is not parking.
    assert get_state(cfg.state_dir, "cuenta2") is AccountState.IDLE
    assert not (tmp_path / "state").exists()
    assert [c[0] for c in fake.calls] == ["agent-cuenta1", "agent-cuenta2"]
    assert fake.calls[0][2] == "/usage"
    assert fake.calls[0][3]["timeout_seconds"] == operator_mod._USAGE_PROBE_TIMEOUT_SECONDS


def test_probe_reports_a_refusal_but_does_not_record_it(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path)
    refusal = _refusal_result()
    monkeypatch.setattr(
        operator_mod.docker_exec,
        "exec_claude",
        _FakeExec({"agent-cuenta1": refusal, "agent-cuenta2": _usage_result(_CLEAN_USAGE)}),
    )

    by_name = {r.name: r for r in operator_mod.account_reports(cfg, probe=True)}

    assert by_name["cuenta1"].probe_refused is True
    assert by_name["cuenta1"].usage is None
    # record_rate_limit is how a refusal becomes a fact the dispatcher acts
    # on, and status must never be the thing that creates one.
    assert get_rate_limited_at(cfg.state_dir, "cuenta1") is None


def test_probe_survives_a_container_that_is_not_there(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        operator_mod.docker_exec,
        "exec_claude",
        _FakeExec({
            "agent-cuenta1": RuntimeError("No such container: agent-cuenta1"),
            "agent-cuenta2": _usage_result("not a usage report at all"),
        }),
    )

    by_name = {r.name: r for r in operator_mod.account_reports(cfg, probe=True)}

    assert "No such container" in by_name["cuenta1"].probe_error
    # Unparseable output is a reading too, not a crash: the operator is
    # looking at a broken container and needs to be told which one.
    assert by_name["cuenta2"].probe_error is not None
    assert by_name["cuenta2"].usage is None


def test_task_reports_show_owned_cards_whatever_their_status(tmp_path):
    cfg = _make_config(tmp_path, heartbeat_ttl_seconds=120)
    _write_task(cfg, "T-001", status="in_progress", owner="cuenta1", heartbeat_age=10)
    _write_task(cfg, "T-002", status="in_progress", owner="cuenta2", heartbeat_age=9999)
    # Owned but not in_progress: exactly what a crash between the handoff and
    # the release leaves, and the row most worth seeing.
    _write_task(cfg, "T-003", status="pending", owner="cuenta1", heartbeat_age=None)
    _write_task(cfg, "T-004", status="done", owner=None)

    by_id = {t.task_id: t for t in operator_mod.task_reports(cfg)}

    assert sorted(by_id) == ["T-001", "T-002", "T-003"]
    assert by_id["T-001"].lock_live is True
    assert by_id["T-002"].lock_live is False
    # No heartbeat is not a live lock, which is the opposite of what
    # is_lock_expired answers for the same card.
    assert by_id["T-003"].lock_live is False
    assert by_id["T-003"].age_seconds is None


def test_task_reports_skip_a_card_they_cannot_parse(tmp_path):
    cfg = _make_config(tmp_path)
    _write_task(cfg, "T-001", owner="cuenta1", heartbeat_age=5)
    (tmp_path / "hive" / "broken.md").write_text("no frontmatter here\n")

    assert [t.task_id for t in operator_mod.task_reports(cfg)] == ["T-001"]


def test_format_status_names_the_state_the_task_and_the_quota(tmp_path):
    cfg = _make_config(tmp_path)
    set_state(cfg.state_dir, "cuenta1", AccountState.BUSY, "T-008")
    record_rate_limit(cfg.state_dir, "cuenta2", at=time.time() - 60)
    set_state(cfg.state_dir, "cuenta2", AccountState.COOLING_DOWN)
    _write_task(cfg, "T-008", owner="cuenta1", heartbeat_age=9999)

    text = operator_mod.format_status(
        cfg, operator_mod.account_reports(cfg), operator_mod.task_reports(cfg)
    )

    assert "cuenta1" in text and "BUSY" in text and "on T-008" in text
    assert "COOLING_DOWN" in text and "of cooldown left" in text
    assert "T-008" in text and "STALE" in text
    assert "--probe" in text  # says how to get the part it did not fetch


def test_format_status_calls_an_unheld_card_free_not_stale(tmp_path):
    # After a release the card is still in progress and still worth listing,
    # but there is no lock on it. STALE would send somebody looking for a
    # process that was already let go.
    cfg = _make_config(tmp_path)
    _write_task(cfg, "T-008", owner="cuenta1", heartbeat_age=9999)
    operator_mod.release_account(cfg, "cuenta1", force=True)

    text = operator_mod.format_status(cfg, [], operator_mod.task_reports(cfg))

    assert "T-008" in text and "lock free" in text
    assert "STALE" not in text


def test_format_status_says_so_when_there_is_nothing_to_show(tmp_path):
    cfg = _make_config(tmp_path, accounts=[])

    text = operator_mod.format_status(cfg, operator_mod.account_reports(cfg), [])

    assert "(no accounts configured)" in text
    assert "(none in progress)" in text


# --- release-account --------------------------------------------------------


def test_release_unsticks_a_busy_account_and_its_stale_card(tmp_path):
    cfg = _make_config(tmp_path, heartbeat_ttl_seconds=120)
    set_state(cfg.state_dir, "cuenta1", AccountState.BUSY, "T-008")
    _write_task(cfg, "T-008", owner="cuenta1", heartbeat_age=9999)

    outcome = operator_mod.release_account(cfg, "cuenta1")

    assert outcome.refused is False
    assert outcome.lock_released is True
    assert "BUSY -> IDLE" in outcome.detail and "T-008" in outcome.detail
    assert get_state(cfg.state_dir, "cuenta1") is AccountState.IDLE
    card = context_transfer.read_task_file(
        context_transfer.task_file_path(cfg.hive_tasks_dir, "T-008")
    )
    assert card.owner is None and card.heartbeat is None
    # The status is the task's business, not the account's: a released lock
    # does not mean the work was abandoned.
    assert card.status == "in_progress"


def test_release_refuses_while_the_lock_is_live(tmp_path):
    cfg = _make_config(tmp_path, heartbeat_ttl_seconds=120)
    set_state(cfg.state_dir, "cuenta1", AccountState.BUSY, "T-008")
    _write_task(cfg, "T-008", owner="cuenta1", heartbeat_age=10)

    outcome = operator_mod.release_account(cfg, "cuenta1")

    assert outcome.refused is True
    assert "live lock" in outcome.detail and "T-008" in outcome.detail
    assert "--force" in outcome.detail
    assert get_state(cfg.state_dir, "cuenta1") is AccountState.BUSY


def test_force_releases_over_a_live_lock(tmp_path):
    cfg = _make_config(tmp_path, heartbeat_ttl_seconds=120)
    set_state(cfg.state_dir, "cuenta1", AccountState.BUSY, "T-008")
    _write_task(cfg, "T-008", owner="cuenta1", heartbeat_age=10)

    outcome = operator_mod.release_account(cfg, "cuenta1", force=True)

    assert outcome.refused is False
    assert get_state(cfg.state_dir, "cuenta1") is AccountState.IDLE
    card = context_transfer.read_task_file(
        context_transfer.task_file_path(cfg.hive_tasks_dir, "T-008")
    )
    assert card.owner is None


def test_release_refuses_inside_the_refusal_cooldown(tmp_path):
    cfg = _make_config(tmp_path, quota_cooldown_seconds=1800)
    now = time.time()
    record_rate_limit(cfg.state_dir, "cuenta1", at=now - 300)
    set_state(cfg.state_dir, "cuenta1", AccountState.COOLING_DOWN)

    outcome = operator_mod.release_account(cfg, "cuenta1", now=now)

    assert outcome.refused is True
    assert "refused by the service" in outcome.detail
    assert "--clear-rate-limit" in outcome.detail
    assert get_state(cfg.state_dir, "cuenta1") is AccountState.COOLING_DOWN


def test_release_goes_ahead_once_the_cooldown_has_elapsed(tmp_path):
    cfg = _make_config(tmp_path, quota_cooldown_seconds=1800)
    now = time.time()
    record_rate_limit(cfg.state_dir, "cuenta1", at=now - 4000)
    set_state(cfg.state_dir, "cuenta1", AccountState.COOLING_DOWN)

    outcome = operator_mod.release_account(cfg, "cuenta1", now=now)

    assert outcome.refused is False
    assert get_state(cfg.state_dir, "cuenta1") is AccountState.IDLE
    # Kept, not erased: an elapsed refusal is harmless history, and only
    # --clear-rate-limit is allowed to destroy evidence.
    assert get_rate_limited_at(cfg.state_dir, "cuenta1") == pytest.approx(now - 4000)
    assert "was kept" in outcome.detail


def test_clear_rate_limit_forgets_the_refusal(tmp_path):
    cfg = _make_config(tmp_path)
    now = time.time()
    record_rate_limit(cfg.state_dir, "cuenta1", at=now - 300)
    set_state(cfg.state_dir, "cuenta1", AccountState.COOLING_DOWN)

    outcome = operator_mod.release_account(cfg, "cuenta1", clear_rate_limit=True, now=now)

    assert outcome.refused is False
    assert get_rate_limited_at(cfg.state_dir, "cuenta1") is None
    assert get_state(cfg.state_dir, "cuenta1") is AccountState.IDLE


def test_force_releases_over_the_cooldown_but_keeps_the_refusal(tmp_path):
    cfg = _make_config(tmp_path)
    now = time.time()
    record_rate_limit(cfg.state_dir, "cuenta1", at=now - 300)
    set_state(cfg.state_dir, "cuenta1", AccountState.COOLING_DOWN)

    outcome = operator_mod.release_account(cfg, "cuenta1", force=True, now=now)

    assert outcome.refused is False
    assert get_state(cfg.state_dir, "cuenta1") is AccountState.IDLE
    assert get_rate_limited_at(cfg.state_dir, "cuenta1") == pytest.approx(now - 300)


def test_release_finds_a_card_the_state_file_never_named(tmp_path):
    # The state file is written before the lock is taken and after it is
    # dropped, so a crash in between leaves the two disagreeing. Both are
    # consulted, or the card stays locked against the next cycle.
    cfg = _make_config(tmp_path)
    set_state(cfg.state_dir, "cuenta1", AccountState.BUSY, None)
    _write_task(cfg, "T-009", owner="cuenta1", heartbeat_age=9999)

    outcome = operator_mod.release_account(cfg, "cuenta1")

    assert "T-009" in outcome.detail
    card = context_transfer.read_task_file(
        context_transfer.task_file_path(cfg.hive_tasks_dir, "T-009")
    )
    assert card.owner is None


def test_release_leaves_another_accounts_card_alone(tmp_path):
    cfg = _make_config(tmp_path)
    set_state(cfg.state_dir, "cuenta1", AccountState.BUSY, "T-008")
    _write_task(cfg, "T-008", owner="cuenta1", heartbeat_age=9999)
    _write_task(cfg, "T-010", owner="cuenta2", heartbeat_age=9999)

    operator_mod.release_account(cfg, "cuenta1")

    other = context_transfer.read_task_file(
        context_transfer.task_file_path(cfg.hive_tasks_dir, "T-010")
    )
    assert other.owner == "cuenta2"
    assert get_state(cfg.state_dir, "cuenta2") is AccountState.IDLE


def test_release_tolerates_a_state_file_naming_a_card_that_is_gone(tmp_path):
    cfg = _make_config(tmp_path)
    set_state(cfg.state_dir, "cuenta1", AccountState.BUSY, "T-404")

    outcome = operator_mod.release_account(cfg, "cuenta1")

    assert outcome.refused is False
    assert get_state(cfg.state_dir, "cuenta1") is AccountState.IDLE


def test_releasing_an_idle_account_is_a_no_op_not_a_refusal(tmp_path):
    cfg = _make_config(tmp_path)

    outcome = operator_mod.release_account(cfg, "cuenta1")

    assert outcome.refused is False
    assert outcome.lock_released is False
    assert "already IDLE" in outcome.detail


def test_an_idle_account_still_gets_its_orphaned_card_back(tmp_path):
    cfg = _make_config(tmp_path)
    _write_task(cfg, "T-011", owner="cuenta1", heartbeat_age=9999)

    outcome = operator_mod.release_account(cfg, "cuenta1")

    assert "T-011" in outcome.detail
    card = context_transfer.read_task_file(
        context_transfer.task_file_path(cfg.hive_tasks_dir, "T-011")
    )
    assert card.owner is None


def test_release_rejects_an_account_the_config_does_not_have(tmp_path):
    cfg = _make_config(tmp_path)

    with pytest.raises(ValueError) as exc:
        operator_mod.release_account(cfg, "cuenta7")

    # Names what is configured: the likeliest cause is a typo, and the fix is
    # one line away in the message.
    assert "cuenta7" in str(exc.value)
    assert "cuenta1" in str(exc.value) and "cuenta2" in str(exc.value)


def test_a_released_account_is_pickable_again(tmp_path):
    # The point of the whole verb, stated against the function that could not
    # see the account before.
    from dispatcher.state_machine import list_idle_accounts

    cfg = _make_config(tmp_path)
    set_state(cfg.state_dir, "cuenta1", AccountState.BUSY, "T-008")
    _write_task(cfg, "T-008", owner="cuenta1", heartbeat_age=9999)
    assert list_idle_accounts(cfg.state_dir, cfg.accounts) == ["cuenta2"]

    operator_mod.release_account(cfg, "cuenta1")

    assert list_idle_accounts(cfg.state_dir, cfg.accounts) == ["cuenta1", "cuenta2"]
