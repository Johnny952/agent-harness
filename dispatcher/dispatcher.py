# dispatcher/dispatcher.py
from __future__ import annotations

import dataclasses
import threading

from dispatcher import context_transfer, docker_exec, quota, state_machine
from dispatcher.config import Config
from dispatcher.state_machine import AccountState
from dispatcher.vibe_kanban_client import VibeKanbanClient

ROLE_SEQUENCE = ["arquitecto", "implementador", "revisor", "auditor"]


@dataclasses.dataclass
class DispatchResult:
    success: bool
    session_id: str | None
    result_text: str
    account: str


class _HeartbeatLoop:
    def __init__(self, hive_dir: str, task_id: str, interval_seconds: int):
        self._hive_dir = hive_dir
        self._task_id = task_id
        self._interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                context_transfer.refresh_heartbeat(self._hive_dir, self._task_id)
            except Exception:
                # A single transient I/O hiccup must not permanently kill the
                # loop — that would silently stop heartbeats for the rest of
                # a long-running phase while it's still legitimately active.
                pass

    def __enter__(self) -> "_HeartbeatLoop":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()


def _container_for(cfg: Config, account: str) -> str:
    for acc in cfg.accounts:
        if acc.name == account:
            return acc.container
    raise ValueError(f"Unknown account: {account}")


def pick_idle_account(cfg: Config) -> str | None:
    idle = state_machine.list_idle_accounts(cfg.state_dir, cfg.accounts)
    return idle[0] if idle else None


def check_quota_ok(cfg: Config, account: str) -> bool:
    container = _container_for(cfg, account)
    result = docker_exec.exec_claude(container, cfg.projects_root, "/usage")
    usage = quota.parse_usage_output(result.result_text)
    if quota.exceeds_threshold(usage, cfg.quota_threshold_pct):
        state_machine.set_state(cfg.state_dir, account, AccountState.PRE_COOLDOWN)
        return False
    return True


def _recheck_cooling_accounts(cfg: Config) -> list[str]:
    # Free-text /usage reset timestamps (session_reset/week_reset) aren't
    # reliably parseable to an exact wake time (design spec sec. 4a caveat),
    # so recovery is re-check-on-dispatch rather than a scheduled expiry:
    # every account parked in PRE_COOLDOWN/COOLING_DOWN gets a fresh /usage
    # probe whenever no account is IDLE, and flips back to IDLE the moment
    # it clears the threshold. Without this, a account that ever crosses the
    # threshold stays dead for the life of the process.
    recovered = []
    for acc in cfg.accounts:
        state = state_machine.get_state(cfg.state_dir, acc.name)
        if state not in (AccountState.PRE_COOLDOWN, AccountState.COOLING_DOWN):
            continue
        container = _container_for(cfg, acc.name)
        result = docker_exec.exec_claude(container, cfg.projects_root, "/usage")
        usage = quota.parse_usage_output(result.result_text)
        if not quota.exceeds_threshold(usage, cfg.quota_threshold_pct):
            state_machine.set_state(cfg.state_dir, acc.name, AccountState.IDLE)
            recovered.append(acc.name)
    return recovered


def is_rate_limit_error(result: docker_exec.ClaudeResult) -> bool:
    # Exact reactive signal strings are unverified against the real Claude
    # Code CLI (design spec sec. 4a caveat) — match conservatively and
    # tighten once confirmed empirically.
    if not result.raw.get("is_error"):
        return False
    text = (result.result_text or "").lower()
    return any(term in text for term in ("rate limit", "usage limit", "quota"))


def _exec_succeeded(result: docker_exec.ClaudeResult) -> bool:
    # exec_claude sets raw={} whenever stdout was empty (a crash or a
    # stderr-only failure never produces the `--output-format json` object).
    # Without this positive check, that case fell through to an unconditional
    # success below, silently discarding the failure and defeating failover.
    if not result.raw:
        return False
    return not result.raw.get("is_error", False)


def reap_expired_locks(cfg: Config) -> list[str]:
    reaped = []
    for task_id in context_transfer.list_task_ids(cfg.hive_tasks_dir):
        task = context_transfer.read_task_file(context_transfer.task_file_path(cfg.hive_tasks_dir, task_id))
        if task.status == "in_progress" and context_transfer.is_lock_expired(task, cfg.heartbeat_ttl_seconds):
            context_transfer.release_stale_lock(cfg.hive_tasks_dir, task_id)
            reaped.append(task_id)
    return reaped


def dispatch_phase(
    cfg: Config,
    task_id: str,
    slug: str,
    role: str,
    prompt: str,
    resume_session_id: str | None = None,
) -> DispatchResult:
    tried: set[str] = set()
    while True:
        account = pick_idle_account(cfg)
        if account is None:
            if _recheck_cooling_accounts(cfg):
                continue
            return DispatchResult(success=False, session_id=resume_session_id, result_text="no accounts available", account="")
        if account in tried:
            return DispatchResult(success=False, session_id=resume_session_id, result_text="no accounts available", account="")
        tried.add(account)

        if not check_quota_ok(cfg, account):
            continue

        container = _container_for(cfg, account)
        state_machine.set_state(cfg.state_dir, account, AccountState.BUSY, current_task_id=task_id)
        lock_acquired = False
        try:
            workdir = docker_exec.create_worktree(container, cfg.projects_root, slug, task_id, role)
            context_transfer.acquire_lock(cfg.hive_tasks_dir, task_id, owner=account)
            lock_acquired = True
            with _HeartbeatLoop(cfg.hive_tasks_dir, task_id, cfg.heartbeat_interval_seconds):
                result = docker_exec.exec_claude(container, workdir, prompt, resume_session_id=resume_session_id)
        except Exception:
            # Never leave an account stuck BUSY (disk-persisted, survives
            # restart) because of an exception between claiming it and
            # returning — that permanently removes it from the pool.
            state_machine.set_state(cfg.state_dir, account, AccountState.IDLE)
            if lock_acquired:
                context_transfer.release_stale_lock(cfg.hive_tasks_dir, task_id)
            raise

        if is_rate_limit_error(result):
            state_machine.set_state(cfg.state_dir, account, AccountState.COOLING_DOWN)
            context_transfer.release_stale_lock(cfg.hive_tasks_dir, task_id)
            resume_session_id = result.session_id or resume_session_id
            continue

        if not _exec_succeeded(result):
            state_machine.set_state(cfg.state_dir, account, AccountState.IDLE)
            context_transfer.release_stale_lock(cfg.hive_tasks_dir, task_id)
            return DispatchResult(success=False, session_id=result.session_id, result_text=result.result_text, account=account)

        state_machine.set_state(cfg.state_dir, account, AccountState.IDLE)
        return DispatchResult(success=True, session_id=result.session_id, result_text=result.result_text, account=account)


def run_task_cycle(cfg: Config, task_id: str, slug: str, kanban: VibeKanbanClient) -> None:
    reap_expired_locks(cfg)
    task_file = context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)
    resume_session_id: str | None = None
    for role in ROLE_SEQUENCE:
        kanban.update_task_status(task_id, f"in_progress:{role}")
        result = dispatch_phase(
            cfg, task_id, slug, role,
            prompt=(
                f"Role: {role}. Task: {task_id}. "
                f"Read {task_file} for context handed off from the previous phase before starting."
            ),
            resume_session_id=resume_session_id,
        )
        if not result.success:
            kanban.update_task_status(task_id, "blocked")
            return
        context_transfer.handoff(
            cfg.hive_tasks_dir, task_id,
            new_status="done" if role == ROLE_SEQUENCE[-1] else "pending",
            body=f"## {role}\n\n{result.result_text[:2000]}",
        )
        resume_session_id = None
    kanban.update_task_status(task_id, "done")
