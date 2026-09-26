from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from pathlib import Path

from dispatcher import context_transfer, docker_exec, learnings, operator, project_docs, role_skills
from dispatcher.config import Config, load_config
from dispatcher.dispatcher import (
    cleanup_container,
    close_resolved_debt,
    container_for,
    run_single_phase,
    run_task_cycle,
)
from dispatcher.vibe_kanban_client import NullKanbanClient, VibeKanbanClient


#: The roles `run-phase` will dispatch. Held here rather than derived from
#: ROLE_SKILLS because the cartografo has no skills and is still a phase, and
#: because a typo in --role should be a usage error and not a full-price run
#: of a role nothing knows how to prompt.
_PHASE_ROLES = frozenset(role_skills.ROLE_SKILLS) | {project_docs.MAPPER_ROLE}


def _resolve_description(
    args: argparse.Namespace, cfg: Config, parser: argparse.ArgumentParser
) -> str | None:
    """The description to seed this run with, or None to keep the stored one.

    Refuses to dispatch a task nobody described: without an ask, the four
    phases can only produce noise at full quota cost. argparse's own
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
        stored = context_transfer.read_description(cfg.hive_tasks_dir, args.task_id)
        if (stored or "").strip():
            return None  # a resume: the ask is already on disk, don't rewrite it
        parser.error(
            f"task {args.task_id} has no description: pass --description or "
            "--description-file (use '-' to read stdin). Without one the "
            "roles are dispatched with nothing to work from."
        )

    if not text.strip():
        parser.error("the task description is empty")
    return text


def _seed_kanban_issue_id(
    args: argparse.Namespace, cfg: Config, parser: argparse.ArgumentParser
) -> None:
    """Record which board issue this task mirrors, when the operator named one.

    Stored in the task file rather than kept for this run only: the id is
    what every later phase — and every later `run-task` on the same task —
    needs to move the issue along.
    """
    if args.kanban_issue_id is None:
        return
    if cfg.vibe_kanban is None:
        parser.error(
            "--kanban-issue-id, but config.yaml has no vibe_kanban block: nothing "
            "would ever read the id. Configure the board, or drop the flag."
        )
    try:
        uuid.UUID(args.kanban_issue_id)
    except ValueError:
        # The board shows a short id (VK-7); every MCP tool wants the uuid.
        # Catching the mix-up here beats a rejected update_issue four phases in.
        parser.error(
            f"--kanban-issue-id: {args.kanban_issue_id!r} is not a uuid. Vibe Kanban's "
            "issue_id is the issue's uuid, not the short id shown on the card."
        )
    context_transfer.set_kanban_issue_id(cfg.hive_tasks_dir, args.task_id, args.kanban_issue_id)


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


def _kanban(cfg: Config) -> NullKanbanClient | VibeKanbanClient:
    """The board this config talks to, or the one that does nothing.

    Every subcommand that can move a card builds it the same way, so that a
    project with no `vibe_kanban` block runs the identical code path and says
    nothing about a board it was never given.
    """
    return VibeKanbanClient(cfg.vibe_kanban) if cfg.vibe_kanban else NullKanbanClient()


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
        help="What the task actually asks for. Stored in the task file's frontmatter "
        "and embedded in every role's prompt. Optional only when the task file "
        "already carries a description (i.e. re-running a task).",
    )
    description_group.add_argument(
        "--description-file",
        help="Read the description from this file instead of the command line; "
        "'-' reads stdin. Use it for anything longer than a sentence.",
    )
    run_parser.add_argument(
        "--kanban-issue-id",
        help="The uuid of the Vibe Kanban issue this task mirrors, so the board "
        "follows the run. Stored in the task file, so it is only needed once "
        "per task. Without it (or without a vibe_kanban block in config.yaml) "
        "the run simply has no board.",
    )

    phase_parser = sub.add_parser(
        "run-phase",
        help="Run one role's phase on a task that is already under way, instead of "
        "the whole cycle. The repair path: run-task always begins at the "
        "arquitecto, so an interrupted run could otherwise only be continued by "
        "paying for every phase again. It runs the phase and nothing else — no "
        "further round, no verdict read, no debt card filed, no merge.",
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
        "beforehand, the task file and the board are moved to done, and the "
        "entries are not orphaned on the way out. In the role set that is the "
        "auditor. Without it the handoff leaves the task pending, which is "
        "what every phase before the last one should do.",
    )
    phase_parser.add_argument(
        "--note",
        default="",
        help="Extra context appended to this one phase's prompt. The cycle uses "
        "it to hand the auditor the debt cards it just filed; an operator "
        "resuming a run uses it to say what the dead process took with it.",
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

    merge_parser = sub.add_parser(
        "merge-task",
        help="Merge agent/task/<task-id> into the branch the project is checked out on. "
        "A --no-ff merge that refuses on a detached HEAD or a dirty tree and rolls "
        "itself back on a conflict; the task branch is never touched. Set "
        "merge_on_done in config.yaml to have a finished cycle do this by itself.",
    )
    merge_parser.add_argument("--task-id", required=True)
    merge_parser.add_argument("--project", required=True, help="Project slug")

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

    args = parser.parse_args()
    cfg = load_config(args.config)

    if args.command == "run-task":
        description = _resolve_description(args, cfg, parser)
        _seed_kanban_issue_id(args, cfg, parser)
        # No vibe_kanban block, no board: the cycle runs exactly as before and
        # says nothing about a board it was never given.
        kanban = _kanban(cfg)
        run_task_cycle(cfg, args.task_id, args.project, kanban, description=description)
    elif args.command == "run-phase":
        if args.round_num is not None and args.round_num < 1:
            parser.error("--round counts from 1")
        # No --description: this verb only ever runs on a task some earlier
        # run already described, and rewriting the ask underneath a phase
        # that is resuming somebody else's work is not a thing to make easy.
        if not (context_transfer.read_description(cfg.hive_tasks_dir, args.task_id) or "").strip():
            parser.error(
                f"task {args.task_id} has no description: run-phase resumes a task that "
                "is already under way. Start it with run-task."
            )
        result = run_single_phase(
            cfg,
            args.task_id,
            args.project,
            _kanban(cfg),
            args.role,
            round_num=args.round_num,
            final=args.final,
            note=args.note,
        )
        if result is None:
            # Same contract as merge-task and release-account: a phase that did
            # not land is an exit code, so the operator driving a cycle by hand
            # does not run the next one over the top of it.
            print(f"phase {args.role} did not finish; task {args.task_id} is blocked", file=sys.stderr)
            raise SystemExit(1)
        print(f"phase {args.role} finished; {context_transfer.task_file_path(cfg.hive_tasks_dir, args.task_id)} has its handoff")
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
        project_dir = f"{cfg.projects_root}/{args.project}"
        owner = docker_exec.read_owner(container, project_dir)
        try:
            outcome = docker_exec.merge_task_branch(
                container, cfg.projects_root, args.project, args.task_id
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
        # The same thing the automatic merge does, for the same reason: what
        # the task learned is in the project's docs now, on the branch that
        # just landed, so the inbox copy has stopped earning its place in
        # every later prompt.
        dropped = learnings.drop_promoted(cfg.hive_tasks_dir, args.task_id)
        if dropped:
            print(f"dropped {len(dropped)} filed inbox entr(y/ies): {', '.join(dropped)}")
        # And the other half of the same "the branch landed": the debt this
        # task says it resolved is resolved in the docs now, so its cards have
        # stopped being work anybody should pick up.
        closed = close_resolved_debt(cfg, _kanban(cfg), args.task_id, args.project)
        if closed:
            # Named by entry, not by card: the entry id is the one a human can
            # look the work up by, on the board and in the index both.
            print(f"closed the card of {len(closed)} resolved debt entr(y/ies): {', '.join(closed)}")
    elif args.command == "status":
        print(
            operator.format_status(
                cfg,
                operator.account_reports(cfg, probe=args.probe),
                operator.task_reports(cfg),
            )
        )
    elif args.command == "release-account":
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


if __name__ == "__main__":
    main()
