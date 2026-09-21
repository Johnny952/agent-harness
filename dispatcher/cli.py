from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dispatcher import context_transfer, docker_exec
from dispatcher.config import Config, load_config
from dispatcher.dispatcher import cleanup_container, container_for, run_task_cycle
from dispatcher.vibe_kanban_client import VibeKanbanClient


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


def main() -> None:
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

    args = parser.parse_args()
    cfg = load_config(args.config)

    if args.command == "run-task":
        description = _resolve_description(args, cfg, parser)
        kanban = VibeKanbanClient(cfg.vibe_kanban_mcp_url)
        run_task_cycle(cfg, args.task_id, args.project, kanban, description=description)
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


if __name__ == "__main__":
    main()
