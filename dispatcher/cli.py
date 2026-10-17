from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

from dispatcher import (
    context_transfer,
    docker_exec,
    handoff,
    learnings,
    operator,
    project_docs,
    project_keys,
    role_skills,
    tasks,
)
from dispatcher import queue as queue_mod
from dispatcher.config import Config, load_config
from dispatcher.dispatcher import (
    CycleOutcome,
    _base_lock,
    cleanup_container,
    container_for,
    file_saved_split,
    left_behind_report,
    pre_merge_check,
    push_base,
    record_merge,
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


def _run_migrate(cfg: Config) -> None:
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
    if report.refused:
        raise SystemExit(EXIT_BLOCKED)


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


def main() -> None:
    # Before the parser, so that even an unparseable invocation logs the way
    # a real run does.
    bad_level = _configure_logging()
    if bad_level:
        logging.getLogger(__name__).warning(
            "%s=%r is not a log level; using INFO", _LOG_LEVEL_ENV, bad_level
        )
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
        "whether the escalated effort applies — so a resumed round 2 has to "
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
        "--limit", type=int,
        help="The ceiling in measured tokens from the usage log (ADR 79)",
    )
    queue_parser.add_argument(
        "--parallel", type=int, default=1,
        help="Cycles at a time, at most one per account; overlapping surfaces "
        "never write at once (docs/plans/autonomy.md, B-3, ADR 83)",
    )
    queue_parser.add_argument(
        "--yes", action="store_true",
        help="Start without asking; the confirmation screen is still printed",
    )
    queue_parser.add_argument(
        "--resume", metavar="QUEUE_ID",
        help="Go on with a queue a kill or a stop left, under its own tasks and limit",
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
        "migrate",
        help="Move the description out of every task file that still carries it "
        "in the frontmatter, into the task's description.md on the board. Run "
        "it once after upgrading; a second run changes nothing. It prints what "
        "it moved, and refuses a task whose cycle is still in flight: run it "
        "again when that cycle has ended.",
    )

    args = parser.parse_args()
    cfg = load_config(args.config)

    if args.command == "migrate":
        _run_migrate(cfg)
    elif args.command == "run-task":
        _require_migrated(cfg, parser)
        description = _resolve_description(args, cfg, parser)
        outcome = run_task_cycle(cfg, args.task_id, args.project, description=description)
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
        result = run_single_phase(
            cfg,
            args.task_id,
            args.project,
            args.role,
            round_num=args.round_num,
            final=args.final,
            note=args.note,
        )
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
        recorded, problem = record_merge(cfg, args.task_id, outcome)
        for line in recorded:
            print(line)
        if problem:
            print(problem, file=sys.stderr)
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
                cfg, args.name, force=args.force, clear_rate_limit=args.clear_rate_limit
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
    if args.parallel < 1:
        parser.error("--parallel: at least 1")
    if args.parallel > len(cfg.accounts):
        # One phase per account (out of B-3's scope to change): a slot no
        # account can serve only holds its cycle at the next phase.
        parser.error(
            f"--parallel: at most {len(cfg.accounts)}, one cycle per configured account"
        )
    if args.resume:
        if args.task_ids or args.limit is not None or args.project or args.parallel != 1:
            parser.error("--resume takes the queue's own project, tasks, limit and parallelism")
        try:
            queue = queue_mod.resume(cfg, args.resume)
        except queue_mod.QueueError as exc:
            parser.error(str(exc))
    else:
        if not args.project or not args.task_ids or args.limit is None:
            parser.error("run-queue needs --project, --task-id and --limit, or --resume")
        try:
            queue = queue_mod.plan(cfg, args.project, args.task_ids, args.limit)
        except queue_mod.QueueError as exc:
            parser.error(str(exc))
        queue.parallel = args.parallel
        print(queue_mod.confirmation(cfg, queue))
        if not args.yes:
            try:
                answer = input("Start this queue? [y/N] ")
            except EOFError:
                answer = ""
            if answer.strip().lower() not in ("y", "yes"):
                print("not started", file=sys.stderr)
                raise SystemExit(EXIT_BLOCKED)
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
