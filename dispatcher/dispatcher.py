# dispatcher/dispatcher.py
from __future__ import annotations

import dataclasses
import logging
import re
import threading

from dispatcher import context_transfer, docker_exec, quota, role_skills, state_machine
from dispatcher.config import Config
from dispatcher.state_machine import AccountState
from dispatcher.vibe_kanban_client import KanbanClient

logger = logging.getLogger(__name__)

# The /usage probe is a short, fixed-shape command, not user work — it never
# needs the (potentially very long) configured phase timeout, so it gets its
# own short fixed budget instead of cfg.phase_timeout_seconds.
_USAGE_PROBE_TIMEOUT_SECONDS = 120


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
        usage = quota.parse_usage_output(result.result_text)
    except Exception as exc:
        logger.warning("quota probe failed for account %s: %s", account, exc)
        return True
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
    # it clears the threshold. Without this, an account that ever crosses the
    # threshold stays dead for the life of the process.
    recovered = []
    for acc in cfg.accounts:
        state = state_machine.get_state(cfg.state_dir, acc.name)
        if state not in (AccountState.PRE_COOLDOWN, AccountState.COOLING_DOWN):
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
            usage = quota.parse_usage_output(result.result_text)
            exceeds = quota.exceeds_threshold(usage, cfg.quota_threshold_pct)
        except Exception as exc:
            logger.warning("quota recheck failed for account %s: %s", acc.name, exc)
            exceeds = False
        if not exceeds:
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


def revisor_approved(result_text: str) -> bool:
    # No structured verdict field exists in the task handoff or Kanban clients
    # (design spec caveat) — the revisor is instructed (see _role_prompt) to end
    # its response with a literal "VERDICT: APPROVED"/"VERDICT: CHANGES_REQUESTED"
    # line, and this fails closed (treated as not-approved) on anything else,
    # so a malformed or missing verdict still burns a revision round instead of
    # silently passing.
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


def _truncate_for_handoff(text: str, head: int = 500, tail: int = 1500) -> str:
    # Interim measure until the structured handoff (README Future work:
    # "Structured handoff") replaces free-text phase output with a bounded,
    # per-role schema. The tail is kept, not just the head, because a
    # revisor's verdict line always closes its response — a head-only cut
    # (the old `result.result_text[:2000]`) drops that verdict on any long
    # response.
    if len(text) <= head + tail:
        return text
    omitted = len(text) - head - tail
    return f"{text[:head]}\n\n[… {omitted} chars omitted …]\n\n{text[-tail:]}"


def _role_prompt(
    role: str,
    task_id: str,
    task_file: str,
    description: str,
    round_num: int | None = None,
) -> str:
    # The description is embedded whole, never run through
    # _truncate_for_handoff: a cut phase summary loses detail, but a cut
    # ask misinforms — the role would confidently build the wrong thing.
    # It also stays in the task file, so this is belt and braces: the role
    # has the ask even if it never opens the file.
    prompt = (
        f"Role: {role}. Task: {task_id}.\n\n"
        f"Task description:\n{description}\n\n"
        f"Read {task_file} for context handed off from the previous phase before starting."
    )
    if round_num is not None:
        prompt += f" This is revision round {round_num}."
    if role == "revisor":
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
    """
    return (
        role in docker_exec.WRITER_ROLES
        and _exec_succeeded(result)
        and not is_rate_limit_error(result)
    )


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
            return DispatchResult(success=False, session_id=resume_session_id, result_text="no accounts available", account="")
        tried.add(account)

        if not check_quota_ok(cfg, account):
            continue

        container = container_for(cfg, account)
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
                result = docker_exec.exec_claude(
                    container, workdir, prompt,
                    resume_session_id=resume_session_id, model=model, effort=effort,
                    timeout_seconds=cfg.phase_timeout_seconds,
                    # Chosen from the role here rather than passed in by the
                    # caller: the role is what decides the set, and a phase
                    # dispatched by any other path should get the same one.
                    plugin_dirs=role_skills.plugin_dirs(role),
                    append_system_prompt=role_skills.system_prompt(role),
                )
            if _should_commit(role, result):
                docker_exec.commit_worktree(
                    container, workdir,
                    message=_commit_message(role, task_id, account, result.session_id, round_num),
                    author_name=f"{role} ({account})",
                    author_email=f"{role}@ia-harness.invalid",
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


def _merge_task_branch(cfg: Config, task_id: str, slug: str) -> None:
    """Offer a finished task's branch to the branch the project sits on.

    Best-effort in the same sense as the cleanup above: the task is already
    done, the work is already committed on its own branch, and a merge that
    did not happen is a `dispatch merge-task` away. So a refusal is logged
    and the cycle ends normally rather than turning a finished task into a
    crashed one.
    """
    container = cleanup_container(cfg)
    if container is None:
        return
    project_dir = f"{cfg.projects_root}/{slug}"
    owner = docker_exec.read_owner(container, project_dir)
    try:
        outcome = docker_exec.merge_task_branch(container, cfg.projects_root, slug, task_id)
    except Exception as exc:
        logger.warning("could not merge task %s: %s", task_id, exc)
        return
    finally:
        docker_exec.restore_owner(container, project_dir, owner)
    if outcome.refused:
        logger.warning("task %s: not merged: %s", task_id, outcome.detail)
    else:
        logger.info("task %s: %s", task_id, outcome.detail)


def run_task_cycle(
    cfg: Config,
    task_id: str,
    slug: str,
    kanban: KanbanClient,
    description: str | None = None,
) -> None:
    reap_expired_locks(cfg)
    task_file = context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)
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

    def run_phase(role: str, round_num: int | None = None, final: bool = False) -> DispatchResult | None:
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
                prompt=_role_prompt(role, task_id, task_file, description, round_num=round_num),
                model=cfg.default_model,
                effort=effort,
                round_num=round_num,
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
            _update_task_status(kanban, issue_id, "blocked")
            return None
        label = role if round_num is None else f"{role} (round {round_num})"
        context_transfer.handoff(
            cfg.hive_tasks_dir, task_id,
            new_status="done" if final else "pending",
            body=f"## {label}\n\n{_truncate_for_handoff(result.result_text)}",
        )
        return result

    try:
        if run_phase("arquitecto") is None:
            return

        approved = False
        for round_num in range(1, cfg.max_revision_rounds + 1):
            if run_phase("implementador", round_num=round_num) is None:
                return
            revisor_result = run_phase("revisor", round_num=round_num)
            if revisor_result is None:
                return
            if revisor_approved(revisor_result.result_text):
                approved = True
                break

        if not approved:
            _update_task_status(kanban, issue_id, "blocked")
            return

        if run_phase("auditor", final=True) is None:
            return

        _update_task_status(kanban, issue_id, "done")
        if cfg.merge_on_done:
            _merge_task_branch(cfg, task_id, slug)
    finally:
        # Every way out of here is terminal for this run — done, blocked, or a
        # crash — and the reviewing checkouts are rebuilt on demand, so they can
        # go now rather than pile up per task. The exception is a task this run
        # never owned: another dispatcher is still working in those worktrees.
        if not foreign_lock:
            _drop_review_worktrees(cfg, task_id, slug)
