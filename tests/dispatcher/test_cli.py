import io
import logging
import sys
from pathlib import Path

import pytest

import dispatcher.cli as cli_mod
import dispatcher.dispatcher as dispatcher_mod
from dispatcher import learnings
from dispatcher.context_transfer import read_kanban_issue_id, set_description, set_resolved_debt
from dispatcher.docker_exec import MERGED, REFUSED, MergeOutcome
from dispatcher.vibe_kanban_client import LocalBoardClient, NullKanbanClient, VibeKanbanClient

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

LOCAL_BOARD_YAML = """
local_board:
  dir: {board_dir}
"""

ISSUE_ID = "0e1d2c3b-4a59-6878-9706-5a4b3c2d1e0f"


def _write_config(tmp_path: Path, board: bool = False, local_board: bool = False) -> Path:
    """A config whose hive_tasks_dir is a real directory: run-task reads it
    to decide whether the task already carries a description. `board` adds the
    optional vibe_kanban block and `local_board` the optional local one, which
    most runs don't have — and which config.yaml refuses to carry together."""
    config_path = tmp_path / "config.yaml"
    text = CONFIG_YAML.format(hive_tasks_dir=str(tmp_path / "hive"))
    if board:
        text += KANBAN_YAML
    if local_board:
        text += LOCAL_BOARD_YAML.format(board_dir=str(tmp_path / "board"))
    config_path.write_text(text)
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


def test_cli_run_task_with_a_local_board_uses_the_local_client(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = _write_config(tmp_path, local_board=True)
    captured = _capture_cycle(monkeypatch)

    _run(monkeypatch, config_path, "--description", "Add a /healthz endpoint.")

    assert isinstance(captured["kanban"], LocalBoardClient)
    assert captured["kanban"].config.dir == str(tmp_path / "board")


def test_cli_run_task_stores_an_issue_id_for_a_local_board(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path, local_board=True)
    _capture_cycle(monkeypatch)

    _run(monkeypatch, config_path, "--description", "Add a /healthz.", "--kanban-issue-id", ISSUE_ID)

    # A local board reads the id the same way the remote one does, so the flag
    # has to be accepted for it too — the check is "no board", not "no MCP".
    assert read_kanban_issue_id(str(tmp_path / "hive"), "task-1") == ISSUE_ID


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


# --- `dispatch learnings`, the human half of the inbox ---------------------


def _learning(tmp_path: Path, name: str, **meta) -> Path:
    """One inbox entry, in the shape the phases are told to write."""
    fields = dict(
        project="myproj",
        task="task-8",
        phase="implementador",
        scope=learnings.SCOPE_PROJECT,
        status=learnings.UNCONFIRMED,
        when="the suite talks to a database",
    )
    fields.update(meta)
    root = Path(learnings.ensure_dirs(str(tmp_path / "hive")))
    path = root / learnings.INBOX_NAME / name
    path.write_text(
        "---\n"
        + "".join(f"{key}: {value}\n" for key, value in fields.items())
        + "---\n\n"
        "## Symptom\n\n```\nECONNREFUSED 127.0.0.1:5432\n```\n\n"
        "## Why\n\nThe fixture assumed a server that nothing starts.\n\n"
        "## Rule\n\nStart postgres before the suite, not with it.\n\n"
        "## Evidence\n\n`pytest -q tests/db` in the writers' worktree.\n"
    )
    return path


def _run_command(monkeypatch, config_path: Path, *argv: str) -> None:
    monkeypatch.setattr(sys, "argv", [
        "ia-harness-dispatcher", "--config", str(config_path), *argv,
    ])
    cli_mod.main()


def test_cli_learnings_says_where_the_empty_inbox_is(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """An operator who runs this before any task has is told where the files
    would be, not handed a blank screen."""
    config_path = _write_config(tmp_path)

    _run_command(monkeypatch, config_path, "learnings")

    out = capsys.readouterr().out
    assert "no learnings in" in out
    assert learnings.root_dir(str(tmp_path / "hive")) in out


def test_cli_learnings_prints_the_table_the_phases_are_shown(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """Same renderer as the prompt fragment: what a human rules on has to be
    what the agents read, or the ruling is about something else."""
    config_path = _write_config(tmp_path)
    _learning(tmp_path, "task-8-db.md")

    _run_command(monkeypatch, config_path, "learnings")

    out = capsys.readouterr().out
    assert "Start postgres before the suite" in out
    assert "the suite talks to a database" in out
    assert learnings.UNCONFIRMED in out
    # And the pointer to the files, because the table is one line per entry.
    assert learnings.root_dir(str(tmp_path / "hive")) in out


def test_cli_learnings_filters_to_what_one_project_would_be_shown(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """Another project's unreviewed entry is one phase's word about code this
    project does not have."""
    config_path = _write_config(tmp_path)
    _learning(tmp_path, "task-8-db.md", project="otherproj")

    _run_command(monkeypatch, config_path, "learnings", "--project", "myproj")

    assert "no learnings in" in capsys.readouterr().out


def test_cli_learnings_promotes_one_entry_into_the_shared_store(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """The only door into the cross-project store, and deliberately a human
    one: it is one phase's word applied to every project at once."""
    config_path = _write_config(tmp_path)
    path = _learning(tmp_path, "task-8-db.md", scope=learnings.SCOPE_HARNESS)

    _run_command(monkeypatch, config_path, "learnings", "--promote", "task-8-db.md")

    assert not path.exists()
    assert (Path(learnings.harness_dir(str(tmp_path / "hive"))) / "task-8-db.md").exists()
    assert "every project" in capsys.readouterr().out


def test_cli_learnings_fails_loudly_on_a_ref_that_is_not_there(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """A typo in a ref must not read as "promoted": the operator would move on
    believing every project now has the trap."""
    config_path = _write_config(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        _run_command(monkeypatch, config_path, "learnings", "--promote", "typo.md")

    assert excinfo.value.code == 1
    assert "no such inbox entry" in capsys.readouterr().err


def test_cli_learnings_confirms_an_entry_without_a_second_task(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """A human who has hit the trap themselves is the other half of the
    confirmation rule — waiting for a second task to burn quota on it is not
    the only way an entry becomes trustworthy."""
    config_path = _write_config(tmp_path)
    path = _learning(tmp_path, "task-8-db.md")

    _run_command(monkeypatch, config_path, "learnings", "--confirm", "task-8-db.md")

    assert f"status: {learnings.CONFIRMED}" in path.read_text()
    assert learnings.CONFIRMED in capsys.readouterr().out


def test_cli_learnings_drops_a_trap_that_was_wrong(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    config_path = _write_config(tmp_path)
    path = _learning(tmp_path, "task-8-db.md")

    _run_command(monkeypatch, config_path, "learnings", "--drop", "task-8-db.md")

    assert not path.exists()
    assert "deleted inbox/task-8-db.md" in capsys.readouterr().out


def _fake_merge(monkeypatch, outcome) -> None:
    """A merge-task whose docker half is all fake: container, owner, merge."""
    monkeypatch.setattr(cli_mod, "cleanup_container", lambda cfg: "agent-cuenta1")
    monkeypatch.setattr(cli_mod.docker_exec, "read_owner", lambda container, path: "1000:1000")
    monkeypatch.setattr(cli_mod.docker_exec, "restore_owner", lambda container, path, owner: None)
    monkeypatch.setattr(
        cli_mod.docker_exec,
        "merge_task_branch",
        lambda container, projects_root, slug, task_id: outcome,
    )


def test_cli_merge_task_drops_the_entries_the_merged_branch_filed(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """The manual merge has to do what the automatic one does: the docs are in
    the repo now, so the inbox copy would charge every later prompt twice."""
    config_path = _write_config(tmp_path)
    path = _learning(tmp_path, "task-8-db.md", carried_by="task-1")
    _fake_merge(monkeypatch, MergeOutcome(MERGED, "main", "merged agent/task/task-1 into main"))

    _run_command(
        monkeypatch, config_path, "merge-task", "--task-id", "task-1", "--project", "myproj",
    )

    assert not path.exists()
    assert "dropped 1 filed inbox" in capsys.readouterr().out


def test_cli_merge_task_keeps_the_entries_another_task_is_carrying(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """Only what *this* branch filed goes: a run in flight elsewhere still
    needs the rows it is about to write into its own project's docs."""
    config_path = _write_config(tmp_path)
    mine = _learning(tmp_path, "task-1-db.md", carried_by="task-1")
    theirs = _learning(tmp_path, "task-2-db.md", carried_by="task-2")
    _fake_merge(monkeypatch, MergeOutcome(MERGED, "main", "merged agent/task/task-1 into main"))

    _run_command(
        monkeypatch, config_path, "merge-task", "--task-id", "task-1", "--project", "myproj",
    )

    assert not mine.exists()
    assert theirs.exists()


def test_cli_merge_task_that_was_refused_leaves_the_inbox_alone(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """Nothing landed, so the docs are still only on the task branch."""
    config_path = _write_config(tmp_path)
    path = _learning(tmp_path, "task-8-db.md", carried_by="task-1")
    _fake_merge(monkeypatch, MergeOutcome(REFUSED, "main", "has uncommitted changes"))

    with pytest.raises(SystemExit) as excinfo:
        _run_command(
            monkeypatch, config_path, "merge-task", "--task-id", "task-1", "--project", "myproj",
        )

    assert excinfo.value.code == 1
    assert path.exists()


_DEBT_INDEX = (
    "| id | what | where | fix | card |\n"
    "|---|---|---|---|---|\n"
    "| `task-4-D1` | The retry loop has no test | backoff changes | a fake clock | `card-9` |\n"
)


class _CountingBoard:
    """A board that only remembers what was asked of it."""

    enabled = True

    def __init__(self) -> None:
        self.statuses: list[tuple[str, str]] = []

    def set_status(self, issue_id: str, status: str) -> None:
        self.statuses.append((issue_id, status))


def _fake_debt(monkeypatch, board=None, index: str = _DEBT_INDEX) -> None:
    """The debt half of a merge, faked where `close_resolved_debt` reads it."""
    monkeypatch.setattr(dispatcher_mod, "cleanup_container", lambda cfg: "agent-cuenta1")
    monkeypatch.setattr(dispatcher_mod.debt, "read_index", lambda container, workdir: index)
    if board is not None:
        monkeypatch.setattr(cli_mod, "_kanban", lambda cfg: board)


def test_cli_merge_task_closes_the_cards_of_the_debt_the_branch_resolved(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """The card outlives the run that opened it, so closing it is the merge's
    job, not the cycle's: the entry can be resolved days later by another task
    in another process, which is exactly when this command gets run by hand."""
    config_path = _write_config(tmp_path, board=True)
    set_resolved_debt(str(tmp_path / "hive"), "task-1", ["task-4-D1"])
    board = _CountingBoard()
    _fake_merge(monkeypatch, MergeOutcome(MERGED, "main", "merged agent/task/task-1 into main"))
    _fake_debt(monkeypatch, board)

    _run_command(
        monkeypatch, config_path, "merge-task", "--task-id", "task-1", "--project", "myproj",
    )

    assert board.statuses == [("card-9", "done")]
    assert "closed the card of 1 resolved debt entr(y/ies): task-4-D1" in capsys.readouterr().out


def test_cli_merge_task_without_a_board_says_nothing_about_debt_cards(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """`vibe_kanban` is optional and the index is the source of truth, so a
    project with no board merges exactly as before and reads as before."""
    config_path = _write_config(tmp_path)
    set_resolved_debt(str(tmp_path / "hive"), "task-1", ["task-4-D1"])
    _fake_merge(monkeypatch, MergeOutcome(MERGED, "main", "merged agent/task/task-1 into main"))

    _run_command(
        monkeypatch, config_path, "merge-task", "--task-id", "task-1", "--project", "myproj",
    )

    out = capsys.readouterr().out
    assert "merged agent/task/task-1 into main" in out
    assert "debt" not in out


def _basic_config_recorder(monkeypatch) -> dict:
    """`logging.basicConfig` is a no-op once the root logger has a handler,
    and pytest gives it one, so what the dispatcher asked for cannot be read
    back off the root logger here. The ask itself is what these tests are
    about, so it is recorded instead."""
    captured: dict = {}
    monkeypatch.setattr(cli_mod.logging, "basicConfig", lambda **kwargs: captured.update(kwargs))
    return captured


def test_the_cli_gives_the_dispatcher_log_somewhere_to_go(tmp_path: Path, monkeypatch) -> None:
    """Without this nothing configured logging at all: the root logger sat at
    WARNING with no handler, so every `logger.info` in a run was dropped and a
    full end-to-end run left five lines of log. INFO by default, and to stderr
    because the subcommands print their results to stdout."""
    monkeypatch.delenv(cli_mod._LOG_LEVEL_ENV, raising=False)
    captured = _basic_config_recorder(monkeypatch)
    _capture_cycle(monkeypatch)

    _run(monkeypatch, _write_config(tmp_path), "--description", "do the thing")

    assert captured["level"] == logging.INFO
    assert captured["stream"] is sys.stderr
    assert "%(asctime)s" in captured["format"]
    assert "%(name)s" in captured["format"]


def test_logging_is_up_before_the_arguments_are_parsed(tmp_path: Path, monkeypatch) -> None:
    """An invocation argparse rejects exits through logging's last-resort
    handler otherwise — no timestamp, no logger name — which is the format a
    run is read in when something went wrong with how it was called."""
    captured = _basic_config_recorder(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["ia-harness-dispatcher", "--nonsense"])

    with pytest.raises(SystemExit):
        cli_mod.main()

    assert captured["level"] == logging.INFO


@pytest.mark.parametrize("wanted,expected", [("DEBUG", logging.DEBUG), ("debug", logging.DEBUG), ("ERROR", logging.ERROR)])
def test_the_environment_can_make_a_run_louder_or_quieter(
    tmp_path: Path, monkeypatch, wanted, expected,
) -> None:
    """Through the environment rather than the config file, because the config
    is mounted into every container and a noisy run is a property of the run,
    not of the deployment. Case-insensitive: it is typed by hand."""
    monkeypatch.setenv(cli_mod._LOG_LEVEL_ENV, wanted)
    captured = _basic_config_recorder(monkeypatch)
    _capture_cycle(monkeypatch)

    _run(monkeypatch, _write_config(tmp_path), "--description", "do the thing")

    assert captured["level"] == expected


def test_an_unusable_log_level_falls_back_instead_of_killing_the_run(
    tmp_path: Path, monkeypatch, caplog,
) -> None:
    """A typo in an environment variable should not cost a task. The fallback
    is announced, and it is announced after logging is up rather than raised
    from inside the call that sets logging up — otherwise the complaint would
    go wherever the unconfigured logger sends it."""
    monkeypatch.setenv(cli_mod._LOG_LEVEL_ENV, "verbose")
    captured = _basic_config_recorder(monkeypatch)
    cycle = _capture_cycle(monkeypatch)

    with caplog.at_level("WARNING"):
        _run(monkeypatch, _write_config(tmp_path), "--description", "do the thing")

    assert captured["level"] == logging.INFO
    assert cycle["task_id"] == "task-1"
    assert any("verbose" in r.getMessage() for r in caplog.records)


# --- run-phase ---


def _capture_single_phase(monkeypatch, result: object = object()) -> dict:
    """Stand in for the one phase, and record what the parser decided."""
    captured: dict = {}

    def fake_run_single_phase(cfg, task_id, slug, kanban, role, round_num=None, final=False, description=None, note=""):
        captured.update(
            task_id=task_id, slug=slug, role=role,
            round_num=round_num, final=final, description=description, note=note,
        )
        captured["kanban"] = kanban
        return result

    monkeypatch.setattr(cli_mod, "run_single_phase", fake_run_single_phase)
    return captured


def _described(tmp_path: Path, board: bool = False) -> Path:
    """A config whose task-1 is already under way, which is the only kind of
    task this verb runs on."""
    config_path = _write_config(tmp_path, board=board)
    set_description(tmp_path / "hive", "task-1", "Add a /healthz endpoint that returns 200.")
    return config_path


def test_cli_run_phase_passes_the_phase_it_was_given_through(tmp_path: Path, monkeypatch, capsys) -> None:
    captured = _capture_single_phase(monkeypatch)

    _run_command(
        monkeypatch, _described(tmp_path),
        "run-phase", "--task-id", "task-1", "--project", "myproj",
        "--role", "revisor", "--round", "2", "--note", "the host rebooted",
    )

    assert captured["task_id"] == "task-1"
    assert captured["slug"] == "myproj"
    assert captured["role"] == "revisor"
    assert captured["round_num"] == 2
    assert captured["final"] is False
    assert captured["note"] == "the host rebooted"
    # The verb never rewrites the ask: the task on disk already carries it.
    assert captured["description"] is None
    assert "task-1.md" in capsys.readouterr().out


def test_cli_run_phase_defaults_to_a_phase_that_does_not_close_the_task(
    tmp_path: Path, monkeypatch,
) -> None:
    """--final is the destructive half of this verb — it moves the card to
    done. Leaving it out has to mean the ordinary middle-of-a-cycle phase."""
    captured = _capture_single_phase(monkeypatch)

    _run_command(
        monkeypatch, _described(tmp_path),
        "run-phase", "--task-id", "task-1", "--project", "myproj", "--role", "auditor",
    )

    assert captured["final"] is False
    assert captured["round_num"] is None
    assert captured["note"] == ""


def test_cli_run_phase_final_is_passed_along(tmp_path: Path, monkeypatch) -> None:
    captured = _capture_single_phase(monkeypatch)

    _run_command(
        monkeypatch, _described(tmp_path),
        "run-phase", "--task-id", "task-1", "--project", "myproj",
        "--role", "auditor", "--final",
    )

    assert captured["final"] is True


def test_cli_run_phase_gives_the_cycle_a_board_when_the_config_has_one(
    tmp_path: Path, monkeypatch,
) -> None:
    """Same rule as run-task: no vibe_kanban block, no board — and a phase
    run by hand still has to move whatever card the task already has."""
    captured = _capture_single_phase(monkeypatch)
    _run_command(
        monkeypatch, _described(tmp_path),
        "run-phase", "--task-id", "task-1", "--project", "myproj", "--role", "auditor",
    )
    assert isinstance(captured["kanban"], NullKanbanClient)

    captured = _capture_single_phase(monkeypatch)
    _run_command(
        monkeypatch, _described(tmp_path, board=True),
        "run-phase", "--task-id", "task-1", "--project", "myproj", "--role", "auditor",
    )
    assert isinstance(captured["kanban"], VibeKanbanClient)


def test_cli_run_phase_rejects_a_role_nothing_knows_how_to_prompt(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """A typo in --role should be a usage error, not a full-price run of a
    role the prompt builder has no case for."""
    captured = _capture_single_phase(monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        _run_command(
            monkeypatch, _described(tmp_path),
            "run-phase", "--task-id", "task-1", "--project", "myproj", "--role", "revisorr",
        )

    assert excinfo.value.code == 2
    assert captured == {}


def test_cli_run_phase_rejects_a_round_below_one(tmp_path: Path, monkeypatch, capsys) -> None:
    """Rounds count from 1, and 0 would read as "no round at all" the moment
    it reached the prompt — which is exactly the bug this verb exists to fix."""
    captured = _capture_single_phase(monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        _run_command(
            monkeypatch, _described(tmp_path),
            "run-phase", "--task-id", "task-1", "--project", "myproj",
            "--role", "revisor", "--round", "0",
        )

    assert excinfo.value.code == 2
    assert "--round" in capsys.readouterr().err
    assert captured == {}


def test_cli_run_phase_refuses_a_task_that_was_never_started(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """This verb resumes; it does not start. A task with no description on
    disk was never run, so there is no phase to resume and nothing to pay for."""
    captured = _capture_single_phase(monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        _run_command(
            monkeypatch, _write_config(tmp_path),
            "run-phase", "--task-id", "task-1", "--project", "myproj", "--role", "revisor",
        )

    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "run-task" in err
    assert captured == {}


def test_cli_run_phase_exits_nonzero_when_the_phase_did_not_land(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """The operator is driving the cycle by hand, one command per phase: a
    failure has to stop the shell loop rather than let the next phase run over
    the top of a task that is now blocked."""
    _capture_single_phase(monkeypatch, result=None)

    with pytest.raises(SystemExit) as excinfo:
        _run_command(
            monkeypatch, _described(tmp_path),
            "run-phase", "--task-id", "task-1", "--project", "myproj", "--role", "auditor",
        )

    assert excinfo.value.code == 1
    assert "blocked" in capsys.readouterr().err
