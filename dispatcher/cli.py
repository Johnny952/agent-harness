from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from dispatcher import (
    allowlist,
    backup as backup_mod,
    command_run,
    context_transfer,
    debt,
    docker_exec,
    handoff,
    learnings,
    operator,
    project_docs,
    project_keys,
    projects,
    role_skills,
    tasks,
)
from dispatcher import queue as queue_mod
from dispatcher import worker as worker_mod
from dispatcher.config import Config, load_config
from dispatcher.project_lock import ProjectBusy
from dispatcher.dispatcher import (
    CycleOutcome,
    _base_lock,
    cleanup_container,
    container_for,
    file_saved_split,
    left_behind_report,
    pre_merge_check,
    push_base,
    recheck_awaiting_review,
    record_merge,
    resolve_conflict_locked,
    run_draft_specs,
    run_single_phase,
    run_task_cycle,
)


#: The roles `run-phase` will dispatch. Held here rather than derived from
#: ROLE_SKILLS because the cartografo has no skills and is still a phase, and
#: because a typo in --role should be a usage error and not a full-price run
#: of a role nothing knows how to prompt.
_PHASE_ROLES = frozenset(role_skills.ROLE_SKILLS) | {project_docs.MAPPER_ROLE}

#: A run that stopped and needs a person: a blocked task, a refused merge, an
#: entry that is not there. The code every other refusal here already uses.
EXIT_BLOCKED = 1
#: A run that stopped only because no account could take its next phase. Not
#: 1, because nothing is wrong with the task and the answer is to wait for a
#: reset, not to read a log; not 2, which argparse owns for usage errors. 75 is
#: sysexits.h's EX_TEMPFAIL, "try again later", which is exactly this
#: (`docs/decisions.md` ADR 52).
EXIT_HELD = 75
#: A run that stopped because the arquitecto split the task and the split was
#: filed: the task is an epic now and its sub-tasks are what runs. Not 0,
#: because the task did not ship; not 1, because nothing needs repair; not 2,
#: argparse's; not 75, because waiting changes nothing (`docs/decisions.md`
#: ADR 60).
EXIT_SPLIT = 3
#: `merge-task --push` whose merge landed and whose push did not: the upstream
#: moved, or refused the credential. Not 1, which here means the branch did not
#: land, and it did; not 3 or 75, which mean other things; so a script can tell
#: "merged, push again later" from both (`docs/decisions.md` ADR 78).
EXIT_NOT_PUSHED = 4
#: `run-queue` that stopped on its token limit with tasks left: nothing is
#: wrong and no reset helps, the owner raises the limit or lets it be. Not 4,
#: whose push did not land; a queue stopped on a refused push exits 4 as
#: `merge-task --push` does (`docs/decisions.md` ADR 79).
EXIT_LIMIT = 5
#: The smallest `run-queue --limit`, in measured tokens. One phase alone
#: measures more, so a smaller number is a task count misread as a limit
#: (`docs/decisions.md` ADR 333).
MIN_QUEUE_LIMIT = 100_000


def _epic_line(cfg: Config, task_id: str) -> str:
    """What `run-task` and `file-split` print once a split is filed.

    The ids are the task file's `split_into`. A file that will not read still
    gets the line, without them: the split is filed, and the log named them.
    """
    ids = "its sub-tasks"
    try:
        task = context_transfer.read_task_file(
            context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)
        )
        if isinstance(task.split_into, list) and task.split_into:
            ids = ", ".join(str(item) for item in task.split_into)
    except Exception:
        pass
    return f"task {task_id} is now an epic: {ids}; run them in that order"


def _exit_on_resumed_outcome(cfg: Config, args: argparse.Namespace, outcome: CycleOutcome) -> None:
    """What `command-verdict` and `command-resume` say and exit with, short of a finish.

    The codes `run-task` uses. A hold names the command that moves the task
    on: `command-resume` while the verdict's own resume is the one no account
    took, `run-phase` once it ran and a later phase was the one held.
    """
    for line in left_behind_report(cfg, args.task_id):
        print(line, file=sys.stderr)
    if outcome is CycleOutcome.HELD:
        if command_run.awaits_resume(cfg, args.task_id):
            then = (
                f"resume it with command-resume {args.task_id} --project {args.project} "
                "once one resets; the verdict stays and the command does not run again"
            )
        else:
            then = "run the phase again with run-phase once one resets"
        print(
            f"task {args.task_id} is held: no account could take its next phase; {then}",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_HELD)
    if outcome is CycleOutcome.BLOCKED:
        print(f"task {args.task_id} is blocked; the log above says why", file=sys.stderr)
        raise SystemExit(EXIT_BLOCKED)
    if outcome is CycleOutcome.SPLIT:
        print(_epic_line(cfg, args.task_id))
        raise SystemExit(EXIT_SPLIT)


def _exit_project_busy(exc: ProjectBusy, task_id: str):
    """A start on a project another cycle holds: one line, exit 75 (ADR 200, 5)."""
    print(f"{exc}; {task_id} was not started", file=sys.stderr)
    raise SystemExit(EXIT_HELD)


def _print_proposed_split(args: argparse.Namespace, payload: dict | None) -> None:
    """Say what a lone arquitecto phase proposed. `run-phase` files nothing."""
    if handoff.blocked(payload):
        return
    subtasks = handoff.split_of(payload)
    if not subtasks:
        return
    print(
        f"phase arquitecto proposes splitting task {args.task_id} into "
        f"{len(subtasks)} sub-tasks; nothing was filed:"
    )
    for line in handoff._split(payload):
        print(f"  {line}")
    print(
        f"run `dispatch file-split --task-id {args.task_id} --project {args.project}` "
        "to file them"
    )
    problems = handoff.split_problems(subtasks)
    if problems:
        print(f"file-split would refuse it: {'; '.join(problems)}", file=sys.stderr)


#: How many task ids a refusal lists before it says "and N more".
_UNMIGRATED_SHOWN = 5


def _require_migrated(cfg: Config, parser: argparse.ArgumentParser) -> None:
    """Refuse a verb that writes task files while any of them still holds its description.

    The whole-hive rule of `docs/business.md` *Task descriptions*: one
    unmigrated file stops every task, until `dispatch migrate` has moved it
    out. Losing the ask is no longer what this guards — `tasks.py`'s writer
    refuses such a file itself — but that refusal is per file and raises from
    wherever the write happens to be. This one is hive-wide and a usage
    error, like every other refusal made before a run starts.
    """
    stale = tasks.unmigrated_ids(cfg.hive_tasks_dir)
    if not stale:
        return
    shown = ", ".join(stale[:_UNMIGRATED_SHOWN])
    if len(stale) > _UNMIGRATED_SHOWN:
        shown += f" and {len(stale) - _UNMIGRATED_SHOWN} more"
    parser.error(
        f"{len(stale)} task file(s) still carry their description in the frontmatter "
        f"({shown}): run `{tasks.MIGRATE_COMMAND}` first"
    )


def _run_close_task(cfg: Config, args: argparse.Namespace) -> None:
    """`close-task`: `status: done` on a merged task, a refusal is exit 1 (ADR 255)."""
    task_id = args.task_id
    path = context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)
    try:
        card = context_transfer.read_task_file(path)
    except (OSError, ValueError):
        card = None  # mark_closed below says what is wrong with the file
    if card is not None and card.project is not None and card.project != args.project:
        print(
            f"task {task_id}: not closed: its card names project {card.project}, "
            f"not {args.project}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    try:
        was = tasks.mark_closed(cfg.hive_tasks_dir, task_id)
    except FileNotFoundError:
        print(f"task {task_id}: not closed: no task file at {path}", file=sys.stderr)
        raise SystemExit(1)
    except (tasks.NotMergedError, tasks.UnmigratedTaskError) as exc:
        print(f"task {task_id}: not closed: {exc}", file=sys.stderr)
        raise SystemExit(1)
    if was is None:
        print(f"task {task_id}: already done; nothing changed")
    else:
        print(f"task {task_id}: {was} -> done")


def _run_migrate(cfg: Config, default: str | None = None) -> None:
    """Move every description out of its task file, then register the projects.

    Two halves, each idempotent. The first moves descriptions to the board.
    The second (`docs/plans/projects.md` step 1, ADR 199) writes a project
    record for every clone under `projects_root` that has none, and stamps the
    cards that name no project with `default`: `--project`, or the one record
    when there is exactly one. A record already there is never written over.
    """
    refused = _run_migrate_descriptions(cfg)
    refused = _run_migrate_projects(cfg, default) or refused
    if refused:
        raise SystemExit(EXIT_BLOCKED)


def _run_migrate_projects(cfg: Config, default: str | None) -> bool:
    """The project half of `migrate`; True when something was refused.

    The clones are listed in an agent container, which mounts the projects;
    the dispatcher's does not. With no container, or a listing that fails, no
    record is written, the cards are still stamped against the records that
    exist, and the run exits refused so the operator runs it again.
    """
    clones: list[projects.Clone] = []
    unlisted = None
    container = cleanup_container(cfg)
    if container is None:
        unlisted = "no accounts configured: nothing to list the clones in"
    else:
        try:
            clones = projects.discover(container, cfg.projects_root)
        except projects.DiscoverError as exc:
            unlisted = str(exc)
    if unlisted is not None:
        print(f"projects: not listed: {unlisted}", file=sys.stderr)
    report = projects.migrate(
        cfg.hive_tasks_dir, clones,
        keys_dir=cfg.project_keys_dir, ttl_seconds=cfg.heartbeat_ttl_seconds, default=default,
    )
    for slug in report.written:
        print(f"{slug}: project record written to {projects.record_path(context_transfer.hive_root(cfg.hive_tasks_dir), slug)}")
    for name, reason in report.skipped:
        print(f"{name}: no project record: {reason}", file=sys.stderr)
    for task_id in report.stamped:
        print(f"{task_id}: project {report.default}")
    for task_id, reason in report.refused:
        print(f"{task_id}: not stamped: {reason}", file=sys.stderr)
    if report.written or report.stamped or report.refused or report.skipped:
        print(
            f"wrote {len(report.written)} project record(s), {len(report.existing)} already there; "
            f"stamped {len(report.stamped)} card(s), {report.already} already named a project"
        )
    return unlisted is not None or bool(report.refused)


def _run_migrate_descriptions(cfg: Config) -> bool:
    """Move every description out of its task file, and print what moved."""
    report = tasks.migrate(cfg.hive_tasks_dir, cfg.heartbeat_ttl_seconds)
    for task_id, path in report.moved:
        print(f"{task_id}: description moved to {path}")
    for task_id in report.emptied:
        print(f"{task_id}: empty description dropped")
    for task_id, reason in report.refused:
        print(f"{task_id}: not migrated: {reason}", file=sys.stderr)
    if report.moved or report.emptied or report.refused:
        print(
            f"moved {len(report.moved)} description(s); {report.already} already migrated"
        )
    else:
        print("nothing to migrate")
    return bool(report.refused)


def _run_import_debt(args: argparse.Namespace, cfg: Config, parser: argparse.ArgumentParser) -> None:
    """`import-debt`: every open entry of a debt index as a `proposed` item (ADR 131).

    The one-time filing of the entries the index held before the dispatcher
    mirrored new ones, through the writer that mirroring uses, so a second
    run, or one after a cycle mirrored some, files nothing twice. The index
    is the project checkout's (`--project`, read in a container as the
    dispatcher reads it) or a file on this host (`--index`).
    """
    if args.index is not None:
        try:
            text = Path(args.index).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"cannot read {args.index}: {exc}", file=sys.stderr)
            raise SystemExit(EXIT_BLOCKED) from None
        source = args.index
    else:
        container = cleanup_container(cfg)
        if container is None:
            parser.error("no accounts configured: nothing to read the index in")
        project_dir = f"{cfg.projects_root}/{args.project}"
        proc = docker_exec.run_docker_exec(container, project_dir, ["cat", project_docs.DEBT_INDEX])
        if proc.returncode != 0:
            print(
                f"cannot read {project_docs.DEBT_INDEX} in {project_dir}: "
                f"{(proc.stderr or '').strip() or 'no such file'}",
                file=sys.stderr,
            )
            raise SystemExit(EXIT_BLOCKED)
        text = proc.stdout
        source = f"{args.project}:{project_docs.DEBT_INDEX}"
    try:
        results = tasks.file_debt(cfg.hive_tasks_dir, debt.index_rows(text), dry_run=args.dry_run)
    except tasks.BoardWriteError as exc:
        print(f"not imported: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_BLOCKED) from None
    new = [result for result in results if result["created"]]
    gated = [result for result in results if result["gated"]]
    for result in results:
        if result["gated"]:
            already = f"; already {result['id']}" if result["id"] else ""
            print(
                f"{result['debt_id']}: gated on {result['blocked_by']}, not filed{already} "
                f"(priority {result['priority']}): {result['title']}"
            )
            continue
        if result["created"]:
            verb = "would file" if args.dry_run else f"filed as {result['id']}"
        else:
            verb = f"already {result['id']}"
        if result["depends_on"]:
            verb += f" after {', '.join(result['depends_on'])}"
        if result["unknown_blocker"]:
            verb += f", blocked by {result['blocked_by']}, which the board cannot read"
        print(f"{result['debt_id']}: {verb} (priority {result['priority']}): {result['title']}")
    prefix = "dry run: " if args.dry_run else ""
    tail = f", {len(gated)} gated on a plan step" if gated else ""
    print(
        f"{prefix}{len(results)} open entr{'y' if len(results) == 1 else 'ies'} in {source}; "
        f"{len(new)} {'to file' if args.dry_run else 'filed'}, "
        f"{len(results) - len(new) - len(gated)} already filed{tail}"
    )


def _resolve_description(
    args: argparse.Namespace, cfg: Config, parser: argparse.ArgumentParser
) -> str | None:
    """The description to seed this run with, or None to keep the stored one.

    Refuses to dispatch a task nobody described: without an ask, the four
    phases can only produce noise at full quota cost. Refuses one missing
    `## Goal` or `## Done when` too, naming the section. argparse's own
    parser.error is used so the failure reads like any other usage error
    and exits 2.
    """
    if args.description_file is not None:
        # "-" is the shape that makes piping work from a compose run:
        #   docker compose run --rm -T dispatcher ... --description-file - < spec.md
        if args.description_file == "-":
            text = sys.stdin.read()
        else:
            try:
                text = Path(args.description_file).read_text()
            except OSError as exc:
                parser.error(f"--description-file: {exc}")
    elif args.description is not None:
        text = args.description
    else:
        stored = tasks.read_description(cfg.hive_tasks_dir, args.task_id)
        if (stored or "").strip():
            return None  # a resume: the ask is already on disk, don't rewrite it
        parser.error(
            f"task {args.task_id} has no description: pass --description or "
            "--description-file (use '-' to read stdin). Without one the "
            "roles are dispatched with nothing to work from."
        )

    if not text.strip():
        parser.error("the task description is empty")
    try:
        tasks.parse_description(text)
    except tasks.DescriptionError as exc:
        parser.error(f"the task description {exc}")
    return text


def _run_learnings(args: argparse.Namespace, cfg: Config) -> None:
    """The human half of the learnings inbox.

    Everything here is a judgement the dispatcher deliberately does not make:
    whether a trap is real before a second task has hit it, and whether one
    project's trap is every project's. The listing is the same table the
    phases are handed, so what a human rules on is what the agents read.
    """
    hive = cfg.hive_tasks_dir
    if args.promote:
        entry = learnings.promote(hive, args.promote)
        if entry is None:
            print(
                f"{args.promote}: no such inbox entry (already promoted ones cannot be "
                "promoted again)",
                file=sys.stderr,
            )
            raise SystemExit(1)
        print(f"promoted to {entry.ref}: every project's phases now read it")
        return
    for flag, status in (
        ("confirm", learnings.CONFIRMED),
        ("unconfirm", learnings.UNCONFIRMED),
        ("refute", learnings.REFUTED),
    ):
        ref = getattr(args, flag)
        if not ref:
            continue
        entry = learnings.set_status(hive, ref, status)
        if entry is None:
            print(f"{ref}: no such entry", file=sys.stderr)
            raise SystemExit(1)
        print(f"{entry.ref}: {status}")
        return
    if args.drop:
        ref = learnings.delete(hive, args.drop)
        if ref is None:
            print(f"{args.drop}: no such entry", file=sys.stderr)
            raise SystemExit(1)
        print(f"deleted {ref}")
        return

    entries = learnings.read_all(hive)
    if args.project:
        entries = learnings.applicable(entries, args.project)
    if not entries:
        print(f"no learnings in {learnings.root_dir(hive)}")
        return
    # Against the surface a run would have right now, so the listing marks the
    # same rows stale that the next task's phases will see marked stale.
    print(learnings.table(entries, learnings.harness_fingerprint(
        cfg.permission_mode, cfg.allowed_tools, docker_exec.WRITER_ROLES
    )))
    print()
    print(f"the entries themselves are in {learnings.root_dir(hive)}/, one file per row")


#: Read once, at startup, so a run can be made louder or quieter without
#: touching the config file the containers share.
_LOG_LEVEL_ENV = "DISPATCH_LOG_LEVEL"


def _configure_logging() -> str | None:
    """Give the dispatcher's own log somewhere to go.

    Nothing configured logging before this, so the root logger sat at WARNING
    with no handler: every `logger.info` in a run was dropped, and the few
    warnings that survived went through logging's last-resort handler with no
    timestamp and no logger name. A full end-to-end run produced five lines of
    log, which is not enough to tell what a task did.

    stderr, not stdout: the subcommands print their results to stdout and a
    caller may be reading them.

    Returns the unusable level name when the environment asked for one, for
    the caller to report once logging is actually up.
    """
    wanted = os.environ.get(_LOG_LEVEL_ENV)
    level = logging.getLevelName((wanted or "INFO").upper())
    bad = None
    if not isinstance(level, int):
        bad, level = wanted, logging.INFO
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return bad


def _run_allowlist(args: argparse.Namespace, cfg: Config, parser: argparse.ArgumentParser) -> None:
    """`allowlist`: print a project's entries, or revoke one (ADR 121)."""
    if not context_transfer._is_single_path_component(args.project):
        parser.error(f"--project {args.project!r} is not a project slug")
    if args.revoke is not None:
        if not args.role:
            parser.error("--revoke needs --role")
        if not allowlist.revoke(cfg.state_dir, args.project, args.role, args.revoke):
            print(
                f"{args.project}: the {args.role} has no allowlist entry {args.revoke}",
                file=sys.stderr,
            )
            raise SystemExit(1)
        print(f"{args.project}: revoked {args.revoke} for the {args.role}")
        return
    found = allowlist.entries(cfg.state_dir, args.project, args.role)
    lines = []
    for role in sorted(found):
        for entry in found[role]:
            source = entry.get("from") or {}
            lines.append(
                f"{role}\t{entry.get('pattern')}\tby {entry.get('by')} at {entry.get('at')}"
                f"\tfrom {source.get('task_id')} request {source.get('n')}"
            )
    if not lines:
        print(f"{args.project}: no allowlist entries" + (f" for the {args.role}" if args.role else ""))
        return
    print("\n".join(lines))


def build_parser() -> argparse.ArgumentParser:
    """The dispatcher's whole argument parser; `docs/cli.md` is checked against it."""
    parser = argparse.ArgumentParser(prog="ia-harness-dispatcher")
    parser.add_argument("--config", required=True, help="Path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run-task", help="Run the full role cycle for one task")
    run_parser.add_argument("--task-id", required=True)
    run_parser.add_argument("--project", required=True, help="Project slug")
    description_group = run_parser.add_mutually_exclusive_group()
    description_group.add_argument(
        "--description",
        help="What the task actually asks for, in sections: `## Goal` and "
        "`## Done when` are required, `## Surface`, `## Steps` and "
        "`## Out of scope` optional. Stored as the task's description.md on "
        "the board, beside the hive tasks dir, and embedded in every role's "
        "prompt. Optional only when the task already has a stored description "
        "(i.e. re-running a task).",
    )
    description_group.add_argument(
        "--description-file",
        help="Read the description from this file instead of the command line; "
        "'-' reads stdin. Use it for anything longer than a sentence.",
    )

    phase_parser = sub.add_parser(
        "run-phase",
        help="Run one role's phase on a task that is already under way, instead of "
        "the whole cycle. The repair path: run-task always begins at the "
        "arquitecto, so an interrupted run could otherwise only be continued by "
        "paying for every phase again. It runs the phase and nothing else — no "
        "further round, no verdict read, no merge.",
    )
    phase_parser.add_argument("--task-id", required=True)
    phase_parser.add_argument("--project", required=True, help="Project slug")
    phase_parser.add_argument(
        "--role",
        required=True,
        choices=sorted(_PHASE_ROLES),
        help="Which role's turn to run.",
    )
    phase_parser.add_argument(
        "--round",
        type=int,
        dest="round_num",
        help="The revision round this phase belongs to, for the roles that have "
        "rounds (implementador, revisor). It is what the phase is told it is "
        "on, what labels its section in the task file, and what decides "
        "whether the escalated effort and model apply — so a resumed round 2 has to "
        "say 2, or it is dispatched as though the first round never happened.",
    )
    phase_parser.add_argument(
        "--final",
        action="store_true",
        help="This phase closes the task: the learnings are carried to it "
        "beforehand, the debt the earlier phases declared is recorded and "
        "filed off the handoffs they left on disk, the task file is moved "
        "to done, and the entries are not orphaned on the "
        "way out. In the role set that is the auditor. Without it the handoff "
        "leaves the task pending, which is what every phase before the last "
        "one should do.",
    )
    phase_parser.add_argument(
        "--note",
        default="",
        help="Extra context appended to this one phase's prompt. An operator "
        "resuming a run uses it to say what the dead process took with it. "
        "Under --final the dispatcher appends its own note to it, naming the "
        "debt entries it gave ids, rather than replacing it: the phase is owed "
        "both.",
    )

    split_parser = sub.add_parser(
        "file-split",
        help="File the split in a task's saved arquitecto handoff: one <task-id>-S<n> "
        "task per entry, and the task itself an epic. Runs no model. For a split "
        "that `run-phase --role arquitecto` only proposed, or one a run died before "
        "filing. Refuses a task with no saved handoff, no split or an invalid one, "
        "and one that is done, already an epic or still locked by a live run.",
    )
    split_parser.add_argument("--task-id", required=True)
    split_parser.add_argument("--project", required=True, help="Project slug")

    verdict_parser = sub.add_parser(
        "command-verdict",
        help="Run, deny or allow one command request a task is blocked on, then resume the "
        "phase that filed it in its own session and carry the cycle on from there "
        "(gates, revisor, auditor; the arquitecto on after the cartografo). <n> "
        "counts from 1 over the requests the check accepted. Refused when the task "
        "is not blocked on command_request, <n> names no accepted request, the "
        "block already has a verdict, or, on run or allow, the request no longer passes "
        "its check. allow runs it as run does and also adds it to the project's "
        "allowlist for the filing role, so that role's later phases run it "
        "themselves; it is refused for a command off the agent's own container "
        "or one no Bash pattern can say exactly.",
    )
    verdict_parser.add_argument("task_id", metavar="task")
    verdict_parser.add_argument("n", type=int)
    verdict_parser.add_argument("verdict", choices=command_run.VERDICTS)
    verdict_parser.add_argument("--project", required=True, help="Project slug")
    verdict_parser.add_argument(
        "--comment",
        help="What the phase is told with the verdict. On deny it is the reason; "
        f"without one the phase reads \"{command_run.DENIED_NO_COMMENT}\".",
    )
    verdict_parser.add_argument(
        "--prefix", type=int, metavar="N",
        help="On allow only: write the entry as the argv's first N tokens followed "
        "by anything (Bash(<tokens>:*)) instead of the exact command. Refused for a "
        "prefix that could cover a denylisted command: fewer than 2 tokens, an "
        "option in it, or a shell, interpreter, launcher, privilege or docker "
        "command, or a git subcommand outside the local ones, at its head.",
    )

    resume_parser = sub.add_parser(
        "command-resume",
        help="Resume the phase on the verdict a task's command-request block "
        "already has, when no account could take that resume: the same session, "
        "on its own account, told the same thing command-verdict would have told "
        "it, and the cycle on from there. The command is never run again. Refused "
        "when the task is not blocked on command_request, the block has no "
        "verdict, the verdict was already resumed, or the phase's session is not "
        "on record.",
    )
    resume_parser.add_argument("task_id", metavar="task")
    resume_parser.add_argument("--project", required=True, help="Project slug")

    allowlist_parser = sub.add_parser(
        "allowlist",
        help="List a project's allowlist, the Bash patterns allow verdicts added "
        "to its phases' --allowed-tools, by role; or revoke one entry.",
    )
    allowlist_parser.add_argument("--project", required=True, help="Project slug")
    allowlist_parser.add_argument("--role", help="Only this role's entries; required with --revoke")
    allowlist_parser.add_argument(
        "--revoke", metavar="PATTERN",
        help="Remove the role's entry for this exact pattern, as the listing prints it",
    )

    bootstrap_parser = sub.add_parser(
        "bootstrap-project",
        help="Create the per-project directory (projects_root/<slug>) on one account's container",
    )
    bootstrap_parser.add_argument("--account", required=True, help="Account name from config.yaml's accounts list")
    bootstrap_parser.add_argument("--project", required=True, help="Project slug")

    cleanup_parser = sub.add_parser(
        "cleanup-task",
        help="Delete every worktree of one task, including the writers' one. "
        "The branch agent/task/<task-id> is kept, so no committed work is lost. "
        "A finished cycle already drops the reviewing worktrees by itself; this "
        "is the deliberate step that reclaims the rest, once you are done with it.",
    )
    cleanup_parser.add_argument("--task-id", required=True)
    cleanup_parser.add_argument("--project", required=True, help="Project slug")

    close_parser = sub.add_parser(
        "close-task",
        help="Set `status: done` on a task that already has a `merged` block but whose "
        "status was left behind; refuse one with no `merged` block (exit 1). Touches "
        "no git and no container (docs/decisions.md ADR 255).",
    )
    close_parser.add_argument("--task-id", required=True)
    close_parser.add_argument("--project", required=True, help="Project slug")

    merge_parser = sub.add_parser(
        "merge-task",
        help="Merge agent/task/<task-id> into the branch the project is checked out on. "
        "First the pre-merge check: the base is merged into the task branch and the "
        "test gate runs on the result; a conflict or a red gate moves the task to "
        "`conflict` and merges nothing. Then a --no-ff merge that refuses on a "
        "detached HEAD or a dirty tree and rolls itself back on a conflict. Set "
        "merge_on_done in config.yaml to have a finished cycle do this by itself.",
    )
    merge_parser.add_argument("--task-id", required=True)
    merge_parser.add_argument("--project", required=True, help="Project slug")
    merge_parser.add_argument(
        "--push",
        action="store_true",
        help="Once the branch is in the base (merged now, or already there), push the "
        "base to its upstream, never with force. No upstream is said and is not an "
        f"error; a refused push keeps the merge local and exits {EXIT_NOT_PUSHED}.",
    )

    resolve_parser = sub.add_parser(
        "resolve-conflict",
        help="Resolve a task the pre-merge check moved to `conflict`. The base is "
        "merged into the task branch again (a merge, never a rebase) in the writers' "
        "checkout; the integrador resolves the conflicting files, or for a red gate "
        "the files the failure names, and writing any other file fails it. Then the "
        "merge commit, the test gate and the revisor on the resolution alone, and the "
        "auditor only when an edit fell outside the conflicting hunks. Success moves "
        "the task to awaiting_review, and no earlier approval carries over; a design "
        "conflict leaves it in `conflict` with a note, for request changes "
        "(docs/decisions.md ADR 277).",
    )
    resolve_parser.add_argument("--task-id", required=True)
    resolve_parser.add_argument("--project", required=True, help="Project slug")

    learnings_parser = sub.add_parser(
        "learnings",
        help="Read and rule on the traps the phases filed. With no flag it prints "
        "every entry the harness holds: the inbox any task can write to, and the "
        "cross-project store, which only this command writes. An entry is "
        "confirmed on its own once a second task hits the same wall; the flags "
        "are for the calls only a human can make.",
    )
    learnings_parser.add_argument(
        "--project",
        help="Show only the entries this project's phases would be shown: "
        "everything reviewed, plus everything this project found itself.",
    )
    learnings_group = learnings_parser.add_mutually_exclusive_group()
    learnings_group.add_argument(
        "--promote",
        metavar="REF",
        help="Move one inbox entry into the cross-project store, as a trap that "
        "holds for every project this harness runs. This is the only way in, and "
        "there is no automated path: it is one phase's word about every project "
        "at once, so read the entry first.",
    )
    learnings_group.add_argument(
        "--confirm", metavar="REF", help="Mark one entry confirmed without waiting for a second task."
    )
    learnings_group.add_argument(
        "--unconfirm", metavar="REF", help="Take a confirmation back: the entry stays, as a claim."
    )
    learnings_group.add_argument(
        "--refute",
        metavar="REF",
        help="Retire one entry on evidence: it stays on disk and stays in this listing, "
        "but the phases stop being handed it. Use this rather than --drop whenever the "
        "entry was ever true, so that what was learned and what changed both survive.",
    )
    learnings_group.add_argument(
        "--drop", metavar="REF", help="Delete one entry. For a trap that was wrong, or one that no longer bites."
    )

    status_parser = sub.add_parser(
        "status",
        help="What every account and every locked task is doing right now. "
        "Reads the state directory and the task cards and writes nothing, so "
        "it is safe to run against a harness mid-cycle. This is the command "
        "that tells you whether a run can start at all.",
    )
    status_parser.add_argument(
        "--probe",
        action="store_true",
        help="Also run /usage in each account's container. Unlike the quota gate "
        "inside a cycle, this only reports: it never parks an account and never "
        "records a refusal. Costs no quota (a local slash command) but does need "
        "every container to be up.",
    )

    release_parser = sub.add_parser(
        "release-account",
        help="Hand one account back to the pool after a cycle died without "
        "returning it. Nothing else can: list_idle_accounts only ever answers "
        "IDLE, and the lock reaper runs inside run_task_cycle, which is the "
        "thing that can no longer start. Refuses while the lock is live or a "
        "refusal is still inside its cooldown.",
    )
    release_parser.add_argument("--name", required=True, help="Account name from config.yaml's accounts list")
    release_parser.add_argument(
        "--force",
        action="store_true",
        help="Release over every guard, including a live heartbeat. Only when you "
        "know the process that took the lock is gone: two dispatchers on one "
        "account means two writers in one container.",
    )
    release_parser.add_argument(
        "--task",
        dest="task_id",
        default=None,
        help="Free only this task's hold on the account, for an account that "
        "runs several phases at once. The others keep running and a park stays. "
        "Without it every hold is freed.",
    )
    release_parser.add_argument(
        "--clear-rate-limit",
        action="store_true",
        help="Also forget the recorded refusal. Separate from --force because it "
        "destroys the only evidence the harness has that the service turned this "
        "account away; /usage cannot see that.",
    )

    keygen_parser = sub.add_parser(
        "project-keygen",
        help="Make an SSH deploy key for one project and print its public half "
        "and fingerprint, never the private one. Add the public half under the "
        "repository's Settings -> Deploy keys; from then on the project fetches "
        "over SSH with it instead of the https:// credential.",
    )
    keygen_parser.add_argument("--project", required=True, help="Project slug")
    keygen_parser.add_argument(
        "--replace",
        action="store_true",
        help="Make a new pair over an existing one. The old public key stops "
        "working at once and stays on GitHub until you delete it there.",
    )
    keygen_parser.add_argument(
        "--keys-dir",
        help="Where the keys live on this machine; defaults to project_keys_dir. "
        "From the host rather than the dispatcher's container, that is "
        ".data/credentials/projects.",
    )

    queue_parser = sub.add_parser(
        "run-queue",
        help="Run a confirmed list of tasks, one cycle each, in dependency order, "
        "until they are done or the token limit is reached",
    )
    queue_parser.add_argument("--project", help="Project slug")
    queue_parser.add_argument(
        "--task-id", nargs="+", action="extend", default=[], dest="task_ids",
        help="The tasks to run, each with a stored description; repeatable",
    )
    queue_parser.add_argument(
        "--ready", type=int, default=0, metavar="N",
        help="Add the first N tasks of the board's Ready order after the listed ones; "
        "the listed order is kept (ADR 132)",
    )
    queue_parser.add_argument(
        "--limit", type=int, metavar="TOKENS",
        help="The ceiling in measured tokens (input + output + cache, from the "
        "usage log, ADR 79), checked before each phase; not a number of tasks. "
        f"At least {MIN_QUEUE_LIMIT} (ADR 333)",
    )
    queue_parser.add_argument(
        "--parallel", type=int, default=None,
        help="Cycles at a time (1 for a new queue; a --resume may change the queue's, "
        "ADR 336), at most the pool's phase slots (sum of "
        "max_concurrent_phases, ADR 252); overlapping surfaces "
        "never write at once (docs/plans/autonomy.md, B-3, ADR 83)",
    )
    queue_parser.add_argument(
        "--yes", action="store_true",
        help="Start without asking; the confirmation screen is still printed",
    )
    queue_parser.add_argument(
        "--resume", metavar="QUEUE_ID",
        help="Go on with a queue a kill or a stop left, under its own project and "
        "tasks; --limit and --parallel may change (ADR 336)",
    )

    draft_parser = sub.add_parser(
        "draft-specs",
        help="Have the arquitecto draft a plan's tasks as specs for the owner to accept; "
        "nothing it drafts runs until `accept-drafts` files it (ADR 82)",
    )
    draft_parser.add_argument("--project", required=True, help="Project slug")
    draft_parser.add_argument(
        "--plan", required=True,
        help="The plan to draft from, as a path inside the project, e.g. docs/plans/x.md",
    )

    accept_parser = sub.add_parser(
        "accept-drafts",
        help="File drafts as pending tasks with fresh ids, their depends_on rewritten "
        "to those ids (ADR 82)",
    )
    accept_parser.add_argument("draft_id", metavar="DRAFT_ID", help="The set `draft-specs` printed")
    accept_parser.add_argument(
        "--only", nargs="+", action="extend", metavar="NAME",
        help="Accept only these drafts, by name (D1, D2...); repeatable",
    )

    sub.add_parser(
        "worker",
        help="Drain the action queue under <state_dir>/actions until signalled; "
        "one at a time (ADR 142)",
    )

    sub.add_parser(
        "backup",
        help="Copy the hive and the state into a timestamped directory under "
        "BACKUP_DIR, keeping BACKUP_KEEP copies (ADR 327). Refuses (75) while any "
        "project's cycle runs; never copies a credential.",
    )
    restore_parser = sub.add_parser(
        "restore",
        help="Put a copy back over the hive and the state, in place, setting aside "
        "what it replaces (ADR 328, 351). Refuses (75) while the worker or a cycle runs.",
    )
    restore_parser.add_argument(
        "--from", dest="copy", required=True, metavar="NAME|latest",
        help="A copy under BACKUP_DIR, or the newest",
    )

    migrate_parser = sub.add_parser(
        "migrate",
        help="Move the description out of every task file that still carries it "
        "in the frontmatter, into the task's description.md on the board; then "
        "write a project record for every clone under projects_root that has "
        "none, and stamp the cards that name no project (ADR 199). Run "
        "it once after upgrading; a second run changes nothing, and a record "
        "already there is never written over. It prints what "
        "it did, and refuses a task whose cycle is still in flight: run it "
        "again when that cycle has ended.",
    )
    migrate_parser.add_argument(
        "--project", metavar="SLUG", default=None,
        help="The project the cards that name none belong to. Needed only when "
        "there is more than one project; with one, it is that one.",
    )

    import_debt_parser = sub.add_parser(
        "import-debt",
        help="File every open entry of a debt index as a proposed backlog item, "
        "source debt, tag debt, its priority from the entry's impact (ADR 131). "
        "Run it once; a second run, like the mirroring after each cycle, files "
        "nothing twice. --dry-run prints what it would file and writes nothing.",
    )
    import_debt_source = import_debt_parser.add_mutually_exclusive_group(required=True)
    import_debt_source.add_argument(
        "--project", metavar="SLUG",
        help=f"Read {project_docs.DEBT_INDEX} of this project's checkout, in a container",
    )
    import_debt_source.add_argument(
        "--index", metavar="PATH", help="Read the index from this file instead",
    )
    import_debt_parser.add_argument(
        "--dry-run", action="store_true", help="Print what would be filed; write nothing",
    )
    return parser


def main() -> None:
    # Before the parser, so that even an unparseable invocation logs the way
    # a real run does.
    bad_level = _configure_logging()
    if bad_level:
        logging.getLogger(__name__).warning(
            "%s=%r is not a log level; using INFO", _LOG_LEVEL_ENV, bad_level
        )
    parser = build_parser()
    args = parser.parse_args()
    cfg = load_config(args.config)

    if args.command in ("backup", "restore"):
        _run_backup(args, cfg)
    elif args.command == "migrate":
        _run_migrate(cfg, args.project)
    elif args.command == "import-debt":
        _run_import_debt(args, cfg, parser)
    elif args.command == "run-task":
        _require_migrated(cfg, parser)
        description = _resolve_description(args, cfg, parser)
        try:
            outcome = run_task_cycle(
                cfg, args.task_id, args.project, description=description,
                before_merge=backup_mod.merge_hook(cfg, shared=(args.project,)),
            )
        except ProjectBusy as exc:
            _exit_project_busy(exc, args.task_id)
        for line in left_behind_report(cfg, args.task_id):
            print(line, file=sys.stderr)
        # Before ADR 52 every one of these exited 0, so a cycle that never got
        # an account looked, to whatever ran it, like one that shipped.
        if outcome is CycleOutcome.HELD:
            print(
                f"task {args.task_id} is held: no account could take its next phase; "
                "run it again once one resets",
                file=sys.stderr,
            )
            raise SystemExit(EXIT_HELD)
        if outcome is CycleOutcome.BLOCKED:
            print(f"task {args.task_id} is blocked; the log above says why", file=sys.stderr)
            raise SystemExit(EXIT_BLOCKED)
        if outcome is CycleOutcome.SPLIT:
            # stdout, unlike the two above: the ids are the result a caller
            # reads to know what to run next.
            print(_epic_line(cfg, args.task_id))
            raise SystemExit(EXIT_SPLIT)
    elif args.command == "worker":
        _require_migrated(cfg, parser)
        raise SystemExit(worker_mod.run_worker(cfg))
    elif args.command == "run-queue":
        _require_migrated(cfg, parser)
        _run_queue(args, cfg, parser)
    elif args.command == "file-split":
        _require_migrated(cfg, parser)
        outcome = file_saved_split(cfg, args.task_id, args.project)
        if outcome is not CycleOutcome.SPLIT:
            print(
                f"task {args.task_id}: the split was not filed; the log above says why",
                file=sys.stderr,
            )
            raise SystemExit(EXIT_BLOCKED)
        # Exit 0, not EXIT_SPLIT: here the filing is what was asked for.
        print(_epic_line(cfg, args.task_id))
    elif args.command == "run-phase":
        _require_migrated(cfg, parser)
        if args.round_num is not None and args.round_num < 1:
            parser.error("--round counts from 1")
        # No --description: this verb only ever runs on a task some earlier
        # run already described, and rewriting the ask underneath a phase
        # that is resuming somebody else's work is not a thing to make easy.
        if not (tasks.read_description(cfg.hive_tasks_dir, args.task_id) or "").strip():
            parser.error(
                f"task {args.task_id} has no description: run-phase resumes a task that "
                "is already under way. Start it with run-task."
            )
        try:
            result = run_single_phase(
                cfg,
                args.task_id,
                args.project,
                args.role,
                round_num=args.round_num,
                final=args.final,
                note=args.note,
            )
        except ProjectBusy as exc:
            _exit_project_busy(exc, args.task_id)
        for line in left_behind_report(cfg, args.task_id, role=args.role):
            print(line, file=sys.stderr)
        if result is None:
            # Same contract as merge-task and release-account: a phase that did
            # not land is an exit code, so the operator driving a cycle by hand
            # does not run the next one over the top of it.
            print(f"phase {args.role} did not finish; task {args.task_id} is blocked", file=sys.stderr)
            raise SystemExit(EXIT_BLOCKED)
        if result.no_account:
            # The same split as run-task's: the phase never ran, and the same
            # command will run it once an account resets.
            print(
                f"phase {args.role} is held: no account could take it; "
                f"run it again once one resets",
                file=sys.stderr,
            )
            raise SystemExit(EXIT_HELD)
        print(f"phase {args.role} finished; {context_transfer.task_file_path(cfg.hive_tasks_dir, args.task_id)} has its handoff")
        if args.role == "arquitecto":
            _print_proposed_split(args, result.handoff)
    elif args.command == "command-verdict":
        _require_migrated(cfg, parser)
        if args.n < 1:
            parser.error("<n> counts from 1")
        if args.prefix is not None and args.verdict != command_run.ALLOW:
            parser.error(f"--prefix is for {command_run.ALLOW} only")
        if args.prefix is not None and args.prefix < 1:
            parser.error("--prefix counts tokens from 1")
        try:
            outcome = command_run.run_command_request(
                cfg, args.task_id, args.project, args.n, args.verdict, comment=args.comment,
                prefix=args.prefix,
            )
        except command_run.VerdictRefused as exc:
            print(f"task {args.task_id}: verdict refused: {exc}", file=sys.stderr)
            raise SystemExit(EXIT_BLOCKED) from None
        except ProjectBusy as exc:
            _exit_project_busy(exc, args.task_id)
        _exit_on_resumed_outcome(cfg, args, outcome)
        print(f"task {args.task_id}: request {args.n} {args.verdict}; the cycle finished")
    elif args.command == "command-resume":
        _require_migrated(cfg, parser)
        try:
            outcome = command_run.resume_command_verdict(cfg, args.task_id, args.project)
        except command_run.VerdictRefused as exc:
            print(f"task {args.task_id}: resume refused: {exc}", file=sys.stderr)
            raise SystemExit(EXIT_BLOCKED) from None
        except ProjectBusy as exc:
            _exit_project_busy(exc, args.task_id)
        _exit_on_resumed_outcome(cfg, args, outcome)
        print(f"task {args.task_id}: the verdict was resumed; the cycle finished")
    elif args.command == "allowlist":
        _run_allowlist(args, cfg, parser)
    elif args.command == "bootstrap-project":
        # Only creates the directory create_worktree() needs as its cwd; the
        # config schema has no repo-source field, so cloning the actual
        # project repo into it is still a manual, one-time operator step.
        container = container_for(cfg, args.account)
        project_dir = f"{cfg.projects_root}/{args.project}"
        docker_exec.run_docker_exec(container, "/", ["mkdir", "-p", project_dir])
    elif args.command == "cleanup-task":
        container = cleanup_container(cfg)
        if container is None:
            parser.error("no accounts configured: nothing to run the cleanup in")
        project_dir = f"{cfg.projects_root}/{args.project}"
        owner = docker_exec.read_owner(container, project_dir)
        try:
            removed = docker_exec.remove_task_worktrees(
                container, cfg.projects_root, args.project, args.task_id
            )
        finally:
            docker_exec.restore_owner(container, project_dir, owner)
        branch = docker_exec.task_branch(args.task_id)
        print(f"removed {len(removed)} worktree(s): {', '.join(removed) or '(none)'}")
        print(f"branch {branch} kept; `git worktree add <path> {branch}` checks it out again")
    elif args.command == "close-task":
        _run_close_task(cfg, args)
    elif args.command == "resolve-conflict":
        # The resolution is a base operation, under the lock merge-task takes
        # (ADR 80, ADR 277); the worker runs the same body (ADR 291).
        outcome, detail = resolve_conflict_locked(cfg, args.task_id, args.project)
        if outcome == "not_run":
            detail = f"task {args.task_id}: not resolved: {detail}"
        if outcome != "resolved":
            print(detail, file=sys.stderr)
            raise SystemExit(1)
        print(detail)
    elif args.command == "merge-task":
        container = cleanup_container(cfg)
        if container is None:
            parser.error("no accounts configured: nothing to run the merge in")
        # The merge, its pre-merge check and its push are one base operation,
        # the same lock the dispatcher takes (ADR 80): a refusal is exit 1.
        try:
            base = _base_lock(cfg, args.project, "merge-task").acquire()
        except tasks.BaseLockHeldError as exc:
            print(f"task {args.task_id}: not merged: {exc}", file=sys.stderr)
            raise SystemExit(1)
        with base:
            project_dir = f"{cfg.projects_root}/{args.project}"
            owner = docker_exec.read_owner(container, project_dir)
            left = left_behind_report(cfg, args.task_id)
            for line in left:
                print(line, file=sys.stderr)
            if left:
                print(
                    f"task {args.task_id}: merging anyway; those edits are not on the branch "
                    "and stay out of it",
                    file=sys.stderr,
                )
            try:
                # The same check the automatic merge runs: the base merged into
                # the branch and gated first (`docs/plans/autonomy.md` A-2). A
                # stop is exit 1, like a refused merge: the branch did not land.
                verdict = pre_merge_check(cfg, container, args.task_id, args.project)
                if not verdict.passed:
                    print(verdict.detail, file=sys.stderr)
                    raise SystemExit(1)
                print(verdict.detail)
                # One copy before the merge, best effort (ADR 345): no cycle of
                # this process holds a lock, so a running one refuses it.
                line = backup_mod.merge_hook(cfg)()
                print(line, file=sys.stdout if line.startswith("backup before") else sys.stderr)
                outcome = docker_exec.merge_task_branch(
                    container, cfg.projects_root, args.project, args.task_id
                )
                # Only when the branch is in the base: a merge that landed now, or
                # one already there, so a re-run after a refused push pushes
                # again without merging twice (ADR 78).
                push = (
                    push_base(cfg, container, args.project)
                    if args.push and not outcome.refused
                    else None
                )
            finally:
                # git writes to .git/ as root here too — same restore as everywhere.
                docker_exec.restore_owner(container, project_dir, owner)
        if outcome.refused:
            # Exit non-zero so a script that chains this can tell the refusal
            # from the merge, which the wording alone would not give it.
            print(outcome.detail, file=sys.stderr)
            raise SystemExit(1)
        print(outcome.detail)
        if push is not None:
            print(push.detail, file=sys.stderr if push.refused else sys.stdout)
        # What a dependency waits for, and what closes an epic on its last
        # sub-task (`docs/plans/task-split.md` section 3). Still exit 0 when
        # the record could not be written: exit 1 here means the branch did
        # not land, and it did.
        recorded, problem, warnings = record_merge(cfg, args.task_id, outcome)
        for line in recorded:
            print(line)
        if problem:
            print(problem, file=sys.stderr)
        for line in warnings:
            print(line, file=sys.stderr)
        # The base moved: every other task of the project still in review is
        # merged with it again and gated (ADR 274), when the check is on.
        for line in recheck_awaiting_review(cfg, args.project, args.task_id):
            print(line)
        # The same thing the automatic merge does, for the same reason: what
        # the task learned is in the project's docs now, on the branch that
        # just landed, so the inbox copy has stopped earning its place in
        # every later prompt. This is the call site where that premise can be
        # wrong — a branch landed by hand is one the cycle did not land, and
        # its auditor may never have run — so `drop_promoted` checks it against
        # the task card, and each of the two answers gets its own line: the
        # destination when the check passes, because the entries are in
        # `dropped/` and not gone, and what stayed and why when it does not
        # (`docs/debt/T-012-D1.md`, `docs/decisions.md` ADR 33 and ADR 34).
        dropped = learnings.drop_promoted(cfg.hive_tasks_dir, args.task_id)
        if dropped:
            print(
                f"dropped {len(dropped)} filed inbox entr(y/ies) into "
                f"{learnings.DROPPED_NAME}/: {', '.join(dropped)}"
            )
        else:
            # Empty both when the gate kept them and when there was nothing to
            # drop, so the ordinary merge still says nothing about learnings.
            kept = learnings.droppable(cfg.hive_tasks_dir, args.task_id)
            if kept:
                # Exit 0: landing a branch whose cycle never reached an auditor
                # is the recovery path this verb exists for, not an operator
                # error. Nothing was refused and nothing was lost.
                print(
                    f"kept {len(kept)} filed inbox entr(y/ies) in "
                    f"{learnings.INBOX_NAME}/: no `## {learnings.PROMOTING_ROLE}` section in "
                    f"{context_transfer.task_file_path(cfg.hive_tasks_dir, args.task_id)}, "
                    f"so that phase never filed them: "
                    f"{', '.join(entry.ref for entry in kept)}"
                )
        if push is not None and push.refused:
            # Last, after the merge is recorded and the learnings settled: the
            # branch landed and all of that is true of it whatever the push did.
            raise SystemExit(EXIT_NOT_PUSHED)
    elif args.command == "status":
        print(
            operator.format_status(
                cfg,
                operator.account_reports(cfg, probe=args.probe),
                operator.task_reports(cfg),
            )
        )
    elif args.command == "release-account":
        # The only hive-wide check this verb has: `release_account` writes just
        # the task files the account holds (`tasks.release_stale_lock`), and
        # the writer's refusal would stop it on those alone.
        _require_migrated(cfg, parser)
        try:
            outcome = operator.release_account(
                cfg, args.name, force=args.force, clear_rate_limit=args.clear_rate_limit,
                task_id=args.task_id,
            )
        except ValueError as exc:
            parser.error(str(exc))
        if outcome.refused:
            # Same contract as merge-task: a refusal is an exit code, not just
            # wording, so a script that chains this can tell the two apart.
            print(outcome.detail, file=sys.stderr)
            raise SystemExit(1)
        print(outcome.detail)
    elif args.command == "learnings":
        _run_learnings(args, cfg)
    elif args.command == "project-keygen":
        _run_project_keygen(args, cfg, parser)
    elif args.command == "draft-specs":
        _require_migrated(cfg, parser)
        _run_draft_specs(args, cfg, parser)
    elif args.command == "accept-drafts":
        _require_migrated(cfg, parser)
        _run_accept_drafts(args, cfg)


def _run_backup(args: argparse.Namespace, cfg: Config) -> None:
    """`backup` and `restore`: exit 2 on a refusal, 75 while something dispatches."""
    now = datetime.now(timezone.utc)
    hive = context_transfer.hive_root(cfg.hive_tasks_dir)
    try:
        backup_dir, keep = backup_mod.settings_from_env(os.environ)
        if args.command == "backup":
            result = backup_mod.backup(hive, cfg.state_dir, backup_dir, keep, now=now)
        else:
            result = backup_mod.restore(hive, cfg.state_dir, backup_dir, args.copy, now=now)
    except backup_mod.BackupError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
    except (ProjectBusy, worker_mod.WorkerLocked) as exc:
        print(f"{exc}; nothing was {args.command}d", file=sys.stderr)
        raise SystemExit(EXIT_HELD)
    if args.command == "backup":
        print(
            f"backup {result.name}: {result.files} files, {result.skipped_symlinks} symlinks "
            f"skipped, pruned {', '.join(result.pruned) or 'none'}"
        )
        return
    print(
        f"restored {result.name}: {result.files} files; set aside in "
        f"{', '.join(result.asides) or 'nowhere (nothing was replaced)'}"
    )
    for line in backup_mod.RESTORE_LINES:
        print(line)


def _draft_line(draft: tasks.Draft) -> str:
    goal = tasks.parse_description(draft.description).goal.splitlines()[0]
    deps = f" (depends on {', '.join(draft.depends_on)})" if draft.depends_on else ""
    return f"{draft.name}: {goal}{deps}"


def _run_draft_specs(args: argparse.Namespace, cfg: Config, parser: argparse.ArgumentParser) -> None:
    """`draft-specs`: one arquitecto phase, then the section rules on what it wrote."""
    try:
        draft_id, result = run_draft_specs(cfg, args.project, args.plan)
    except ValueError as exc:
        parser.error(str(exc))
    if result is None:
        print(f"drafts {draft_id}: locked by another owner; the log above says who", file=sys.stderr)
        raise SystemExit(EXIT_BLOCKED)
    if result.no_account:
        print(
            f"drafts {draft_id} are held: no account could take the arquitecto; "
            "run it again once one resets",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_HELD)
    if not result.success:
        print(f"drafts {draft_id}: the arquitecto did not finish; the log above says why", file=sys.stderr)
        raise SystemExit(EXIT_BLOCKED)
    try:
        drafts = tasks.check_drafts(cfg.hive_tasks_dir, draft_id)
    except tasks.DraftError as exc:
        # The set stays on disk: the owner can fix a file by hand and accept
        # the rest with --only, rather than paying for the run again.
        for problem in exc.problems:
            print(problem, file=sys.stderr)
        print(
            f"drafts {draft_id} in {tasks.draft_set_dir(cfg.hive_tasks_dir, draft_id)} do not "
            "hold to the section rules; fix them, or accept the ones that do with --only",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_BLOCKED)
    for draft in drafts:
        print(_draft_line(draft))
    print(
        f"drafts {draft_id}: {len(drafts)} in {tasks.draft_set_dir(cfg.hive_tasks_dir, draft_id)}; "
        f"read them, then `accept-drafts {draft_id}` (or `--only NAME ...`)"
    )


def _run_accept_drafts(args: argparse.Namespace, cfg: Config) -> None:
    """`accept-drafts`: file the accepted drafts and print which task id each became."""
    try:
        ids = tasks.accept_drafts(cfg.hive_tasks_dir, args.draft_id, only=args.only)
    except tasks.DraftError as exc:
        for problem in exc.problems:
            print(problem, file=sys.stderr)
        print(f"drafts {args.draft_id}: nothing was accepted", file=sys.stderr)
        raise SystemExit(EXIT_BLOCKED)
    except ValueError as exc:
        # A malformed id, or an unmigrated task file in the way.
        print(str(exc), file=sys.stderr)
        raise SystemExit(EXIT_BLOCKED)
    for name, task_id in ids.items():
        print(f"{name} -> {task_id}")


def _run_queue(args: argparse.Namespace, cfg: Config, parser: argparse.ArgumentParser) -> None:
    """`run-queue`: confirm a queue and run it, or go on with one (ADR 79).

    The yes is the owner's authorization for every cycle of the queue, and the
    only one: each cycle is passed `authorized=True` because of it, and a
    resume carries the same, since it adds nothing that was not confirmed.
    """
    def check_floor() -> None:
        if args.limit < MIN_QUEUE_LIMIT:
            parser.error(
                f"--limit {args.limit}: the limit is in measured tokens "
                "(input + output + cache, ADR 79), not tasks; "
                f"at least {MIN_QUEUE_LIMIT}, since one phase alone measures more"
            )

    def confirm() -> None:
        if not args.yes:
            try:
                answer = input("Start this queue? [y/N] ")
            except EOFError:
                answer = ""
            if answer.strip().lower() not in ("y", "yes"):
                print("not started", file=sys.stderr)
                raise SystemExit(EXIT_BLOCKED)

    if args.parallel is not None:
        if args.parallel < 1:
            parser.error("--parallel: at least 1")
        slots = sum(a.max_concurrent_phases for a in cfg.accounts)
        if args.parallel > slots:
            # One cycle per phase slot (ADR 88, ADR 252): a slot no account can
            # serve only holds its cycle at the next phase.
            parser.error(
                f"--parallel: at most {slots}, the pool's phase slots "
                "(the sum of max_concurrent_phases, ADR 252)"
            )
    if args.resume:
        if args.task_ids or args.ready or args.project:
            parser.error(
                "--resume takes only --limit, --parallel and --yes: "
                "the project and tasks are the queue's own (ADR 336)"
            )
        if args.limit is not None:
            check_floor()
        try:
            known = len(queue_mod.load(cfg, args.resume).changes)
            queue = queue_mod.reopen(cfg, args.resume, limit=args.limit, parallel=args.parallel)
        except queue_mod.QueueError as exc:
            parser.error(str(exc))
        print(queue_mod.confirmation(cfg, queue))
        if len(queue.changes) > known:
            confirm()  # a new ceiling or parallelism is a new yes (C-4, ADR 336)
        queue_mod.save(cfg, queue)
    else:
        if args.ready < 0:
            parser.error("--ready: at least 0")
        if not args.project or not (args.task_ids or args.ready) or args.limit is None:
            parser.error(
                "run-queue needs --project, --task-id or --ready, and --limit TOKENS, "
                "or --resume QUEUE_ID"
            )
        check_floor()
        try:
            queue = queue_mod.plan(
                cfg, args.project, args.task_ids, args.limit, ready=args.ready
            )
        except queue_mod.QueueError as exc:
            parser.error(str(exc))
        queue.parallel = 1 if args.parallel is None else args.parallel
        print(queue_mod.confirmation(cfg, queue))
        confirm()
        queue.authorized = True
        path = queue_mod.save(cfg, queue)
        print(f"queue {queue.queue_id} saved in {path}; `--resume {queue.queue_id}` goes on from it")
    queue = queue_mod.run(cfg, queue)
    print(queue_mod.summary(queue))
    if queue.status == queue_mod.Q_PUSH_REFUSED:
        raise SystemExit(EXIT_NOT_PUSHED)
    if queue.status == queue_mod.Q_LIMIT:
        raise SystemExit(EXIT_LIMIT)
    if queue.status == queue_mod.Q_WAITING or any(
        e.state in queue_mod.FAILED for e in queue.entries
    ):
        raise SystemExit(EXIT_BLOCKED)


def _run_project_keygen(args: argparse.Namespace, cfg: Config, parser: argparse.ArgumentParser) -> None:
    keys_dir = args.keys_dir or cfg.project_keys_dir
    try:
        key = project_keys.generate(keys_dir, args.project, replace=args.replace)
    except ValueError as exc:
        parser.error(str(exc))
    except project_keys.KeyExists as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(EXIT_BLOCKED)
    except (OSError, subprocess.CalledProcessError) as exc:
        # ssh-keygen's stderr says nothing of the key it did not make, so it is
        # safe to show; a missing binary is an OSError with no stderr at all.
        why = getattr(exc, "stderr", None) or exc
        print(f"could not make a key for {args.project}: {why}", file=sys.stderr)
        raise SystemExit(EXIT_BLOCKED)
    print(key.public)
    print(f"fingerprint: {key.fingerprint}")
    print(
        f"Add the line above under the repository's Settings -> Deploy keys: "
        f"read-only for fetching, with write access only if this project pushes "
        f"its task branches. The private half stays at {key.path}."
    )
    if args.replace:
        print("The key it replaced stays on GitHub until you delete it there.")


if __name__ == "__main__":
    main()
