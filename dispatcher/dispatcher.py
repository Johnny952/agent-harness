# dispatcher/dispatcher.py
from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import enum
import logging
import os
import posixpath
import re
import threading
import time
from collections.abc import Callable

from dispatcher import (
    adr_numbers,
    allowlist,
    auto_approve,
    board_mcp,
    command_targets,
    context_transfer,
    debt,
    docker_exec,
    gates,
    handoff,
    learnings,
    project_docs,
    quota,
    role_skills,
    state_machine,
    subagents,
    surface,
    tasks,
)
from dispatcher.config import DRAFT_ROLE, Config
from dispatcher.state_machine import AccountState

logger = logging.getLogger(__name__)

# The /usage probe is a short, fixed-shape command, not user work — it never
# needs the (potentially very long) configured phase timeout, so it gets its
# own short fixed budget instead of cfg.phase_timeout_seconds.
_USAGE_PROBE_TIMEOUT_SECONDS = 120
#: The longest the probe can hold a claimed account: its own timeout plus the
#: backstop every `claude` call gets on the host.
_PROBE_WORST_CASE_SECONDS = _USAGE_PROBE_TIMEOUT_SECONDS + docker_exec.HOST_BACKSTOP_SECONDS


def _phase_permission_flags(cfg: Config, role: str, slug: str | None = None) -> dict:
    """What every call that runs a role's own work has to carry.

    Three flags, and a fourth for a role with board tools. The first two are
    the two halves of one measured failure
    (2026-09-23, 43 of 62 tool calls denied and no file written):

    `permission_mode`, because under `-p` with no mode and no SDK host there
    is nobody to answer a permission prompt, so the CLI denies instead of
    asking — Write and Edit inside the phase's own worktree included.

    `add_dirs`, because the file tools refuse every path outside the working
    directory, and the role prompt sends the phase to three places outside it:
    the task file holding the previous phase's handoff, the learnings index,
    and the inbox it is asked to file a trap in. All three are under the hive
    root, so the hive root is what gets opened — not the project tree, since
    reaching another phase's worktree is exactly what the worktrees prevent.

    `allowed_tools`, because the mode above stops short of running a program.
    The same run refused every `node --test` a phase attempted, so no phase
    could prove its own work, and a phase cannot lift that for itself: its
    own `.claude/settings.local.json` is refused too. It is empty unless the
    project's config names patterns, so this adds no flag by default.

    `mapping_allowed_tools` is added for the cartografo alone (ADR 115): its
    run-skill generator has to run the project's own build to say what works,
    and every other role keeps exactly the standing grant above.

    With `slug`, the project's allowlist for the role follows (ADR 121):
    the patterns an operator's *allow* verdict wrote, each checked again on
    the way in and skipped if it no longer passes, after the global ones and
    with no repeat. The global list is never changed; a call with no slug
    gets it alone.

    `mcp_config`, for a role that has board tools (ADR 128): the board
    service's server under `board_mcp_url` with `?role=<role>`, and its tool
    names appended to `allowed_tools`, last and with no repeat. Without the
    names the server would be listed and every call to it would prompt, which
    under `-p` is a denial. A role with no tools, or `board_mcp_url` off,
    gets neither, and the key is left out so the call is the one it was.

    The /usage probes do not get these: a probe runs a built-in command, uses
    no tools and touches no files.
    """
    base = (
        [*cfg.allowed_tools, *cfg.mapping_allowed_tools]
        if role == project_docs.MAPPER_ROLE else cfg.allowed_tools
    )
    tools = (
        allowlist.merged_allowed_tools(cfg.state_dir, slug, role, list(base))
        if slug else base
    )
    flags = {
        "permission_mode": cfg.permission_mode,
        "add_dirs": [context_transfer.hive_root(cfg.hive_tasks_dir)],
        "allowed_tools": tools,
    }
    board_tools = board_mcp.allowed_tools(role)
    if cfg.board_mcp_url and board_tools:
        flags["mcp_config"] = board_mcp.mcp_config(cfg.board_mcp_url, role)
        flags["allowed_tools"] = [*tools, *(t for t in board_tools if t not in tools)]
    return flags


@dataclasses.dataclass
class DispatchResult:
    success: bool
    session_id: str | None
    result_text: str
    account: str
    #: The phase's structured return, when it produced one. None means the
    #: role answered in prose and `result_text` is all there is.
    handoff: dict | None = None
    #: What the dispatcher checked itself about this phase's worktree. None
    #: for a phase that is not gated, or a gate run that did not finish.
    gates: gates.Report | None = None
    #: True when the phase never ran because no account could take it — every
    #: one tried, cooling or held back by its quota. Its own flag rather than
    #: read off `account == ""`, which a failure from anywhere else can share,
    #: because this is the one failure that is not the task's: it clears on
    #: its own when an account resets, and the CLI exits with a different code
    #: for it (ADR 52).
    no_account: bool = False
    #: What the CLI refused during the call whose handoff this is — the last
    #: call, since each retry replaces the result — reduced as the usage log
    #: reduces it: a list, `[]` for none, `None` for not reported. Left at
    #: the "absent" default by a phase that made no call, so `save_handoff`
    #: writes no key for it (`docs/decisions.md` ADR 116).
    permission_denials: object = context_transfer.PERMISSION_DENIALS_ABSENT
    #: The phase's command requests as `run_phase` checked and recorded them,
    #: `(accepted, refused)`, so the cycle blocks on the same lists the
    #: envelope holds. None for a role that cannot file one (ADR 118).
    command_requests: tuple[list[dict], list[dict]] | None = None


class CycleOutcome(enum.Enum):
    """How a `run-task` cycle ended, which is what its exit code says.

    Four and not two because "it did not finish" covers three things the
    operator does different things about: a task that is blocked needs a
    person, a task that is held for lack of an account needs only a wait,
    and a task that was split needs its sub-tasks run in its place. The CLI
    maps these to exit codes (ADR 52, ADR 60).
    """

    FINISHED = "finished"
    HELD = "held"
    BLOCKED = "blocked"
    SPLIT = "split"


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
                tasks.refresh_heartbeat(self._hive_dir, self._task_id)
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


def container_for(cfg: Config, account: str) -> str:
    for acc in cfg.accounts:
        if acc.name == account:
            return acc.container
    raise ValueError(f"Unknown account: {account}")


def _capacity_for(cfg: Config, account: str) -> int:
    for acc in cfg.accounts:
        if acc.name == account:
            return acc.max_concurrent_phases
    raise ValueError(f"Unknown account: {account}")


def reap_stale_busy_accounts(cfg: Config) -> list[str]:
    """Free accounts left BUSY by a phase that is no longer running.

    BUSY carries no expiry of its own, so a dispatcher that dies mid-phase — a
    Ctrl+C, a host reboot, an OOM — leaves its account BUSY for the life of the
    state file, and the pool is an account short with nothing running on it. A
    wall clock would be the wrong instrument on its own: a phase that is
    legitimately long is what `phase_timeout_seconds` is hours wide for, and
    expiring one would hand a second phase an account that is still working. So
    the judge is the card's heartbeat, which a running phase refreshes and a
    dead one stops refreshing — the same evidence `reap_expired_locks` reads,
    one level up. `busy_since` is only consulted for an account holding no card
    at all, or a card nothing ever beat on, where there is nothing else to read.

    Each holder of an account is judged on its own card and its own
    `busy_since`, and only a dead one is released: a live phase beside it keeps
    its slot, and a park stays as it is (`docs/decisions.md` ADR 88). An account
    is reported once however many of its holders were dead.
    """
    reaped = []
    now = time.time()
    for acc in cfg.accounts:
        dead = False
        for holder in state_machine.get_holders(cfg.state_dir, acc.name):
            busy_since = holder.busy_since
            task_id = holder.task_id
            if busy_since is None:
                # BUSY written before this stamp existed, or by hand. Start the
                # clock instead of reaping on no evidence: one TTL of patience
                # costs a dispatch, reaping a live phase costs the phase.
                state_machine.stamp_holder(cfg.state_dir, acc.name, task_id)
                continue
            # A floor under the heartbeat test, not a second opinion on it. A phase
            # stamps BUSY before it takes its card, and the card it is about to
            # take may still carry the previous phase's heartbeat, so the TTL alone
            # would let one dispatcher reap another's phase inside that window.
            # The claim now comes before the `/usage` probe, so the window opens
            # with the probe's worst case (`docs/decisions.md` ADR 71).
            if now - busy_since <= cfg.heartbeat_ttl_seconds + _PROBE_WORST_CASE_SECONDS:
                continue
            task = None
            if task_id:
                path = context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)
                if os.path.exists(path):
                    task = context_transfer.read_task_file(path)
            if task is not None and task.heartbeat is not None:
                if not context_transfer.is_lock_expired(task, cfg.heartbeat_ttl_seconds):
                    continue
                why = (
                    f"the heartbeat on card {task_id} is more than "
                    f"{cfg.heartbeat_ttl_seconds}s old"
                )
            else:
                # Nothing to read but the wall clock, so it is given the whole
                # phase timeout rather than the heartbeat TTL: past that the phase
                # is over one way or the other, since `timeout` kills it in the
                # container.
                if now - busy_since <= cfg.phase_timeout_seconds:
                    continue
                why = (
                    f"it has been BUSY for {int(now - busy_since)}s with no card to heartbeat, "
                    f"past the {cfg.phase_timeout_seconds}s phase timeout"
                )
            logger.warning(
                "account %s holds %s but %s; releasing it back to the pool",
                acc.name, task_id, why,
            )
            if state_machine.release(cfg.state_dir, acc.name, task_id):
                dead = True
        if dead:
            reaped.append(acc.name)
    return reaped


def _waitable_workers(
    cfg: Config, exclude: set[str] | None = None, now: float | None = None,
) -> list[str]:
    """The workers that will come back on their own, one phrase each.

    Parked by a counter and parked by a refusal are different, and this is the
    distinction that decides whether to fall back or simply wait. An account
    over the local threshold self-heals: `_recheck_cooling_accounts` re-probes
    every parked account whenever none is IDLE and flips it back the moment the
    numbers clear. An account the service itself refused inside
    `quota_cooldown_seconds` will not come back on a probe at all — /usage
    reads counters this machine wrote and cannot see a refusal — so there is
    nothing to wait for there. Waiting out a counter is free; falling back on a
    refusal is the real spend, and it is the only case the primary's reserve
    exists for.

    An aged-out COOLING_DOWN counts as waitable on purpose. Holding here
    returns None, which is what sends dispatch_phase into that very recheck,
    and recovering a worker is strictly better than spending the primary.
    """
    now = time.time() if now is None else now
    excluded = exclude or set()
    waitable = []
    for acc in cfg.accounts:
        # A worker in `exclude` has already been tried and failed within this
        # call; it is not something this call can wait for.
        if acc.is_primary or acc.name in excluded:
            continue
        state = state_machine.get_state(cfg.state_dir, acc.name)
        if state == AccountState.BUSY:
            waitable.append(f"{acc.name} is running a phase")
            continue
        if state not in (AccountState.PRE_COOLDOWN, AccountState.COOLING_DOWN):
            continue
        refused_at = state_machine.get_rate_limited_at(cfg.state_dir, acc.name)
        if refused_at is not None and now - refused_at < cfg.quota_cooldown_seconds:
            continue
        waitable.append(f"{acc.name} is {state.value} on a counter, not on a refusal")
    return waitable


def pick_idle_account(
    cfg: Config, exclude: set[str] | None = None, role: str | None = None,
) -> str | None:
    """The next account to try for a phase: workers first, the primary last.

    The pool is ordered, not partitioned. The primary is in it like any other
    account and simply ranks last, which is what lets "every worker is out of
    quota" need no special case — it is the ordering running off its end. What
    the gates below add is that running off the end is not free: the primary is
    the account the operator's own console runs on, and a fallback that spends
    it to the wall does not just stall the queue, it takes the thread that was
    supposed to decide what to do about the stall with it. So the last step
    asks two questions the earlier ones never have to: is this phase worth the
    console (`fallback_roles`), and is there a worker that comes back on its
    own if we simply wait (`_waitable_workers`).

    With no `primary_account` configured, or with no worker accounts to fall
    back from, none of that applies and this is the one-liner it used to be.
    """
    # Every path that wants an account comes through here, which is what makes
    # this the place the account-lock TTL is honoured. The reaper only reads
    # state files and is idempotent, so a pick costs a stat per account and a
    # dispatcher killed mid-phase no longer retires its account for good.
    reap_stale_busy_accounts(cfg)
    idle = state_machine.list_idle_accounts(cfg.state_dir, cfg.accounts)
    # A busy account with a free slot is available too, behind every account
    # that holds nothing; the sort is stable, so the primary still ranks last
    # (`docs/decisions.md` ADR 88). With every capacity at 1 `shared` is empty.
    shared = state_machine.list_shared_accounts(cfg.state_dir, cfg.accounts)
    by_name = {a.name: a for a in cfg.accounts}
    idle = sorted(idle + shared, key=lambda n: by_name[n].is_primary)
    if exclude:
        idle = [a for a in idle if a not in exclude]
    if not idle:
        return None
    primary = cfg.primary_account
    has_workers = any(not acc.is_primary for acc in cfg.accounts)
    # `idle` is primary-last, so anything else in front of it means a worker
    # takes the phase and no fallback decision arises at all.
    if primary is None or not has_workers or idle[0] != primary:
        return idle[0]
    if role is not None and role not in cfg.fallback_roles:
        logger.warning(
            "only the primary account %s is left and a %s phase is not in fallback_roles (%s); "
            "the pool is dry for this phase rather than spending the operator's own console on it",
            primary, role, ", ".join(cfg.fallback_roles) or "none",
        )
        return None
    waiting = _waitable_workers(cfg, exclude=exclude)
    if waiting:
        logger.warning(
            "only the primary account %s is left, but %s; holding instead of falling back, "
            "because waiting out a counter is free and the reserve is for refusals",
            primary, "; ".join(waiting),
        )
        return None
    logger.warning(
        "no worker account can come back on its own, so the %s phase falls back to the primary "
        "account %s, held to its %d%% session reserve rather than the %d%% worker threshold, "
        "and on its week to %s",
        role or "requested", primary, cfg.reserve_pct, cfg.quota_threshold_pct,
        # Named, not computed: this function holds no probe, and the paced
        # ceiling is only knowable from one (ADR 48).
        "a ceiling paced against the reset /usage reports" if cfg.pace_primary_week
        else "the same reserve",
    )
    return primary


def _threshold_for(cfg: Config, account: str) -> int:
    """The configured quota ceiling this account is held to.

    The primary's is stricter than a worker's, and every reader of it has to
    agree. The gate that parks an account above the line and the recheck that
    un-parks it below one are the same decision seen twice, so a recheck on the
    worker threshold would wave the primary straight back to IDLE the moment
    the gate parked it on the reserve, and the two would loop.

    Since `docs/decisions.md` ADR 48 neither of them reads this function
    directly: both go through `_quota_decision`, which is the only place in the
    module where a probe is compared with a ceiling, and that is what keeps
    them agreeing now that one of the ceilings moves. For the primary this
    number is its *session* ceiling and its week's fallback; its week's own
    ceiling is `quota.week_ceiling`'s and is not configured.
    """
    for acc in cfg.accounts:
        if acc.name == account:
            return cfg.reserve_pct if acc.is_primary else cfg.quota_threshold_pct
    return cfg.quota_threshold_pct


def _is_primary(cfg: Config, account: str) -> bool:
    """Whether this is the account `primary_account` names.

    By the pool's own mark, never by name: the primary is configuration
    (`docs/charter.md` C-3), so an account the pool cannot identify is not it.
    """
    return any(acc.name == account and acc.is_primary for acc in cfg.accounts)


def _utc_now() -> dt.datetime:
    """The clock the paced weekly ceiling is measured from.

    A function rather than an inline `datetime.now` so a test can replace it:
    the ceiling moves with the calendar, and no test may depend on the day it
    runs on.
    """
    return dt.datetime.now(dt.timezone.utc)


@dataclasses.dataclass(frozen=True)
class _QuotaDecision:
    """One probe measured against whatever ceilings govern its account."""

    exceeds: bool
    #: What the park log says the numbers were measured against, already
    #: carrying the ceiling, the days left and the reset when the week is paced.
    limit_phrase: str


def _quota_decision(cfg: Config, account: str, usage: quota.UsageInfo) -> _QuotaDecision:
    """The only place in this module a probe is compared with a ceiling.

    `check_quota_ok` and `_recheck_cooling_accounts` both come through here, so
    the gate and the recheck cannot drift apart — see `_threshold_for`. A
    worker, and the primary with `pace_primary_week` off, get the one
    configured number on both windows. A paced primary keeps `reserve_pct` on
    its session and answers to `quota.week_ceiling` on its week (ADR 48).

    The clock is `_utc_now` and not a parameter: a probe is always measured
    against the moment it was taken, and `_utc_now` is the one seam a test
    replaces, so a second injection point here would only be a way for the two
    readers to be handed different days.

    It is also the one place that records the probe, for the same reason: both
    readers pass through here, so neither can forget to (ADR 49). The record is
    written after the decision is made and cannot change it.
    """
    threshold = _threshold_for(cfg, account)
    now = _utc_now()
    if not cfg.pace_primary_week or not _is_primary(cfg, account):
        decision = _QuotaDecision(
            exceeds=quota.exceeds_threshold(usage, threshold),
            limit_phrase=f"the {threshold}% threshold",
        )
        week = {
            "paced": False, "pct": threshold, "days_left": None, "reset": None,
            "fallback_reason": None,
        }
        _record_probe(cfg, account, _probe_record(usage, now, decision, threshold, week))
        return decision
    ceiling = quota.week_ceiling(usage.week_reset, now, cfg.reserve_pct)
    exceeds = usage.session_pct >= threshold or usage.week_pct >= ceiling.pct
    week = {
        "paced": True,
        "pct": ceiling.pct,
        "days_left": ceiling.days_left,
        "reset": ceiling.reset.isoformat() if ceiling.reset is not None else None,
        "fallback_reason": ceiling.fallback_reason,
    }
    if ceiling.fallback_reason is not None:
        # Logged here rather than at the two call sites, so the gate and the
        # recheck both report it and neither has to repeat the sentence.
        logger.warning(
            "the primary account %s cannot be paced on this probe, so its week falls back to "
            "the %d%% reserve: %s",
            account, threshold, ceiling.fallback_reason,
        )
        decision = _QuotaDecision(
            exceeds=exceeds,
            limit_phrase=f"the {threshold}% reserve on both windows, because {ceiling.fallback_reason}",
        )
    else:
        decision = _QuotaDecision(
            exceeds=exceeds,
            limit_phrase=(
                f"the {threshold}% session reserve or the {ceiling.pct:.1f}% weekly ceiling "
                f"({ceiling.days_left:.1f} days to the "
                f"{ceiling.reset.strftime('%Y-%m-%d %H:%M %Z')} reset it read)"
            ),
        )
    _record_probe(cfg, account, _probe_record(usage, now, decision, threshold, week))
    return decision


def _probe_record(
    usage: quota.UsageInfo,
    now: dt.datetime,
    decision: _QuotaDecision,
    session_ceiling_pct: int,
    week_ceiling: dict,
) -> dict:
    """One probe as `state_machine.record_probe` keeps it and `/api/accounts`
    serves it (ADR 49).

    `probed_at` is epoch seconds, the unit `rate_limited_at` is already stored
    and served in, so the two stamps on one row compare without a parse. It is
    the moment the ceiling was computed from, not a second clock read, so a
    days-left on the record is exactly `reset - probed_at`. The reset clauses
    go in raw beside the parsed reset: a fallback is explained by the text that
    would not parse, and only the raw clause carries it.
    """
    return {
        "probed_at": now.timestamp(),
        "session_pct": usage.session_pct,
        "week_pct": usage.week_pct,
        "session_reset": usage.session_reset,
        "week_reset": usage.week_reset,
        "exceeds": decision.exceeds,
        "session_ceiling_pct": session_ceiling_pct,
        "week_ceiling": week_ceiling,
    }


def _record_probe(cfg: Config, account: str, record: dict) -> None:
    """Persist the probe, and never let doing so touch the decision it records.

    The record is for the console; the decision is what keeps an account off a
    wall. A full disk or a state file some other writer mangled must not turn
    into a crash of the gate, nor — through `_recheck_cooling_accounts`'s own
    fail-open `except` — into an account waved back to IDLE on a write error.
    So everything is caught here and logged, and the caller carries on.
    """
    try:
        state_machine.record_probe(cfg.state_dir, account, record)
    except Exception as exc:
        logger.warning(
            "could not record the quota probe for account %s: %s; the decision stands", account, exc,
        )


def _number_or_none(value: object) -> int | float | None:
    """A measured number, or nothing — never a coercion and never a default.

    `bool` is excluded explicitly because it is an `int` in Python, and a
    `num_turns` that came back `true` is a format drift rather than one turn.
    Anything else the CLI prints where a number belongs — a string, a list,
    `null` — is a key that no longer means what this harness thinks it means,
    and the honest record of that is `null` (`docs/decisions.md` ADR 53).
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


#: How many refused tool calls one usage line keeps, and how long any string
#: inside a kept `tool_input` may be. Both exist so a line stays a few KB at
#: most: `/api/tasks/<id>/usage` reads up to `MAX_USAGE_CALLS` lines whole,
#: and that bound was set for lines of scalars (`docs/decisions.md` ADR 116).
_MAX_PERMISSION_DENIALS = 50
_MAX_DENIAL_STRING = 500


def _cut_strings(value: object) -> object:
    """`value` with every string in it cut to `_MAX_DENIAL_STRING` characters.

    Walks objects and lists, because a `tool_input` is whatever the tool takes
    and a long string (a file body for Write, a script for Bash) can sit at any
    depth. Only values are cut, never object keys: keys are parameter names the
    tool defined, and they are short. The marker says how much went, so a cut
    value is never mistaken for the whole one (ADR 116).
    """
    if isinstance(value, str):
        if len(value) <= _MAX_DENIAL_STRING:
            return value
        cut = len(value) - _MAX_DENIAL_STRING
        return f"{value[:_MAX_DENIAL_STRING]}…[cut {cut} chars]"
    if isinstance(value, dict):
        return {key: _cut_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_cut_strings(item) for item in value]
    return value


def _reduce_denial(entry: object) -> dict:
    """One refused tool call as the log keeps it: three keys, or `malformed`.

    Only `tool_name`, `tool_use_id` and `tool_input` are kept, so a key the CLI
    adds later does not ride into the log unread. An entry that is not an
    object, or that names no tool, is recorded as what it was instead of being
    dropped, so a format drift shows on the page rather than as a shorter list
    (`_number_or_none`'s rule: never coerce, never hide).
    """
    if not isinstance(entry, dict) or not isinstance(entry.get("tool_name"), str):
        return {"malformed": type(entry).__name__}
    tool_use_id = entry.get("tool_use_id")
    return {
        "tool_name": entry["tool_name"],
        "tool_use_id": tool_use_id if isinstance(tool_use_id, str) else None,
        "tool_input": _cut_strings(entry.get("tool_input")),
    }


def _reduce_permission_denials(raw: dict) -> dict:
    """What the CLI said it refused, reduced for the log (ADR 116).

    `permission_denials` is `null` when the result has no such key or holds
    something other than a list, and `[]` when the CLI reported an empty list:
    "not reported" and "nothing was refused" are different facts, and a reader
    deciding whether a permission failed a phase needs to tell them apart.
    Past `_MAX_PERMISSION_DENIALS` entries the rest are counted, not kept, in
    `permission_denials_dropped`, which is present only when something was.
    """
    denials = raw.get("permission_denials")
    if not isinstance(denials, list):
        return {"permission_denials": None}
    fields: dict = {
        "permission_denials": [_reduce_denial(entry) for entry in denials[:_MAX_PERMISSION_DENIALS]],
    }
    if len(denials) > _MAX_PERMISSION_DENIALS:
        fields["permission_denials_dropped"] = len(denials) - _MAX_PERMISSION_DENIALS
    return fields


def _permission_denial_fields(raw: dict, task_id: str, role: str, call: str) -> dict:
    """`_reduce_permission_denials`, or `null` if building it raised.

    The rest of the line is worth more than this field: a usage line is what
    says a call happened and what it cost, so a result shaped in a way the
    reduction did not foresee loses only its denials, with a warning, and never
    the line or the phase (ADR 116).
    """
    try:
        return _reduce_permission_denials(raw)
    except Exception as exc:
        logger.warning(
            "task %s: could not read the permission denials of the %s phase's %s call: %s; "
            "recorded as null",
            task_id, role, call, exc,
        )
        return {"permission_denials": None}


def _handoff_denials(task_id: str, role: str, result: docker_exec.ClaudeResult) -> list | None:
    """The denials list for the handoff envelope, from the call that answered.

    Only the list: the envelope is one call's record and keeps the same
    null/[] rule as the usage line, which is where `permission_denials_dropped`
    lives for a call that hit the cap. Guarded like the usage line, so a result
    the reduction chokes on is written as `null` with a warning, never a phase
    that fails at its last step (ADR 116).
    """
    return _permission_denial_fields(result.raw, task_id, role, "handoff")["permission_denials"]


def _usage_record(
    task_id: str,
    role: str,
    round_num: int | None,
    account: str,
    call: str,
    model: str | None,
    result: docker_exec.ClaudeResult,
    now: dt.datetime,
) -> dict:
    """One `claude` call as the task's usage log keeps it (ADR 53, ADR 68).

    Twenty keys in two halves, plus one that only a long list adds. The first
    ten are the dispatcher's own and
    it always knows them; the rest keep the CLI's own spelling, so there is no
    translation table to maintain and a key the CLI renames shows up as a
    column that went all-null rather than as a number quietly meaning
    something else. `model` is what the dispatcher asked for (`null` when it
    passed none); `models_used` is the names the CLI's own `modelUsage` says
    ran, never its values, and `null` when it is absent or not an object.

    Pure, and `now` is a parameter rather than a second clock read: the module
    has one seam (`_utc_now`) and the caller reads it, which is the shape
    `_probe_record` already has. The stamp is ISO-8601 UTC like
    `save_handoff`'s `saved_at`, not `_probe_record`'s epoch seconds — this
    record sits beside that envelope, and nothing compares it with
    `rate_limited_at` (ADR 49).

    `measured` is false exactly when `raw` is empty, which is every way
    `exec_claude` can fail to parse a result object; then every measured field
    is `null`. That is a different fact from a call that cost nothing, and the
    distinction is the whole point: nothing here is summed, scaled, defaulted
    to zero or carried over from another call.

    `permission_denials` is what the CLI refused during the call, reduced by
    `_reduce_permission_denials`; `permission_denials_dropped` joins it only
    when there were more entries than a line keeps (ADR 116).
    """
    raw = result.raw
    usage = raw.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    is_error = raw.get("is_error")
    model_usage = raw.get("modelUsage")
    return {
        "task_id": task_id,
        "role": role,
        "round": round_num,
        "account": account,
        "call": call,
        "model": model if isinstance(model, str) else None,
        "session_id": result.session_id if isinstance(result.session_id, str) else None,
        "recorded_at": now.isoformat(),
        "measured": bool(raw),
        # Names only, never values: the first real run says on its own which
        # keys the CLI printed, without the live probe this task was refused.
        "raw_keys": sorted(raw),
        "models_used": sorted(model_usage) if isinstance(model_usage, dict) else None,
        "is_error": is_error if isinstance(is_error, bool) else None,
        "num_turns": _number_or_none(raw.get("num_turns")),
        "duration_ms": _number_or_none(raw.get("duration_ms")),
        "total_cost_usd": _number_or_none(raw.get("total_cost_usd")),
        "input_tokens": _number_or_none(usage.get("input_tokens")),
        "output_tokens": _number_or_none(usage.get("output_tokens")),
        "cache_read_input_tokens": _number_or_none(usage.get("cache_read_input_tokens")),
        "cache_creation_input_tokens": _number_or_none(usage.get("cache_creation_input_tokens")),
        **_permission_denial_fields(raw, task_id, role, call),
    }


@dataclasses.dataclass(frozen=True)
class _UsageRecorder:
    """The six things a usage line needs that are fixed for one attempt.

    Built once the account is picked and handed to the retry helpers, so each
    call site records its own result as the call that produced it without
    taking six more parameters to do it. Frozen because an attempt's account,
    round and model do not change under it — a failover builds a new one.
    """

    cfg: Config
    task_id: str
    role: str
    round_num: int | None
    account: str
    model: str | None

    def record(self, call: str, result: docker_exec.ClaudeResult) -> None:
        """Write the line, and never let doing so touch the phase.

        `_record_probe`'s contract, for `_record_probe`'s reason: this is
        bookkeeping on a path that may already be failing, and a full disk must
        not be what fails a phase or changes a decision. The warning names the
        task, the role and the call, because a missing line is otherwise
        indistinguishable from a call that was never made.
        """
        try:
            context_transfer.append_usage(
                self.cfg.hive_tasks_dir,
                self.task_id,
                _usage_record(
                    self.task_id, self.role, self.round_num, self.account, call,
                    self.model, result, _utc_now(),
                ),
            )
        except Exception as exc:
            logger.warning(
                "task %s: could not record what the %s phase's %s call cost: %s; "
                "the phase carries on",
                self.task_id, self.role, call, exc,
            )


def check_quota_ok(cfg: Config, account: str) -> bool:
    container = container_for(cfg, account)
    # The /usage probe parses free-text CLI output (design spec sec. 4a
    # caveat), so any drift in that format must not crash dispatch — fail
    # open here and let the reactive rate-limit handling in dispatch_phase
    # (is_rate_limit_error on the real phase call) be the fallback.
    try:
        result = docker_exec.exec_claude(
            container, cfg.projects_root, "/usage", timeout_seconds=_USAGE_PROBE_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning(
            "quota probe failed for account %s: %s; waving it through unverified", account, exc,
        )
        return True
    # Read the refusal before the numbers. /usage is a local slash command and
    # normally costs nothing, but if the CLI refuses even that, the refusal is
    # the one thing the probe can tell us that its own counters never could —
    # and parse_usage_output would raise on the error text, dropping it into
    # the fail-open below.
    if is_rate_limit_error(result):
        logger.warning(
            "account %s answered the quota probe with a rate-limit refusal; it goes COOLING_DOWN "
            "and the phase looks for another account",
            account,
        )
        state_machine.record_rate_limit(cfg.state_dir, account)
        state_machine.park(cfg.state_dir, account, AccountState.COOLING_DOWN)
        return False
    # Same reasoning, and it has to come before the parse for the same
    # reason: a rejected login is something a probe can actually report, and
    # waving it through unverified hands the phase to an account that cannot
    # run it.
    if is_auth_error(result):
        _park_auth_expired(cfg, account, "the quota probe")
        return False
    try:
        usage = quota.parse_usage_output(result.result_text)
    except Exception as exc:
        logger.warning(
            "quota probe for account %s returned an unreadable usage report: %s; "
            "waving it through unverified", account, exc,
        )
        return True
    decision = _quota_decision(cfg, account, usage)
    if decision.exceeds:
        logger.warning(
            "account %s is at %d%% of its session and %d%% of its week, over %s; "
            "parking it PRE_COOLDOWN and looking for another",
            account, usage.session_pct, usage.week_pct, decision.limit_phrase,
        )
        state_machine.park(cfg.state_dir, account, AccountState.PRE_COOLDOWN)
        return False
    return True


def _recheck_cooling_accounts(cfg: Config) -> list[str]:
    # No wake is ever booked off a /usage reset: recovery is
    # re-check-on-dispatch rather than a scheduled expiry, so every account
    # parked in PRE_COOLDOWN/COOLING_DOWN gets a fresh /usage probe whenever no
    # account is IDLE, and flips back to IDLE the moment it clears the ceiling
    # it answers to. Without this, an account that ever crosses that line stays
    # dead for the life of the process.
    #
    # The session's reset clause is still read by nothing — free text, no year,
    # and not worth an exact wake time (design spec sec. 4a caveat). The week's
    # is read on every probe since docs/decisions.md ADR 48, and that is the
    # second reason this loop is the right shape: a primary parked by the paced
    # weekly ceiling is released by the ceiling rising on one of these probes,
    # with no new scheduler.
    #
    # The exception is an account the service itself refused: that probe is
    # blind to a refusal, so a recorded one holds the account until the
    # cooldown has passed, whatever the local counters say.
    recovered = []
    now = time.time()
    for acc in cfg.accounts:
        state = state_machine.get_state(cfg.state_dir, acc.name)
        if state not in (AccountState.PRE_COOLDOWN, AccountState.COOLING_DOWN):
            continue
        # A refusal the service itself issued outranks the probe, for the
        # length of the cooldown. /usage reads counters this machine wrote, so
        # an account turned away a moment ago still reports healthy numbers
        # here — and recovering it on those numbers is exactly how a real 429
        # was thrown away: refused, parked, probed, IDLE again, refused again.
        # Holding it costs nothing; the alternative spends a phase to relearn
        # what the last one already found out.
        refused_at = state_machine.get_rate_limited_at(cfg.state_dir, acc.name)
        if refused_at is not None and now - refused_at < cfg.quota_cooldown_seconds:
            logger.warning(
                "account %s was refused %ds ago and stays %s: the /usage probe reads local "
                "counters and cannot see that refusal, so it waits out the %ds cooldown",
                acc.name, int(now - refused_at), state.value, cfg.quota_cooldown_seconds,
            )
            continue
        container = container_for(cfg, acc.name)
        # Same free-text format drift risk as check_quota_ok: a probe failure
        # here must not strand the account in COOLING_DOWN forever. Treat it
        # as recovered (fail open) rather than leaving the account stuck. It
        # can't loop: dispatch_phase picks each account at most once per call,
        # and only a pick can put an account back into cooling, so a
        # still-limited account just falls back to the reactive rate-limit
        # handling.
        try:
            result = docker_exec.exec_claude(
                container, cfg.projects_root, "/usage", timeout_seconds=_USAGE_PROBE_TIMEOUT_SECONDS,
            )
            # Same reason as in check_quota_ok, and here it also restarts the
            # clock: a probe that is itself refused is fresh evidence, so the
            # floor above holds the account on the next pass instead of asking
            # again immediately.
            if is_rate_limit_error(result):
                logger.warning(
                    "account %s is still being refused and stays %s", acc.name, state.value,
                )
                state_machine.record_rate_limit(cfg.state_dir, acc.name)
                continue
            usage = quota.parse_usage_output(result.result_text)
            exceeds = _quota_decision(cfg, acc.name, usage).exceeds
        except Exception as exc:
            logger.warning("quota recheck failed for account %s: %s", acc.name, exc)
            exceeds = False
        if not exceeds:
            logger.info("account %s came back from %s and is IDLE again", acc.name, state.value)
            # The cooldown is over and the numbers agree, so the old refusal
            # must not go on holding the account on the next pass.
            # Conditional: unpark does nothing on an account that is no longer
            # parked, so a claim made during the probe is not undone. It keeps
            # the holders the park left running.
            if state_machine.unpark(cfg.state_dir, acc.name, forget_rate_limit=True):
                recovered.append(acc.name)
    return recovered


def is_rate_limit_error(result: docker_exec.ClaudeResult) -> bool:
    # Verified against the real Claude Code CLI 2.1.273 result JSON: an error
    # result sets `is_error` and, for an API-side error, `api_error_status`
    # (the HTTP status). "Context limit reached" is a distinct, non-quota
    # error that must not match, so there's no bare "quota" term below.
    if not result.raw.get("is_error"):
        return False
    if result.raw.get("api_error_status") in (429, "429"):
        return True
    text = (result.result_text or "").lower()
    return any(term in text for term in ("rate limit", "rate_limit", "usage limit", "hit your limit"))


#: What the CLI says when the account's login is no longer accepted. The first
#: two are verbatim from T-055's implementador on 2026-10-15 ("Failed to
#: authenticate: OAuth session expired and could not be refreshed"); the rest
#: are other wordings of the same fact, not yet seen here, kept because each
#: names the login and so cannot match a rate limit or a failure of the work.
_AUTH_ERROR_TERMS = (
    "failed to authenticate",
    "oauth session expired",
    "oauth token has expired",
    "invalid api key",
    "please run /login",
    "authentication_error",
)


def is_auth_error(result: docker_exec.ClaudeResult) -> bool:
    """Whether the service refused this call because the login is not valid.

    Read the way `is_rate_limit_error` reads a refusal: only an error result,
    and the HTTP status first when the CLI reports one. 403 is left out on
    purpose — a forbidden call can be a permission on one resource, and
    parking an account on it would take a healthy login out of the pool.
    """
    if not result.raw.get("is_error"):
        return False
    if result.raw.get("api_error_status") in (401, "401"):
        return True
    text = (result.result_text or "").lower()
    return any(term in text for term in _AUTH_ERROR_TERMS)


def _park_auth_expired(cfg: Config, account: str, where: str) -> None:
    logger.error(
        "account %s was refused by the service at %s because its login is no longer valid; "
        "it goes AUTH_EXPIRED and stays out of the pool until the operator logs it in again "
        "(docker exec -it %s claude, then /login) and runs `release %s`",
        account, where, container_for(cfg, account), account,
    )
    state_machine.park(cfg.state_dir, account, AccountState.AUTH_EXPIRED)


def _exec_succeeded(result: docker_exec.ClaudeResult) -> bool:
    # exec_claude sets raw={} whenever stdout was empty (a crash or a
    # stderr-only failure never produces the `--output-format json` object).
    # Without this positive check, that case fell through to an unconditional
    # success below, silently discarding the failure and defeating failover.
    if not result.raw:
        return False
    return not result.raw.get("is_error", False)


_VERDICT_RE = re.compile(r"VERDICT:\s*APPROVED", re.IGNORECASE)


def revisor_approved(result_text: str, payload: dict | None = None) -> bool:
    # The verdict is a field of the revisor's structured return, and that is
    # what is read when there is one — with a schema in play the `result` text
    # can be a CLI placeholder ("Structured output provided successfully")
    # with no verdict line anywhere in it.
    #
    # The line below it is the fallback for a phase that answered in prose
    # (an image whose CLI has no --json-schema, a role dispatched without a
    # schema). Either way this fails closed: a malformed or missing verdict
    # burns a revision round instead of silently passing.
    verdict = handoff.verdict_of(payload)
    if verdict is not None:
        return verdict == handoff.APPROVED
    #
    # Only the last non-empty line counts — a bare `MULTILINE` search matched
    # any line ending in "VERDICT: APPROVED", including a quoted one above a
    # real "VERDICT: CHANGES_REQUESTED", or a hypothetical mention embedded in
    # prose ("Not VERDICT: APPROVED").
    #
    # Each line is normalized before matching, not just edge-stripped: markdown
    # emphasis can wrap just the label ("**VERDICT:** APPROVED"), and a
    # trailing closing code fence or blockquote marker must not become (or
    # hide) the line that decides the verdict.
    lines = []
    for line in (result_text or "").splitlines():
        normalized = re.sub(r"[*_`>]", "", line).strip()
        if normalized:
            lines.append(normalized)
    if not lines:
        return False
    return bool(_VERDICT_RE.fullmatch(lines[-1]))


def review_reasons(payload: dict | None) -> dict[str, str]:
    """What a review sent the round back for, keyed by fingerprint.

    The `findings` lines and the debt it rejected, both the revisor's own
    words. Keyed by `debt.fingerprint` so a comparison between two reviews is
    set intersection, not a reading of prose: the revisor is told to copy a
    finding that still holds rather than reword it, and the fingerprint only
    absorbs what a copy can drift on (case, punctuation, a number) — the
    same normalization the debt index files by (`docs/decisions.md` ADR 73).
    """
    reasons: dict[str, str] = {}
    for text in [*handoff.findings_of(payload), *debt.rejected(payload)]:
        key = debt.fingerprint(text)
        if key and key not in reasons:
            reasons[key] = text
    return reasons


# Why a role is told this at all, when nothing in `dispatcher/` fetches during a
# cycle: the role is the one with a shell in the worktree, and reaching for the
# remote is a helpful-looking thing to do when a tree looks older than expected.
# ADR 36 settles the base once, before the first worktree exists, precisely
# because the four phases share `agent/task/<id>`; a phase that re-settles it
# halfway through undoes that, and the failure lands on whoever reviews next. It
# rides in the prompt rather than in a vendored skill for the same reason the
# docs duties do: a skill is method and travels between projects, while this is
# about how this harness runs a task.
_REMOTE_IS_NOT_YOURS = (
    "One standing rule about git. The base you are on was settled before this task's first "
    "phase and does not move while the task runs: do not run `git fetch`, `git pull`, "
    "`git rebase`, or a merge from a remote branch, and do not push. Every phase of this task "
    "works on the same branch in the same worktree, so a base that moves mid-task is how a "
    "review ends up reading a diff that is partly someone else's, and a rebase rewrites "
    "commits the phases before you already handed on. If the tree looks older than the work "
    "you were handed, or something you need is missing from it, that is a finding for your "
    "handoff and not yours to repair: the dispatcher owns the remote, and a base that really "
    "did diverge ends the task rather than being reconciled inside it."
)


# Why a role is told this up front rather than left to the learnings table: T-017,
# T-018 and T-019 each lost turns to the same refusal, in a different shape every
# time — a trailing `; echo "EXIT=$?"`, an expansion, two `cd`s, a `cd` before
# `git` — and a table of traps reaches the phase only once it has already walked
# into one. The CLI's permission check reads a Bash call as a chain and refuses it
# whole when any link is not granted on its own, so the rule is the shape and not
# the command. `.hive/learnings/harness/T-019-a-trailing-echo-exit-refuses-the-whole-call.md`
# is the evidence and names the siblings it retired. Short on purpose: every phase
# pays for it.
_ONE_OPERATION_PER_CALL = (
    "One standing rule about Bash. Run one operation per call: no `;`, `&&`, `||` or pipe "
    "to chain a status echo, and no `$?`, `$(...)`, `${...}` or loops — a call shaped like "
    "that is refused whole, even when every piece is allowed. Read the exit status from the "
    "command's own output instead. The one exception is the `cd <dir> && <granted command>` "
    "form your task card writes, used exactly as written. To read a file outside your "
    "worktree, use the Read tool with its absolute path rather than `cd`."
)

#: The roles pointed at `description.md` for the optional sections
#: (`docs/plans/board-service.md` section 2, *What each phase is given*).
_DESCRIPTION_READERS = frozenset({"arquitecto", "implementador"})


def _ask(parsed: tasks.Description) -> str:
    """The two sections every phase is given, under their own headings."""
    return f"## Goal\n\n{parsed.goal}\n\n## Done when\n\n{parsed.done_when}"


def _role_prompt(
    role: str,
    task_id: str,
    slug: str,
    task_file: str,
    description: str,
    scratch_dir: str,
    hive_dir: str,
    harness: str = "",
    round_num: int | None = None,
    max_turns: int | None = None,
    note: str = "",
) -> str:
    # `DescriptionError` for a text missing a required section: `open_cycle`
    # refuses that before any phase, and a caller that skipped it gets the
    # exception, never a prompt with half an ask.
    parsed = tasks.parse_description(description)
    if role == project_docs.MAPPER_ROLE:
        # The mapper runs before the first phase, so there is no handoff to
        # read and the task is context, not its job: it is told what is coming
        # so it maps the parts of the project that task will touch first. The
        # generator's line goes before everything else: it is a slash command,
        # and only the prompt's first one runs (ADR 115).
        prompt = (
            f"{project_docs.mapper_lead()}\n\n"
            f"Role: {role}. Task: {task_id}.\n\n"
            f"The task about to run on this project is:\n{_ask(parsed)}\n\n"
            "You are not doing that task. You run once, ahead of it, because this project has "
            "no docs for the agents that work on it."
        )
    else:
        # *Goal* and *Done when* are embedded whole, never clamped: a cut
        # phase summary loses detail, but a cut ask misinforms — the role
        # would confidently build the wrong thing. The optional sections are
        # not in the prompt: the two roles that plan and build are given the
        # path and the names of the ones this description has, one file read
        # away, and a reviewing role judges against *Done when* alone.
        prompt = f"Role: {role}. Task: {task_id}.\n\nTask description:\n{_ask(parsed)}\n\n"
        if role in _DESCRIPTION_READERS:
            path = tasks.description_path(hive_dir, task_id)
            optional = parsed.present_optional()
            if optional:
                prompt += (
                    "Those are its Goal and Done when sections, whole. It also has "
                    f"{', '.join(optional)}, not in this prompt: read {path} for them.\n\n"
                )
            else:
                prompt += (
                    "Those are its Goal and Done when sections, whole, and it has no others. "
                    f"The stored text is {path}.\n\n"
                )
        prompt += (
            f"Read {task_file} for context handed off from the previous phase before starting."
        )
        if round_num is not None:
            prompt += f" This is revision round {round_num}."
    # Every role, the mapper included: it is the one that reads the whole tree
    # before anything has been cut from it, which is the moment a pull looks
    # most reasonable.
    prompt += f"\n\n{_REMOTE_IS_NOT_YOURS}"
    # Every role, the mapper included: it has the same shell and the same
    # permission check, and its whole job is reading files across the tree.
    prompt += f"\n\n{_ONE_OPERATION_PER_CALL}"
    # What this role owes the project's own docs. Here rather than in a
    # vendored skill because a skill is method and travels between projects,
    # while this is about the docs of the project in front of it.
    duties = project_docs.duties(role, task_id, max_turns=max_turns)
    if duties:
        prompt += f"\n\n{duties}"
    # Read fresh on every phase, not once per task: an entry the implementador
    # wrote in round 2 is in the revisor's table in the same round, which is
    # the earliest anyone can be warned off a trap.
    traps = learnings.duties(role, task_id, slug, hive_dir, harness)
    if traps:
        prompt += f"\n\n{traps}"
    # Something this one call has to be told that no rule covers: today, the
    # debt ids the auditor is about to file. It is computed by
    # the dispatcher between phases, so it cannot come from `duties`.
    if note:
        prompt += f"\n\n{note}"
    # Only the roles whose schema carries the field: the others have no way to
    # file one, and being told of it would read as an invitation (ADR 117).
    if handoff.files_command_requests(role):
        prompt += f"\n\n{handoff.COMMAND_REQUESTS_PROMPT}"
    schema = handoff.schema_for(role)
    if schema is not None:
        # Says out loud what the schema can only imply: the handoff is the
        # summary, the detail lives in files, and the two have different
        # lifetimes. Without this the role writes its findings into the
        # return, blows the budget, and costs a round to say it again.
        #
        # The limit is stated twice, in entries and in bytes, because they are
        # not equally useful to the one reading it: nothing can count its own
        # bytes while writing, which is most of why every run that logged an
        # overage logged one. Entries are countable, and an entry is what the
        # fields hold anyway. The bytes stay because that is what the
        # dispatcher measures, and a limit you are checked against should be
        # a limit you were told.
        #
        # This used to say the entries were a line each, which is true of the
        # flat lists and wrong by a factor of three for a debt declaration:
        # measured across eight tasks, a flat entry runs 92 bytes and a debt
        # 465. That is the one exception worth naming. Pricing every field
        # separately was measured too and did not pay: the spread in what an
        # entry costs is inside each field, not between them, so a per-field
        # tariff predicts the total no better than one number does and costs
        # a paragraph of prompt to state.
        prompt += (
            f"\n\nReturn the structured handoff your schema describes: about "
            f"{handoff.lines_for(role)} entries in all, counting every list together, one line "
            "each — a debt declaration carries several fields and counts as three — "
            f"{handoff.budget_for(role)} bytes of JSON. Every later phase reads it, so it "
            "carries the summary and not the detail: anything longer than a line goes in a file "
            "that you cite under `paths`, by path plus heading or symbol name, never by line "
            f"number. Detail worth keeping (an ADR, a learning, docs/implementations/{task_id}.md) "
            "goes on the task branch; working notes for this task alone (review findings, test "
            f"logs, a scratch plan) go in {scratch_dir}/, which is outside the repo and is not "
            "read once the task is done."
        )
    if role == "revisor":
        if schema is not None:
            prompt += (
                " Set `verdict` to APPROVED only if the implementation is ready to proceed to the "
                "next phase, and to CHANGES_REQUESTED if it needs another revision round. That "
                "field is what the dispatcher reads to decide."
            )
        else:
            prompt += (
                " End your response with a line reading exactly 'VERDICT: APPROVED' if the "
                "implementation is ready to proceed to the next phase, or exactly "
                "'VERDICT: CHANGES_REQUESTED' if it needs another revision round."
            )
    return prompt


def _draft_prompt(draft_id: str, plan: str, drafts_dir: str, scratch_dir: str) -> str:
    """The arquitecto's prompt for a `draft-specs` run (ADR 82).

    Not `_role_prompt`: there is no task description to embed, and the
    project-docs duties are a task's (an ADR, its implementation notes), while
    this run must leave the repo as it found it. What it shares with every
    phase is said the same way: the git rule, the Bash rule, the handoff.
    """
    sections = ", ".join(f"`## {name}`" for name in tasks.REQUIRED + tasks.OPTIONAL)
    prompt = (
        f"Role: arquitecto, in draft mode. Run: {draft_id}.\n\n"
        f"Read the plan at {plan} in your worktree, and the code and docs it names, and "
        "draft the tasks that would build it: one task per build step, each small enough "
        "for one cycle of this harness. You are not building anything and not designing one "
        "task; you are writing the specs the owner will read, correct and accept before any "
        "of them runs. Nothing you write here is run: a draft is not a task until the owner "
        "accepts it.\n\n"
        f"Write each draft as its own file in {drafts_dir}/, named `D1.md`, `D2.md` and so on "
        "in build order. That directory is outside the repo. Each file starts with YAML "
        "frontmatter whose one key is `depends_on`, the list of the other drafts' names this "
        "one waits for (`depends_on: [D1, D2]`, or `depends_on: []`), between two `---` "
        f"lines. Under it is the task description, in the sections {sections}, each a "
        "column-zero `## ` heading. A draft is refused unless it has Goal, Done when, "
        "Surface and Out of scope: Goal says what and why, Done when says how a reviewer "
        "tells it is finished, Surface lists every file or directory it touches as a "
        "backticked path, and Out of scope says what it leaves to other drafts. Steps is "
        "optional. Two drafts whose Surfaces overlap cannot run side by side, so split the "
        "plan along its files where you can and say so in `depends_on` where you cannot. A "
        "`depends_on` may name only drafts of this set, and never in a cycle.\n\n"
        "Do not change, commit or stage anything in the repo: this run's whole output is the "
        f"files in {drafts_dir}/. Working notes go in {scratch_dir}/."
    )
    prompt += f"\n\n{_REMOTE_IS_NOT_YOURS}"
    prompt += f"\n\n{_ONE_OPERATION_PER_CALL}"
    if handoff.schema_for("arquitecto") is not None:
        prompt += (
            "\n\nReturn the structured handoff your schema describes, short: the drafts you "
            f"wrote, one line each, and anything in the plan you could not turn into a task. "
            f"The drafts themselves are the detail; cite {drafts_dir}/ under `paths`."
        )
    return prompt


def run_draft_specs(
    cfg: Config, slug: str, plan: str, draft_id: str | None = None,
) -> tuple[str, DispatchResult | None]:
    """Run the arquitecto in draft mode on a plan, and return the set's id and the result.

    The set's id is the run's task id: the account claim, the lock, the
    worktree, the usage records and the task file all key on it as on any
    phase's. The worktree is a detached checkout of the project's base, no
    branch and no commit, removed when the run ends (ADR 94). The task file
    ends `drafted`, a status nothing runs. The result is None when another
    owner holds that lock. What the phase wrote is not checked here:
    `tasks.check_drafts` is.
    """
    if os.path.isabs(plan) or ".." in plan.split("/"):
        raise ValueError(f"plan {plan!r} must be a path inside the project, relative to its root")
    draft_id = draft_id or tasks.new_draft_id()
    hive = cfg.hive_tasks_dir
    drafts_dir = tasks.draft_set_dir(hive, draft_id)
    # Made here, not by the phase, as `open_cycle` makes the scratch dir: a
    # phase told to write somewhere should find the somewhere already there.
    context_transfer._makedirs_readable(context_transfer.hive_root(hive), drafts_dir)
    scratch_dir = context_transfer.ensure_scratch_dir(hive, draft_id)
    try:
        result = dispatch_phase(
            cfg, draft_id, slug, "arquitecto",
            prompt=_draft_prompt(draft_id, plan, drafts_dir, scratch_dir),
            model=cfg.model_for(DRAFT_ROLE),
            effort=cfg.effort_for("arquitecto", None),
            at_base=True,
        )
    except context_transfer.LockHeldError as exc:
        # No cleanup: this run made no checkout, and one at that path belongs
        # to the lock's live holder (ADR 94).
        logger.warning("drafts %s are locked by another owner: %s", draft_id, exc)
        return draft_id, None
    except BaseException:
        _drop_draft_worktree(cfg, draft_id, slug)
        raise
    _drop_draft_worktree(cfg, draft_id, slug)
    if result.success:
        tasks.handoff(
            hive, draft_id, new_status="drafted",
            body=handoff.body("arquitecto (draft)", result.result_text, result.handoff),
        )
    else:
        logger.error(
            "drafts %s: the arquitecto did not finish: %s",
            draft_id, result.result_text or "no diagnosis: the phase produced no output",
        )
    return draft_id, result


def reap_expired_locks(cfg: Config) -> list[str]:
    reaped = []
    for task_id in context_transfer.list_task_ids(cfg.hive_tasks_dir):
        task = context_transfer.read_task_file(context_transfer.task_file_path(cfg.hive_tasks_dir, task_id))
        if task.status == "in_progress" and context_transfer.is_lock_expired(task, cfg.heartbeat_ttl_seconds):
            tasks.release_stale_lock(cfg.hive_tasks_dir, task_id)
            reaped.append(task_id)
    return reaped


def _should_commit(role: str, result: docker_exec.ClaudeResult) -> bool:
    """Whether this phase's work belongs in a commit.

    Only the writing roles produce any, and only a phase that actually ran to
    completion: a rate-limited or failed exec is retried in the same worktree
    (resuming the same session), so committing its half-done state would put
    the same work in history twice.

    A phase that spent its turn budget is the exception. The CLI reports it as
    an error, but nothing went wrong — the work up to that turn is real, and
    the writers share one worktree, so leaving it uncommitted would smuggle it
    into the next role's commit under the next role's name.
    """
    if role not in docker_exec.WRITER_ROLES:
        return False
    if is_rate_limit_error(result):
        return False
    return _exec_succeeded(result) or _hit_turn_budget(result)


def _hit_turn_budget(result: docker_exec.ClaudeResult) -> bool:
    """Whether the phase stopped because it ran out of turns.

    The CLI's own subtype, verified on 2.1.273: `error_max_turns`, alongside
    `is_error: true` and a result text reading "Reached maximum number of
    turns (N)".
    """
    return result.raw.get("subtype") == "error_max_turns"


def _commit_message(
    role: str,
    task_id: str,
    account: str,
    session_id: str | None,
    round_num: int | None,
) -> str:
    subject = f"agent({role}): {task_id}"
    if round_num is not None:
        subject += f" round {round_num}"
    # The session id is what lets a commit be traced back to the transcript
    # that produced it, which is the only record of *why* the change is there.
    return (
        f"{subject}\n\n"
        f"Committed by the ia-harness dispatcher after the {role} phase.\n\n"
        f"Account: {account}\n"
        f"Session: {session_id or 'unknown'}\n"
    )


# Measured on T-006: an arquitecto that failed over mid-phase resumed on the
# second account and was handed its own opening environment block again, with
# `Status: (clean)` over three files it had modified minutes earlier. That run
# checked the tree itself and lost nothing; a phase that trusted the block
# would have redone the work.
_STALE_CONTEXT_NOTE = (
    "One warning about your own context before you start. The environment block at the top of "
    "this session — the working directory, the git status, the recent commits — was written when "
    "the session first opened, and it is re-sent to you unchanged every time the session resumes. "
    "It does not describe the worktree as it is now, and a `Status: (clean)` in it is not evidence "
    "that your earlier edits were lost or never made: it is a snapshot taken before them. Run "
    "`git status --short` and `git diff` to see the tree, and do not redo work on the strength of "
    "that block alone."
)


def _with_resume_notes(container: str, prompt: str, session_id: str | None) -> str:
    """The prompt, plus what a phase picking a session back up needs to know.

    Only ever called on a `--resume`, and both notes are here for the same
    reason: neither is true of a fresh session, and the role's system prompt is
    paid for on every call.

    The stale-context warning goes on unconditionally, because the block it
    warns about is re-sent on every resume whatever else is true. The revive
    note only appears when the session left subagents behind — a resume is the
    one point in a phase's life where one can be revived rather than started
    again, and the CLI is what names the ones that outlived the session.

    The shrink retry and the discarded-write one are deliberately left out of
    both. They resume a session too, but each asks for the same return said
    differently — fewer bytes, or a finding in place of a claim: there is no
    work there to hand back to a subagent, and neither reads the tree, so
    there is nothing either could conclude about it out of date.
    """
    notes = [_STALE_CONTEXT_NOTE]
    revive = subagents.revive_note(subagents.of_session(container, session_id))
    if revive:
        notes.append(revive)
    return "\n\n".join([prompt, *notes])


def _phase_handoff(container: str, result: docker_exec.ClaudeResult) -> dict | None:
    """The phase's structured return, with the subagent ids filled in.

    The ids come from disk rather than from the return itself because the
    Agent tool tells the role not to repeat them; see `dispatcher/subagents.py`.
    """
    return subagents.backfill(handoff.parse(result), subagents.of_session(container, result.session_id))


def _phase_system_prompt(role: str) -> str | None:
    """The role's skills line, and the unattended paragraph for any role that
    hands off: both are charged on every call, so neither is sent where it has
    nothing to say."""
    parts = [role_skills.system_prompt(role)]
    if handoff.schema_for(role) is not None:
        parts.append(handoff.UNATTENDED_PHASE)
    return "\n\n".join(p for p in parts if p) or None


def _resume_missing_handoff(
    cfg: Config,
    container: str,
    workdir: str,
    role: str,
    result: docker_exec.ClaudeResult,
    model: str | None,
    effort: str | None,
    recorder: _UsageRecorder,
    slug: str | None = None,
) -> docker_exec.ClaudeResult:
    """Up to `max_handoff_resumes` `--resume`s for a phase that stopped short.

    A phase that ended without its structured return ended on a report, not a
    completion (`docs/plans/models-5-5.md`, *Decisions*, 3). Each resume is one
    "continue" line into the same session, on the run already authorized, and
    recorded against it like any retry. When they run out, or there is no
    session to resume, the result is turned into a failure: the next phase
    would otherwise start from a handoff that was never written.

    A failed or rate-limited resume is returned as it is, so the caller's own
    paths handle it: a rate limit fails over with the session, which the next
    account resumes. Runs inside the heartbeat loop, as the other retries do.
    """
    if not _exec_succeeded(result) or is_rate_limit_error(result):
        return result
    if handoff.schema_for(role) is None or handoff.parse(result) is not None:
        return result
    for attempt in range(1, cfg.max_handoff_resumes + 1):
        if not result.session_id:
            break
        logger.warning(
            "%s phase ended without its handoff; resuming it to finish (%d of %d)",
            role, attempt, cfg.max_handoff_resumes,
        )
        retry = docker_exec.exec_claude(
            container, workdir, handoff.continue_prompt(),
            resume_session_id=result.session_id, model=model, effort=effort,
            timeout_seconds=cfg.phase_timeout_seconds,
            # The session already read its skills; the schema has to travel,
            # or the return it is being asked for comes back as prose.
            json_schema=handoff.schema_for(role),
            **_phase_permission_flags(cfg, role, slug),
        )
        recorder.record("handoff-resume", retry)
        if not _exec_succeeded(retry) or is_rate_limit_error(retry):
            return retry
        if handoff.parse(retry) is not None:
            logger.info("%s handoff arrived after %d resume(s)", role, attempt)
            return retry
        result = retry
    logger.error(
        "%s phase ended without its handoff after %d resume(s)%s; it counts as failed",
        role, cfg.max_handoff_resumes, "" if result.session_id else " (no session to resume)",
    )
    return docker_exec.ClaudeResult(
        session_id=result.session_id, result_text=result.result_text,
        raw={**result.raw, "is_error": True},
    )


def _shrink_over_budget(
    cfg: Config,
    container: str,
    workdir: str,
    role: str,
    result: docker_exec.ClaudeResult,
    model: str | None,
    effort: str | None,
    recorder: _UsageRecorder,
    slug: str | None = None,
) -> docker_exec.ClaudeResult:
    """One `--resume` asking for a shorter handoff, when one blew its budget.

    Exactly one: a second would cost as much as the phase it is trimming, and
    a role that ignored the budget twice is not going to find the third ask
    more convincing. The retry is taken as it comes, over budget or not — the
    point is to keep the task file readable, not to win an argument.

    Only overages worth the call are asked about. A `--resume` is a model call,
    and four of the eleven overages on record were under 300 bytes: a line and
    a half in a file nobody was struggling to read.

    What it logs is what landed, not what was drafted. A first draft over
    budget that the rewrite brings back under it is the system working, so it
    goes to INFO with its size, which is the data the budgets are tuned from.
    A WARNING means a handoff went into the task file over budget anyway.

    This runs inside the heartbeat loop by construction (its caller holds it),
    because the task lock's TTL is only kept alive there: a retry outside it
    is a window for another dispatcher to take the task mid-call.
    """
    if not _exec_succeeded(result) or is_rate_limit_error(result):
        return result
    overage = handoff.over_budget(role, result)
    if not overage:
        return result

    size = handoff.measure(result)
    budget = handoff.budget_for(role)
    if not handoff.worth_shrinking(role, overage):
        logger.info(
            "%s handoff was %d bytes, %d over its %d-byte budget; inside the margin, kept as it is",
            role, size, overage, budget,
        )
        return result
    logger.info(
        "%s handoff was %d bytes, %d over its %d-byte budget; asking for a shorter one",
        role, size, overage, budget,
    )
    if not result.session_id:
        # Nothing to resume into. The clamp in `handoff.body` still keeps the
        # task file bounded, so this is a worse handoff, not a failed phase.
        logger.warning(
            "%s handoff is %d bytes over its %d-byte budget and there is no session to resume: "
            "it goes in the task file as it is",
            role, overage, budget,
        )
        return result

    retry = docker_exec.exec_claude(
        container, workdir, handoff.shrink_prompt(role, size, budget),
        resume_session_id=result.session_id, model=model, effort=effort,
        timeout_seconds=cfg.phase_timeout_seconds,
        # No skills on the retry: the session already read them, and this call
        # rewords a return rather than doing any of the role's work. The
        # schema does travel — without it the shorter answer comes back as
        # prose and the parse falls through to the clamp.
        json_schema=handoff.schema_for(role),
        # A resume is a new `claude` process and inherits no flags, so the
        # permissions have to be restated here too.
        **_phase_permission_flags(cfg, role, slug),
    )
    # Before the branch below, not after: a retry this discards as a *result*
    # still cost the account a turn, and the log is about what was spent.
    recorder.record("shrink-retry", retry)
    if not _exec_succeeded(retry) or is_rate_limit_error(retry):
        # The first return is over budget but complete; a failed or
        # rate-limited retry is nothing. Keep the first, and let the phase
        # succeed on it — failing over the account here would throw away work
        # that is already done over a formatting problem.
        logger.warning(
            "%s could not be asked for a shorter handoff; the %d-byte return goes in the task "
            "file over its %d-byte budget",
            role, size, budget,
        )
        return result

    left = handoff.over_budget(role, retry)
    if left:
        # Asked, answered, still long. This is the line that says a budget may
        # be wrong rather than a role careless, because it survived an edit.
        logger.warning(
            "%s handoff is %d bytes after a rewrite, still %d over its %d-byte budget",
            role, handoff.measure(retry), left, budget,
        )
    else:
        logger.info(
            "%s handoff came back at %d bytes, inside its %d-byte budget",
            role, handoff.measure(retry), budget,
        )
    return retry


def _gate_report(
    cfg: Config,
    container: str,
    workdir: str,
    project_dir: str,
    task_id: str,
    round_num: int | None,
) -> gates.Report | None:
    """The gates, with a log path for the one that needs one.

    The log goes to the task's scratch directory rather than into the
    worktree: the dispatcher and the agents mount it at the same path, so a
    finding can name the file and the next round can open it, and a reviewing
    checkout that gets rebuilt every round cannot take it away.

    Anything that goes wrong in here is swallowed. A gate is a saving, not a
    dependency — a check that breaks should cost a review call, not a task.
    """
    try:
        log_path = os.path.join(
            context_transfer.ensure_scratch_dir(cfg.hive_tasks_dir, task_id),
            f"gates-round-{round_num or 1}.log",
        )
        return gates.run(
            container, workdir, project_dir,
            test_timeout_seconds=cfg.gates_test_timeout_seconds,
            test_log_path=log_path,
        )
    except Exception:
        logger.exception("the gates did not finish; this phase goes to review ungated")
        return None


def _run_gates(
    cfg: Config,
    container: str,
    workdir: str,
    project_dir: str,
    task_id: str,
    role: str,
    result: docker_exec.ClaudeResult,
    model: str | None,
    effort: str | None,
    round_num: int | None,
    recorder: _UsageRecorder,
    account: str = "",
    slug: str | None = None,
) -> tuple[docker_exec.ClaudeResult, gates.Report | None]:
    """The deterministic checks, and the one `--resume` they are worth.

    Only the implementador: the gates judge code against tests and docs, and
    the reading roles have neither to answer for. Only a phase that finished,
    too — gating a rate-limited return means grading an empty worktree.

    The one retry is the whole quota argument. It costs a single turn in a
    session that is already warm, and what it replaces is a revisor call that
    would have found the same thing plus the implementador round that answers
    it. The gates then run again on whatever the retry left behind, because a
    phase saying it fixed the tests is exactly the claim these checks exist to
    stop taking on faith.

    Like `_shrink_over_budget`, this runs inside the heartbeat loop by
    construction: its caller holds it, and a retry outside it is a window for
    another dispatcher to take the task mid-call.
    """
    if not cfg.gates_enabled or role != "implementador":
        return result, None
    if not _exec_succeeded(result) or is_rate_limit_error(result):
        return result, None
    # A filed command request ends the cycle blocked (ADR 117): grading the
    # worktree of a phase that stopped for a command it was denied is a cost
    # spent on work the phase says it could not finish. Only one that passes
    # step 3 too (ADR 118): one it refuses does not block, and the cycle goes
    # on to a revisor that should read a graded branch.
    slug = posixpath.basename(project_dir.rstrip("/"))
    if _command_request_lists(cfg, account, slug, task_id, handoff.parse(result))[0]:
        logger.info("task %s: no gates: the implementador filed a command request", task_id)
        return result, None

    report = _gate_report(cfg, container, workdir, project_dir, task_id, round_num)
    if report is None or not report.needs_answer or not result.session_id:
        return result, report

    logger.info(
        "task %s: the gates are asking %s to answer %d finding(s)",
        task_id, role, len(report.findings),
    )
    retry = docker_exec.exec_claude(
        container, workdir, _with_resume_notes(container, report.resume_prompt(), result.session_id),
        resume_session_id=result.session_id, model=model, effort=effort,
        timeout_seconds=cfg.phase_timeout_seconds,
        # No skills on the retry, for the same reason the shrink retry gets
        # none: the session has already read them. The schema does travel —
        # the retry's return is the one that lands in the task file.
        json_schema=handoff.schema_for(role),
        # Restated for the same reason as the shrink retry's: new process,
        # no inherited flags.
        **_phase_permission_flags(cfg, role, slug),
    )
    # Recorded whether or not it is kept, for the shrink retry's reason.
    recorder.record("gate-retry", retry)
    if not _exec_succeeded(retry) or is_rate_limit_error(retry):
        # The first return stands: a phase that finished with findings against
        # it is worse than one that answered them and better than nothing, and
        # the report reaches the revisor either way.
        return result, report
    return retry, _gate_report(cfg, container, workdir, project_dir, task_id, round_num)


def _refuse_review_writes(
    cfg: Config,
    container: str,
    workdir: str,
    task_id: str,
    role: str,
    result: docker_exec.ClaudeResult,
    model: str | None,
    effort: str | None,
    recorder: _UsageRecorder,
    slug: str | None = None,
) -> docker_exec.ClaudeResult:
    """One `--resume` telling a reviewing phase the edits it made are gone.

    A reviewing role's checkout is detached at the task branch's tip and
    rebuilt every round (docker_exec.create_worktree), and `_should_commit`
    never commits it, so whatever it writes there is dropped with the checkout
    and nobody is told. Measured on T-005: the revisor closed a gate by
    editing the implementation doc, its own grep in its own worktree agreed
    with it, the branch never saw the change, and the APPROVED verdict was
    issued over a fix that did not exist.

    The write itself cannot be rescued — committing it would put the
    reviewer's own change on the branch it is in the middle of approving, and
    the detached checkout is what keeps a reviewer reading the code as it
    stands. What is fixable is the silence: the WARNING is the record that it
    happened, and the retry asks the phase to hand the change on as a finding
    instead of reporting work it did not land.

    Like the other two retries this runs inside the heartbeat loop by
    construction, costs one turn in a session that is already warm, and is
    only spent when the reviewer actually wrote something.
    """
    if role in docker_exec.WRITER_ROLES:
        return result
    if not _exec_succeeded(result) or is_rate_limit_error(result):
        # A phase that did not finish has no handoff to correct, and a
        # rate-limited one is being handed to another account anyway.
        return result
    paths = docker_exec.dirty_paths(container, workdir)
    if not paths:
        return result

    logger.warning(
        "task %s: the %s phase wrote to %s, a detached review checkout that is deleted at "
        "the end of the round; the change never reaches the branch: %s",
        task_id, role, workdir, ", ".join(paths[:10]),
    )
    if not result.session_id:
        # Nothing to resume into. The phase keeps whatever it claimed, which
        # is what used to happen every time; the WARNING above is still more
        # than the run said before.
        logger.warning(
            "task %s: there is no session to tell the %s phase those edits were discarded; "
            "its handoff goes on as it is",
            task_id, role,
        )
        return result

    retry = docker_exec.exec_claude(
        container, workdir, handoff.discarded_writes_prompt(role, paths),
        resume_session_id=result.session_id, model=model, effort=effort,
        timeout_seconds=cfg.phase_timeout_seconds,
        # No skills, schema restated, permissions restated: the same reasons
        # as the shrink retry's, and the corrected return is the one that has
        # to land in the task file.
        json_schema=handoff.schema_for(role),
        **_phase_permission_flags(cfg, role, slug),
    )
    # Recorded whether or not it is kept, for the shrink retry's reason.
    recorder.record("review-retry", retry)
    if not _exec_succeeded(retry) or is_rate_limit_error(retry):
        # The first return stands. It overstates what the phase did, but it
        # holds the review itself, and the WARNING is in the log either way.
        logger.warning(
            "task %s: the %s phase could not be told its edits were discarded; its first "
            "return goes on as it is",
            task_id, role,
        )
        return result
    return retry


def dispatch_phase(
    cfg: Config,
    task_id: str,
    slug: str,
    role: str,
    prompt: str,
    resume_session_id: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    round_num: int | None = None,
    max_turns: int | None = None,
    at_base: bool = False,
    only_account: str | None = None,
) -> DispatchResult:
    # `only_account` pins the call to that one account: a command request's verdict
    # resumes the phase's own session, which lives in that account's CLI
    # config (ADR 119). Every other account counts as tried, so a pinned call
    # that cannot get it ends `no_account` instead of moving on.
    tried: set[str] = (
        {a.name for a in cfg.accounts if a.name != only_account} if only_account else set()
    )
    while True:
        # Exclude `tried` up front rather than picking idle[0] and bailing
        # when it's already been tried: a recheck can recover the very
        # account this call just rate-limited (it's IDLE again) alongside
        # others that were cooling before this call started, and those
        # never-tried accounts are still worth a shot.
        account = pick_idle_account(cfg, exclude=tried, role=role)
        if account is None:
            # Only loop back if the recheck freed an account this call hasn't
            # tried: recovering just the one that rate-limited a moment ago
            # would re-probe every cooling account for nothing. Each loop-back
            # is followed by a fresh pick, so this is bounded by 2N+1 passes.
            if any(acc not in tried for acc in _recheck_cooling_accounts(cfg)):
                continue
            logger.error(
                "task %s: no account is left for the %s phase; tried %s",
                task_id, role, ", ".join(sorted(tried)) or "none",
            )
            return DispatchResult(
                success=False, session_id=resume_session_id,
                result_text="no accounts available", account="", no_account=True,
            )
        tried.add(account)

        # Take the account before the probe, not after: the probe takes
        # seconds, and a claim made after it would leave that whole window for
        # another process to pick the same account (`docs/decisions.md`
        # ADR 71). A loser moves on like a failed probe does.
        if not state_machine.claim(
            cfg.state_dir, account, task_id, capacity=_capacity_for(cfg, account),
        ):
            logger.info(
                "task %s: account %s was taken by another dispatcher; trying the next",
                task_id, account,
            )
            continue
        try:
            quota_ok = check_quota_ok(cfg, account)
        except BaseException:
            # BaseException, so a Ctrl+C during the probe gives the account
            # back too: no card carries a heartbeat yet, and the reaper would
            # otherwise wait out the whole phase timeout to free it.
            state_machine.release(cfg.state_dir, account, task_id)
            raise
        if not quota_ok:
            # check_quota_ok parked the account (PRE_COOLDOWN, COOLING_DOWN
            # or AUTH_EXPIRED) but cannot release its claimant: park first,
            # then give this phase's slot back (`docs/decisions.md` ADR 88).
            state_machine.release(cfg.state_dir, account, task_id)
            continue

        container = container_for(cfg, account)
        # One per attempt, built once the account is known: a failover is a
        # different account paying for a different call, and gets its own.
        recorder = _UsageRecorder(cfg, task_id, role, round_num, account, model)
        logger.info(
            "task %s: %s%s goes to account %s, %s",
            task_id, role, f" round {round_num}" if round_num is not None else "", account,
            f"resuming session {resume_session_id}" if resume_session_id else "in a new session",
        )
        lock_acquired = False
        project_dir = f"{cfg.projects_root}/{slug}"
        # Read this before anything in the container touches the tree, while it
        # is still whatever the host user owns.
        owner = docker_exec.read_owner(container, project_dir)
        try:
            # Claim the task before touching the worktree: a task another
            # account still owns (live heartbeat) must be refused outright,
            # not after work has already started on disk. The role goes on the
            # task with the lock, and comes off with it on every path out of
            # here and the handoff after (ADR 126).
            tasks.acquire_lock(
                cfg.hive_tasks_dir, task_id, owner=account,
                ttl_seconds=cfg.heartbeat_ttl_seconds, phase=role,
            )
            lock_acquired = True
            # The keyword goes only when asked for: every other phase calls
            # create_worktree exactly as it always did.
            workdir = docker_exec.create_worktree(
                container, cfg.projects_root, slug, task_id, role,
                **({"at_base": True} if at_base else {}),
            )
            with _HeartbeatLoop(cfg.hive_tasks_dir, task_id, cfg.heartbeat_interval_seconds):
                # `prompt` itself is never rewritten: the note is recomputed
                # from whichever session this attempt resumes, so a phase that
                # fails over twice gets one note about the session it is
                # actually picking up, not two stacked from earlier accounts.
                phase_prompt = (
                    _with_resume_notes(container, prompt, resume_session_id)
                    if resume_session_id
                    else prompt
                )
                result = docker_exec.exec_claude(
                    container, workdir, phase_prompt,
                    resume_session_id=resume_session_id, model=model, effort=effort,
                    timeout_seconds=cfg.phase_timeout_seconds,
                    max_turns=max_turns,
                    # Chosen from the role here rather than passed in by the
                    # caller: the role is what decides the set, and a phase
                    # dispatched by any other path should get the same one.
                    plugin_dirs=role_skills.plugin_dirs(role),
                    append_system_prompt=_phase_system_prompt(role),
                    json_schema=handoff.schema_for(role),
                    **_phase_permission_flags(cfg, role, slug),
                )
                # Here, before the retries: each of them *replaces* `result`,
                # so a record taken afterwards would describe the last retry
                # and not the phase. Written before the gates, too, so that a
                # crash in one cannot cost the phase's own numbers.
                recorder.record("phase", result)
                # First of the retries: the gates, the review check and the
                # shrink all work on a handoff, and this is what makes one.
                result = _resume_missing_handoff(
                    cfg, container, workdir, role, result, model, effort, recorder, slug,
                )
                # Before the shrink, not after: the gate retry writes a new
                # handoff, and the byte budget has to be enforced on the return
                # that actually lands in the task file.
                result, gate_report = _run_gates(
                    cfg, container, workdir, project_dir, task_id, role,
                    result, model, effort, round_num, recorder, account=account, slug=slug,
                )
                # Between the two for the same reason: it can rewrite the
                # handoff as well, and the gates only ever run for the
                # implementador, which this never fires for.
                result = _refuse_review_writes(
                    cfg, container, workdir, task_id, role, result, model, effort, recorder, slug,
                )
                result = _shrink_over_budget(
                    cfg, container, workdir, role, result, model, effort, recorder, slug,
                )
            # A checkout at the base has no branch to commit to (ADR 94).
            if not at_base and _should_commit(role, result):
                # The mapping commit is a base operation (ADR 80).
                with (
                    _base_lock(cfg, slug, "mapping commit")
                    if role == project_docs.MAPPER_ROLE else contextlib.nullcontext()
                ):
                    if role == project_docs.MAPPER_ROLE:
                        # Staged because a phase cannot write under .claude/
                        # (ADR 115); moved here, with no model in the loop,
                        # so the skill lands in the mapping commit.
                        project_docs.move_staged_run_skills(container, workdir)
                    docker_exec.commit_worktree(
                        container, workdir,
                        message=_commit_message(role, task_id, account, result.session_id, round_num),
                        author_name=f"{role} ({account})",
                        author_email=f"{role}@ia-harness.invalid",
                        paths=project_docs.commit_scope(role),
                        excludes=project_docs.commit_excludes(),
                    )
                _record_left_behind(cfg, container, workdir, task_id, role, round_num)
        except Exception:
            # Never leave an account stuck BUSY (disk-persisted, survives
            # restart) because of an exception between claiming it and
            # returning — that permanently removes it from the pool.
            state_machine.release(cfg.state_dir, account, task_id)
            if lock_acquired:
                tasks.release_stale_lock(cfg.hive_tasks_dir, task_id)
            raise
        finally:
            # Covers the failure paths too: `git worktree add` alone is enough
            # to leave root-owned files behind. Nothing after this block writes
            # to the tree.
            docker_exec.restore_owner(container, project_dir, owner)

        if is_rate_limit_error(result):
            logger.warning(
                "task %s: account %s refused the %s phase as rate-limited; it goes COOLING_DOWN, "
                "the task lock is released, and the phase is handed on resuming session %s",
                task_id, account, role, result.session_id or resume_session_id or "unknown",
            )
            # The refusal is the only direct evidence this harness ever gets
            # that the service is turning the account away, and the /usage
            # recheck cannot reproduce it. Write it down before parking the
            # account, or the next recheck recovers it on stale local numbers.
            state_machine.record_rate_limit(cfg.state_dir, account)
            state_machine.park(cfg.state_dir, account, AccountState.COOLING_DOWN)
            state_machine.release(cfg.state_dir, account, task_id)
            tasks.release_stale_lock(cfg.hive_tasks_dir, task_id)
            resume_session_id = result.session_id or resume_session_id
            continue

        if is_auth_error(result):
            # Handed on like a rate limit, with the session: the phase did not
            # fail, the account could not run it, and another login can resume
            # the same work. Not COOLING_DOWN, which the recheck would undo on
            # local counters that say nothing about a login.
            _park_auth_expired(cfg, account, f"the {role} phase of task {task_id}")
            # Park first, then give this phase's slot back: the other holders
            # keep running, and no slot is claimable in between.
            state_machine.release(cfg.state_dir, account, task_id)
            tasks.release_stale_lock(cfg.hive_tasks_dir, task_id)
            resume_session_id = result.session_id or resume_session_id
            continue

        if not _exec_succeeded(result):
            state_machine.release(cfg.state_dir, account, task_id)
            tasks.release_stale_lock(cfg.hive_tasks_dir, task_id)
            return DispatchResult(
                success=False, session_id=result.session_id, result_text=result.result_text,
                account=account, handoff=_phase_handoff(container, result), gates=gate_report,
                permission_denials=_handoff_denials(task_id, role, result),
            )

        # A turn the service actually served is proof the refusal is over —
        # better proof than any /usage report, and the only kind worth
        # dropping the mark for.
        state_machine.clear_rate_limit(cfg.state_dir, account)
        state_machine.release(cfg.state_dir, account, task_id)
        return DispatchResult(
            success=True, session_id=result.session_id, result_text=result.result_text,
            account=account, handoff=_phase_handoff(container, result), gates=gate_report,
            permission_denials=_handoff_denials(task_id, role, result),
        )


def _file_accepted_debt(
    cfg: Config,
    task_id: str,
    slug: str,
    implemented: dict | None,
    reviewed: dict | None,
) -> list[tuple[str, debt.Declaration]]:
    """Give the debt this task declared its ids, and name it for the auditor.

    The dispatcher does this, not an agent: an agent that numbers its own
    entries numbers them again when it is re-run for a second review round.

    It runs *before* the auditor because the ids go into the auditor's
    prompt: the ids are assigned here, and the auditor — still the only
    writer of the indexes — writes one row per id (`docs/decisions.md` ADR 59:
    nothing is opened anywhere else).

    Takes the two handoffs rather than the two results, because the caller may
    not have either result: `run-phase` closes a task out of what previous
    phases left on disk, and only the handoffs are on disk. Both are
    `dict | None` all the way down — a handoff that is missing declares
    nothing, which is the same answer as a handoff that declared nothing.
    """
    accepted = debt.accepted(implemented, reviewed)
    if not accepted:
        return []
    # Read from the branch being built, not the project checkout: a re-run of
    # a task that already filed entries has them on its own branch, and an
    # index that fills with the same entry once per run is an index nobody
    # reads. A read that fails costs a duplicate row, never the entry.
    known: set[str] = set()
    container = cleanup_container(cfg)
    if container is not None:
        worktree = (
            f"{docker_exec.task_worktrees_dir(cfg.projects_root, slug, task_id)}"
            f"/{docker_exec.WRITER_WORKTREE_NAME}"
        )
        try:
            known = debt.index_fingerprints(debt.read_index(container, worktree))
        except Exception as exc:
            logger.warning("task %s: could not read %s: %s", task_id, project_docs.DEBT_INDEX, exc)
    filed: list[tuple[str, debt.Declaration]] = []
    for declaration in accepted:
        if declaration.fingerprint in known:
            logger.info("task %s: debt already in the index, not filed again: %s", task_id, declaration.what)
            continue
        known.add(declaration.fingerprint)
        filed.append((debt.entry_id(task_id, len(filed) + 1), declaration))
    return filed


def cleanup_container(cfg: Config) -> str | None:
    """Which container to run worktree housekeeping in.

    Any of them: every agent container bind-mounts the same host projects root
    (see docker/compose/docker-compose.agents.yml), so this does not have to be
    the account that ran the phases — which by then may be rate-limited.
    """
    return cfg.accounts[0].container if cfg.accounts else None


# How long a base operation waits for another holder of the project's base lock
# before it is refused (ADR 80). Longer than the default heartbeat TTL, so a
# holder that crashed is reaped within the wait rather than refusing the work.
BASE_LOCK_WAIT_SECONDS = 300


def _base_lock(cfg: Config, slug: str, operation: str) -> tasks.BaseLock:
    """The project's base lock for one operation on its checkout (ADR 80).

    Taken before the operation's read_owner and released after its
    restore_owner, so two operations never interleave on the base nor restore
    an owner the other one read while the tree was root's.
    """
    return tasks.BaseLock(
        cfg.state_dir,
        slug,
        operation,
        ttl_seconds=cfg.heartbeat_ttl_seconds,
        heartbeat_interval_seconds=cfg.heartbeat_interval_seconds,
        wait_seconds=BASE_LOCK_WAIT_SECONDS,
    )


def update_project_branch(cfg: Config, slug: str) -> docker_exec.UpdateOutcome | None:
    """Bring a project's checkout up to date with its remote, once, per task.

    Returns None when there is nothing to run it in or the operator has not
    asked for it, and otherwise whatever the update came to — the caller
    decides what a DIVERGED answer costs, because only a cycle knows whether a
    task is about to start on the base this moves.

    Runs in cleanup_container's container for cleanup_container's reason: every
    agent bind-mounts the same projects root, so this does not need the account
    the phases will run on. Ownership is read and restored around it the way
    every other git call into a container is — git writes to .git/ as root in
    there, and the host checkout is the same files.
    """
    if not cfg.update_project_before_task:
        return None
    container = cleanup_container(cfg)
    if container is None:
        return None
    project_dir = f"{cfg.projects_root}/{slug}"
    with _base_lock(cfg, slug, "update"):
        owner = docker_exec.read_owner(container, project_dir)
        try:
            return docker_exec.update_project_branch(
                container,
                cfg.projects_root,
                slug,
                credentials_path=cfg.git_credentials_path,
                keys_dir=cfg.project_keys_dir,
            )
        finally:
            docker_exec.restore_owner(container, project_dir, owner)


def _drop_review_worktrees(cfg: Config, task_id: str, slug: str) -> None:
    """Remove a finished task's reviewing checkouts, keeping the writers' one.

    Best-effort: this runs on the way out of a cycle that may already have
    failed, so it must never be the thing that raises, and a removal that does
    not happen only costs disk — create_worktree rebuilds these anyway.
    """
    container = cleanup_container(cfg)
    if container is None:
        return
    project_dir = f"{cfg.projects_root}/{slug}"
    owner = docker_exec.read_owner(container, project_dir)
    try:
        removed = docker_exec.remove_review_worktrees(container, cfg.projects_root, slug, task_id)
    except Exception as exc:
        logger.warning("could not clean up review worktrees for task %s: %s", task_id, exc)
        return
    finally:
        # `git worktree prune` writes to .git/worktrees/ as root, same as the
        # phases do, so the tree goes back to the host user either way.
        docker_exec.restore_owner(container, project_dir, owner)
    if removed:
        logger.info("task %s: removed review worktrees %s", task_id, ", ".join(removed))


def _drop_draft_worktree(cfg: Config, draft_id: str, slug: str) -> None:
    """Remove a draft run's checkout of the base, and never raise (ADR 94).

    Best-effort like `_drop_review_worktrees`: it runs on the way out of a run
    that may already have failed. A draft phase is a reader, so whatever it
    left modified in the checkout is discarded, and said so.
    """
    container = cleanup_container(cfg)
    if container is None:
        return
    project_dir = f"{cfg.projects_root}/{slug}"
    owner = None
    try:
        owner = docker_exec.read_owner(container, project_dir)
        worktrees_dir = docker_exec.task_worktrees_dir(cfg.projects_root, slug, draft_id)
        dirty = docker_exec.dirty_paths(container, f"{worktrees_dir}/arquitecto")
        if dirty:
            logger.warning(
                "drafts %s: the draft phase changed the repo, and the changes are discarded: %s",
                draft_id, ", ".join(dirty[:10]),
            )
        removed = docker_exec.remove_task_worktrees(container, cfg.projects_root, slug, draft_id)
        if removed:
            logger.info("drafts %s: removed checkout %s", draft_id, ", ".join(removed))
    except Exception as exc:
        logger.warning("could not clean up the checkout of drafts %s: %s", draft_id, exc)
    finally:
        try:
            docker_exec.restore_owner(container, project_dir, owner)
        except Exception as exc:
            logger.warning("could not restore ownership after drafts %s: %s", draft_id, exc)


def _record_left_behind(
    cfg: Config,
    container: str,
    workdir: str,
    task_id: str,
    role: str,
    round_num: int | None,
) -> None:
    """Keep what a scoped commit left uncommitted, and never fail the phase.

    The worktree goes at merge and the log scrolls, so the paths and their
    diff are saved under the task's hive directory (ADR 70). Nothing is
    committed here.
    """
    try:
        left = docker_exec.left_behind(container, workdir, project_docs.commit_excludes())
        if not left.paths:
            context_transfer.clear_left_behind(cfg.hive_tasks_dir, task_id, role)
            return
        patch = docker_exec.diff_of(container, workdir, left.paths)
        context_transfer.save_left_behind(
            cfg.hive_tasks_dir, task_id, role, round_num,
            left.outside, left.excluded, patch,
        )
    except Exception as exc:
        logger.warning(
            "task %s: could not record what the %s phase left uncommitted: %s; "
            "the phase carries on",
            task_id, role, exc,
        )
        return
    for line in left_behind_report(cfg, task_id, role):
        logger.warning("%s", line)


def left_behind_report(cfg: Config, task_id: str, role: str | None = None) -> list[str]:
    """One line per phase of this task that left paths out of its commit."""
    lines: list[str] = []
    for record in context_transfer.read_left_behind(cfg.hive_tasks_dir, task_id):
        if role is not None and record.get("role") != role:
            continue
        outside = [str(p) for p in record.get("outside") or []]
        excluded = [str(p) for p in record.get("excluded") or []]
        clauses = []
        if outside:
            clauses.append(f"outside its scope: {', '.join(outside)}")
        if excluded:
            clauses.append(f"excluded: {', '.join(excluded)}")
        rnd = record.get("round")
        when = f" (round {rnd})" if rnd is not None else ""
        lines.append(
            f"task {task_id}: the {record.get('role')} phase{when} left "
            f"{len(outside) + len(excluded)} path(s) uncommitted, NOT on the branch - "
            f"{'; '.join(clauses)} - their diff is in {record.get('patch')}; "
            "apply it by hand if it is wanted"
        )
    return lines


#: The status a task gets when the pre-merge check stops its merge
#: (`docs/plans/autonomy.md` *Decisions*, 2). The integrador resolves it once
#: that role exists; until then, the operator.
CONFLICT_STATUS = "conflict"

#: The heading of the note the pre-merge check appends to the task file.
PRE_MERGE_HEADING = "## pre-merge check"


@dataclasses.dataclass(frozen=True)
class PreMergeVerdict:
    """Whether a task branch may be merged, and the line that says why."""

    passed: bool
    detail: str


def _task_status(cfg: Config, task_id: str) -> str | None:
    try:
        return context_transfer.read_task_file(
            context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)
        ).status
    except Exception:
        return None


def _stop_on_conflict(
    cfg: Config, task_id: str, outcome: docker_exec.PreMergeOutcome, why: str,
) -> str:
    """Move the task to `conflict` with a note naming why and the base commit."""
    branch = docker_exec.task_branch(task_id)
    tip = (outcome.tip or "")[:7]
    note = (
        f"{PRE_MERGE_HEADING}\n\n"
        f"Not merged: {why}. The base was {outcome.base} at {outcome.base_commit}. "
        f"The pre-merge was undone: {branch} is still at {tip} and {outcome.base} "
        f"is unchanged. Resolve it on the branch (merge {outcome.base} in, never "
        "rebase), then run `dispatch merge-task` again."
    )
    try:
        tasks.handoff(cfg.hive_tasks_dir, task_id, CONFLICT_STATUS, note)
    except Exception as exc:
        logger.warning("task %s: could not record the conflict on the task: %s", task_id, exc)
    return f"task {task_id}: moved to {CONFLICT_STATUS}: {why} (base {outcome.base} at {(outcome.base_commit or '')[:7]})"


def pre_merge_check(cfg: Config, container: str, task_id: str, slug: str) -> PreMergeVerdict:
    """Merge the base into the task branch and gate the result, before any merge.

    `docs/plans/autonomy.md` *Decisions*, 2, and the one function both merge
    paths call, the cycle's `merge_on_done` and `merge-task`. No quota: git
    and the test gate, no model.

    - The base already in the branch: nothing new came in, so the branch's
      own tip is what lands, and the gate is not run again. It passes.
    - A conflict, or a test gate that goes red on the merged result: the
      pre-merge is dropped (the branch never moved; it was built in a
      detached checkout), the base is untouched, the task moves to
      `conflict` with a note naming the files or the failing gate and the
      base commit, and the merge does not happen.
    - Clean and green: the merge commit is kept on the task branch, and the
      merge that follows lands it. A pool with `gates_enabled: false` keeps a
      clean merge without running the gate, as its phases ran without one.

    A check that cannot run, or a gate that did not finish, stops the merge
    without touching the task's status: there is nothing to resolve, only
    something to run again.
    """
    project_dir = f"{cfg.projects_root}/{slug}"
    try:
        outcome = docker_exec.premerge_base(container, cfg.projects_root, slug, task_id)
    except Exception as exc:
        return PreMergeVerdict(False, f"task {task_id}: the pre-merge check could not run: {exc}")
    if outcome.refused:
        return PreMergeVerdict(False, f"task {task_id}: pre-merge check refused: {outcome.detail}")
    if outcome.conflicted:
        why = (
            f"merging {outcome.base} in conflicts in {', '.join(outcome.conflicts)}"
            if outcome.conflicts else outcome.detail
        )
        return PreMergeVerdict(False, _stop_on_conflict(cfg, task_id, outcome, why))
    if outcome.up_to_date:
        return _passed(cfg, task_id, f"task {task_id}: pre-merge check: {outcome.detail}; nothing new to gate")

    gated = "test gate green"
    log_path = None
    try:
        # Before the test gate and whatever `gates_enabled` says: a log the
        # union merge left with one ADR number twice is broken as a record,
        # not as code (ADR 81), and it costs one `cat`.
        try:
            duplicated = gates.decisions_log(container, outcome.workdir)
        except Exception as exc:
            return PreMergeVerdict(
                False, f"task {task_id}: the pre-merge decisions-log gate did not finish: {exc}"
            )
        if duplicated:
            why = f"{outcome.base} merged in leaves a broken decisions log: {duplicated[0].detail}"
            return PreMergeVerdict(False, _stop_on_conflict(cfg, task_id, outcome, why))
        if cfg.gates_enabled:
            log_path = os.path.join(
                context_transfer.ensure_scratch_dir(cfg.hive_tasks_dir, task_id),
                "gates-premerge.log",
            )
            try:
                report = gates.run(
                    container, outcome.workdir, project_dir,
                    test_timeout_seconds=cfg.gates_test_timeout_seconds,
                    test_log_path=log_path,
                )
            except Exception as exc:
                return PreMergeVerdict(
                    False, f"task {task_id}: the pre-merge test gate did not finish: {exc}"
                )
        else:
            # The pool turned its gates off, so the merged result is kept
            # ungated, the way every phase of the task went ungated.
            report = gates.Report()
            gated = "test gate off (gates_enabled: false)"
        if report.blocking:
            failed = "; ".join(
                f"`{f.gate}` {f.detail}" for f in report.findings if f.level == gates.BLOCKING
            )
            why = f"the test gate went red on {outcome.base} merged in: {failed} (log: {log_path})"
            return PreMergeVerdict(False, _stop_on_conflict(cfg, task_id, outcome, why))
        try:
            docker_exec.adopt_premerge(container, cfg.projects_root, slug, task_id, outcome)
        except Exception as exc:
            return PreMergeVerdict(False, f"task {task_id}: pre-merge passed but was not kept: {exc}")
    finally:
        try:
            docker_exec.drop_premerge_worktree(container, cfg.projects_root, slug, task_id)
        except Exception as exc:
            logger.warning("task %s: could not drop the pre-merge checkout: %s", task_id, exc)
    return _passed(cfg, task_id, f"task {task_id}: pre-merge check: {outcome.detail}, {gated}")


def _passed(cfg: Config, task_id: str, detail: str) -> PreMergeVerdict:
    """A pass; a task the check stopped before is `done` again once it passes."""
    if _task_status(cfg, task_id) == CONFLICT_STATUS:
        try:
            tasks.handoff(
                cfg.hive_tasks_dir, task_id, "done",
                f"{PRE_MERGE_HEADING}\n\nResolved: {detail}.",
            )
        except Exception as exc:
            logger.warning("task %s: could not clear its conflict status: %s", task_id, exc)
    return PreMergeVerdict(True, detail)


def _merge_task_branch(cfg: Config, task_id: str, slug: str) -> bool:
    """Offer a finished task's branch to the branch the project sits on.

    Best-effort in the same sense as the cleanup above: the task is already
    done, the work is already committed on its own branch, and a merge that
    did not happen is a `dispatch merge-task` away. So a refusal is logged
    and the cycle ends normally rather than turning a finished task into a
    crashed one.

    Returns whether the branch landed, because what the task wrote on it —
    its docs, its learnings — is only readable to the next task once it has.
    """
    container = cleanup_container(cfg)
    if container is None:
        return False
    try:
        base = _base_lock(cfg, slug, "merge").acquire()
    except tasks.BaseLockHeldError as exc:
        logger.warning("task %s: not merged: %s", task_id, exc)
        return False
    with base:
        project_dir = f"{cfg.projects_root}/{slug}"
        owner = docker_exec.read_owner(container, project_dir)
        for line in left_behind_report(cfg, task_id):
            logger.warning("%s", line)
        try:
            verdict = pre_merge_check(cfg, container, task_id, slug)
            if not verdict.passed:
                logger.warning("task %s: not merged: %s", task_id, verdict.detail)
                return False
            logger.info("%s", verdict.detail)
            outcome = docker_exec.merge_task_branch(container, cfg.projects_root, slug, task_id)
        except Exception as exc:
            logger.warning("could not merge task %s: %s", task_id, exc)
            return False
        finally:
            docker_exec.restore_owner(container, project_dir, owner)
    if outcome.refused:
        logger.warning("task %s: not merged: %s", task_id, outcome.detail)
        return False
    logger.info("task %s: %s", task_id, outcome.detail)
    recorded, problem = record_merge(cfg, task_id, outcome)
    for line in recorded:
        logger.info("%s", line)
    if problem:
        logger.warning("%s", problem)
    return True


def _task_surface(description: str | None) -> frozenset[str]:
    """The paths a description's *Surface* section names; the whole repo when
    it has none or the description does not parse."""
    if not description:
        return surface.WHOLE_REPO
    try:
        return surface.parse(tasks.parse_description(description).surface)
    except Exception:
        return surface.WHOLE_REPO


def _record_auto_approve(cfg: Config, task_id: str, note: str) -> None:
    """Write the automatic approve's comment, keeping a `conflict` the
    pre-merge check set; every other refusal leaves the task `done`."""
    status = CONFLICT_STATUS if _task_status(cfg, task_id) == CONFLICT_STATUS else "done"
    try:
        tasks.handoff(cfg.hive_tasks_dir, task_id, status, note)
    except Exception as exc:
        logger.warning("task %s: could not record the automatic approve: %s", task_id, exc)


def push_base(cfg: Config, container: str, slug: str) -> docker_exec.PushOutcome:
    """Push a project's base to its upstream after a merge landed, never raising.

    The one place both pushing paths call, the automatic approve's and
    `merge-task --push`'s, and only after the branch is in the base. Run inside
    the caller's read_owner/restore_owner window: a push writes the
    remote-tracking ref under .git/ as root, the same files as the host
    checkout. Anything that raises comes back as a refused push, because a push
    that did not land is what it is whatever the reason (ADR 78).
    """
    try:
        return docker_exec.push_base(
            container,
            cfg.projects_root,
            slug,
            credentials_path=cfg.git_credentials_path,
            keys_dir=cfg.project_keys_dir,
        )
    except Exception as exc:
        return docker_exec.PushOutcome(
            docker_exec.PUSH_REFUSED, None, f"the push could not run: {exc}"
        )


@dataclasses.dataclass(frozen=True)
class ApproveResult:
    """What the automatic approve came to: whether the branch landed, and the
    push of the base after it (None when nothing was merged, so nothing was
    due). The queue (A-5) reads `push.refused` to stop."""

    merged: bool
    push: docker_exec.PushOutcome | None = None


def _auto_approve_and_merge(
    cfg: Config,
    task_id: str,
    slug: str,
    *,
    description: str | None,
    implemented: DispatchResult,
    reviewed: DispatchResult,
    audited: DispatchResult,
    filed: list[tuple[str, debt.Declaration]],
    authorized: bool,
) -> ApproveResult:
    """The automatic approve of `docs/plans/autonomy.md` *Decisions*, 1 (ADR 77).

    Where `_merge_task_branch` merges unconditionally, this merges only when
    every condition of `auto_approve.decide` holds, and writes a comment on
    the task either way: the conditions that failed, or each one checked and
    the merge commit. The pre-merge check runs whatever else failed, so a
    branch that conflicts with its base still moves to `conflict`. Like the
    unconditional merge it never raises and never turns a finished task into
    a crashed one.

    A merge that landed is then pushed to the base's upstream (A-4, ADR 78),
    which `_merge_task_branch` never does. A refused push keeps the merge local
    and the task `done`, and says so in the same comment. Returns whether the
    branch landed and what the push came to.
    """
    entries = _task_surface(description)
    patterns = auto_approve.protected_patterns(
        cfg.projects_root, slug,
        cfg.protected_paths.get(slug, []),
        (cfg.git_credentials_path, cfg.project_keys_dir),
    )
    audit = audited.handoff
    audit_status = audit.get("status") if isinstance(audit, dict) else None
    inputs = dict(
        gates=implemented.gates,
        revisor_approved=revisor_approved(reviewed.result_text, reviewed.handoff),
        revisor_findings=tuple(handoff.findings_of(reviewed.handoff)),
        revisor_debt_open=tuple(
            debt.rejected(reviewed.handoff) + debt.blocking(reviewed.handoff)
        ),
        auditor_status=audit_status.strip().lower() if isinstance(audit_status, str) else None,
        auditor_pending=tuple(handoff.pending(audit)),
        auditor_debt=tuple(d.what for d in debt.declarations(audit)),
        filed_debt=tuple(entry_id for entry_id, _ in filed),
        surface=entries,
        protected=patterns,
        authorized=authorized,
    )

    container = cleanup_container(cfg)
    if container is None:
        decision = auto_approve.decide(auto_approve.Inputs(
            **inputs,
            pre_merge_passed=False,
            pre_merge_detail="no agent container to run it in",
            changed_paths=None,
        ))
        logger.warning("task %s: not merged: no agent container", task_id)
        _record_auto_approve(cfg, task_id, auto_approve.refusal_note(decision))
        return ApproveResult(False)

    # One base operation from the pre-merge check to the push (ADR 80): a
    # verdict on a base that moves before the merge is a verdict on nothing.
    try:
        base = _base_lock(cfg, slug, "merge").acquire()
    except tasks.BaseLockHeldError as exc:
        decision = auto_approve.decide(auto_approve.Inputs(
            **inputs,
            pre_merge_passed=False,
            pre_merge_detail=str(exc),
            changed_paths=None,
        ))
        logger.warning("task %s: not merged: %s", task_id, exc)
        _record_auto_approve(cfg, task_id, auto_approve.refusal_note(decision))
        return ApproveResult(False)
    with base:
        project_dir = f"{cfg.projects_root}/{slug}"
        owner = docker_exec.read_owner(container, project_dir)
        for line in left_behind_report(cfg, task_id):
            logger.warning("%s", line)
        outcome = None
        merge_commit = None
        merge_problem = None
        push = None
        try:
            try:
                verdict = pre_merge_check(cfg, container, task_id, slug)
            except Exception as exc:
                verdict = PreMergeVerdict(False, f"task {task_id}: the pre-merge check could not run: {exc}")
            try:
                changed = docker_exec.branch_changed_paths(
                    container, cfg.projects_root, slug, task_id
                )
            except Exception as exc:
                logger.warning("task %s: could not read the changed paths: %s", task_id, exc)
                changed = None
            decision = auto_approve.decide(auto_approve.Inputs(
                **inputs,
                pre_merge_passed=verdict.passed,
                pre_merge_detail=verdict.detail,
                changed_paths=tuple(changed) if changed is not None else None,
            ))
            if decision.approved:
                try:
                    outcome = docker_exec.merge_task_branch(container, cfg.projects_root, slug, task_id)
                    if not outcome.refused:
                        merge_commit = docker_exec.head_commit(container, project_dir)
                except Exception as exc:
                    merge_problem = f"the merge could not run: {exc}"
                if merge_problem is None and not outcome.refused:
                    push = push_base(cfg, container, slug)
        finally:
            docker_exec.restore_owner(container, project_dir, owner)

    if not decision.approved:
        logger.warning(
            "task %s: not merged: the automatic approve refused on %s",
            task_id, ", ".join(decision.failed_conditions),
        )
        _record_auto_approve(cfg, task_id, auto_approve.refusal_note(decision))
        return ApproveResult(False)
    if merge_problem is None and outcome is not None and outcome.refused:
        merge_problem = outcome.detail
    if merge_problem is not None:
        logger.warning("task %s: not merged: %s", task_id, merge_problem)
        _record_auto_approve(cfg, task_id, auto_approve.merge_failed_note(decision, merge_problem))
        return ApproveResult(False)
    logger.info("task %s: automatic approve: %s", task_id, outcome.detail)
    if push.refused:
        logger.warning("task %s: merged but not pushed: %s", task_id, push.detail)
    else:
        logger.info("task %s: %s", task_id, push.detail)
    _record_auto_approve(
        cfg, task_id,
        auto_approve.approval_note(
            decision, outcome.target or "the project's branch", merge_commit, push
        ),
    )
    recorded, problem = record_merge(cfg, task_id, outcome)
    for line in recorded:
        logger.info("%s", line)
    if problem:
        logger.warning("%s", problem)
    return ApproveResult(True, push)


def record_merge(
    cfg: Config, task_id: str, outcome: docker_exec.MergeOutcome
) -> tuple[list[str], str | None]:
    """Write a merge that landed onto the task, and close its epic if it was the last.

    `docs/plans/task-split.md` section 3, "Merged is recorded on the task":
    the one function both merge paths call, the cycle's and `merge-task`'s,
    and only with an outcome that was not refused. A merge writes the block.
    A branch the target already had writes it only where the file has none,
    so running `merge-task` again keeps the first record and repairs one that
    was never written. The epic check runs either way.

    Returns what it recorded, one line each, and what it could not, or None.
    It never raises: the branch landed whatever happens here, and the
    dependency gate is where a merge with no record is met again.
    """
    hive = cfg.hive_tasks_dir
    recorded = []
    try:
        task = context_transfer.read_task_file(context_transfer.task_file_path(hive, task_id))
        if outcome.merged or not task.merged:
            tasks.mark_merged(hive, task_id, outcome.target)
            recorded.append(f"task {task_id}: recorded as merged into {outcome.target}")
        closed = tasks.close_epic(hive, task_id)
        if closed:
            recorded.append(f"epic {closed}: every sub-task is merged, so it is done")
    except FileNotFoundError:
        return recorded, f"task {task_id}: it has no task file, so the merge is not recorded"
    except Exception as exc:
        return recorded, f"task {task_id}: the merge could not be recorded: {exc}"
    return recorded, None


def _needs_mapping(cfg: Config, slug: str) -> bool:
    """Whether this run should map the project before working on it.

    Three ways to answer no, in order of what they cost: the operator did not
    ask for it, there is no container to ask, or the project already has an
    index. Only the last needs a `docker exec`, and it runs no model.
    """
    if not cfg.mapping_enabled:
        return False
    container = cleanup_container(cfg)
    if container is None:
        return False
    project_dir = f"{cfg.projects_root}/{slug}"
    if project_docs.has_index(container, project_dir):
        return False
    logger.info("project %s has no %s: mapping it first", slug, project_docs.INDEX)
    return True




@dataclasses.dataclass
class CycleContext:
    """What a run settles once, ahead of its first phase, and every phase reads.

    `run-task` and `run-phase` disagree about how many phases follow and about
    almost nothing else: both reap the same stale locks, want the same scratch
    directory and prompt against the same harness fingerprint before their
    first phase, and have to put the learnings back the same
    way after their last one. Until this was an object that bookkeeping lived
    in `run_task_cycle`'s locals, which is why a phase was not a thing the CLI
    could run one of.
    """

    cfg: Config
    task_id: str
    slug: str
    description: str
    task_file: str
    scratch_dir: str
    harness: str
    # Set when a phase bounced off another owner's lock: that run's worktrees
    # are in use, so close_cycle has to keep its hands off them.
    foreign_lock: bool = False
    # Set when a phase the run depends on stopped because no account could
    # take it: the run stops the same as any other stop, but it is held, not
    # failed, and says so in its exit code (ADR 52).
    held: bool = False
    # Set once a phase of this run took the task's lock. Every phase lets go
    # of it before `run_phase` returns, so at the end of a blocked cycle the
    # file cannot say whether this run ever held it; this can.
    took_lock: bool = False
    # Asked before each phase starts; False stops the run there, held, with
    # nothing started and nothing marked. The queue's token limit (A-5, ADR
    # 79) is the one caller: a limit reached mid-cycle starts no new phase.
    may_start_phase: Callable[[], bool] | None = None
    # Set once the auditor has filed what this task learned: `close_cycle`'s
    # `completed`, kept here since the stages after the mapping are their own
    # functions (ADR 119).
    completed: bool = False
    # Set once the scheduler granted this cycle its write window.
    write_window: bool = False


def open_cycle(
    cfg: Config,
    task_id: str,
    slug: str,
    description: str | None = None,
) -> CycleContext | None:
    """Get a run to the point where a phase can be dispatched.

    Returns None when it cannot get there, having already said why. That is a task nobody described, a description missing a
    required section, a hive `dispatch migrate` has not been run on, an epic,
    a task still in the backlog or with a `priority` or `source` a hand edit
    got wrong (ADR 127), a task with a dependency that is not merged, and a
    child of a malformed epic. Nothing is marked on a task refused here: it stays as it was.
    A draft set's id is refused too: a draft runs only once accepted, under
    the task id `accept-drafts` gives it (ADR 82).
    """
    if tasks.is_draft_id(task_id):
        logger.error(
            "%s names drafts, not a task: accept them with `dispatch accept-drafts %s` "
            "and run the task ids that prints",
            task_id, task_id,
        )
        return None
    # The whole-hive rule (`docs/business.md` *Task descriptions*): one
    # unmigrated task file stops every cycle. `tasks.py`'s writer refuses such
    # a file itself, so the ask is safe either way; this check is what makes
    # the refusal one logged line before anything is written, not a traceback
    # out of `reap_expired_locks` or the first phase's lock.
    stale = tasks.unmigrated_ids(cfg.hive_tasks_dir)
    if stale:
        logger.error(
            "%d task file(s) still carry their description in the frontmatter (%s%s): "
            "run `%s` first",
            len(stale),
            ", ".join(stale[:5]),
            f" and {len(stale) - 5} more" if len(stale) > 5 else "",
            tasks.MIGRATE_COMMAND,
        )
        return None
    reap_expired_locks(cfg)
    task_file = context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)
    # Made by the dispatcher, not by the roles: a phase told to write its
    # detail somewhere should find the somewhere already there.
    scratch_dir = context_transfer.ensure_scratch_dir(cfg.hive_tasks_dir, task_id)
    # Same reason, one directory over: the phases are told to write a trap
    # into the inbox the moment they hit it, so the inbox has to exist before
    # the first of them runs. The reconcile is the confirmation pass — it
    # runs no model, it only notices that a second task has now reported an
    # error some earlier task reported alone.
    learnings.ensure_dirs(cfg.hive_tasks_dir)
    # What this run lets a phase do, as one string. An entry written when the
    # answer was different is still shown, but it stops counting as evidence:
    # two of the entries in this harness's own inbox went false exactly this
    # way, when a permission they described as missing was wired up.
    harness = learnings.harness_fingerprint(
        cfg.permission_mode, cfg.allowed_tools, docker_exec.WRITER_ROLES
    )
    learnings.reconcile(cfg.hive_tasks_dir, harness)

    supplied = description is not None
    if not supplied:
        # A resume (or a task seeded by an earlier `run-task`) already has
        # the ask on disk; nothing to write.
        description = tasks.read_description(cfg.hive_tasks_dir, task_id)
    if not (description or "").strip():
        # Dispatching here would burn quota on a role that was told a task id
        # and nothing else. The CLI rejects this case before it gets here;
        # this is the backstop for library callers.
        logger.error(
            "task %s has no description: pass --description/--description-file to run-task, "
            "or write one to %s",
            task_id,
            tasks.description_path(cfg.hive_tasks_dir, task_id),
        )
        return None
    try:
        # A stored one is parsed too: a hand-edited `description.md` missing a
        # section stops here, with the section named, rather than at a phase.
        tasks.parse_description(description)
    except tasks.DescriptionError as exc:
        logger.error("task %s: the task description %s", task_id, exc)
        return None
    # The gate of `docs/plans/task-split.md` section 4: a dependency counts
    # once it is merged, and a hand-written epic gets the checks a filed split
    # got. Before the supplied description is written, so a refused run
    # leaves nothing behind. A task with no file yet has nothing to gate; one
    # that will not parse raises from the first phase's lock, as it always did.
    try:
        task = context_transfer.read_task_file(task_file)
    except FileNotFoundError:
        task = None
    if task is not None:
        if task.status == "epic":
            # An epic is run through its sub-tasks (`docs/decisions.md` ADR
            # 60). Here and not in `run_task_cycle` alone, because `run-phase`
            # comes through too and its handoff would write `pending` over
            # the `epic`.
            logger.error(
                "task %s is an epic, split into %s: run its sub-tasks, not the epic",
                task_id, _split_into(task),
            )
            return None
        # A backlog task first (ADR 127): it is not accepted, so whether its
        # dependencies are merged is not the question yet.
        reasons = (
            tasks.dispatch_problems(task)
            + tasks.unmet_dependencies(cfg.hive_tasks_dir, task)
            + tasks.epic_problems(cfg.hive_tasks_dir, task)
        )
        if reasons:
            logger.error("task %s cannot start: %s", task_id, "; ".join(reasons))
            return None
    if supplied:
        tasks.write_description(cfg.hive_tasks_dir, task_id, description)

    return CycleContext(
        cfg=cfg,
        task_id=task_id,
        slug=slug,
        description=description,
        task_file=task_file,
        scratch_dir=scratch_dir,
        harness=harness,
    )


def _adr_block_note(cfg: Config, task_id: str, slug: str) -> str:
    """The arquitecto's line naming its ADR numbers (ADR 81), or "" when the
    block could not be taken: the phase runs anyway, and the pre-merge
    check's decisions-log gate still stops a number used twice."""
    container = cleanup_container(cfg)
    if container is None:
        return ""
    try:
        proc = docker_exec.run_docker_exec(
            container, f"{cfg.projects_root}/{slug}", ["cat", "--", project_docs.DECISIONS],
        )
        block = adr_numbers.reserve(
            cfg.state_dir, slug, task_id, proc.stdout if proc.returncode == 0 else None,
        )
    except Exception as exc:
        logger.warning("task %s: no ADR numbers reserved: %s", task_id, exc)
        return ""
    logger.info("task %s: ADR numbers %d to %d", task_id, block.first, block.last)
    return block.prompt_line()


def run_phase(
    ctx: CycleContext,
    role: str,
    round_num: int | None = None,
    final: bool = False,
    fatal: bool = True,
    model: str | None = None,
    max_turns: int | None = None,
    note: str = "",
    repeated_finding: bool = False,
    resume_session_id: str | None = None,
    prompt: str | None = None,
    account: str | None = None,
) -> DispatchResult | None:
    """One role's turn, with everything that has to happen around it.

    The status block, the account and card locks, the worktree, the commit,
    the gates and the handoff the next phase reads — all of it per phase, none
    of it per cycle, which is what lets `run-phase` exist at all.
    `repeated_finding` is the cycle's signal for `Config.effort_for`.

    `resume_session_id`, `prompt` and `account` are a command request's
    verdict resuming the phase that filed it (ADR 119): that session, on that
    account only, told the verdict instead of the role's prompt. Everything
    after the call is the same as for a fresh phase.
    """
    cfg = ctx.cfg
    if ctx.may_start_phase is not None and not ctx.may_start_phase():
        logger.warning(
            "task %s: %s not started, the caller's limit was reached", ctx.task_id, role
        )
        ctx.held = True
        return None
    effort = cfg.effort_for(role, round_num, repeated_finding=repeated_finding)
    if role == "arquitecto":
        note = "\n\n".join(part for part in (_adr_block_note(cfg, ctx.task_id, ctx.slug), note) if part)
    try:
        result = dispatch_phase(
            cfg, ctx.task_id, ctx.slug, role,
            prompt=prompt if prompt is not None else _role_prompt(
                role, ctx.task_id, ctx.slug, ctx.task_file, ctx.description,
                ctx.scratch_dir, cfg.hive_tasks_dir,
                harness=ctx.harness,
                round_num=round_num, max_turns=max_turns, note=note,
            ),
            resume_session_id=resume_session_id,
            model=model or cfg.model_for(role),
            effort=effort,
            round_num=round_num,
            max_turns=max_turns,
            only_account=account,
        )
    except context_transfer.LockHeldError as exc:
        # A re-run within the TTL after a Ctrl+C, or a second dispatcher
        # process, hits this on every phase. Stop the run instead of
        # letting the LockHeldError climb out as a CLI traceback. The
        # foreign lock is left alone: after a Ctrl+C the in-container
        # claude keeps running until its own timeout, so the heartbeat
        # TTL, not this run, decides when the task can be taken over.
        logger.warning("task %s is locked by another owner: %s", ctx.task_id, exc)
        ctx.foreign_lock = True
        return None
    if not result.no_account:
        # The one return that proves `tasks.acquire_lock` succeeded: an
        # account ran the phase, whatever became of it.
        ctx.took_lock = True
    if not result.success:
        # A phase pinned to one account (a command request's verdict, ADR 119)
        # that found it taken never ran: held, never an optional phase that
        # failed, which would hand the task off as if the phase had run
        # (ADR 120).
        if fatal or (account is not None and result.no_account):
            # Said out loud because otherwise nothing says it: this return is
            # the whole of the cycle's failure path, and without a line here a
            # timed-out or crashed phase ends the run at exit 0 with the last
            # log entry being "goes to account …". `result_text` already
            # carries the diagnosis — "claude timed out after 1800s", the
            # exit code and output tail, or the CLI's own error — and
            # throwing it away costs an hour of reconstruction from a
            # worktree. The round belongs in the label: "implementador"
            # alone does not say which of three attempts died.
            logger.error(
                "task %s: %s did not finish, so the cycle stops here: %s",
                ctx.task_id,
                role if round_num is None else f"{role} (round {round_num})",
                result.result_text or "no diagnosis: the phase produced no output",
            )
            ctx.held = result.no_account
            return None
        # A phase the task does not depend on. It still hands off what it
        # managed — a partial map is worth having, and the next phase
        # should know it is partial — but it does not stop the run.
        logger.warning(
            "optional phase %s did not finish for task %s: %s",
            role, ctx.task_id, result.result_text,
        )
    label = role if round_num is None else f"{role} (round {round_num})"
    body = handoff.body(label, result.result_text, result.handoff)
    # Under the phase's own return, not instead of it: the role says what
    # it did and the dispatcher says what it found, and the next phase to
    # read this file can tell the two apart.
    gate_section = result.gates.render() if result.gates is not None else ""
    if gate_section:
        body = f"{body}\n\n{gate_section}"
    tasks.handoff(
        cfg.hive_tasks_dir, ctx.task_id,
        new_status="done" if final else "pending",
        body=body,
    )
    # The prose above is for whoever reads next; this is for whoever has to
    # act. `result.handoff` is the parsed return, and the debt a task declares,
    # accepts and resolves lives in it as structure — none of which survives
    # the rendering. Written here rather than in the cycle because this is the
    # one place every phase passes through, whichever entry point dispatched
    # it, which is what lets a task driven a phase at a time still be closed.
    # The denials ride along for the same reason: the envelope is the one
    # record of this phase's answer, and what it was refused is part of
    # reading that answer (`docs/decisions.md` ADR 116).
    # The checked command requests too, for the roles that can file one, so
    # the steps after this one read the verdict on each without checking it
    # again (ADR 117), resolved to a target and past the denylist (ADR 118).
    requests: dict[str, object] = {}
    if handoff.files_command_requests(role):
        accepted, refused = _checked_command_requests(ctx, result)
        result.command_requests = (accepted, refused)
        requests["command_requests"] = {"accepted": accepted, "refused": refused}
    context_transfer.save_handoff(
        cfg.hive_tasks_dir, ctx.task_id, role, result.handoff, round_num=round_num,
        permission_denials=result.permission_denials, **requests,
        # What a verdict on a request filed here needs to resume this call.
        session_id=result.session_id, account=result.account,
    )
    return result


def close_cycle(ctx: CycleContext, completed: bool) -> None:
    """Put back what the run borrowed, whichever way it ended.

    `completed` means the auditor filed what this task learned. Every other
    way out — done-but-not-audited, blocked, bounced, crashed — leaves entries
    nobody filed, and those have to go back to being unowned.
    """
    # Every way into here is terminal for this run — done, blocked, or a
    # crash — and the reviewing checkouts are rebuilt on demand, so they can
    # go now rather than pile up per task. The exception is a task this run
    # never owned: another dispatcher is still working in those worktrees.
    if ctx.foreign_lock:
        return
    _drop_review_worktrees(ctx.cfg, ctx.task_id, ctx.slug)
    # Whether or not it finished: what a phase was allowed to do while
    # it wrote these is settled, and stamping it here is the only way
    # a later run can tell an entry that aged out from one that holds.
    learnings.stamp(ctx.cfg.hive_tasks_dir, ctx.task_id, ctx.harness)
    if not completed:
        # The entries stay — this task is the reason nobody has
        # filed them yet — but they stop claiming a carrier that is
        # gone, and a status this task alone vouched for goes back
        # to being one phase's word.
        learnings.mark_orphaned(ctx.cfg.hive_tasks_dir, ctx.task_id)


def _mark_blocked(ctx: CycleContext, reason: str | None = None) -> None:
    """Write `blocked` on the task of a cycle that stopped blocked, if it was this run's.

    The user's ruling of 2026-10-11 (`docs/business.md` *Task status*). Only a
    run that took the task's lock marks it, and never one that bounced off
    another owner's. An owner on the file now is a run that took the task
    since this one's last phase let go, and `tasks.mark_blocked` would release
    its lock, so that is left alone too. No task file means nothing to mark.
    `reason` becomes the file's `blocked_reason` (ADR 117). It never raises:
    the cycle has ended, and a traceback here would replace the BLOCKED exit
    with a crash.
    """
    if not ctx.took_lock or ctx.foreign_lock:
        return
    try:
        if context_transfer.read_task_file(ctx.task_file).owner is not None:
            return
        tasks.mark_blocked(ctx.cfg.hive_tasks_dir, ctx.task_id, reason=reason)
    except FileNotFoundError:
        return
    except Exception as exc:
        logger.warning("task %s: could not be marked blocked: %s", ctx.task_id, exc)


def _checked_command_requests(
    ctx: CycleContext, result: DispatchResult,
) -> tuple[list[dict], list[dict]]:
    """The phase's command requests after step 2's check and step 3's.

    Step 2 (`handoff.command_requests`) reads the shape; step 3
    (`command_targets.check`) resolves each accepted one against the account
    the phase ran on and the task's writer worktree, and runs the denylist.
    What fails either is refused with its reason (ADR 117, ADR 118).
    """
    # Step 2 first: with nothing accepted there is nothing to resolve, and the
    # context is not read.
    accepted, refused = handoff.command_requests(result.handoff)
    if not accepted:
        return accepted, refused
    return _command_request_lists(ctx.cfg, result.account, ctx.slug, ctx.task_id, result.handoff)


def _command_request_lists(
    cfg: Config, account_name: str, slug: str, task_id: str, parsed: dict | None,
) -> tuple[list[dict], list[dict]]:
    """Step 2's and step 3's checks on one handoff, from what both callers have.

    Shared by the record `run_phase` writes and the gates' skip in
    `_run_gates`, so the two never disagree on whether a request stands. The
    project's running services are read from the dind only if a `service`
    request asks for one.
    """
    accepted, refused = handoff.command_requests(parsed)
    if not accepted:
        return accepted, refused
    account = next((acc for acc in cfg.accounts if acc.name == account_name), None)
    project_dir = f"{cfg.projects_root}/{slug}"
    return command_targets.check(
        accepted, refused, account, cfg.accounts,
        command_targets.writer_worktree(cfg.projects_root, slug, task_id),
        lambda: command_targets.running_services(account.dind, project_dir),
    )


def _filed_command_requests(ctx: CycleContext, result: DispatchResult | None) -> bool:
    """Whether the phase filed a command request the cycle has to stop for.

    One accepted request is enough (`docs/plans/command-requests.md`,
    decision 2; ADR 117). Refused ones never block: they are logged here and
    recorded in the envelope `run_phase` saved, and the cycle goes on as if
    none had been filed. A phase that did not return files nothing. The lists
    are the ones `run_phase` checked and recorded, so a request resolution or
    the denylist refused (ADR 118) does not block either.
    """
    if result is None:
        return False
    # A result built by hand (a test's stand-in) may lack the field.
    checked = getattr(result, "command_requests", None)
    if checked is not None:
        accepted, refused = checked
    else:
        accepted, refused = _checked_command_requests(ctx, result)
    if refused:
        logger.warning(
            "task %s: refused %d command request(s): %s",
            ctx.task_id, len(refused), "; ".join(item["reason"] for item in refused),
        )
    if not accepted:
        return False
    logger.warning(
        "task %s: blocked on %d command request(s): %s",
        ctx.task_id, len(accepted), "; ".join(" ".join(item["argv"]) for item in accepted),
    )
    return True


def _split_into(task: context_transfer.TaskFile) -> str:
    """An epic's sub-task ids as a log line names them."""
    ids = task.split_into
    if not isinstance(ids, list) or not ids:
        return "no sub-tasks"
    return ", ".join(str(item) for item in ids)


def _file_split(ctx: CycleContext, subtasks: list[handoff.SubTask]) -> CycleOutcome:
    """File the arquitecto's split, or say why it cannot be filed.

    `docs/decisions.md` ADR 60. SPLIT when the sub-tasks are on disk and the
    task is their epic, BLOCKED otherwise, with nothing written. It marks
    nothing either way: a cycle marks its own block, and `file-split` has no
    cycle. A `done` or `epic` task is not split (`docs/business.md`,
    *Task status*), and neither is one a live run
    still holds, since that run's next handoff would write `pending` over the
    `epic` and filing again does not repair a parent.
    """
    cfg = ctx.cfg
    problems = handoff.split_problems(subtasks)
    try:
        parent = context_transfer.read_task_file(ctx.task_file)
    except FileNotFoundError:
        parent = None  # `tasks.file_split` reports it.
    if parent is not None:
        if parent.status in ("done", "epic"):
            problems.append(
                f"task {ctx.task_id} is {parent.status}; only a pending, in_progress or "
                "blocked task can be split"
            )
        elif tasks._lock_is_live(parent, cfg.heartbeat_ttl_seconds):
            problems.append(
                f"task {ctx.task_id} is locked by {parent.owner}; file the split once "
                "that run has ended"
            )
    if not problems:
        try:
            ids = tasks.file_split(cfg.hive_tasks_dir, ctx.task_id, subtasks)
        except tasks.SplitError as exc:
            problems = list(exc.problems)
    if problems:
        logger.warning(
            "task %s: the split cannot be filed: %s", ctx.task_id, "; ".join(problems)
        )
        return CycleOutcome.BLOCKED
    logger.info(
        "task %s is now an epic: %s; run them in that order", ctx.task_id, ", ".join(ids)
    )
    return CycleOutcome.SPLIT


def file_saved_split(cfg: Config, task_id: str, slug: str) -> CycleOutcome:
    """File the split in a task's saved arquitecto handoff, running no model.

    The `file-split` verb: what `run-phase --role arquitecto` proposed, or what
    a cycle that died before filing left behind. It opens no cycle, so nothing
    is reaped, reconciled or marked, and a refusal leaves the task as it was.
    """
    hive = cfg.hive_tasks_dir
    payload = context_transfer.read_handoff(hive, task_id, "arquitecto")
    if payload is None:
        logger.error(
            "task %s: no saved arquitecto handoff at %s",
            task_id, context_transfer.handoff_path(hive, task_id, "arquitecto"),
        )
        return CycleOutcome.BLOCKED
    if handoff.blocked(payload):
        logger.error(
            "task %s: its arquitecto blocked, and a block wins over a split", task_id
        )
        return CycleOutcome.BLOCKED
    subtasks = handoff.split_of(payload)
    if not subtasks:
        logger.error("task %s: its arquitecto handoff has no split", task_id)
        return CycleOutcome.BLOCKED
    ctx = CycleContext(
        cfg=cfg,
        task_id=task_id,
        slug=slug,
        description=tasks.read_description(hive, task_id) or "",
        task_file=context_transfer.task_file_path(hive, task_id),
        scratch_dir=context_transfer.scratch_dir(hive, task_id),
        harness=learnings.harness_fingerprint(
            cfg.permission_mode, cfg.allowed_tools, docker_exec.WRITER_ROLES
        ),
    )
    return _file_split(ctx, subtasks)


def run_single_phase(
    cfg: Config,
    task_id: str,
    slug: str,
    role: str,
    round_num: int | None = None,
    final: bool = False,
    description: str | None = None,
    note: str = "",
) -> DispatchResult | None:
    """Dispatch exactly the phase named, on a task that is already under way.

    The repair path. `run_task_cycle` always begins at the arquitecto, so a
    run interrupted in the revisor's second round could only be continued by
    paying for every phase again — which is what a host reboot did to T-008 on
    2026-09-25. This runs the one phase that is missing.

    What it deliberately does not do is the cycle's own judgement: it reads no
    verdict, runs no further round and merges nothing. Those stay `run-task`'s,
    and the operator is told so rather than left to assume. `final=True` is the
    operator saying this phase closes the task — in the role set, the auditor —
    and buys what the full cycle gives its last phase: the learnings carried in
    beforehand, the debt the earlier phases declared recorded and filed,
    `status: done` in the task file, and no orphaning on the way out.

    The debt half of that is only possible because `run_phase` persists each
    phase's structured return, so the implementador's and the revisor's answers
    are still readable in a later process. A task whose earlier phases ran
    before that existed has no such record; it closes the way this path always
    did, with nothing filed, and the log line says which of the two happened.

    None means the phase did not land. A phase that
    never ran because no account could take it comes back instead as an
    unsuccessful result with `no_account` set — it did not land either,
    but the CLI exits on it with the held code, not the failed one.
    """
    ctx = open_cycle(cfg, task_id, slug, description=description)
    if ctx is None:
        return None
    result: DispatchResult | None = None
    try:
        if final:
            # Stamped before the phase that files them, exactly as the full
            # cycle does it, so the auditor's prompt can name the entries it
            # owns and a later run can tell an entry that was handed to a task
            # from one nobody has picked up yet.
            learnings.carry(cfg.hive_tasks_dir, slug, task_id)
            # The two steps the full cycle runs between the carry and its last
            # phase, off what `run_phase` left on disk rather than off results
            # this call never held. Written before the phase for the same
            # reason: the ids are assigned here so the auditor's prompt can
            # name them.
            implemented = context_transfer.read_handoff(
                cfg.hive_tasks_dir, task_id, "implementador"
            )
            reviewed = context_transfer.read_handoff(cfg.hive_tasks_dir, task_id, "revisor")
            # Only when there is a record to write from: set_resolved_debt
            # replaces, so a task with no persisted implementador handoff would
            # otherwise have a hand-set list erased by a call that knows
            # nothing.
            if implemented is not None:
                tasks.set_resolved_debt(
                    cfg.hive_tasks_dir, task_id, debt.resolved(implemented)
                )
            filed = _file_accepted_debt(cfg, task_id, slug, implemented, reviewed)
            # Appended rather than replaced: `--note` is the operator's
            # instruction to this phase and the filing note is the
            # dispatcher's, and the phase is owed both.
            note = "\n\n".join(part for part in (note, debt.filing_note(filed)) if part)
        result = run_phase(ctx, role, round_num=round_num, final=final, note=note)
        if result is None:
            if ctx.held:
                # Handed back rather than flattened to None, so the CLI can
                # tell a phase that waits on an account from one that failed:
                # `no_account` is the whole difference, and it exits on it.
                # Never a success, so `completed` below stays False.
                return DispatchResult(
                    success=False, session_id=None,
                    result_text="no accounts available", account="", no_account=True,
                )
            return None
        if final:
            # Said out loud because the difference is invisible from the task
            # file: a task closed this way was closed by a phase, not by a
            # cycle that read a verdict, and the merge a finished `run-task`
            # can do has not happened. Which handoffs were found is part of
            # that difference — "no debt filed" means something very different
            # when there was a record to file from.
            if implemented is None and reviewed is None:
                logger.info(
                    "task %s: closed by a single %s phase. No stored handoff was found "
                    "for the implementador or the revisor, so no resolved debt was "
                    "recorded and no debt was filed; phases run before the "
                    "dispatcher started storing them leave no record to read. No merge "
                    "was attempted — use `merge-task` when the branch is ready.",
                    task_id, role,
                )
            else:
                logger.info(
                    "task %s: closed by a single %s phase, off the stored handoffs "
                    "(implementador: %s, revisor: %s). %d debt entr(y/ies) named for filing. No merge "
                    "was attempted — use `merge-task` when the branch is ready.",
                    task_id, role,
                    "found" if implemented is not None else "missing",
                    "found" if reviewed is not None else "missing",
                    len(filed),
                )
        return result
    finally:
        close_cycle(ctx, completed=final and result is not None)


# --- The write window (`docs/plans/autonomy.md` decision 6, task B-3, ADR 83) ---
#
# With cycles in parallel, the base a task's arquitecto planned against can
# move before its implementador starts: another task of the queue merged in
# the meantime. So a cycle that runs under a scheduler merges the base into
# its branch as it enters its write window, and tells the implementador when
# what came in touches what the plan names.


@dataclasses.dataclass(frozen=True)
class BaseMove:
    """What merging the base into a task branch at its write window brought in.

    `changed` is the paths the merge changed on the branch; `conflicts` the
    files a merge that was aborted would have conflicted in, in which case the
    branch did not move. `merged_tasks` names the task merges the base gained
    since the branch was cut, read from their merge commits.
    """

    changed: tuple[str, ...]
    merged_tasks: tuple[str, ...]
    conflicts: tuple[str, ...] = ()
    detail: str = ""


#: The subject `docker_exec.merge_task_branch` gives a task's merge commit.
_TASK_MERGE_SUBJECT = re.compile(r"^Merge agent/task/(\S+)$")


def _git_lines(container: str, workdir: str, argv: list[str]) -> list[str]:
    proc = docker_exec.run_docker_exec(container, workdir, argv, env=docker_exec._GIT_ENV)
    if proc.returncode != 0:
        return []
    return [line for line in proc.stdout.replace("\0", "\n").splitlines() if line.strip()]


def merge_base_at_write_window(cfg: Config, task_id: str, slug: str) -> BaseMove | None:
    """Merge the base into the task branch before its first implementador.

    None when nothing moved — the base is already in the branch — or when the
    merge could not be tried (no container, no branch, a git error, logged):
    either way no note is given and no phase is spent. A clean merge is kept
    on the branch without a gate, since the implementador's own gates run on
    it next; a conflict is aborted and left for the pre-merge check, and is
    reported so the implementador hears of it. Under the base lock (ADR 80):
    it reads the base and moves a ref while the base may be merging.
    `tasks.BaseLockHeldError` is the caller's.
    """
    container = cleanup_container(cfg)
    if container is None:
        return None
    project_dir = f"{cfg.projects_root}/{slug}"
    with _base_lock(cfg, slug, "write-window merge"):
        owner = docker_exec.read_owner(container, project_dir)
        try:
            outcome = docker_exec.premerge_base(container, cfg.projects_root, slug, task_id)
            if outcome.refused or outcome.up_to_date:
                logger.info("task %s: write window: %s", task_id, outcome.detail)
                return None
            merged = tuple(
                match.group(1)
                for subject in _git_lines(
                    container, project_dir,
                    ["git", "log", "--merges", "--format=%s", f"{outcome.tip}..{outcome.base_commit}"],
                )
                if (match := _TASK_MERGE_SUBJECT.match(subject.strip()))
            )
            if outcome.conflicted:
                logger.warning("task %s: write window: %s", task_id, outcome.detail)
                return BaseMove((), merged, outcome.conflicts, outcome.detail)
            try:
                changed = tuple(_git_lines(
                    container, outcome.workdir,
                    ["git", "diff", "--name-only", "--no-renames", "-z",
                     outcome.tip, outcome.merged_tip],
                ))
                docker_exec.adopt_premerge(container, cfg.projects_root, slug, task_id, outcome)
            finally:
                docker_exec.drop_premerge_worktree(container, cfg.projects_root, slug, task_id)
            logger.info("task %s: write window: %s", task_id, outcome.detail)
            return BaseMove(changed, merged, detail=outcome.detail)
        except Exception as exc:
            # The pre-merge check before the merge still catches a conflict;
            # what is lost is only the warning to the implementador.
            logger.warning("task %s: write window: the base could not be merged in: %s", task_id, exc)
            return None
        finally:
            docker_exec.restore_owner(container, project_dir, owner)


def _plan_entries(arquitecto_handoff: dict | None) -> frozenset[str]:
    """The paths the arquitecto's handoff names under Changed, as surface entries.

    A line is "by path or symbol", so a path is its first token or a
    backticked one, and a symbol is dropped the way `surface.parse` drops it.
    """
    entries: set[str] = set()
    for line in handoff._lines(arquitecto_handoff or {}, "changed"):
        tokens = [line.split()[0].rstrip(":,")] if line.split() else []
        tokens += re.findall(r"`([^`\n]+)`", line)
        entries.update(
            token.strip() for token in tokens if surface._looks_like_path(token.strip())
        )
    return frozenset(entries)


def stale_plan_note(
    move: BaseMove | None, task_surface: frozenset[str], plan: frozenset[str],
) -> str | None:
    """The implementador's note when the base moved under the plan, or None.

    Only the paths the merge changed (or conflicted in) that the task's
    Surface or the arquitecto's Changed lines cover count; the append-only
    logs never do. A conflict always counts: it is the base against the
    arquitecto's own commits.
    """
    if move is None:
        return None

    def named(path: str) -> bool:
        if surface.is_append_only(path):
            return False
        return surface.covers(task_surface, path) or (bool(plan) and surface.covers(plan, path))

    touched = [path for path in move.changed if named(path)]
    if not touched and not move.conflicts:
        return None
    who = ", ".join(move.merged_tasks) if move.merged_tasks else "work outside this queue's tasks"
    lines = [
        "## The base moved under the plan",
        "",
        f"While this task waited for its write window, {who} merged into the base.",
    ]
    if touched:
        lines.append(
            "Merging the base into this task's branch changed paths the plan names: "
            + ", ".join(f"`{p}`" for p in touched) + "."
        )
    if move.conflicts:
        lines.append(
            "Merging the base in conflicts with this branch in "
            + ", ".join(f"`{p}`" for p in move.conflicts)
            + ", so it was not merged; the pre-merge check will stop on it."
        )
    lines += [
        "",
        "The arquitecto planned before that. Read what changed on those paths "
        "before you implement. If the plan no longer holds, do not improvise a "
        "new one: end with `status: blocked` and say in `pending` what has to "
        "be replanned.",
    ]
    return "\n".join(lines)


def run_task_cycle(
    cfg: Config,
    task_id: str,
    slug: str,
    description: str | None = None,
    authorized: bool = True,
    on_approve: Callable[[ApproveResult], None] | None = None,
    may_start_phase: Callable[[], bool] | None = None,
    scheduler=None,
) -> CycleOutcome:
    """Run a task from the arquitecto to the auditor, and say how it ended.

    HELD when a phase the task depends on stopped because no account could
    take it, SPLIT when the arquitecto's split was filed and the task is now
    an epic, BLOCKED for every other stop, FINISHED when the auditor closed
    it — merged or not, since a refused merge is reported by `merge-task` and
    leaves the task done.

    `authorized` says the cycle was started by a person's authorization, one
    condition of the automatic approve (ADR 77). `run-task` is a command the
    operator typed, so it is the default; a caller that starts cycles on its
    own passes False and its tasks are never merged automatically.

    `on_approve` is handed the ApproveResult when the automatic approve ran,
    so a caller can read what the outcome does not carry: the queue (A-5)
    stops on `push.refused`. `may_start_phase` is asked before every phase;
    False stops the cycle HELD before that phase starts (CycleContext).

    `scheduler` is the parallel queue's (B-3, ADR 83), None everywhere else,
    and None runs the cycle exactly as before. With one, the mapping runs once
    for the queue (`scheduler.mapping_lock`, `scheduler.mapped_by`), and the
    cycle enters its write window before the first implementador
    (`scheduler.enter_write_window(task_id, surface)`, which waits for the
    surface lock) and leaves it when it ends (`leave_write_window(task_id)`).
    """
    ctx = open_cycle(cfg, task_id, slug, description=description)
    if ctx is None:
        return CycleOutcome.BLOCKED
    ctx.may_start_phase = may_start_phase
    def blocked(reason: str | None = None) -> CycleOutcome:
        return _blocked_outcome(ctx, reason)

    try:
        try:
            update = update_project_branch(cfg, slug)
        except tasks.BaseLockHeldError as exc:
            # Another operation held the base past the wait (ADR 80): the
            # task has not started, so it stops before anything is cut.
            logger.warning("task %s: %s", task_id, exc)
            return CycleOutcome.BLOCKED
        if update is not None:
            if update.blocking:
                # Before any worktree exists, so nothing has been cut from the
                # base this would have moved. A divergence is two histories and
                # one of them is nobody's to pick here: the charter's C-1 has a
                # phase that needs a decision the task never gave it end blocked
                # and say so, and this is the same decision one step earlier.
                logger.warning(
                    "task %s: the project's own branch diverged from its remote: %s",
                    task_id, update.detail,
                )
                return CycleOutcome.BLOCKED
            # Said out loud either way: a task that started from a stale base is
            # the thing this exists to prevent, and a run that could not reach
            # the remote has to be readable as such afterwards.
            log = logger.warning if update.status == docker_exec.UNAVAILABLE else logger.info
            log("task %s: %s", task_id, update.detail)
        if scheduler is not None:
            if _needs_mapping(cfg, slug):
                # One mapping per queue: a second task that needs one waits
                # for the first and asks again. The first's map is on its own
                # branch until it merges, so asking again can still say no
                # index; the queue has mapped once, and this task runs
                # unmapped as a failed mapping would leave it.
                mapped = None
                with scheduler.mapping_lock:
                    if _needs_mapping(cfg, slug):
                        if scheduler.mapped_by is not None:
                            logger.info(
                                "task %s: not mapped again: task %s mapped %s in this queue, "
                                "and its map lands with its merge",
                                task_id, scheduler.mapped_by, slug,
                            )
                        else:
                            scheduler.mapped_by = task_id
                            mapped = run_phase(
                                ctx,
                                project_docs.MAPPER_ROLE,
                                fatal=False,
                                model=cfg.mapping_model,
                                max_turns=cfg.mapping_max_turns,
                            )
                if ctx.foreign_lock:
                    return CycleOutcome.BLOCKED
                if _filed_command_requests(ctx, mapped):
                    return blocked(handoff.COMMAND_REQUEST)
        elif _needs_mapping(cfg, slug):
            # Before the arquitecto, because what it writes is what the
            # arquitecto reads. Optional in both directions: the operator has
            # to turn it on, and if it fails the task runs anyway on a project
            # that stays unmapped.
            mapped = run_phase(
                ctx,
                project_docs.MAPPER_ROLE,
                fatal=False,
                model=cfg.mapping_model,
                max_turns=cfg.mapping_max_turns,
            )
            if ctx.foreign_lock:
                return CycleOutcome.BLOCKED
            if _filed_command_requests(ctx, mapped):
                return blocked(handoff.COMMAND_REQUEST)

        return _cycle_from_arquitecto(ctx, authorized, on_approve, scheduler)
    finally:
        try:
            close_cycle(ctx, completed=ctx.completed)
        finally:
            if ctx.write_window:
                scheduler.leave_write_window(task_id)


def _stopped(ctx: CycleContext) -> CycleOutcome:
    """The stop after a phase that returned None: the phase knows whether it was held."""
    return CycleOutcome.HELD if ctx.held else _blocked_outcome(ctx)


def _blocked_outcome(ctx: CycleContext, reason: str | None = None) -> CycleOutcome:
    """Every stop that leaves the task blocked after a phase of this run took its lock.

    `_mark_blocked` decides whether there is anything to write, so a foreign
    lock goes through here too and marks nothing.
    """
    _mark_blocked(ctx, reason)
    return CycleOutcome.BLOCKED


def _cycle_from_arquitecto(
    ctx: CycleContext,
    authorized: bool,
    on_approve: Callable[[ApproveResult], None] | None,
    scheduler=None,
) -> CycleOutcome:
    """`run_task_cycle` from the arquitecto on: the plan, the write window, the rounds.

    Its own function so a verdict's resume of the mapping (ADR 119) carries on
    from here through the same code a cycle does. The caller leaves the write
    window this enters, on `ctx.write_window`.
    """
    cfg, task_id, slug = ctx.cfg, ctx.task_id, ctx.slug
    arquitecto = run_phase(ctx, "arquitecto")
    if arquitecto is None:
        return _stopped(ctx)
    if handoff.blocked(arquitecto.handoff):
        # The one phase whose block costs less than the work it stops. The
        # arquitecto reads the plan, the charter and the indexes before
        # anything is written, so a decision the task was never given is
        # visible here and nowhere earlier — and an implementador released
        # without it does not stall, it invents one, and the revisor then
        # reviews the invention.
        missing = handoff.pending(arquitecto.handoff)
        logger.warning(
            "task %s: the arquitecto blocked before implementation: %s",
            task_id, "; ".join(missing) or "no reason given",
        )
        return _blocked_outcome(ctx)
    subtasks = handoff.split_of(arquitecto.handoff)
    if subtasks:
        # The arquitecto's other early end (ADR 60): the task is more than
        # one. A split that was filed leaves the task `epic` and is not a
        # block; one that could not be filed is, and is marked like any.
        outcome = _file_split(ctx, subtasks)
        return _blocked_outcome(ctx) if outcome is CycleOutcome.BLOCKED else outcome

    stale_note = None
    if scheduler is not None:
        # The write window: from here to the merge, no task whose surface
        # overlaps this one's writes (ADR 83). Left in the `finally`.
        task_surface = _task_surface(ctx.description)
        scheduler.enter_write_window(task_id, task_surface)
        ctx.write_window = True
        try:
            move = merge_base_at_write_window(cfg, task_id, slug)
        except tasks.BaseLockHeldError as exc:
            logger.warning("task %s: %s", task_id, exc)
            return _blocked_outcome(ctx)
        stale_note = stale_plan_note(move, task_surface, _plan_entries(arquitecto.handoff))
        if stale_note:
            logger.warning(
                "task %s: the base moved under the plan; the implementador is told", task_id,
            )

    return _cycle_rounds(ctx, authorized, on_approve, stale_note=stale_note)


def _cycle_rounds(
    ctx: CycleContext,
    authorized: bool,
    on_approve: Callable[[ApproveResult], None] | None,
    stale_note: str | None = None,
    resumed: tuple[int, DispatchResult] | None = None,
) -> CycleOutcome:
    """The implementador and revisor rounds, then the auditor and the merge.

    `resumed` is `(round, result)` for a verdict that already resumed that
    round's implementador (ADR 119): the rounds start there, from the result,
    and everything after it — the gates, the revisor, the auditor — is what a
    cycle does after the phase. Sets `ctx.completed` once the auditor filed.
    """
    cfg, task_id, slug = ctx.cfg, ctx.task_id, ctx.slug
    approved = False
    # Counted separately from `round_num`: `max_revision_rounds: 0` makes
    # the range empty and leaves the loop variable unbound, and the one
    # place that reads this is the branch that runs when the loop did not
    # approve — including when it never ran.
    rounds_run = 0
    # What the last review that sent a round back sent it back for, and
    # whether the one after it repeated any of it (ADR 73). A round the
    # gates blocked has no review, so it changes neither.
    previous_reasons: dict[str, str] = {}
    repeated_finding = False
    first_round, last_round = 1, cfg.max_revision_rounds
    if resumed is not None:
        # The resumed round runs even when it is past the rounds allowed: a
        # `run-phase --round` can have filed it from beyond them.
        first_round = resumed[0]
        last_round = max(last_round, first_round)
    for round_num in range(first_round, last_round + 1):
        rounds_run = round_num
        if resumed is not None and round_num == first_round:
            # A verdict's resume already ran this round's implementador
            # (ADR 119): the round goes on from what it returned.
            implemented = resumed[1]
        else:
            # Only the first round hears of the moved base: later rounds open
            # on the review's findings, written against the merged branch.
            noted = {"note": stale_note} if stale_note and round_num == 1 else {}
            implemented = run_phase(
                ctx, "implementador", round_num=round_num, repeated_finding=repeated_finding,
                **noted,
            )
        if implemented is None:
            return _stopped(ctx)
        if _filed_command_requests(ctx, implemented):
            # Before the gates' verdict and the revisor: the phase stopped
            # for a command it was denied, so its work is not done.
            return _blocked_outcome(ctx, handoff.COMMAND_REQUEST)
        if stale_note and round_num == 1 and handoff.blocked(implemented.handoff):
            # The block back the note offers: the plan no longer holds,
            # and the arquitecto replans on the next run of the task.
            logger.warning(
                "task %s: the implementador blocked on the moved base: %s",
                task_id, "; ".join(handoff.pending(implemented.handoff)) or "no reason given",
            )
            return _blocked_outcome(ctx)
        if implemented.gates is not None and implemented.gates.blocking:
            # The gates already asked for this in-session and checked the
            # answer. Paying a revisor to read a branch whose tests fail
            # buys a finding the dispatcher has in hand, so the round goes
            # back around instead — the findings are in the task file, and
            # the next implementador round opens on them.
            logger.warning(
                "task %s: the gates blocked round %d before review", task_id, round_num,
            )
            continue
        revisor_result = run_phase(ctx, "revisor", round_num=round_num)
        if revisor_result is None:
            return _stopped(ctx)
        disguised = debt.blocking(revisor_result.handoff)
        if disguised:
            # A block wearing a debt costume. Another round cannot supply
            # a decision the task was never given, so this ends here
            # rather than burning the remaining rounds to say so again.
            logger.warning(
                "task %s: the revisor read %d declaration(s) as blocks: %s",
                task_id, len(disguised), "; ".join(disguised),
            )
            break
        sent_back = debt.rejected(revisor_result.handoff)
        if revisor_approved(revisor_result.result_text, revisor_result.handoff):
            if not sent_back:
                approved = True
                break
            # Rejected debt is work the revisor says this task should have
            # done, which is a finding; a verdict of APPROVED over the top
            # of one is a contradiction, and the finding wins. It costs a
            # round like any other finding, so this still terminates.
            logger.info(
                "task %s: round %d came back APPROVED with %d debt declaration(s) rejected, "
                "so it is not an approval: %s",
                task_id, round_num, len(sent_back), "; ".join(sent_back),
            )
        reasons = review_reasons(revisor_result.handoff)
        repeated = [text for key, text in reasons.items() if key in previous_reasons]
        previous_reasons = reasons
        if (
            repeated
            and cfg.escalate_effort_on_repeated_finding
            and round_num < cfg.max_revision_rounds
        ):
            repeated_finding = True
            logger.info(
                "task %s: round %d runs the implementador at effort %s, "
                "the revisor sent back the same finding(s) twice: %s",
                task_id, round_num + 1, cfg.escalated_effort, "; ".join(repeated),
            )

    if not approved:
        # Said out loud because otherwise nothing says it: the task
        # file's `status:` is only written by a handoff, and before ADR 52
        # the run exited 0 either way — so a task that used up every round
        # looked exactly like one that was never dispatched.
        logger.warning(
            "task %s: blocked after %d of %d revision round(s) without an approval",
            task_id, rounds_run, cfg.max_revision_rounds,
        )
        return _blocked_outcome(ctx)

    # Stamped before the phase that files them, so the auditor's prompt
    # can name the entries it owns and a later run can tell an entry that
    # was handed to a task from one nobody has picked up yet.
    learnings.carry(cfg.hive_tasks_dir, slug, task_id)
    # Written to the task file rather than only handed to the auditor: the
    # merge can happen days later, in `merge-task`, long after this
    # handoff is prose in a file nobody parses, and the api serves the
    # list from the task file.
    tasks.set_resolved_debt(
        cfg.hive_tasks_dir, task_id, debt.resolved(implemented.handoff)
    )
    # Before the auditor, because the auditor writes the rows these ids
    # name.
    filed = _file_accepted_debt(
        cfg, task_id, slug, implemented.handoff, revisor_result.handoff
    )
    audited = run_phase(ctx, "auditor", final=True, note=debt.filing_note(filed))
    if audited is None:
        return _stopped(ctx)
    ctx.completed = True

    if cfg.auto_approves(slug):
        # The conditions win over `merge_on_done` (ADR 77): a project
        # with both on merges only what the automatic approve allows.
        approve = _auto_approve_and_merge(
            cfg, task_id, slug,
            description=ctx.description,
            implemented=implemented,
            reviewed=revisor_result,
            audited=audited,
            filed=filed,
            authorized=authorized,
        )
        if on_approve is not None:
            on_approve(approve)
        merged = approve.merged
    else:
        # Never pushes: the push of the base belongs to an approve, a
        # person's or the automatic one (ADR 78), not to `merge_on_done`.
        merged = cfg.merge_on_done and _merge_task_branch(cfg, task_id, slug)
    if merged:
        # Only now: the entries are in the project's docs on the branch
        # that just landed, so the inbox copy would charge every later
        # phase for a row the repo already has. A branch that did not
        # merge keeps them here, where the next task still sees them.
        learnings.drop_promoted(cfg.hive_tasks_dir, task_id)
    return CycleOutcome.FINISHED
