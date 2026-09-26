"""The verbs a human runs between cycles, with no model in the loop.

Everything in `dispatcher.py` runs *inside* a task: it picks an account,
holds it, and hands it back when the phase returns. That leaves one hole,
and a host reboot on 2026-09-25 fell straight into it — an account parked
`BUSY` by a cycle that never returned stays `BUSY` forever, because
`list_idle_accounts` only ever answers `IDLE` and the only reaper in the
codebase, `reap_expired_locks`, is called from inside `run_task_cycle`, the
very thing that can no longer start. Two accounts, one of them stuck, and
`pick_idle_account` returns None for every task from then on.

So these two functions are deliberately not part of the cycle: `status`
reads the same state the cycle reads and writes nothing, and
`release_account` is the one way back to `IDLE` that does not require a
dispatcher to be running. Both are pure state and docker-exec; neither
spends a turn.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import time

from dispatcher import context_transfer, docker_exec, quota, state_machine
from dispatcher.config import AccountConfig, Config
from dispatcher.state_machine import AccountState

# One probe budget for the harness, not two: `status --probe` runs the same
# command the quota gate runs, so it inherits the gate's timeout rather than
# growing a second number that can drift away from it.
from dispatcher.dispatcher import _USAGE_PROBE_TIMEOUT_SECONDS, is_rate_limit_error


@dataclasses.dataclass
class AccountReport:
    """One account as the state directory currently describes it."""

    name: str
    container: str
    state: AccountState
    current_task_id: str | None
    rate_limited_at: float | None
    #: Seconds of `quota_cooldown_seconds` still to run on the recorded
    #: refusal; 0.0 when there is none or it has already elapsed.
    cooldown_remaining: float
    #: Only filled by an explicit probe, which is the only part of this
    #: report that leaves the process.
    usage: quota.UsageInfo | None = None
    probe_error: str | None = None
    probe_refused: bool = False


@dataclasses.dataclass
class TaskReport:
    """One task card that is holding, or should be holding, a lock."""

    task_id: str
    status: str
    owner: str | None
    heartbeat: str | None
    age_seconds: float | None
    lock_live: bool


@dataclasses.dataclass
class ReleaseOutcome:
    refused: bool
    detail: str
    #: True when the account's task card was unlocked as part of the release.
    lock_released: bool = False


def _account(cfg: Config, name: str) -> AccountConfig:
    for acc in cfg.accounts:
        if acc.name == name:
            return acc
    known = ", ".join(a.name for a in cfg.accounts) or "(none configured)"
    raise ValueError(f"unknown account {name!r}; config.yaml has: {known}")


def _cooldown_remaining(rate_limited_at: float | None, cooldown_seconds: int, now: float) -> float:
    if rate_limited_at is None:
        return 0.0
    return max(0.0, rate_limited_at + cooldown_seconds - now)


def _heartbeat_age(task: context_transfer.TaskFile, now: dt.datetime) -> float | None:
    if task.heartbeat is None:
        return None
    try:
        stamp = dt.datetime.fromisoformat(task.heartbeat)
    except ValueError:
        # `status` is the command you run when the harness is already wrong.
        # A heartbeat nobody can read is reported as no heartbeat; it must not
        # be the reason the whole listing refuses to print.
        return None
    return (now - stamp).total_seconds()


def _lock_is_live(task: context_transfer.TaskFile, ttl_seconds: int, now: dt.datetime) -> bool:
    """Is somebody demonstrably still holding this card?

    A missing heartbeat is not a live lock, which is the opposite of what
    `is_lock_expired` answers for the same input — it returns False there
    because there is no timestamp to compare. `acquire_lock` already treats
    that case as takeable, and so does this.
    """
    if task.heartbeat is None:
        return False
    try:
        return not context_transfer.is_lock_expired(task, ttl_seconds, now=now)
    except (ValueError, TypeError):
        return False


def _read_task(cfg: Config, task_id: str) -> context_transfer.TaskFile | None:
    path = context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)
    try:
        return context_transfer.read_task_file(path)
    except (OSError, ValueError, KeyError):
        # Missing, truncated, or missing its frontmatter. A card this command
        # cannot parse is a card it must not act on, and saying so is the
        # status output's job, not an exception out of a read helper.
        return None


def _held_task_ids(cfg: Config, account: str) -> list[str]:
    """Every card this account could be holding, from both places that know.

    The account's own state file names one (`current_task_id`) and the cards
    name their holder (`owner=account`, dispatcher.py:830). After a crash the
    two can disagree — the state file is written before the lock is taken and
    after it is dropped — so a release that trusted only one of them would
    leave the other behind.
    """
    held = []
    current = state_machine.get_current_task(cfg.state_dir, account)
    if current:
        held.append(current)
    for task_id in context_transfer.list_task_ids(cfg.hive_tasks_dir):
        if task_id in held:
            continue
        task = _read_task(cfg, task_id)
        if task is not None and task.owner == account:
            held.append(task_id)
    return held


def account_reports(cfg: Config, probe: bool = False, now: float | None = None) -> list[AccountReport]:
    """Every account's state, in the order config.yaml lists them.

    Read-only by construction, and that is the whole point of not reusing
    `check_quota_ok`: that function parks accounts PRE_COOLDOWN and records
    refusals as a side effect of being asked a question. An operator looking
    at the pool must not change it by looking.
    """
    now = time.time() if now is None else now
    reports = []
    for acc in cfg.accounts:
        rate_limited_at = state_machine.get_rate_limited_at(cfg.state_dir, acc.name)
        report = AccountReport(
            name=acc.name,
            container=acc.container,
            state=state_machine.get_state(cfg.state_dir, acc.name),
            current_task_id=state_machine.get_current_task(cfg.state_dir, acc.name),
            rate_limited_at=rate_limited_at,
            cooldown_remaining=_cooldown_remaining(
                rate_limited_at, cfg.quota_cooldown_seconds, now
            ),
        )
        if probe:
            _probe_into(cfg, report)
        reports.append(report)
    return reports


def _probe_into(cfg: Config, report: AccountReport) -> None:
    try:
        result = docker_exec.exec_claude(
            report.container,
            cfg.projects_root,
            "/usage",
            timeout_seconds=_USAGE_PROBE_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # the container may be down; that is a reading too
        report.probe_error = str(exc)
        return
    if is_rate_limit_error(result):
        # Reported, not recorded. record_rate_limit is how a refusal becomes
        # a fact the dispatcher acts on, and a status command has no business
        # creating one.
        report.probe_refused = True
        return
    try:
        report.usage = quota.parse_usage_output(result.result_text)
    except Exception as exc:
        report.probe_error = str(exc)


def task_reports(cfg: Config, now: dt.datetime | None = None) -> list[TaskReport]:
    """The cards that are in progress or owned, whichever they are.

    An owned card whose status is not `in_progress` is exactly the shape a
    crash leaves behind, so filtering on status alone would hide the rows
    worth looking at.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    reports = []
    for task_id in sorted(context_transfer.list_task_ids(cfg.hive_tasks_dir)):
        task = _read_task(cfg, task_id)
        if task is None or (task.status != "in_progress" and task.owner is None):
            continue
        reports.append(
            TaskReport(
                task_id=task_id,
                status=task.status,
                owner=task.owner,
                heartbeat=task.heartbeat,
                age_seconds=_heartbeat_age(task, now),
                lock_live=_lock_is_live(task, cfg.heartbeat_ttl_seconds, now),
            )
        )
    return reports


def release_account(
    cfg: Config,
    name: str,
    force: bool = False,
    clear_rate_limit: bool = False,
    now: float | None = None,
) -> ReleaseOutcome:
    """Hand one account back to the pool, and say when that would be wrong.

    Two guards, because there are two ways this command can do damage and
    they need different answers:

    - A live lock means a dispatcher is still running the phase. Releasing
      then does not recover an account, it hands the same account to a second
      phase — one `docker exec` into one container, two writers.
    - A refusal recorded inside `quota_cooldown_seconds` is the only evidence
      the harness has that the *service* turned this account away; the local
      counters cannot see it (state_machine.record_rate_limit). Releasing over
      it queues the account straight back into a 429.

    `--force` overrides both. `--clear-rate-limit` answers only the second,
    and does it by forgetting the refusal, which is a separate decision from
    unsticking a crashed BUSY and so is a separate flag.
    """
    account = _account(cfg, name)
    now = time.time() if now is None else now
    now_dt = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    state = state_machine.get_state(cfg.state_dir, name)
    rate_limited_at = state_machine.get_rate_limited_at(cfg.state_dir, name)
    cooldown_left = _cooldown_remaining(rate_limited_at, cfg.quota_cooldown_seconds, now)

    held = _held_task_ids(cfg, name)
    live = []
    stale = []
    for task_id in held:
        task = _read_task(cfg, task_id)
        if task is None:
            continue  # the state file names a card that is not there any more
        (live if _lock_is_live(task, cfg.heartbeat_ttl_seconds, now_dt) else stale).append(task_id)

    refusals = []
    if live:
        refusals.append(
            f"{name} holds a live lock on {', '.join(live)}: the heartbeat was refreshed "
            f"inside the {cfg.heartbeat_ttl_seconds}s TTL, so a dispatcher is still running "
            "that phase. Releasing now would let a second phase run in the same container. "
            "Wait for the TTL to lapse, or pass --force if you know that process is gone."
        )
    if cooldown_left > 0 and not clear_rate_limit:
        refusals.append(
            f"{name} was refused by the service {_duration(now - rate_limited_at)} ago and "
            f"{_duration(cooldown_left)} of its {cfg.quota_cooldown_seconds}s cooldown is left. "
            "That refusal is the one thing /usage cannot see, so it outranks the counters. "
            "Pass --clear-rate-limit to forget it, or --force to release over it and keep it."
        )
    if refusals and not force:
        return ReleaseOutcome(refused=True, detail="\n".join(refusals))

    if state is AccountState.IDLE and not stale and not (clear_rate_limit and rate_limited_at):
        return ReleaseOutcome(refused=False, detail=f"{name} is already IDLE; nothing to release.")

    lines = []
    for task_id in stale + (live if force else []):
        # The card, not just the account: nothing outside run_task_cycle
        # reaps these (reap_expired_locks is called from there and nowhere
        # else), so an account released without its card leaves the task
        # unstartable for the next cycle.
        context_transfer.release_stale_lock(cfg.hive_tasks_dir, task_id)
        lines.append(f"unlocked task {task_id} (owner and heartbeat cleared; status untouched)")
    if clear_rate_limit and rate_limited_at is not None:
        state_machine.clear_rate_limit(cfg.state_dir, name)
        lines.append(f"forgot the refusal recorded {_duration(now - rate_limited_at)} ago")
    state_machine.set_state(cfg.state_dir, name, AccountState.IDLE)
    lines.insert(0, f"{name}: {state.value} -> IDLE ({account.container})")
    if rate_limited_at is not None and not clear_rate_limit:
        lines.append(
            "the recorded refusal was kept: it is history now, but a re-park will "
            "still honour what is left of its cooldown."
        )
    return ReleaseOutcome(refused=False, detail="\n".join(lines), lock_released=bool(lines[1:]))


def _duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def format_status(cfg: Config, accounts: list[AccountReport], tasks: list[TaskReport]) -> str:
    """The whole pool as one screen, because that is the question being asked.

    No table helper and no column maths beyond a pad: this is read by a human
    at a terminal that may be narrow, and a wrapped table is worse than a
    ragged one.
    """
    out = [f"accounts ({cfg.state_dir})"]
    width = max((len(a.name) for a in accounts), default=0)
    for acc in accounts:
        notes = []
        if acc.current_task_id:
            notes.append(f"on {acc.current_task_id}")
        if acc.cooldown_remaining > 0:
            notes.append(f"refused, {_duration(acc.cooldown_remaining)} of cooldown left")
        elif acc.rate_limited_at is not None:
            notes.append("refused once, cooldown elapsed")
        if acc.probe_refused:
            notes.append("probe REFUSED by the service")
        elif acc.usage is not None:
            notes.append(
                f"session {acc.usage.session_pct}% · week {acc.usage.week_pct}% "
                f"(threshold {cfg.quota_threshold_pct}%)"
            )
        elif acc.probe_error is not None:
            notes.append(f"probe failed: {acc.probe_error}")
        out.append(
            f"  {acc.name:<{width}}  {acc.state.value:<13} {'; '.join(notes) or '-'}"
        )
    if not accounts:
        out.append("  (no accounts configured)")

    out.append("")
    out.append(f"tasks held or in progress ({cfg.hive_tasks_dir})")
    for task in tasks:
        age = "never" if task.age_seconds is None else f"{_duration(task.age_seconds)} ago"
        if task.lock_live:
            lock = "LIVE"
        elif task.owner is None and task.heartbeat is None:
            # A card nobody holds is not a stale lock, it is no lock. Saying
            # STALE here would send an operator looking for a process that was
            # already released.
            lock = "free"
        else:
            lock = "STALE"
        out.append(
            f"  {task.task_id}  {task.status}  owner {task.owner or '-'}  "
            f"heartbeat {age}  lock {lock}"
        )
    if not tasks:
        out.append("  (none in progress)")

    if any(not a.probe_refused and a.usage is None and a.probe_error is None for a in accounts):
        out.append("")
        out.append("state only; --probe also asks each container's /usage (nothing is written).")
    return "\n".join(out)
