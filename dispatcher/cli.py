from __future__ import annotations

import argparse

from dispatcher.config import load_config
from dispatcher.dispatcher import run_task_cycle
from dispatcher.vibe_kanban_client import VibeKanbanClient


def main() -> None:
    parser = argparse.ArgumentParser(prog="ia-harness-dispatcher")
    parser.add_argument("--config", required=True, help="Path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run-task", help="Run the full role cycle for one task")
    run_parser.add_argument("--task-id", required=True)
    run_parser.add_argument("--project", required=True, help="Project slug")

    args = parser.parse_args()
    cfg = load_config(args.config)

    if args.command == "run-task":
        kanban = VibeKanbanClient(cfg.vibe_kanban_mcp_url)
        run_task_cycle(cfg, args.task_id, args.project, kanban)


if __name__ == "__main__":
    main()
