from __future__ import annotations

import argparse

from dispatcher import docker_exec
from dispatcher.config import load_config
from dispatcher.dispatcher import container_for, run_task_cycle
from dispatcher.vibe_kanban_client import VibeKanbanClient


def main() -> None:
    parser = argparse.ArgumentParser(prog="ia-harness-dispatcher")
    parser.add_argument("--config", required=True, help="Path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run-task", help="Run the full role cycle for one task")
    run_parser.add_argument("--task-id", required=True)
    run_parser.add_argument("--project", required=True, help="Project slug")

    bootstrap_parser = sub.add_parser(
        "bootstrap-project",
        help="Create the per-project directory (projects_root/<slug>) on one account's container",
    )
    bootstrap_parser.add_argument("--account", required=True, help="Account name from config.yaml's accounts list")
    bootstrap_parser.add_argument("--project", required=True, help="Project slug")

    args = parser.parse_args()
    cfg = load_config(args.config)

    if args.command == "run-task":
        kanban = VibeKanbanClient(cfg.vibe_kanban_mcp_url)
        run_task_cycle(cfg, args.task_id, args.project, kanban)
    elif args.command == "bootstrap-project":
        # Only creates the directory create_worktree() needs as its cwd; the
        # config schema has no repo-source field, so cloning the actual
        # project repo into it is still a manual, one-time operator step.
        container = container_for(cfg, args.account)
        project_dir = f"{cfg.projects_root}/{args.project}"
        docker_exec.run_docker_exec(container, "/", ["mkdir", "-p", project_dir])


if __name__ == "__main__":
    main()
