import io
import sys
from pathlib import Path

import pytest

import dispatcher.cli as cli_mod
from dispatcher.context_transfer import read_kanban_issue_id, set_description
from dispatcher.vibe_kanban_client import NullKanbanClient, VibeKanbanClient

CONFIG_YAML = """
accounts:
  - name: cuenta1
    container: agent-cuenta1
quota_threshold_pct: 90
heartbeat_ttl_seconds: 120
heartbeat_interval_seconds: 30
projects_root: /data/projects
hive_tasks_dir: {hive_tasks_dir}
state_dir: /data/dispatcher_state
collector_url: http://127.0.0.1:8787
"""

KANBAN_YAML = """
vibe_kanban:
  command: ["vibe-kanban", "mcp"]
"""

ISSUE_ID = "0e1d2c3b-4a59-6878-9706-5a4b3c2d1e0f"


def _write_config(tmp_path: Path, board: bool = False) -> Path:
    """A config whose hive_tasks_dir is a real directory: run-task reads it
    to decide whether the task already carries a description. `board` adds the
    optional vibe_kanban block, which most runs don't have."""
    config_path = tmp_path / "config.yaml"
    text = CONFIG_YAML.format(hive_tasks_dir=str(tmp_path / "hive"))
    config_path.write_text(text + KANBAN_YAML if board else text)
    return config_path


def _capture_cycle(monkeypatch) -> dict:
    captured: dict = {}

    def fake_run_task_cycle(cfg, task_id, slug, kanban, description=None):
        captured.update(task_id=task_id, slug=slug, description=description)
        captured["kanban"] = kanban

    monkeypatch.setattr(cli_mod, "run_task_cycle", fake_run_task_cycle)
    return captured


def _run(monkeypatch, config_path: Path, *extra: str) -> None:
    monkeypatch.setattr(sys, "argv", [
        "ia-harness-dispatcher", "--config", str(config_path),
        "run-task", "--task-id", "task-1", "--project", "myproj", *extra,
    ])
    cli_mod.main()


def test_cli_run_task_invokes_run_task_cycle(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path)
    captured = _capture_cycle(monkeypatch)

    _run(monkeypatch, config_path, "--description", "Add a /healthz endpoint.")

    assert captured["task_id"] == "task-1"
    assert captured["slug"] == "myproj"
    assert captured["description"] == "Add a /healthz endpoint."


def test_cli_run_task_reads_description_from_a_file(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path)
    spec = tmp_path / "spec.md"
    spec.write_text("# Goal\n\nAdd a /healthz endpoint.\n")
    captured = _capture_cycle(monkeypatch)

    _run(monkeypatch, config_path, "--description-file", str(spec))

    assert captured["description"] == "# Goal\n\nAdd a /healthz endpoint.\n"


def test_cli_run_task_reads_description_from_stdin(tmp_path: Path, monkeypatch) -> None:
    """`--description-file -` is the shape that works from a container:
    `docker compose run --rm -T dispatcher ... --description-file - < spec.md`,
    with no need to bind-mount the spec."""
    config_path = _write_config(tmp_path)
    captured = _capture_cycle(monkeypatch)
    monkeypatch.setattr(sys, "stdin", io.StringIO("piped in from a heredoc"))

    _run(monkeypatch, config_path, "--description-file", "-")

    assert captured["description"] == "piped in from a heredoc"


def test_cli_run_task_without_any_description_exits_without_dispatching(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """The gap this closes: the roles used to be dispatched knowing only a
    task id, burning four phases of quota on a task nobody described. With
    nothing on the command line and nothing stored, this is a usage error
    (argparse's exit 2), not a run."""
    config_path = _write_config(tmp_path)
    captured = _capture_cycle(monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        _run(monkeypatch, config_path)

    assert excinfo.value.code == 2
    assert captured == {}
    assert "description" in capsys.readouterr().err


def test_cli_run_task_with_a_blank_description_exits_without_dispatching(
    tmp_path: Path, monkeypatch,
) -> None:
    config_path = _write_config(tmp_path)
    captured = _capture_cycle(monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        _run(monkeypatch, config_path, "--description", "   \n  ")

    assert excinfo.value.code == 2
    assert captured == {}


def test_cli_run_task_resumes_on_the_stored_description(tmp_path: Path, monkeypatch) -> None:
    """Re-running a task that was already seeded (after a Ctrl+C, or a
    second run of the same id) needs no --description: the ask is in the
    task file. None is passed so the stored text isn't rewritten."""
    config_path = _write_config(tmp_path)
    set_description(str(tmp_path / "hive"), "task-1", "Add a /healthz endpoint.")
    captured = _capture_cycle(monkeypatch)

    _run(monkeypatch, config_path)

    assert captured["task_id"] == "task-1"
    assert captured["slug"] == "myproj"
    assert captured["description"] is None


def test_cli_run_task_rejects_both_description_flags(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path)
    spec = tmp_path / "spec.md"
    spec.write_text("from the file")
    captured = _capture_cycle(monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        _run(monkeypatch, config_path, "--description", "inline", "--description-file", str(spec))

    assert excinfo.value.code == 2
    assert captured == {}


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


def test_cli_run_task_without_a_board_uses_the_null_client(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path)
    captured = _capture_cycle(monkeypatch)

    _run(monkeypatch, config_path, "--description", "Add a /healthz endpoint.")

    # No vibe_kanban block: the cycle gets a board that answers every call and
    # reaches nothing, rather than a client pointed at a server that isn't there.
    assert isinstance(captured["kanban"], NullKanbanClient)


def test_cli_run_task_with_a_board_uses_the_real_client(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path, board=True)
    captured = _capture_cycle(monkeypatch)

    _run(monkeypatch, config_path, "--description", "Add a /healthz endpoint.")

    assert isinstance(captured["kanban"], VibeKanbanClient)
    assert captured["kanban"].config.command == ["vibe-kanban", "mcp"]


def test_cli_run_task_stores_the_kanban_issue_id(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path, board=True)
    _capture_cycle(monkeypatch)

    _run(monkeypatch, config_path, "--description", "Add a /healthz.", "--kanban-issue-id", ISSUE_ID)

    # In the task file, so a re-run of the same task needs the flag only once.
    assert read_kanban_issue_id(str(tmp_path / "hive"), "task-1") == ISSUE_ID


def test_cli_run_task_leaves_an_unnamed_issue_unset(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path, board=True)
    _capture_cycle(monkeypatch)

    _run(monkeypatch, config_path, "--description", "Add a /healthz endpoint.")

    assert read_kanban_issue_id(str(tmp_path / "hive"), "task-1") is None


def test_cli_run_task_rejects_the_short_id_shown_on_the_card(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """Every MCP tool takes the issue's uuid; the board shows "VK-7". Caught
    here, the mix-up costs a usage error instead of a rejected update_issue
    four phases into a run."""
    config_path = _write_config(tmp_path, board=True)
    captured = _capture_cycle(monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        _run(monkeypatch, config_path, "--description", "Add a /healthz.", "--kanban-issue-id", "VK-7")

    assert excinfo.value.code == 2
    assert captured == {}
    assert "uuid" in capsys.readouterr().err


def test_cli_run_task_rejects_an_issue_id_with_no_board_configured(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    config_path = _write_config(tmp_path)
    captured = _capture_cycle(monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        _run(monkeypatch, config_path, "--description", "Add a /healthz.", "--kanban-issue-id", ISSUE_ID)

    assert excinfo.value.code == 2
    assert captured == {}
    # Silently storing an id nothing reads would look like a wired-up board.
    assert "vibe_kanban" in capsys.readouterr().err
