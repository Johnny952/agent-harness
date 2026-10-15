import datetime as dt
import io
import logging
import sys
from pathlib import Path

import pytest

import dispatcher.cli as cli_mod
import dispatcher.dispatcher as dispatcher_mod
from dispatcher import learnings, tasks
from dispatcher.tasks import set_resolved_debt
from dispatcher.dispatcher import CycleOutcome, DispatchResult
from dispatcher.docker_exec import (
    MERGED, PREMERGE_CLEAN, PREMERGE_CONFLICT, REFUSED, UP_TO_DATE, MergeOutcome, PreMergeOutcome,
)
from dispatcher.handoff import SubTask

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

#: A description the CLI accepts: the two required sections.
_DESCRIPTION = "## Goal\n\nAdd a /healthz endpoint.\n\n## Done when\n\nGET /healthz answers 200.\n"

#: A task file from before T-026, with its description still in the frontmatter.
_LEGACY_TASK_FILE = (
    "---\ntask_id: {task_id}\nstatus: pending\nowner: null\ndepends_on: []\n"
    "heartbeat: null\ndescription: Add a /healthz endpoint.\n---\n\n"
)


def _legacy(tmp_path: Path, task_id: str = "task-9") -> Path:
    hive = tmp_path / "hive"
    hive.mkdir(parents=True, exist_ok=True)
    path = hive / f"{task_id}.md"
    path.write_text(_LEGACY_TASK_FILE.format(task_id=task_id))
    return path


def _write_config(tmp_path: Path) -> Path:
    """A config whose hive_tasks_dir is a real directory: run-task reads it
    to decide whether the task already carries a description."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML.format(hive_tasks_dir=str(tmp_path / "hive")))
    return config_path


def _capture_cycle(monkeypatch, outcome: CycleOutcome | None = CycleOutcome.FINISHED) -> dict:
    captured: dict = {}

    def fake_run_task_cycle(cfg, task_id, slug, description=None):
        captured.update(task_id=task_id, slug=slug, description=description)
        return outcome

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

    _run(monkeypatch, config_path, "--description", _DESCRIPTION)

    assert captured["task_id"] == "task-1"
    assert captured["slug"] == "myproj"
    assert captured["description"] == _DESCRIPTION


def test_cli_run_task_reads_description_from_a_file(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path)
    spec = tmp_path / "spec.md"
    spec.write_text(_DESCRIPTION)
    captured = _capture_cycle(monkeypatch)

    _run(monkeypatch, config_path, "--description-file", str(spec))

    assert captured["description"] == _DESCRIPTION


def test_cli_run_task_rejects_a_description_missing_a_required_section(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    config_path = _write_config(tmp_path)
    captured = _capture_cycle(monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        _run(monkeypatch, config_path, "--description", "## Goal\n\nAdd a /healthz endpoint.\n")

    assert excinfo.value.code == 2
    assert captured == {}
    assert "`## Done when`" in capsys.readouterr().err


def test_cli_run_task_rejects_a_description_file_missing_a_required_section(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    config_path = _write_config(tmp_path)
    spec = tmp_path / "spec.md"
    spec.write_text("## Done when\n\nGET /healthz answers 200.\n")
    captured = _capture_cycle(monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        _run(monkeypatch, config_path, "--description-file", str(spec))

    assert excinfo.value.code == 2
    assert captured == {}
    assert "`## Goal`" in capsys.readouterr().err


def test_cli_run_task_refuses_a_hive_that_was_not_migrated(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """Another task's legacy file is enough: the cycle's first write is
    `reap_expired_locks`, over every task file."""
    config_path = _write_config(tmp_path)
    legacy = _legacy(tmp_path)
    before = legacy.read_bytes()
    captured = _capture_cycle(monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        _run(monkeypatch, config_path, "--description", _DESCRIPTION)

    assert excinfo.value.code == 2
    assert captured == {}
    err = capsys.readouterr().err
    assert "dispatch migrate" in err and "task-9" in err
    assert legacy.read_bytes() == before


def test_cli_run_phase_refuses_a_hive_that_was_not_migrated(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    config_path = _write_config(tmp_path)
    _legacy(tmp_path)
    captured = _capture_single_phase(monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        _run_command(
            monkeypatch, config_path,
            "run-phase", "--task-id", "task-1", "--project", "myproj", "--role", "revisor",
        )

    assert excinfo.value.code == 2
    assert captured == {}
    assert "dispatch migrate" in capsys.readouterr().err


def test_cli_release_account_refuses_a_hive_that_was_not_migrated(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    config_path = _write_config(tmp_path)
    legacy = _legacy(tmp_path)
    before = legacy.read_bytes()
    monkeypatch.setattr(
        cli_mod.operator, "release_account",
        lambda *a, **k: pytest.fail("released over an unmigrated hive"),
    )

    with pytest.raises(SystemExit) as excinfo:
        _run_command(monkeypatch, config_path, "release-account", "--name", "cuenta1")

    assert excinfo.value.code == 2
    assert "dispatch migrate" in capsys.readouterr().err
    assert legacy.read_bytes() == before


def test_cli_migrate_prints_what_it_moved_and_then_nothing(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    config_path = _write_config(tmp_path)
    _legacy(tmp_path)
    hive = str(tmp_path / "hive")

    _run_command(monkeypatch, config_path, "migrate")

    out = capsys.readouterr().out
    assert f"task-9: description moved to {tasks.description_path(hive, 'task-9')}" in out
    assert "moved 1 description(s); 0 already migrated" in out
    assert tasks.unmigrated_ids(hive) == []
    assert tasks.parse_description(tasks.read_description(hive, "task-9")).goal == (
        "Add a /healthz endpoint."
    )

    _run_command(monkeypatch, config_path, "migrate")

    assert capsys.readouterr().out == "nothing to migrate\n"


def test_cli_migrate_exits_one_when_it_refused_a_file(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    config_path = _write_config(tmp_path)
    legacy = _legacy(tmp_path)
    legacy.write_text(legacy.read_text().replace(
        "description: Add a /healthz endpoint.", "description: [not, text]"
    ))
    before = legacy.read_bytes()

    with pytest.raises(SystemExit) as excinfo:
        _run_command(monkeypatch, config_path, "migrate")

    assert excinfo.value.code == 1
    assert "task-9: not migrated" in capsys.readouterr().err
    assert legacy.read_bytes() == before


def test_cli_migrate_exits_one_on_a_task_whose_cycle_is_still_running(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    config_path = _write_config(tmp_path)
    legacy = _legacy(tmp_path)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    legacy.write_text(
        legacy.read_text()
        .replace("status: pending", "status: in_progress")
        .replace("owner: null", "owner: cuenta1")
        .replace("heartbeat: null", f"heartbeat: '{now}'")
    )
    before = legacy.read_bytes()

    with pytest.raises(SystemExit) as excinfo:
        _run_command(monkeypatch, config_path, "migrate")

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "task-9: not migrated" in err and "lock is live" in err
    assert legacy.read_bytes() == before


def test_cli_run_task_reads_description_from_stdin(tmp_path: Path, monkeypatch) -> None:
    """`--description-file -` is the shape that works from a container:
    `docker compose run --rm -T dispatcher ... --description-file - < spec.md`,
    with no need to bind-mount the spec."""
    config_path = _write_config(tmp_path)
    captured = _capture_cycle(monkeypatch)
    monkeypatch.setattr(sys, "stdin", io.StringIO(_DESCRIPTION))

    _run(monkeypatch, config_path, "--description-file", "-")

    assert captured["description"] == _DESCRIPTION


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
    tasks.write_description(str(tmp_path / "hive"), "task-1", _DESCRIPTION)
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


def test_cli_run_task_no_longer_takes_a_kanban_issue_id(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """ADR 59 removed the flag with the board seam: a usage error, before any
    cycle runs and before anything is written."""
    config_path = _write_config(tmp_path)
    captured = _capture_cycle(monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        _run(
            monkeypatch, config_path, "--description", _DESCRIPTION,
            "--kanban-issue-id", "0e1d2c3b-4a59-6878-9706-5a4b3c2d1e0f",
        )

    assert excinfo.value.code == 2
    assert captured == {}
    assert "--kanban-issue-id" in capsys.readouterr().err
    assert not (tmp_path / "hive" / "task-1.md").exists()


@pytest.mark.parametrize("block", ['vibe_kanban:\n  command: ["vibe-kanban", "mcp"]\n', "local_board:\n  dir: /state/board\n"])
def test_cli_refuses_to_start_on_a_config_that_still_configures_a_board(
    tmp_path: Path, monkeypatch, block: str,
) -> None:
    """The refusal is `load_config`'s, so it is every verb's: nothing runs."""
    config_path = _write_config(tmp_path)
    config_path.write_text(config_path.read_text() + block)
    captured = _capture_cycle(monkeypatch)

    with pytest.raises(ValueError, match="the board seam was removed .*ADR 59"):
        _run(monkeypatch, config_path, "--description", _DESCRIPTION)

    assert captured == {}


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


def _card(tmp_path: Path, task_id: str, *, body: str = "## auditor\n\nFiled.\n") -> Path:
    """The task card the drop reads for its evidence.

    `merge-task` moves nothing unless the card carries a section for the
    promoting role, so a test that expects the drop has to write one
    (`docs/decisions.md` ADR 34).
    """
    hive = tmp_path / "hive"
    hive.mkdir(parents=True, exist_ok=True)
    path = hive / f"{task_id}.md"
    path.write_text(f"---\ntask_id: {task_id}\nstatus: done\n---\n\n{body}")
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
    _fake_premerge(monkeypatch, PreMergeOutcome(
        UP_TO_DATE, "main", "b" * 40, "a" * 40, "agent/task/task-1 already holds main",
        merged_tip="a" * 40,
    ))


def _fake_premerge(monkeypatch, outcome) -> list:
    """The pre-merge check's docker half, faked: what merging the base came to."""
    adopted: list = []
    monkeypatch.setattr(
        cli_mod.docker_exec, "premerge_base",
        lambda container, projects_root, slug, task_id, *a, **k: outcome,
    )
    monkeypatch.setattr(
        cli_mod.docker_exec, "adopt_premerge",
        lambda container, projects_root, slug, task_id, o: adopted.append(o),
    )
    monkeypatch.setattr(
        cli_mod.docker_exec, "drop_premerge_worktree",
        lambda container, projects_root, slug, task_id: None,
    )
    return adopted


def test_cli_merge_task_drops_the_entries_the_merged_branch_filed(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """The manual merge has to do what the automatic one does: the docs are in
    the repo now, so the inbox copy would charge every later prompt twice. The
    card's `## auditor` section is what says the docs really hold them."""
    config_path = _write_config(tmp_path)
    _card(tmp_path, "task-1")
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
    _card(tmp_path, "task-1")
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


def test_cli_merge_task_says_what_it_kept_when_no_auditor_ever_ran(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """The recovery path, and it is not an error: the branch landed, so the
    merge refuses nothing and exits zero — but its cycle never reached an
    auditor, so nothing was promoted into the project's docs and the entries
    stay in the inbox. The line says how many stayed, where they stayed and
    which section was missing, because the operator reading it is the one who
    may have to run the auditor by hand (`docs/decisions.md` ADR 34)."""
    config_path = _write_config(tmp_path)
    _card(tmp_path, "task-1", body="## implementador\n\nBuilt it.\n")
    path = _learning(tmp_path, "task-8-db.md", carried_by="task-1")
    _fake_merge(monkeypatch, MergeOutcome(MERGED, "main", "merged agent/task/task-1 into main"))

    _run_command(
        monkeypatch, config_path, "merge-task", "--task-id", "task-1", "--project", "myproj",
    )

    assert path.exists()
    out = capsys.readouterr().out
    assert "kept 1 filed inbox" in out
    assert f"{learnings.INBOX_NAME}/" in out
    assert "## auditor" in out
    assert "inbox/task-8-db.md" in out
    assert "dropped" not in out


def _merge_task(monkeypatch, config_path: Path, task_id: str = "task-1") -> None:
    _run_command(
        monkeypatch, config_path, "merge-task", "--task-id", task_id, "--project", "myproj",
    )


def test_cli_merge_task_records_the_merge_on_the_task(tmp_path: Path, monkeypatch, capsys) -> None:
    """`docs/plans/task-split.md` section 3: the block a dependency waits for."""
    config_path = _write_config(tmp_path)
    card = _card(tmp_path, "task-1")
    _fake_merge(monkeypatch, MergeOutcome(MERGED, "release", "merged agent/task/task-1 into release"))

    _merge_task(monkeypatch, config_path)

    task = dispatcher_mod.context_transfer.read_task_file(str(card))
    assert task.merged["into"] == "release"
    assert task.status == "done"
    assert "task task-1: recorded as merged into release" in capsys.readouterr().out


def test_cli_merge_task_does_not_merge_a_branch_that_conflicts_with_the_base(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """`docs/plans/autonomy.md` *Decisions*, 2: the manual merge goes through
    the same pre-merge check the cycle's does, and stops where it stops."""
    config_path = _write_config(tmp_path)
    card = _card(tmp_path, "task-1")
    merges = []
    _fake_merge(monkeypatch, MergeOutcome(MERGED, "main", "merged"))
    monkeypatch.setattr(
        cli_mod.docker_exec, "merge_task_branch",
        lambda container, projects_root, slug, task_id: merges.append(task_id),
    )
    _fake_premerge(monkeypatch, PreMergeOutcome(
        PREMERGE_CONFLICT, "main", "b" * 40, "a" * 40, "conflicts", conflicts=("app.py",),
    ))

    with pytest.raises(SystemExit) as excinfo:
        _merge_task(monkeypatch, config_path)

    assert excinfo.value.code == 1
    assert merges == []
    task = dispatcher_mod.context_transfer.read_task_file(str(card))
    assert task.status == dispatcher_mod.CONFLICT_STATUS
    assert task.merged is None
    assert "app.py" in task.body and "b" * 40 in task.body
    assert "app.py" in capsys.readouterr().err


def test_cli_merge_task_keeps_a_clean_premerge_before_it_merges(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    config_path = _write_config(tmp_path)
    _card(tmp_path, "task-1")
    _fake_merge(monkeypatch, MergeOutcome(MERGED, "main", "merged agent/task/task-1 into main"))
    clean = PreMergeOutcome(
        PREMERGE_CLEAN, "main", "b" * 40, "a" * 40, "merged main cleanly",
        merged_tip="c" * 40, workdir="/data/projects/myproj/worktrees/task-1/premerge",
    )
    adopted = _fake_premerge(monkeypatch, clean)
    monkeypatch.setattr(dispatcher_mod.gates, "run", lambda *a, **k: dispatcher_mod.gates.Report([]))

    _merge_task(monkeypatch, config_path)

    assert adopted == [clean]
    assert "test gate green" in capsys.readouterr().out


def test_cli_merge_task_that_was_refused_records_nothing(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    config_path = _write_config(tmp_path)
    card = _card(tmp_path, "task-1")
    before = card.read_bytes()
    _fake_merge(monkeypatch, MergeOutcome(REFUSED, "main", "has uncommitted changes"))

    with pytest.raises(SystemExit) as excinfo:
        _merge_task(monkeypatch, config_path)

    assert excinfo.value.code == 1
    assert card.read_bytes() == before


def test_cli_merge_task_closes_the_epic_on_its_last_child_and_not_before(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    config_path = _write_config(tmp_path)
    hive = str(tmp_path / "hive")
    _card(tmp_path, "task-0")
    subtask = SubTask(title="t", surface="docs/", description=_DESCRIPTION, after=())
    tasks.file_split(hive, "task-0", [subtask, subtask])
    _fake_merge(monkeypatch, MergeOutcome(MERGED, "main", "merged"))
    parent = dispatcher_mod.context_transfer.task_file_path(hive, "task-0")

    _merge_task(monkeypatch, config_path, "task-0-S1")
    assert dispatcher_mod.context_transfer.read_task_file(parent).status == "epic"
    assert "epic task-0" not in capsys.readouterr().out

    _merge_task(monkeypatch, config_path, "task-0-S2")
    assert dispatcher_mod.context_transfer.read_task_file(parent).status == "done"
    assert "epic task-0: every sub-task is merged, so it is done" in capsys.readouterr().out


def test_cli_merge_task_still_exits_zero_when_the_task_has_no_file(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """The branch landed: exit 1 would say it had not. The line says what is
    missing, and the dependency gate is where the operator meets it again."""
    config_path = _write_config(tmp_path)
    (tmp_path / "hive").mkdir()
    _fake_merge(monkeypatch, MergeOutcome(MERGED, "main", "merged agent/task/task-1 into main"))

    _merge_task(monkeypatch, config_path)

    captured = capsys.readouterr()
    assert "merged agent/task/task-1 into main" in captured.out
    assert "task task-1: it has no task file, so the merge is not recorded" in captured.err
    assert list((tmp_path / "hive").glob("*.md")) == []


_DEBT_INDEX = (
    "| id | what | where | fix | card |\n"
    "|---|---|---|---|---|\n"
    "| `task-4-D1` | The retry loop has no test | backoff changes | a fake clock | `card-9` |\n"
)


def test_cli_merge_task_closes_no_card_for_the_debt_the_branch_resolved(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """ADR 59: the auditor marks the row resolved on the branch and nothing
    mirrors it, so the merge reads no index and says nothing about debt."""
    config_path = _write_config(tmp_path)
    set_resolved_debt(str(tmp_path / "hive"), "task-1", ["task-4-D1"])
    _fake_merge(monkeypatch, MergeOutcome(MERGED, "main", "merged agent/task/task-1 into main"))
    reads = []
    monkeypatch.setattr(
        dispatcher_mod.debt, "read_index",
        lambda container, workdir: reads.append(workdir) or _DEBT_INDEX,
    )

    _run_command(
        monkeypatch, config_path, "merge-task", "--task-id", "task-1", "--project", "myproj",
    )

    out = capsys.readouterr().out
    assert "merged agent/task/task-1 into main" in out
    assert "debt" not in out
    assert reads == []


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

    _run(monkeypatch, _write_config(tmp_path), "--description", _DESCRIPTION)

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

    _run(monkeypatch, _write_config(tmp_path), "--description", _DESCRIPTION)

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
        _run(monkeypatch, _write_config(tmp_path), "--description", _DESCRIPTION)

    assert captured["level"] == logging.INFO
    assert cycle["task_id"] == "task-1"
    assert any("verbose" in r.getMessage() for r in caplog.records)


# --- run-phase ---


_LANDED = DispatchResult(success=True, session_id="s1", result_text="done", account="cuenta1")


def _capture_single_phase(monkeypatch, result: DispatchResult | None = _LANDED) -> dict:
    """Stand in for the one phase, and record what the parser decided."""
    captured: dict = {}

    def fake_run_single_phase(cfg, task_id, slug, role, round_num=None, final=False, description=None, note=""):
        captured.update(
            task_id=task_id, slug=slug, role=role,
            round_num=round_num, final=final, description=description, note=note,
        )
        return result

    monkeypatch.setattr(cli_mod, "run_single_phase", fake_run_single_phase)
    return captured


def _described(tmp_path: Path) -> Path:
    """A config whose task-1 is already under way, which is the only kind of
    task this verb runs on."""
    config_path = _write_config(tmp_path)
    tasks.write_description(str(tmp_path / "hive"), "task-1", _DESCRIPTION)
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
    """--final is the destructive half of this verb — it moves the task to
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


def test_cli_run_task_exits_zero_when_the_cycle_finished(tmp_path: Path, monkeypatch) -> None:
    """A finished cycle still exits 0: nothing that chains run-task on success
    has to change."""
    _capture_cycle(monkeypatch, outcome=CycleOutcome.FINISHED)

    _run(monkeypatch, _write_config(tmp_path), "--description", _DESCRIPTION)


def test_cli_run_task_exits_with_the_held_code_when_no_account_took_a_phase(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """The case ADR 52 exists for: on 2026-10-09 two cycles stopped for lack of
    an account and exited 0, which read as a pass to whatever ran them. Held
    is its own code, not the 1 a blocked task gets, because the answer to it
    is a wait and not a person."""
    _capture_cycle(monkeypatch, outcome=CycleOutcome.HELD)

    with pytest.raises(SystemExit) as excinfo:
        _run(monkeypatch, _write_config(tmp_path), "--description", _DESCRIPTION)

    assert excinfo.value.code == cli_mod.EXIT_HELD == 75
    assert excinfo.value.code != cli_mod.EXIT_BLOCKED
    assert "held" in capsys.readouterr().err


def test_cli_run_task_exits_one_when_the_cycle_blocked(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """Every other stop is the code the CLI's other refusals already use."""
    _capture_cycle(monkeypatch, outcome=CycleOutcome.BLOCKED)

    with pytest.raises(SystemExit) as excinfo:
        _run(monkeypatch, _write_config(tmp_path), "--description", _DESCRIPTION)

    assert excinfo.value.code == cli_mod.EXIT_BLOCKED == 1
    assert "blocked" in capsys.readouterr().err


# --- the split (ADR 60) ---

_SPLIT = [
    SubTask(title="The api", surface="api/", description=_DESCRIPTION, after=()),
    SubTask(title="The screen", surface="front/", description=_DESCRIPTION, after=(1,)),
]
_SPLIT_HANDOFF = {
    "status": "complete",
    "split": [
        {"title": s.title, "surface": s.surface, "description": s.description, "after": list(s.after)}
        for s in _SPLIT
    ],
}


def _filed(tmp_path: Path) -> Path:
    """A config whose task-1 is an epic of two sub-tasks."""
    config_path = _described(tmp_path)
    hive = str(tmp_path / "hive")
    tasks.handoff(hive, "task-1", new_status="pending", body="## arquitecto")
    tasks.file_split(hive, "task-1", _SPLIT)
    return config_path


def test_cli_run_task_exits_three_when_the_task_was_split(tmp_path: Path, monkeypatch, capsys) -> None:
    """Its own code: not 0, the task did not ship; not 1, nothing needs
    repair; not 75, waiting changes nothing. The ids go to stdout, because
    they are what the caller runs next."""
    config_path = _filed(tmp_path)
    _capture_cycle(monkeypatch, outcome=CycleOutcome.SPLIT)

    with pytest.raises(SystemExit) as excinfo:
        _run(monkeypatch, config_path)

    assert excinfo.value.code == cli_mod.EXIT_SPLIT == 3
    assert excinfo.value.code not in (cli_mod.EXIT_BLOCKED, cli_mod.EXIT_HELD)
    assert (
        "task task-1 is now an epic: task-1-S1, task-1-S2; run them in that order"
        in capsys.readouterr().out
    )


def test_cli_run_task_split_with_an_unreadable_task_file_still_exits_three(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    _capture_cycle(monkeypatch, outcome=CycleOutcome.SPLIT)

    with pytest.raises(SystemExit) as excinfo:
        _run(monkeypatch, _write_config(tmp_path), "--description", _DESCRIPTION)

    assert excinfo.value.code == cli_mod.EXIT_SPLIT
    assert "task task-1 is now an epic: its sub-tasks; run them in that order" in capsys.readouterr().out


def _run_arquitecto_phase(monkeypatch, tmp_path: Path, role: str = "arquitecto") -> None:
    _run_command(
        monkeypatch, _described(tmp_path),
        "run-phase", "--task-id", "task-1", "--project", "myproj", "--role", role,
    )


def test_cli_run_phase_prints_the_split_the_arquitecto_proposed_and_files_nothing(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    _capture_single_phase(monkeypatch, result=DispatchResult(
        success=True, session_id="s1", result_text="done", account="cuenta1",
        handoff=_SPLIT_HANDOFF,
    ))

    _run_arquitecto_phase(monkeypatch, tmp_path)

    captured = capsys.readouterr()
    assert captured.out.splitlines()[1:] == [
        "phase arquitecto proposes splitting task task-1 into 2 sub-tasks; nothing was filed:",
        "  1. The api (api/)",
        "  2. The screen (front/) — after 1",
        "run `dispatch file-split --task-id task-1 --project myproj` to file them",
    ]
    assert "file-split would refuse it" not in captured.err
    assert not (tmp_path / "hive" / "task-1-S1.md").exists()


def test_cli_run_phase_says_when_file_split_would_refuse_the_proposal(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    _capture_single_phase(monkeypatch, result=DispatchResult(
        success=True, session_id="s1", result_text="done", account="cuenta1",
        handoff={"status": "complete", "split": _SPLIT_HANDOFF["split"][:1]},
    ))

    _run_arquitecto_phase(monkeypatch, tmp_path)

    captured = capsys.readouterr()
    assert "proposes splitting task task-1 into 1 sub-tasks" in captured.out
    assert "file-split would refuse it: a split has 2 to 4 sub-tasks, this one has 1" in captured.err


@pytest.mark.parametrize(
    "role,payload",
    [
        ("arquitecto", {"status": "complete", "split": []}),
        ("arquitecto", None),
        ("arquitecto", dict(_SPLIT_HANDOFF, status="blocked")),
        ("revisor", _SPLIT_HANDOFF),
    ],
    ids=["empty", "prose", "blocked", "another-role"],
)
def test_cli_run_phase_proposes_nothing_without_a_split_to_propose(
    tmp_path: Path, monkeypatch, capsys, role, payload,
) -> None:
    _capture_single_phase(monkeypatch, result=DispatchResult(
        success=True, session_id="s1", result_text="done", account="cuenta1", handoff=payload,
    ))

    _run_arquitecto_phase(monkeypatch, tmp_path, role=role)

    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) == 1
    assert "split" not in captured.out + captured.err


def _capture_file_split(monkeypatch, outcome: CycleOutcome) -> dict:
    captured: dict = {}

    def fake_file_saved_split(cfg, task_id, slug):
        captured.update(task_id=task_id, slug=slug)
        return outcome

    monkeypatch.setattr(cli_mod, "file_saved_split", fake_file_saved_split)
    return captured


def _file_split(monkeypatch, config_path: Path) -> None:
    _run_command(
        monkeypatch, config_path, "file-split", "--task-id", "task-1", "--project", "myproj"
    )


def test_cli_file_split_prints_the_sub_tasks_and_exits_zero(tmp_path: Path, monkeypatch, capsys) -> None:
    """Exit 0 and not 3: on this verb the filing is the success."""
    config_path = _filed(tmp_path)
    captured = _capture_file_split(monkeypatch, CycleOutcome.SPLIT)

    _file_split(monkeypatch, config_path)

    assert captured == {"task_id": "task-1", "slug": "myproj"}
    assert (
        "task task-1 is now an epic: task-1-S1, task-1-S2; run them in that order"
        in capsys.readouterr().out
    )


def test_cli_file_split_exits_one_when_nothing_was_filed(tmp_path: Path, monkeypatch, capsys) -> None:
    _capture_file_split(monkeypatch, CycleOutcome.BLOCKED)

    with pytest.raises(SystemExit) as excinfo:
        _file_split(monkeypatch, _described(tmp_path))

    assert excinfo.value.code == cli_mod.EXIT_BLOCKED
    captured = capsys.readouterr()
    assert "task task-1: the split was not filed" in captured.err
    assert captured.out == ""


def test_cli_file_split_files_from_the_saved_handoff_without_a_model(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """The verb end to end, on the real `file_saved_split`."""
    config_path = _described(tmp_path)
    hive = str(tmp_path / "hive")
    tasks.handoff(hive, "task-1", new_status="pending", body="## arquitecto")
    dispatcher_mod.context_transfer.save_handoff(hive, "task-1", "arquitecto", _SPLIT_HANDOFF)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", None)

    _file_split(monkeypatch, config_path)

    assert "task-1-S1, task-1-S2" in capsys.readouterr().out
    assert (tmp_path / "hive" / "task-1-S2.md").exists()
    # A second run finds an epic and refuses.
    with pytest.raises(SystemExit) as excinfo:
        _file_split(monkeypatch, config_path)
    assert excinfo.value.code == cli_mod.EXIT_BLOCKED


@pytest.mark.parametrize(
    "payload",
    [None, {"status": "complete", "split": []}, {"status": "complete", "split": _SPLIT_HANDOFF["split"][:1]}],
    ids=["missing", "empty", "invalid"],
)
def test_cli_file_split_refuses_a_handoff_it_cannot_file(
    tmp_path: Path, monkeypatch, capsys, payload,
) -> None:
    config_path = _described(tmp_path)
    hive = str(tmp_path / "hive")
    tasks.handoff(hive, "task-1", new_status="pending", body="## arquitecto")
    if payload is not None:
        dispatcher_mod.context_transfer.save_handoff(hive, "task-1", "arquitecto", payload)

    with pytest.raises(SystemExit) as excinfo:
        _file_split(monkeypatch, config_path)

    assert excinfo.value.code == cli_mod.EXIT_BLOCKED
    assert not (tmp_path / "hive" / "task-1-S1.md").exists()


def test_cli_file_split_refuses_a_hive_that_was_not_migrated(tmp_path: Path, monkeypatch, capsys) -> None:
    config_path = _described(tmp_path)
    _legacy(tmp_path)
    captured = _capture_file_split(monkeypatch, CycleOutcome.SPLIT)

    with pytest.raises(SystemExit) as excinfo:
        _file_split(monkeypatch, config_path)

    assert excinfo.value.code == 2
    assert captured == {}
    assert "dispatch migrate" in capsys.readouterr().err


def test_cli_run_phase_exits_with_the_held_code_when_no_account_took_it(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """The same split for the repair path: a phase that never ran is held, not
    failed, and the operator's shell loop can tell the two apart."""
    _capture_single_phase(monkeypatch, result=DispatchResult(
        success=False, session_id=None, result_text="no accounts available",
        account="", no_account=True,
    ))

    with pytest.raises(SystemExit) as excinfo:
        _run_command(
            monkeypatch, _described(tmp_path),
            "run-phase", "--task-id", "task-1", "--project", "myproj", "--role", "revisor",
        )

    assert excinfo.value.code == cli_mod.EXIT_HELD
    assert "held" in capsys.readouterr().err


def _left_behind(tmp_path: Path, role: str = "auditor") -> str:
    return dispatcher_mod.context_transfer.save_left_behind(
        str(tmp_path / "hive"), "task-1", role, 2, ["tests/x.py"], ["docs/charter.md"], "p",
    )


def test_cli_run_task_names_what_a_phase_left_uncommitted(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    config_path = _write_config(tmp_path)
    _capture_cycle(monkeypatch)
    patch = _left_behind(tmp_path)

    _run(monkeypatch, config_path, "--description", _DESCRIPTION)

    err = capsys.readouterr().err
    assert "auditor phase (round 2)" in err
    assert "tests/x.py" in err and "docs/charter.md" in err
    assert patch in err


def test_cli_run_phase_names_what_the_phase_left_uncommitted(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    _capture_single_phase(monkeypatch)
    patch = _left_behind(tmp_path, role="auditor")
    _left_behind(tmp_path, role="implementador")

    _run_command(
        monkeypatch, _described(tmp_path),
        "run-phase", "--task-id", "task-1", "--project", "myproj", "--role", "auditor",
    )

    err = capsys.readouterr().err
    assert patch in err
    assert "implementador phase" not in err


def test_cli_merge_task_says_so_before_merging_a_task_with_edits_left_behind(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    config_path = _write_config(tmp_path)
    _card(tmp_path, "task-1")
    patch = _left_behind(tmp_path)
    _fake_merge(monkeypatch, MergeOutcome(MERGED, "main", "merged agent/task/task-1 into main"))

    _merge_task(monkeypatch, config_path)

    captured = capsys.readouterr()
    assert patch in captured.err
    assert "merging anyway" in captured.err
    assert "merged agent/task/task-1 into main" in captured.out


def test_cli_merge_task_without_edits_left_behind_says_nothing_of_them(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    config_path = _write_config(tmp_path)
    _card(tmp_path, "task-1")
    _fake_merge(monkeypatch, MergeOutcome(MERGED, "main", "merged agent/task/task-1 into main"))

    _merge_task(monkeypatch, config_path)

    assert "uncommitted" not in capsys.readouterr().err


def _keygen(monkeypatch, config_path: Path, *extra: str) -> None:
    monkeypatch.setattr(sys, "argv", [
        "ia-harness-dispatcher", "--config", str(config_path), "project-keygen", *extra,
    ])
    cli_mod.main()


@pytest.mark.skipif(not __import__("shutil").which("ssh-keygen"), reason="no ssh-keygen")
def test_cli_project_keygen_prints_the_public_half_and_never_the_private(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    keys = tmp_path / "keys"

    _keygen(monkeypatch, _write_config(tmp_path), "--project", "myproj", "--keys-dir", str(keys))

    out = capsys.readouterr().out
    public = (keys / "myproj" / "id_ed25519.pub").read_text().strip()
    assert out.splitlines()[0] == public
    assert "fingerprint: 256 SHA256:" in out and "Deploy keys" in out
    assert "PRIVATE KEY" not in out
    private_body = (keys / "myproj" / "id_ed25519").read_text().splitlines()[1]
    assert private_body not in out


@pytest.mark.skipif(not __import__("shutil").which("ssh-keygen"), reason="no ssh-keygen")
def test_cli_project_keygen_refuses_an_existing_key_without_replace(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    keys = tmp_path / "keys"
    config_path = _write_config(tmp_path)
    _keygen(monkeypatch, config_path, "--project", "myproj", "--keys-dir", str(keys))
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc:
        _keygen(monkeypatch, config_path, "--project", "myproj", "--keys-dir", str(keys))

    assert exc.value.code == cli_mod.EXIT_BLOCKED
    assert "--replace" in capsys.readouterr().err


def test_cli_project_keygen_rejects_a_bad_slug(tmp_path: Path, monkeypatch) -> None:
    with pytest.raises(SystemExit) as exc:
        _keygen(monkeypatch, _write_config(tmp_path), "--project", "../x", "--keys-dir", str(tmp_path / "k"))

    assert exc.value.code == 2


# --- merge-task --push (A-4, ADR 78) ---


def _fake_push(monkeypatch, outcome) -> list:
    """push_base's docker half, faked: records each call, answers `outcome`."""
    calls: list = []

    def push_base(container, projects_root, slug, *a, **k):
        calls.append((container, projects_root, slug))
        return outcome

    monkeypatch.setattr(cli_mod.docker_exec, "push_base", push_base)
    return calls


def _merge_task_args(*extra):
    return ("merge-task", "--task-id", "task-1", "--project", "myproj", *extra)


def test_cli_merge_task_with_push_pushes_the_base_after_the_merge(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    de = cli_mod.docker_exec
    config_path = _write_config(tmp_path)
    _card(tmp_path, "task-1")
    _fake_merge(monkeypatch, MergeOutcome(MERGED, "main", "merged agent/task/task-1 into main"))
    pushes = _fake_push(monkeypatch, de.PushOutcome(de.PUSHED, "main", "pushed main to origin/main at ccccccc"))

    _run_command(monkeypatch, config_path, *_merge_task_args("--push"))

    assert [(call[0], call[2]) for call in pushes] == [("agent-cuenta1", "myproj")]
    assert "pushed main to origin/main at ccccccc" in capsys.readouterr().out


def test_cli_merge_task_without_push_never_pushes(tmp_path: Path, monkeypatch, capsys) -> None:
    de = cli_mod.docker_exec
    config_path = _write_config(tmp_path)
    _card(tmp_path, "task-1")
    _fake_merge(monkeypatch, MergeOutcome(MERGED, "main", "merged agent/task/task-1 into main"))
    pushes = _fake_push(monkeypatch, de.PushOutcome(de.PUSHED, "main", "pushed"))

    _run_command(monkeypatch, config_path, *_merge_task_args())

    assert pushes == []


def test_cli_merge_task_push_retries_on_a_branch_already_merged(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """The re-run after a refused push: nothing to merge, the push still runs."""
    de = cli_mod.docker_exec
    config_path = _write_config(tmp_path)
    _card(tmp_path, "task-1")
    _fake_merge(monkeypatch, MergeOutcome(UP_TO_DATE, "main", "main already holds agent/task/task-1"))
    pushes = _fake_push(monkeypatch, de.PushOutcome(de.PUSHED, "main", "pushed"))

    _run_command(monkeypatch, config_path, *_merge_task_args("--push"))

    assert len(pushes) == 1


def test_cli_merge_task_push_that_is_refused_keeps_the_merge_and_exits_4(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    de = cli_mod.docker_exec
    config_path = _write_config(tmp_path)
    card = _card(tmp_path, "task-1")
    _fake_merge(monkeypatch, MergeOutcome(MERGED, "main", "merged agent/task/task-1 into main"))
    _fake_push(monkeypatch, de.PushOutcome(
        de.PUSH_REFUSED, "main", "pushing main to origin/main was refused; rejected",
    ))

    with pytest.raises(SystemExit) as excinfo:
        _run_command(monkeypatch, config_path, *_merge_task_args("--push"))

    assert excinfo.value.code == cli_mod.EXIT_NOT_PUSHED == 4
    task = dispatcher_mod.context_transfer.read_task_file(str(card))
    assert task.merged["into"] == "main"
    assert task.status == "done"
    assert "pushing main to origin/main was refused" in capsys.readouterr().err


def test_cli_merge_task_push_without_an_upstream_is_not_an_error(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    de = cli_mod.docker_exec
    config_path = _write_config(tmp_path)
    _card(tmp_path, "task-1")
    _fake_merge(monkeypatch, MergeOutcome(MERGED, "main", "merged agent/task/task-1 into main"))
    _fake_push(monkeypatch, de.PushOutcome(de.NO_UPSTREAM, "main", "main tracks no upstream branch; nothing pushed"))

    _run_command(monkeypatch, config_path, *_merge_task_args("--push"))

    assert "main tracks no upstream branch; nothing pushed" in capsys.readouterr().out


def test_cli_merge_task_push_does_not_push_a_refused_merge(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    de = cli_mod.docker_exec
    config_path = _write_config(tmp_path)
    _card(tmp_path, "task-1")
    _fake_merge(monkeypatch, MergeOutcome(REFUSED, "main", "has uncommitted changes"))
    pushes = _fake_push(monkeypatch, de.PushOutcome(de.PUSHED, "main", "pushed"))

    with pytest.raises(SystemExit) as excinfo:
        _run_command(monkeypatch, config_path, *_merge_task_args("--push"))

    assert excinfo.value.code == 1
    assert pushes == []
