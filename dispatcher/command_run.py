"""An operator's verdict on a filed command request: run it or deny it, then resume.

Step 4 of `docs/plans/command-requests.md`, as amended 2026-10-18 (decisions
4 and 5; `docs/decisions.md` ADR 119). A task blocked on `command_request`
carries, in the envelope of the phase that filed it, the requests step 2
accepted and step 3 resolved. `run_command_request` takes one of them by its
1-based place in that list and either runs it on its resolved target or
denies it, records the verdict on the task, and resumes the phase's own
session with what came of it. The cycle then goes on as it would have after
that phase, through the same `run_phase` and the same stage functions a cycle
runs.

The CLI's `command-verdict` is the one caller today; the board service's
worker is meant to be the next, and the verdicts move to Approvals then.

A verdict is recorded before its command runs, and only one per block, so a
resume that no account could take leaves a verdict and nothing to give it
again. `resume_command_verdict` (T-076, ADR 120) resumes that session with the
same prompt, rebuilt from what the verdict entry and `commands/<n>.log`
recorded, and never runs the command again. `resumed_at` on the entry is what
says a resume has already happened.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
import subprocess

from dispatcher import command_targets, context_transfer, docker_exec, handoff, project_docs, tasks
from dispatcher.config import Config
from dispatcher.dispatcher import (
    CycleContext,
    CycleOutcome,
    _blocked_outcome,
    _cycle_from_arquitecto,
    _cycle_rounds,
    _filed_command_requests,
    _stopped,
    close_cycle,
    open_cycle,
    run_phase,
)

logger = logging.getLogger(__name__)

#: How long a run command may take before the `docker exec` is stopped. A
#: timeout is recorded as one, with no exit code.
COMMAND_TIMEOUT_SECONDS = 300

#: How much of a command's output is kept, in bytes of UTF-8. The rest is cut
#: with `TRUNCATED_MARKER`, and the phase is told the log's path instead of
#: the output.
OUTPUT_CAP_BYTES = 64 * 1024

TRUNCATED_MARKER = "[... output truncated at {cap} bytes; {dropped} more bytes dropped ...]"

#: What a deny with no comment tells the phase.
DENIED_NO_COMMENT = "denied, no comment"

RUN = "run"
DENY = "deny"
VERDICTS = (RUN, DENY)

#: Who gives the verdict when the CLI does. The board service will pass the
#: person who decided in Approvals.
OPERATOR = "operator"

#: The roles that can file a request (ADR 117).
_FILING_ROLES = ("implementador", project_docs.MAPPER_ROLE)


class VerdictRefused(Exception):
    """A verdict that cannot be applied, with the reason; nothing has run."""


@dataclasses.dataclass(frozen=True)
class CommandRun:
    """What running one request came to."""

    exit_code: int | None
    timed_out: bool
    output: str
    truncated: bool
    log_path: str


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _filed_block(cfg: Config, task_id: str) -> tuple[str, dict]:
    """The role and envelope of the phase whose requests blocked the task.

    The newest of the filing roles' envelopes that recorded requests at all:
    a phase that ran after the block re-saves its envelope, so an older one is
    a block already resumed.
    """
    found: list[tuple[str, str, dict]] = []
    for role in _FILING_ROLES:
        try:
            envelope = context_transfer.read_handoff_envelope(cfg.hive_tasks_dir, task_id, role)
        except (OSError, ValueError):
            continue
        requests = envelope.get("command_requests")
        if not isinstance(requests, dict):
            continue
        if not (requests.get("accepted") or requests.get("refused")):
            continue
        found.append((str(envelope.get("saved_at") or ""), role, envelope))
    if not found:
        raise VerdictRefused(f"task {task_id} has no filed command requests on record")
    _, role, envelope = max(found, key=lambda item: item[0])
    return role, envelope


def _session_of(cfg: Config, task_id: str, role: str, envelope: dict) -> tuple[str, str]:
    """The session and account the filing phase ran in.

    The envelope's own, written since ADR 119; for one written before, the
    newest usage line of that role and round that names a session.
    """
    session_id, account = envelope.get("session_id"), envelope.get("account")
    if not session_id or not account:
        round_num = envelope.get("round")
        for line in reversed(context_transfer.read_usage(cfg.hive_tasks_dir, task_id).lines):
            if line.get("role") != role or line.get("round") != round_num:
                continue
            if isinstance(line.get("session_id"), str) and line.get("session_id"):
                session_id = session_id or line["session_id"]
                account = account or line.get("account")
                break
    if not session_id or not account:
        raise VerdictRefused(
            f"task {task_id}: the {role}'s session is not on record, so it cannot be resumed"
        )
    return session_id, account


def _pick_request(task_id: str, role: str, envelope: dict, n: int) -> dict:
    """The accepted request `n` names, or the reason it names none."""
    requests = envelope["command_requests"]
    accepted = list(requests.get("accepted") or [])
    refused = list(requests.get("refused") or [])
    if 1 <= n <= len(accepted):
        return accepted[n - 1]
    if refused:
        # A refused request has no number: it was refused at filing and is
        # not the operator's to run.
        reasons = "; ".join(str(item.get("reason")) for item in refused)
        raise VerdictRefused(
            f"task {task_id}: request {n} is not one the {role} filed and the check "
            f"accepted ({len(accepted)} accepted); {len(refused)} refused at filing "
            f"cannot be run: {reasons}"
        )
    raise VerdictRefused(
        f"task {task_id}: request {n} is out of range: the {role} filed "
        f"{len(accepted)} accepted request(s)"
    )


def _recheck(cfg: Config, task_id: str, slug: str, account_name: str, request: dict) -> dict:
    """The request resolved again and past the denylist again, on the resuming account.

    Step 3's own `check`, so the rules are the same ones as at filing; what
    changed since — a service stopped, a config edit — refuses it here.
    """
    entry = {key: value for key, value in request.items() if key != "resolved"}
    account = next((acc for acc in cfg.accounts if acc.name == account_name), None)
    project_dir = f"{cfg.projects_root}/{slug}"
    still, out = command_targets.check(
        [entry], [], account, cfg.accounts,
        command_targets.writer_worktree(cfg.projects_root, slug, task_id),
        lambda: command_targets.running_services(account.dind, project_dir),
    )
    if not still:
        raise VerdictRefused(
            f"task {task_id}: the request no longer passes its check: {out[0]['reason']}"
        )
    return still[0]


def _command_for(argv: list[str], resolved: dict) -> list[str]:
    """What `docker exec` runs in the resolved container: the argv, as a list.

    A `service` is reached through its dind's compose, run from the resolved
    `cwd` so it finds the project the agent started there; every other kind
    runs the argv as given.
    """
    if resolved["kind"] == "service":
        return ["docker", "compose", "exec", "-T", resolved["service"], *argv]
    return list(argv)


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _capped(output: str) -> tuple[str, bool]:
    """The output cut at `OUTPUT_CAP_BYTES`, and whether it was cut."""
    data = output.encode("utf-8")
    if len(data) <= OUTPUT_CAP_BYTES:
        return output, False
    kept = data[:OUTPUT_CAP_BYTES].decode("utf-8", errors="ignore")
    marker = TRUNCATED_MARKER.format(cap=OUTPUT_CAP_BYTES, dropped=len(data) - OUTPUT_CAP_BYTES)
    return f"{kept}\n{marker}\n", True


def _log_text(argv: list[str], resolved: dict, exit_code: int | None, timed_out: bool, output: str) -> str:
    header = [
        f"argv: {json.dumps(argv)}",
        f"target: {resolved['kind']} {resolved['container']}",
    ]
    if resolved.get("service"):
        header.append(f"service: {resolved['service']}")
    header.append(f"cwd: {resolved['cwd']}")
    if timed_out:
        header.append(f"timeout: after {COMMAND_TIMEOUT_SECONDS}s, no exit code")
    else:
        header.append(f"exit: {exit_code}")
    return "\n".join(header) + "\n--- output ---\n" + output


def run_request(cfg: Config, task_id: str, n: int, request: dict) -> CommandRun:
    """Run one resolved request and write `commands/<n>.log`. No shell anywhere.

    The argv reaches `docker exec` as list items, so a `;` or a `$(...)` in
    one is an argument and nothing else. stdout comes first in the output,
    then stderr.
    """
    argv = list(request["argv"])
    resolved = request["resolved"]
    exit_code: int | None = None
    timed_out = False
    try:
        proc = docker_exec.run_docker_exec(
            resolved["container"], resolved["cwd"], _command_for(argv, resolved),
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
        exit_code = proc.returncode
        stdout, stderr = _as_text(proc.stdout), _as_text(proc.stderr)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout, stderr = _as_text(exc.stdout), _as_text(exc.stderr)
    if stdout and stderr and not stdout.endswith("\n"):
        stdout += "\n"
    output, truncated = _capped(stdout + stderr)
    path = context_transfer.save_command_log(
        cfg.hive_tasks_dir, task_id, n, _log_text(argv, resolved, exit_code, timed_out, output),
    )
    logger.info(
        "task %s: request %d ran on %s: %s; log at %s",
        task_id, n, resolved["container"],
        "timed out" if timed_out else f"exit {exit_code}", path,
    )
    return CommandRun(exit_code, timed_out, output, truncated, path)


def run_prompt(n: int, request: dict, run: CommandRun) -> str:
    """What the resumed phase is told after a *run*."""
    resolved = request["resolved"]
    target = f"{resolved['kind']} {resolved['container']}"
    if resolved.get("service"):
        target += f" (service {resolved['service']})"
    lines = [
        f"The operator ran your command request {n}.",
        f"argv: {json.dumps(list(request['argv']))}",
        f"target: {target}, cwd {resolved['cwd']}",
        f"timed out after {COMMAND_TIMEOUT_SECONDS}s, no exit code" if run.timed_out
        else f"exit code: {run.exit_code}",
    ]
    if run.truncated:
        lines.append(f"The output passed {OUTPUT_CAP_BYTES} bytes; read it in full at {run.log_path}")
    else:
        lines += ["output:", "```", run.output.rstrip("\n"), "```"]
    lines.append("Go on with the phase from here, and end it with your return as before.")
    return "\n".join(lines)


def deny_prompt(n: int, request: dict, comment: str | None) -> str:
    """What the resumed phase is told after a *deny*."""
    return "\n".join([
        f"The operator denied your command request {n}: {json.dumps(list(request['argv']))}",
        f"comment: {(comment or '').strip() or DENIED_NO_COMMENT}",
        "Do not run it another way. Go on with the phase without it, and end it "
        "with your return as before.",
    ])


def _check_blocked(cfg: Config, task_id: str) -> context_transfer.TaskFile:
    try:
        task = context_transfer.read_task_file(context_transfer.task_file_path(cfg.hive_tasks_dir, task_id))
    except FileNotFoundError:
        raise VerdictRefused(f"task {task_id} has no task file") from None
    if task.status != "blocked" or task.blocked_reason != handoff.COMMAND_REQUEST:
        raise VerdictRefused(
            f"task {task_id} is not blocked on a command request "
            f"(status {task.status}, blocked_reason {task.blocked_reason})"
        )
    return task


def run_command_request(
    cfg: Config,
    task_id: str,
    slug: str,
    n: int,
    verdict: str,
    comment: str | None = None,
    by: str = OPERATOR,
) -> CycleOutcome:
    """Apply one verdict on a filed request, resume the phase, and carry the cycle on.

    Raises `VerdictRefused` before anything runs or is recorded when the
    verdict cannot apply: the task is not blocked on `command_request`, `n` is
    no accepted request, the request was refused at filing, the block already
    had a verdict, the phase's session is not on record, or — on *run* — the
    request no longer passes step 3's check on the account that resumes.

    Otherwise the verdict is recorded on the task first, so a crash past this
    point can never run the command twice; *run* then runs it and writes the
    log; the phase's session resumes on its own account with the result; and
    what follows is the cycle after that phase: the gates and the revisor
    rounds for the implementador, the arquitecto on for the cartografo.
    """
    if verdict not in VERDICTS:
        raise VerdictRefused(f"verdict {verdict!r} is not one of {', '.join(VERDICTS)}")
    task = _check_blocked(cfg, task_id)
    role, envelope = _filed_block(cfg, task_id)
    filed_at = envelope.get("saved_at")
    request = _pick_request(task_id, role, envelope, n)
    earlier = [
        item for item in task.command_verdicts
        if item.get("role") == role and item.get("filed_at") == filed_at
    ]
    if earlier:
        # One verdict per block: it resumes the phase, and the block's other
        # requests are moot once the phase has gone on.
        first = earlier[0]
        raise VerdictRefused(
            f"task {task_id}: this block already has a verdict: request {first.get('n')} "
            f"{first.get('verdict')} by {first.get('by')} at {first.get('at')}"
        )
    session_id, account = _session_of(cfg, task_id, role, envelope)
    if verdict == RUN:
        request = _recheck(cfg, task_id, slug, account, request)

    ctx = open_cycle(cfg, task_id, slug)
    if ctx is None:
        raise VerdictRefused(f"task {task_id} cannot be resumed; the log above says why")
    try:
        tasks.record_command_verdict(cfg.hive_tasks_dir, task_id, {
            "n": n, "verdict": verdict, "by": by, "at": _utc_now().isoformat(),
            "comment": comment, "role": role, "filed_at": filed_at,
        })
        if verdict == RUN:
            run = run_request(cfg, task_id, n, request)
            # What the prompt is built from, kept on the verdict so that a
            # resume no account could take can be given the same prompt by
            # `resume_command_verdict` without running the command again. The
            # output stays in the log, which already holds it.
            tasks.update_command_verdict(cfg.hive_tasks_dir, task_id, role, filed_at, {
                "ran": {
                    "exit_code": run.exit_code, "timed_out": run.timed_out,
                    "truncated": run.truncated, "log": run.log_path,
                    "resolved": dict(request["resolved"]),
                },
            })
            prompt = run_prompt(n, request, run)
        else:
            prompt = deny_prompt(n, request, comment)
        return _resume(ctx, role, envelope.get("round"), session_id, account, prompt, filed_at)
    finally:
        close_cycle(ctx, completed=ctx.completed)


def _block_verdict(task: context_transfer.TaskFile, role: str, filed_at: object) -> dict | None:
    """The verdict given on the block `role` filed at `filed_at`, if any."""
    for item in task.command_verdicts:
        if item.get("role") == role and item.get("filed_at") == filed_at:
            return item
    return None


def awaits_resume(cfg: Config, task_id: str) -> bool:
    """Whether the task holds a verdict whose resume has not happened yet.

    What `resume_command_verdict` would take, short of the session check: the
    CLI asks it to say which command moves a held task on.
    """
    try:
        task = _check_blocked(cfg, task_id)
        role, envelope = _filed_block(cfg, task_id)
    except VerdictRefused:
        return False
    entry = _block_verdict(task, role, envelope.get("saved_at"))
    return entry is not None and not entry.get("resumed_at")


_LOG_SEPARATOR = "\n--- output ---\n"


def _logged_run(cfg: Config, task_id: str, entry: dict, request: dict) -> tuple[dict, CommandRun]:
    """The run a *run* verdict came to, read back from its entry and its log.

    The entry's `ran` since T-076; for an entry written before it, or one
    whose run wrote its log and stopped before `ran` was kept, the log's own
    header, which names the same argv, target, exit and timeout. A log that
    is missing, unreadable or for another argv refuses: the command may have
    run, and this path never runs it again to find out.
    """
    n = entry.get("n")
    path = context_transfer.command_log_path(cfg.hive_tasks_dir, task_id, n)
    gone = (
        f"task {task_id}: request {n} has a run verdict but no readable log of what "
        f"it came to at {path}; it is not run again here, so the phase cannot be "
        "told the same thing. Move the task on with run-phase"
    )
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        raise VerdictRefused(gone) from None
    head, sep, output = text.partition(_LOG_SEPARATOR)
    if not sep:
        raise VerdictRefused(gone)
    ran = entry.get("ran")
    if isinstance(ran, dict):
        return (
            {"argv": list(request["argv"]), "resolved": dict(ran["resolved"])},
            CommandRun(ran.get("exit_code"), bool(ran.get("timed_out")), output,
                       bool(ran.get("truncated")), ran.get("log") or path),
        )
    fields: dict[str, str] = {}
    for line in head.splitlines():
        key, _, value = line.partition(": ")
        fields[key] = value
    if fields.get("argv") != json.dumps(list(request["argv"])):
        raise VerdictRefused(gone)
    kind, _, container = fields.get("target", "").partition(" ")
    resolved = {"kind": kind, "container": container, "cwd": fields.get("cwd", "")}
    if fields.get("service"):
        resolved["service"] = fields["service"]
    timed_out = "timeout" in fields
    try:
        exit_code = None if timed_out else int(fields.get("exit", ""))
    except ValueError:
        raise VerdictRefused(gone) from None
    # The marker `_capped` writes is the only trace a cut left in an old log.
    truncated = TRUNCATED_MARKER.split("{cap}")[0] in output
    return (
        {"argv": list(request["argv"]), "resolved": resolved},
        CommandRun(exit_code, timed_out, output, truncated, path),
    )


def resume_command_verdict(cfg: Config, task_id: str, slug: str) -> CycleOutcome:
    """Resume the phase on a verdict already given, whose resume no account took.

    Raises `VerdictRefused` before anything happens when the task is not
    blocked on `command_request`, the block it is blocked on has no verdict,
    that verdict was already resumed, the phase's session is not on record,
    or — on *run* — the log of what the command came to cannot be read.

    Otherwise the phase is told what `run_command_request` would have told
    it: the same `run_prompt`, from the run the verdict kept, or the same
    `deny_prompt`, from its comment. The command is never run here, and the
    request is not checked again: there is nothing left to run. What follows
    is the cycle after that phase, as there.
    """
    task = _check_blocked(cfg, task_id)
    role, envelope = _filed_block(cfg, task_id)
    filed_at = envelope.get("saved_at")
    entry = _block_verdict(task, role, filed_at)
    if entry is None:
        raise VerdictRefused(
            f"task {task_id}: the block the {role} filed at {filed_at} has no verdict yet; "
            "give one with command-verdict"
        )
    if entry.get("resumed_at"):
        raise VerdictRefused(
            f"task {task_id}: the verdict on request {entry.get('n')} ({entry.get('verdict')}) "
            f"was already resumed at {entry['resumed_at']}"
        )
    session_id, account = _session_of(cfg, task_id, role, envelope)
    n = entry.get("n")
    if not isinstance(n, int):
        raise VerdictRefused(f"task {task_id}: the verdict on record names no request")
    request = _pick_request(task_id, role, envelope, n)
    if entry.get("verdict") == RUN:
        request, run = _logged_run(cfg, task_id, entry, request)
        prompt = run_prompt(n, request, run)
    elif entry.get("verdict") == DENY:
        prompt = deny_prompt(n, request, entry.get("comment"))
    else:
        raise VerdictRefused(
            f"task {task_id}: the verdict on record ({entry.get('verdict')!r}) is not one of "
            f"{', '.join(VERDICTS)}"
        )

    ctx = open_cycle(cfg, task_id, slug)
    if ctx is None:
        raise VerdictRefused(f"task {task_id} cannot be resumed; the log above says why")
    try:
        logger.info(
            "task %s: resuming the %s on the %s verdict given on request %d at %s",
            task_id, role, entry.get("verdict"), n, entry.get("at"),
        )
        return _resume(ctx, role, envelope.get("round"), session_id, account, prompt, filed_at)
    finally:
        close_cycle(ctx, completed=ctx.completed)


def _settle_resume(ctx: CycleContext, role: str, filed_at: object) -> None:
    """Record what the resume's own phase came to, before the cycle goes on.

    Taking the task's lock is the point the resume counts as done: an account
    was claimed, `tasks.acquire_lock` moved the task off its block, and the
    session ran `--resume` on it. From there `resumed_at` stays, whatever the
    phase came to, so the session is never resumed on this verdict twice.
    Short of it nothing of the session ran: no account was free, or a foreign
    lock refused it. A held one has its block put back, since a rate limit
    met inside the call can have taken the lock and given it back, and the
    verdict stays to resume once an account resets. An owner on the file by
    then is another run's, and is left alone, as `_mark_blocked` leaves it.
    """
    hive = ctx.cfg.hive_tasks_dir
    if ctx.took_lock:
        tasks.update_command_verdict(hive, ctx.task_id, role, filed_at, {
            "resumed_at": _utc_now().isoformat(),
        })
    elif ctx.held:
        task = context_transfer.read_task_file(context_transfer.task_file_path(hive, ctx.task_id))
        if task.owner is None:
            tasks.mark_blocked(hive, ctx.task_id, handoff.COMMAND_REQUEST)


def _resume(
    ctx: CycleContext, role: str, round_num: int | None, session_id: str, account: str, prompt: str,
    filed_at: object,
) -> CycleOutcome:
    """The filing phase's session resumed, then the cycle from after that phase."""
    cfg = ctx.cfg
    if role == project_docs.MAPPER_ROLE:
        mapped = run_phase(
            ctx, role, fatal=False, model=cfg.mapping_model, max_turns=cfg.mapping_max_turns,
            resume_session_id=session_id, prompt=prompt, account=account,
        )
        _settle_resume(ctx, role, filed_at)
        if mapped is None:
            return _stopped(ctx)
        if _filed_command_requests(ctx, mapped):
            return _blocked_outcome(ctx, handoff.COMMAND_REQUEST)
        return _cycle_from_arquitecto(ctx, True, None)
    round_num = round_num or 1
    implemented = run_phase(
        ctx, role, round_num=round_num,
        resume_session_id=session_id, prompt=prompt, account=account,
    )
    _settle_resume(ctx, role, filed_at)
    # `_cycle_rounds` reads a None and a filed request the way a cycle does.
    return _cycle_rounds(ctx, True, None, resumed=(round_num, implemented))
