import sys
from pathlib import Path

import dispatcher.cli as cli_mod

CONFIG_YAML = """
accounts:
  - name: cuenta1
    container: agent-cuenta1
quota_threshold_pct: 90
heartbeat_ttl_seconds: 120
heartbeat_interval_seconds: 30
projects_root: /data/projects
hive_tasks_dir: /data/.hive/tasks
state_dir: /data/dispatcher_state
vibe_kanban_mcp_url: http://127.0.0.1:9100/sse
collector_url: http://127.0.0.1:8787
"""


def test_cli_run_task_invokes_run_task_cycle(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML)

    captured = {}
    monkeypatch.setattr(
        cli_mod, "run_task_cycle",
        lambda cfg, task_id, slug, kanban: captured.update(task_id=task_id, slug=slug),
    )
    monkeypatch.setattr(sys, "argv", ["ia-harness-dispatcher", "--config", str(config_path), "run-task", "--task-id", "task-1", "--project", "myproj"])

    cli_mod.main()

    assert captured == {"task_id": "task-1", "slug": "myproj"}


def test_cli_bootstrap_project_creates_project_dir(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML)

    captured = {}
    monkeypatch.setattr(
        cli_mod.docker_exec, "run_docker_exec",
        lambda container, workdir, command: captured.update(container=container, workdir=workdir, command=command),
    )
    monkeypatch.setattr(sys, "argv", ["ia-harness-dispatcher", "--config", str(config_path), "bootstrap-project", "--account", "cuenta1", "--project", "myproj"])

    cli_mod.main()

    assert captured == {
        "container": "agent-cuenta1",
        "workdir": "/",
        "command": ["mkdir", "-p", "/data/projects/myproj"],
    }
