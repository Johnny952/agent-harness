# dispatcher/dispatcher.py
from __future__ import annotations

import dataclasses
import logging
import os
import re
import threading
import time

from dispatcher import (
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
)
from dispatcher.config import Config
from dispatcher.state_machine import AccountState
from dispatcher.vibe_kanban_client import KanbanClient

logger = logging.getLogger(__name__)

# The /usage probe is a short, fixed-shape command, not user work — it never
# needs the (potentially very long) configured phase timeout, so it gets its
# own short fixed budget instead of cfg.phase_timeout_seconds.
_USAGE_PROBE_TIMEOUT_SECONDS = 120


def _phase_permission_flags(cfg: Config) -> dict:
    """What every call that runs a role's own work has to carry.

    Three flags. The first two are the two halves of one measured failure
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

    The /usage probes do not get these: a probe runs a built-in command, uses
    no tools and touches no files.
    """
    return {
        "permission_mode": cfg.permission_mode,
        "add_dirs": [context_transfer.hive_root(cfg.hive_tasks_dir)],
        "allowed_tools": cfg.allowed_tools,
    }


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


def container_for(cfg: Config, account: str) -> str:
    for acc in cfg.accounts:
        if acc.name == account:
            return acc.container
    raise ValueError(f"Unknown account: {account}")


def pick_idle_account(cfg: Config, exclude: set[str] | None = None) -> str | None:
    idle = state_machine.list_idle_accounts(cfg.state_dir, cfg.accounts)
    if exclude:
        idle = [a for a in idle if a not in exclude]
    return idle[0] if idle else None


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
        state_machine.set_state(cfg.state_dir, account, AccountState.COOLING_DOWN)
        return False
    try:
        usage = quota.parse_usage_output(result.result_text)
    except Exception as exc:
        logger.warning(
            "quota probe for account %s returned an unreadable usage report: %s; "
            "waving it through unverified", account, exc,
        )
        return True
    if quota.exceeds_threshold(usage, cfg.quota_threshold_pct):
        logger.warning(
            "account %s is at %d%% of its session and %d%% of its week, over the %d%% threshold; "
            "parking it PRE_COOLDOWN and looking for another",
            account, usage.session_pct, usage.week_pct, cfg.quota_threshold_pct,
        )
        state_machine.set_state(cfg.state_dir, account, AccountState.PRE_COOLDOWN)
        return False
    return True


def _recheck_cooling_accounts(cfg: Config) -> list[str]:
    # Free-text /usage reset timestamps (session_reset/week_reset) aren't
    # reliably parseable to an exact wake time (design spec sec. 4a caveat),
    # so recovery is re-check-on-dispatch rather than a scheduled expiry:
    # every account parked in PRE_COOLDOWN/COOLING_DOWN gets a fresh /usage
    # probe whenever no account is IDLE, and flips back to IDLE the moment
    # it clears the threshold. Without this, an account that ever crosses the
    # threshold stays dead for the life of the process.
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
            exceeds = quota.exceeds_threshold(usage, cfg.quota_threshold_pct)
        except Exception as exc:
            logger.warning("quota recheck failed for account %s: %s", acc.name, exc)
            exceeds = False
        if not exceeds:
            logger.info("account %s came back from %s and is IDLE again", acc.name, state.value)
            # The cooldown is over and the numbers agree, so the old refusal
            # must not go on holding the account on the next pass.
            state_machine.clear_rate_limit(cfg.state_dir, acc.name)
            state_machine.set_state(cfg.state_dir, acc.name, AccountState.IDLE)
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
    if role == project_docs.MAPPER_ROLE:
        # The mapper runs before the first phase, so there is no handoff to
        # read and the task is context, not its job: it is told what is coming
        # so it maps the parts of the project that task will touch first.
        prompt = (
            f"Role: {role}. Task: {task_id}.\n\n"
            f"The task about to run on this project is:\n{description}\n\n"
            "You are not doing that task. You run once, ahead of it, because this project has "
            "no docs for the agents that work on it."
        )
    else:
        # The description is embedded whole, never clamped: a cut phase summary
        # loses detail, but a cut ask misinforms — the role would confidently
        # build the wrong thing. It also stays in the task file, so this is belt
        # and braces: the role has the ask even if it never opens the file.
        prompt = (
            f"Role: {role}. Task: {task_id}.\n\n"
            f"Task description:\n{description}\n\n"
            f"Read {task_file} for context handed off from the previous phase before starting."
        )
        if round_num is not None:
            prompt += f" This is revision round {round_num}."
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
    # debt ids and card ids the auditor is about to file. It is computed by
    # the dispatcher between phases, so it cannot come from `duties`.
    if note:
        prompt += f"\n\n{note}"
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
        prompt += (
            f"\n\nReturn the structured handoff your schema describes: about "
            f"{handoff.lines_for(role)} entries in all, counting every list together, one line "
            f"each — {handoff.budget_for(role)} bytes of JSON. Every later phase reads it, so it "
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


def reap_expired_locks(cfg: Config) -> list[str]:
    reaped = []
    for task_id in context_transfer.list_task_ids(cfg.hive_tasks_dir):
        task = context_transfer.read_task_file(context_transfer.task_file_path(cfg.hive_tasks_dir, task_id))
        if task.status == "in_progress" and context_transfer.is_lock_expired(task, cfg.heartbeat_ttl_seconds):
            context_transfer.release_stale_lock(cfg.hive_tasks_dir, task_id)
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


def _shrink_over_budget(
    cfg: Config,
    container: str,
    workdir: str,
    role: str,
    result: docker_exec.ClaudeResult,
    model: str | None,
    effort: str | None,
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
        **_phase_permission_flags(cfg),
    )
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
        **_phase_permission_flags(cfg),
    )
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
        **_phase_permission_flags(cfg),
    )
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
) -> DispatchResult:
    tried: set[str] = set()
    while True:
        # Exclude `tried` up front rather than picking idle[0] and bailing
        # when it's already been tried: a recheck can recover the very
        # account this call just rate-limited (it's IDLE again) alongside
        # others that were cooling before this call started, and those
        # never-tried accounts are still worth a shot.
        account = pick_idle_account(cfg, exclude=tried)
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
            return DispatchResult(success=False, session_id=resume_session_id, result_text="no accounts available", account="")
        tried.add(account)

        if not check_quota_ok(cfg, account):
            continue

        container = container_for(cfg, account)
        logger.info(
            "task %s: %s%s goes to account %s, %s",
            task_id, role, f" round {round_num}" if round_num is not None else "", account,
            f"resuming session {resume_session_id}" if resume_session_id else "in a new session",
        )
        state_machine.set_state(cfg.state_dir, account, AccountState.BUSY, current_task_id=task_id)
        lock_acquired = False
        project_dir = f"{cfg.projects_root}/{slug}"
        # Read this before anything in the container touches the tree, while it
        # is still whatever the host user owns.
        owner = docker_exec.read_owner(container, project_dir)
        try:
            # Claim the task before touching the worktree: a task another
            # account still owns (live heartbeat) must be refused outright,
            # not after work has already started on disk.
            context_transfer.acquire_lock(cfg.hive_tasks_dir, task_id, owner=account, ttl_seconds=cfg.heartbeat_ttl_seconds)
            lock_acquired = True
            workdir = docker_exec.create_worktree(container, cfg.projects_root, slug, task_id, role)
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
                    append_system_prompt=role_skills.system_prompt(role),
                    json_schema=handoff.schema_for(role),
                    **_phase_permission_flags(cfg),
                )
                # Before the shrink, not after: the gate retry writes a new
                # handoff, and the byte budget has to be enforced on the return
                # that actually lands in the task file.
                result, gate_report = _run_gates(
                    cfg, container, workdir, project_dir, task_id, role,
                    result, model, effort, round_num,
                )
                # Between the two for the same reason: it can rewrite the
                # handoff as well, and the gates only ever run for the
                # implementador, which this never fires for.
                result = _refuse_review_writes(
                    cfg, container, workdir, task_id, role, result, model, effort,
                )
                result = _shrink_over_budget(cfg, container, workdir, role, result, model, effort)
            if _should_commit(role, result):
                docker_exec.commit_worktree(
                    container, workdir,
                    message=_commit_message(role, task_id, account, result.session_id, round_num),
                    author_name=f"{role} ({account})",
                    author_email=f"{role}@ia-harness.invalid",
                    paths=project_docs.commit_scope(role),
                )
        except Exception:
            # Never leave an account stuck BUSY (disk-persisted, survives
            # restart) because of an exception between claiming it and
            # returning — that permanently removes it from the pool.
            state_machine.set_state(cfg.state_dir, account, AccountState.IDLE)
            if lock_acquired:
                context_transfer.release_stale_lock(cfg.hive_tasks_dir, task_id)
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
            state_machine.set_state(cfg.state_dir, account, AccountState.COOLING_DOWN)
            context_transfer.release_stale_lock(cfg.hive_tasks_dir, task_id)
            resume_session_id = result.session_id or resume_session_id
            continue

        if not _exec_succeeded(result):
            state_machine.set_state(cfg.state_dir, account, AccountState.IDLE)
            context_transfer.release_stale_lock(cfg.hive_tasks_dir, task_id)
            return DispatchResult(
                success=False, session_id=result.session_id, result_text=result.result_text,
                account=account, handoff=_phase_handoff(container, result), gates=gate_report,
            )

        # A turn the service actually served is proof the refusal is over —
        # better proof than any /usage report, and the only kind worth
        # dropping the mark for.
        state_machine.clear_rate_limit(cfg.state_dir, account)
        state_machine.set_state(cfg.state_dir, account, AccountState.IDLE)
        return DispatchResult(
            success=True, session_id=result.session_id, result_text=result.result_text,
            account=account, handoff=_phase_handoff(container, result), gates=gate_report,
        )


#: A card is a glance, not a log: past this, the ask goes in its description.
_ISSUE_TITLE_MAX = 120


def _issue_title(task_id: str, description: str) -> str:
    """What the card says on the board.

    The task id leads, so a card and a task file can be lined up by eye; the
    first line of the ask follows, so the card says something a human
    recognises without opening it.
    """
    lines = [line.strip() for line in description.strip().splitlines() if line.strip()]
    title = f"{task_id}: {lines[0]}" if lines else task_id
    return title if len(title) <= _ISSUE_TITLE_MAX else title[: _ISSUE_TITLE_MAX - 1].rstrip() + "…"


def _open_kanban_issue(cfg: Config, kanban: KanbanClient, task_id: str, description: str) -> str | None:
    """Put this task on the board, and remember which card it became.

    The uuid is server-assigned, so it goes back into the task file: a later
    phase after a restart, or a second `run-task` on the same task, has to
    move this same card rather than open another one. A board that can't be
    reached costs one warning and nothing else — the run is not a board
    operation.
    """
    try:
        issue_id = kanban.create_issue(_issue_title(task_id, description), description)
    except Exception as exc:
        logger.warning("kanban: task %s runs without a card: %s", task_id, exc)
        return None
    if issue_id is not None:
        context_transfer.set_kanban_issue_id(cfg.hive_tasks_dir, task_id, issue_id)
    return issue_id


def _update_task_status(kanban: KanbanClient, issue_id: str | None, status: str) -> None:
    # Vibe Kanban is a visibility aid, not the source of truth for dispatch
    # state (that's the task file / account state machine) — an outage or
    # API error there must not abort an otherwise-healthy phase run.
    if issue_id is None:
        # No board, or a board this task never got a card on. Silent on
        # purpose: a harness configured without one would otherwise carry a
        # warning per phase about something it was never asked to do.
        return
    try:
        kanban.set_status(issue_id, status)
    except Exception as exc:
        logger.warning("kanban status update failed for issue %s (%s): %s", issue_id, status, exc)


def _file_accepted_debt(
    cfg: Config,
    kanban: KanbanClient,
    task_id: str,
    slug: str,
    implemented: DispatchResult,
    reviewed: DispatchResult,
) -> list[tuple[str, str | None, debt.Declaration]]:
    """Put the debt this task declared on the board, and name it for the auditor.

    The dispatcher does this, not an agent, for two reasons the spec is blunt
    about: an agent that can create tasks can assign itself work, and an agent
    re-run for a second review round would create the same card twice.

    It runs *before* the auditor because the card exists before the row does.
    That order is what lets each side record the other's id: the ids are
    assigned here, the cards are opened here, and the auditor — still the only
    writer of the indexes — writes one row that already points at its card.

    A project with no board loses only the cards. The entries are filed either
    way, because the index is the source of truth and the board is the aid.
    """
    accepted = debt.accepted(implemented.handoff, reviewed.handoff)
    if not accepted:
        return []
    # Read from the branch being built, not the project checkout: a re-run of
    # a task that already filed entries has them on its own branch, and a
    # board that fills with the same entry once per run is a board nobody
    # reads. A read that fails costs a duplicate card, never the entry.
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
    filed: list[tuple[str, str | None, debt.Declaration]] = []
    for declaration in accepted:
        if declaration.fingerprint in known:
            logger.info("task %s: debt already in the index, no card: %s", task_id, declaration.what)
            continue
        known.add(declaration.fingerprint)
        entry = debt.entry_id(task_id, len(filed) + 1)
        card = None
        try:
            card = kanban.create_issue(
                debt.card_title(declaration),
                debt.card_description(task_id, entry, declaration),
            )
        except Exception as exc:
            # Same rule as everywhere else the board is touched: it is a
            # visibility aid, and the entry is filed with or without it.
            logger.warning("kanban: debt %s gets no card: %s", entry, exc)
        filed.append((entry, card, declaration))
    return filed


def close_resolved_debt(cfg: Config, kanban: KanbanClient, task_id: str, slug: str) -> list[str]:
    """Close the cards of the debt a merged task says it resolved.

    Only half of the spec's "mark the entry resolved and close the card" is
    here, and deliberately: the row is marked resolved by the auditor, on the
    branch, because it is the only writer of the indexes and the mark should
    land with the merge commit that made it true. What is left is the half the
    repository cannot do, and it runs after the merge — a branch that never
    lands leaves the board exactly as it was.
    """
    entries = context_transfer.read_resolved_debt(cfg.hive_tasks_dir, task_id)
    if not entries or not kanban.enabled:
        return []
    container = cleanup_container(cfg)
    if container is None:
        return []
    # The merged checkout, this time: the card ids were written by whichever
    # task filed the entry, which is usually not this one.
    project_dir = f"{cfg.projects_root}/{slug}"
    try:
        cards = debt.card_ids(debt.read_index(container, project_dir), entries)
    except Exception as exc:
        logger.warning("task %s: could not read %s: %s", task_id, project_docs.DEBT_INDEX, exc)
        return []
    closed = []
    for entry in entries:
        card = cards.get(entry)
        if card is None:
            # Either the entry id is wrong or its row never got a card. Both
            # are worth saying out loud: the work landed, and a human is still
            # looking at an open card for it.
            logger.warning(
                "task %s: resolved debt %s has no card in %s", task_id, entry, project_docs.DEBT_INDEX
            )
            continue
        _update_task_status(kanban, card, "done")
        closed.append(entry)
    return closed


def cleanup_container(cfg: Config) -> str | None:
    """Which container to run worktree housekeeping in.

    Any of them: every agent container bind-mounts the same host projects root
    (see docker/compose/docker-compose.agents.yml), so this does not have to be
    the account that ran the phases — which by then may be rate-limited.
    """
    return cfg.accounts[0].container if cfg.accounts else None


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
    project_dir = f"{cfg.projects_root}/{slug}"
    owner = docker_exec.read_owner(container, project_dir)
    try:
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
    return True


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


def run_task_cycle(
    cfg: Config,
    task_id: str,
    slug: str,
    kanban: KanbanClient,
    description: str | None = None,
) -> None:
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
    # The card this task already mirrors, if any: seeded by `run-task
    # --kanban-issue-id`, or left behind by an earlier run that opened one.
    issue_id = (
        context_transfer.read_kanban_issue_id(cfg.hive_tasks_dir, task_id)
        if kanban.enabled
        else None
    )

    if description is not None:
        context_transfer.set_description(cfg.hive_tasks_dir, task_id, description)
    else:
        # A resume (or a task seeded by an earlier `run-task`) already has
        # the ask on disk; nothing to write.
        description = context_transfer.read_description(cfg.hive_tasks_dir, task_id)
    if not (description or "").strip():
        # Dispatching here would burn four phases of quota on roles that
        # were told a task id and nothing else. The CLI rejects this case
        # before it gets here; this is the backstop for library callers.
        logger.error(
            "task %s has no description: pass --description/--description-file to run-task, "
            "or add a `description:` key to %s",
            task_id,
            task_file,
        )
        _update_task_status(kanban, issue_id, "blocked")
        return

    if kanban.enabled and issue_id is None:
        # First run of this task against a board nobody pointed at a card:
        # the harness opens one, which is the only way a task id ever becomes
        # a uuid — every id in Vibe Kanban's schema is server-assigned.
        issue_id = _open_kanban_issue(cfg, kanban, task_id, description)

    # Set when a phase bounced off another owner's lock: that run's worktrees
    # are in use, so the cleanup below has to keep its hands off them.
    foreign_lock = False
    # Set once the auditor has filed what this task learned. Every other way
    # out of the cycle — blocked, bounced, crashed — leaves entries nobody
    # filed, and those have to go back to being unowned.
    completed = False

    def run_phase(
        role: str,
        round_num: int | None = None,
        final: bool = False,
        fatal: bool = True,
        model: str | None = None,
        max_turns: int | None = None,
        note: str = "",
    ) -> DispatchResult | None:
        nonlocal foreign_lock
        _update_task_status(kanban, issue_id, f"in_progress:{role}")
        effort = (
            cfg.escalated_effort
            if round_num is not None and round_num > cfg.escalate_effort_after_round
            else None
        )
        try:
            result = dispatch_phase(
                cfg, task_id, slug, role,
                prompt=_role_prompt(
                    role, task_id, slug, task_file, description, scratch_dir, cfg.hive_tasks_dir,
                    harness=harness,
                    round_num=round_num, max_turns=max_turns, note=note,
                ),
                model=model or cfg.default_model,
                effort=effort,
                round_num=round_num,
                max_turns=max_turns,
            )
        except context_transfer.LockHeldError as exc:
            # A re-run within the TTL after a Ctrl+C, or a second dispatcher
            # process, hits this on every phase. Block the task instead of
            # letting the LockHeldError climb out as a CLI traceback and
            # leave Kanban stuck at in_progress. The foreign lock is left
            # alone: after a Ctrl+C the in-container claude keeps running
            # until its own timeout, so the heartbeat TTL, not this run,
            # decides when the task can be taken over.
            logger.warning("task %s is locked by another owner: %s", task_id, exc)
            _update_task_status(kanban, issue_id, "blocked")
            foreign_lock = True
            return None
        if not result.success:
            if fatal:
                _update_task_status(kanban, issue_id, "blocked")
                return None
            # A phase the task does not depend on. It still hands off what it
            # managed — a partial map is worth having, and the next phase
            # should know it is partial — but it does not block the card.
            logger.warning(
                "optional phase %s did not finish for task %s: %s",
                role, task_id, result.result_text,
            )
        label = role if round_num is None else f"{role} (round {round_num})"
        body = handoff.body(label, result.result_text, result.handoff)
        # Under the phase's own return, not instead of it: the role says what
        # it did and the dispatcher says what it found, and the next phase to
        # read this file can tell the two apart.
        gate_section = result.gates.render() if result.gates is not None else ""
        if gate_section:
            body = f"{body}\n\n{gate_section}"
        context_transfer.handoff(
            cfg.hive_tasks_dir, task_id,
            new_status="done" if final else "pending",
            body=body,
        )
        return result

    try:
        if _needs_mapping(cfg, slug):
            # Before the arquitecto, because what it writes is what the
            # arquitecto reads. Optional in both directions: the operator has
            # to turn it on, and if it fails the task runs anyway on a project
            # that stays unmapped.
            run_phase(
                project_docs.MAPPER_ROLE,
                fatal=False,
                model=cfg.mapping_model,
                max_turns=cfg.mapping_max_turns,
            )
            if foreign_lock:
                return

        if run_phase("arquitecto") is None:
            return

        approved = False
        # Counted separately from `round_num`: `max_revision_rounds: 0` makes
        # the range empty and leaves the loop variable unbound, and the one
        # place that reads this is the branch that runs when the loop did not
        # approve — including when it never ran.
        rounds_run = 0
        for round_num in range(1, cfg.max_revision_rounds + 1):
            rounds_run = round_num
            implemented = run_phase("implementador", round_num=round_num)
            if implemented is None:
                return
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
            revisor_result = run_phase("revisor", round_num=round_num)
            if revisor_result is None:
                return
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

        if not approved:
            # Said out loud because otherwise nothing says it: the board move
            # below is a no-op when the task has no kanban issue, the task
            # file's `status:` is only written by a handoff, and the run exits
            # 0 either way — so a task that used up every round looked exactly
            # like one that was never dispatched.
            logger.warning(
                "task %s: blocked after %d of %d revision round(s) without an approval",
                task_id, rounds_run, cfg.max_revision_rounds,
            )
            _update_task_status(kanban, issue_id, "blocked")
            return

        # Stamped before the phase that files them, so the auditor's prompt
        # can name the entries it owns and a later run can tell an entry that
        # was handed to a task from one nobody has picked up yet.
        learnings.carry(cfg.hive_tasks_dir, slug, task_id)
        # Written to the task file rather than only handed to the auditor: the
        # merge that closes these cards can happen days later, in `merge-task`,
        # long after this handoff is prose in a file nobody parses.
        context_transfer.set_resolved_debt(
            cfg.hive_tasks_dir, task_id, debt.resolved(implemented.handoff)
        )
        # Before the auditor, because the auditor writes the rows that point
        # at these cards.
        filed = _file_accepted_debt(cfg, kanban, task_id, slug, implemented, revisor_result)
        if run_phase("auditor", final=True, note=debt.filing_note(filed)) is None:
            return
        completed = True

        _update_task_status(kanban, issue_id, "done")
        if cfg.merge_on_done and _merge_task_branch(cfg, task_id, slug):
            # Only now: the entries are in the project's docs on the branch
            # that just landed, so the inbox copy would charge every later
            # phase for a row the repo already has. A branch that did not
            # merge keeps them here, where the next task still sees them.
            learnings.drop_promoted(cfg.hive_tasks_dir, task_id)
            # Same "only now": the entry is marked resolved on the branch that
            # just landed, so the card it mirrors has stopped being work.
            close_resolved_debt(cfg, kanban, task_id, slug)
    finally:
        # Every way out of here is terminal for this run — done, blocked, or a
        # crash — and the reviewing checkouts are rebuilt on demand, so they can
        # go now rather than pile up per task. The exception is a task this run
        # never owned: another dispatcher is still working in those worktrees.
        if not foreign_lock:
            _drop_review_worktrees(cfg, task_id, slug)
            # Whether or not it finished: what a phase was allowed to do while
            # it wrote these is settled, and stamping it here is the only way
            # a later run can tell an entry that aged out from one that holds.
            learnings.stamp(cfg.hive_tasks_dir, task_id, harness)
            if not completed:
                # The entries stay — this task is the reason nobody has
                # filed them yet — but they stop claiming a carrier that is
                # gone, and a status this task alone vouched for goes back
                # to being one phase's word.
                learnings.mark_orphaned(cfg.hive_tasks_dir, task_id)
