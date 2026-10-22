"""Drafted specs (docs/plans/autonomy.md A-6, docs/decisions.md ADR 82).

Nothing here starts a container: `dispatch_phase` is faked, and the fake
writes the draft files the way the arquitecto is told to, so what the CLI
checks is what a real run would leave on disk.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

from dispatcher import cli as cli_mod
from dispatcher import context_transfer, tasks
from dispatcher import dispatcher as core
from dispatcher import queue as queue_mod
from dispatcher.config import DRAFT_ROLE, load_config
from dispatcher.dispatcher import CycleOutcome, DispatchResult
from tests.dispatcher.test_cli import _write_config
from tests.dispatcher.test_config import CONFIG_YAML as CONFIG_ONLY_YAML
from tests.dispatcher.test_dispatcher import _make_config

DRAFT_ID = "d-20261017T120000Z-abcd"


def _description(goal: str = "Do it.", surface: str | None = "`dispatcher/a.py`",
                 out_of_scope: str | None = "Everything else.", done_when: str = "It is done.") -> str:
    text = f"## Goal\n\n{goal}\n\n## Done when\n\n{done_when}\n"
    if surface is not None:
        text += f"\n## Surface\n\n{surface}\n"
    if out_of_scope is not None:
        text += f"\n## Out of scope\n\n{out_of_scope}\n"
    return text


def _draft(hive, name, depends_on=(), **sections):
    return tasks.write_draft(
        str(hive), DRAFT_ID,
        tasks.Draft(name=name, depends_on=list(depends_on), description=_description(**sections)),
    )


def _set_of_three(hive):
    """D1 alone, D2 after D1, D3 after both."""
    _draft(hive, "D1", goal="First.")
    _draft(hive, "D2", ["D1"], goal="Second.", surface="`dispatcher/b.py`")
    _draft(hive, "D3", ["D1", "D2"], goal="Third.", surface="`dispatcher/c.py`")


def _task(hive, task_id):
    return context_transfer.read_task_file(context_transfer.task_file_path(str(hive), task_id))


def _nothing_filed(hive):
    assert list(context_transfer.list_task_ids(str(hive))) == []
    assert not os.path.exists(os.path.join(tasks.draft_set_dir(str(hive), DRAFT_ID), tasks.ACCEPTED_FILE))


# (1) A draft that fails the section rules is refused.


@pytest.mark.parametrize(
    "sections, problem",
    [
        (dict(surface=None), "`## Surface`"),
        (dict(out_of_scope=None), "`## Out of scope`"),
        (dict(surface=None, out_of_scope=None), "`## Surface` and `## Out of scope` sections"),
        (dict(surface="Some files, unnamed."), "names no path"),
    ],
)
def test_a_draft_missing_a_draft_section_is_refused(tmp_path, sections, problem):
    hive = tmp_path / "hive"
    _draft(hive, "D1", **sections)

    with pytest.raises(tasks.DraftError) as exc:
        tasks.check_drafts(str(hive), DRAFT_ID)
    assert any(problem in p for p in exc.value.problems), exc.value.problems
    with pytest.raises(tasks.DraftError):
        tasks.accept_drafts(str(hive), DRAFT_ID)
    _nothing_filed(hive)


def test_a_draft_missing_a_task_section_is_refused(tmp_path):
    """Goal and Done when are the rules every description is held to."""
    hive = tmp_path / "hive"
    path = Path(_draft(hive, "D1"))
    path.write_text(path.read_text().replace("## Goal", "## Aim"))

    with pytest.raises(tasks.DraftError) as exc:
        tasks.accept_drafts(str(hive), DRAFT_ID)
    assert any("draft D1: the task description" in p and "Goal" in p for p in exc.value.problems)
    _nothing_filed(hive)


@pytest.mark.parametrize(
    "deps, problem",
    [
        (["D9"], "depends on D9, which is not in this set"),
        (["D1"], "depends on itself"),
    ],
)
def test_a_draft_depending_outside_its_set_is_refused(tmp_path, deps, problem):
    hive = tmp_path / "hive"
    _draft(hive, "D1", deps)

    with pytest.raises(tasks.DraftError) as exc:
        tasks.accept_drafts(str(hive), DRAFT_ID)
    assert any(problem in p for p in exc.value.problems), exc.value.problems
    _nothing_filed(hive)


def test_drafts_depending_on_each_other_in_a_cycle_are_refused(tmp_path):
    hive = tmp_path / "hive"
    _draft(hive, "D1", ["D2"])
    _draft(hive, "D2", ["D1"], surface="`dispatcher/b.py`")

    with pytest.raises(tasks.DraftError) as exc:
        tasks.check_drafts(str(hive), DRAFT_ID)
    assert any("cycle" in p for p in exc.value.problems)
    with pytest.raises(tasks.DraftError):
        tasks.accept_drafts(str(hive), DRAFT_ID)
    _nothing_filed(hive)


def test_a_draft_file_without_frontmatter_is_refused(tmp_path):
    hive = tmp_path / "hive"
    _draft(hive, "D1")
    (Path(tasks.draft_set_dir(str(hive), DRAFT_ID)) / "D2.md").write_text(_description())

    with pytest.raises(tasks.DraftError) as exc:
        tasks.accept_drafts(str(hive), DRAFT_ID)
    assert any("draft D2: has no frontmatter" in p for p in exc.value.problems)
    _nothing_filed(hive)


def test_every_problem_is_named_at_once(tmp_path):
    hive = tmp_path / "hive"
    _draft(hive, "D1", surface=None)
    _draft(hive, "D2", ["D7"])

    with pytest.raises(tasks.DraftError) as exc:
        tasks.check_drafts(str(hive), DRAFT_ID)
    assert len(exc.value.problems) == 2


def test_an_empty_set_is_refused(tmp_path):
    with pytest.raises(tasks.DraftError, match="no drafts"):
        tasks.check_drafts(str(tmp_path / "hive"), DRAFT_ID)
    with pytest.raises(tasks.DraftError, match="no drafts"):
        tasks.accept_drafts(str(tmp_path / "hive"), DRAFT_ID)


def test_a_set_that_holds_is_returned_in_name_order(tmp_path):
    hive = tmp_path / "hive"
    for n in (10, 2, 1):
        _draft(hive, f"D{n}", surface=f"`dispatcher/m{n}.py`")

    assert [d.name for d in tasks.check_drafts(str(hive), DRAFT_ID)] == ["D1", "D2", "D10"]


# (2) accept-drafts files pending tasks with fresh ids and rewrites depends_on.


def test_accepting_files_pending_tasks_with_fresh_ids(tmp_path):
    hive = tmp_path / "hive"
    tasks.handoff(str(hive), "T-041", "merged", "")
    _set_of_three(hive)

    ids = tasks.accept_drafts(str(hive), DRAFT_ID)

    assert ids == {"D1": "T-042", "D2": "T-043", "D3": "T-044"}
    for name, task_id in ids.items():
        task = _task(hive, task_id)
        assert task.status == "pending"
        assert task.owner is None
        assert tasks.read_description(str(hive), task_id) == _description(
            goal={"D1": "First.", "D2": "Second.", "D3": "Third."}[name],
            surface={"D1": "`dispatcher/a.py`", "D2": "`dispatcher/b.py`",
                     "D3": "`dispatcher/c.py`"}[name],
        )
    assert _task(hive, "T-042").depends_on == []
    assert _task(hive, "T-043").depends_on == ["T-042"]
    assert _task(hive, "T-044").depends_on == ["T-042", "T-043"]
    assert tasks.accepted_drafts(str(hive), DRAFT_ID) == ids


def test_an_empty_board_starts_at_T_001(tmp_path):
    hive = tmp_path / "hive"
    _draft(hive, "D1")

    assert tasks.accept_drafts(str(hive), DRAFT_ID) == {"D1": "T-001"}


def test_a_described_task_with_no_task_file_is_not_reused(tmp_path):
    """The description directories count: an id fresh in one place only is not fresh."""
    hive = tmp_path / "hive"
    tasks.write_description(str(hive), "T-007", _description())
    _draft(hive, "D1")

    assert tasks.accept_drafts(str(hive), DRAFT_ID) == {"D1": "T-008"}


def test_accepting_again_files_nothing_twice(tmp_path):
    hive = tmp_path / "hive"
    _set_of_three(hive)
    tasks.accept_drafts(str(hive), DRAFT_ID)

    with pytest.raises(tasks.DraftError, match="every draft is already accepted"):
        tasks.accept_drafts(str(hive), DRAFT_ID)
    assert sorted(context_transfer.list_task_ids(str(hive))) == ["T-001", "T-002", "T-003"]


def test_a_run_that_died_part_way_is_finished_under_the_same_ids(tmp_path):
    hive = tmp_path / "hive"
    _set_of_three(hive)
    ids = tasks.accept_drafts(str(hive), DRAFT_ID)
    os.remove(context_transfer.task_file_path(str(hive), ids["D3"]))

    assert tasks.accept_drafts(str(hive), DRAFT_ID) == {"D3": "T-003"}
    assert _task(hive, "T-003").depends_on == ["T-001", "T-002"]


# (3) --only accepts a subset.


def test_only_accepts_the_named_drafts(tmp_path):
    hive = tmp_path / "hive"
    _set_of_three(hive)

    assert tasks.accept_drafts(str(hive), DRAFT_ID, only=["D1", "D2"]) == {"D1": "T-001", "D2": "T-002"}
    assert sorted(context_transfer.list_task_ids(str(hive))) == ["T-001", "T-002"]

    # The rest later, against what the first call filed.
    assert tasks.accept_drafts(str(hive), DRAFT_ID) == {"D3": "T-003"}
    assert _task(hive, "T-003").depends_on == ["T-001", "T-002"]


def test_only_ignores_a_bad_draft_it_does_not_name(tmp_path):
    hive = tmp_path / "hive"
    _draft(hive, "D1")
    _draft(hive, "D2", surface=None)
    (Path(tasks.draft_set_dir(str(hive), DRAFT_ID)) / "D3.md").write_text("no frontmatter")

    assert tasks.accept_drafts(str(hive), DRAFT_ID, only=["D1"]) == {"D1": "T-001"}


def test_only_refuses_a_draft_whose_dependency_is_left_out(tmp_path):
    hive = tmp_path / "hive"
    _set_of_three(hive)

    with pytest.raises(tasks.DraftError) as exc:
        tasks.accept_drafts(str(hive), DRAFT_ID, only=["D2"])
    assert any("depends on D1, which is not accepted" in p for p in exc.value.problems)
    _nothing_filed(hive)


def test_only_refuses_an_unknown_or_already_accepted_name(tmp_path):
    hive = tmp_path / "hive"
    _set_of_three(hive)
    tasks.accept_drafts(str(hive), DRAFT_ID, only=["D1"])

    with pytest.raises(tasks.DraftError) as exc:
        tasks.accept_drafts(str(hive), DRAFT_ID, only=["D1", "D9"])
    assert "no draft named D9 in this set" in exc.value.problems
    assert "draft D1: already accepted as T-001" in exc.value.problems
    assert sorted(context_transfer.list_task_ids(str(hive))) == ["T-001"]


# (4) A draft is never runnable.


def test_a_draft_id_never_gets_a_description(tmp_path):
    hive = tmp_path / "hive"
    for task_id in (DRAFT_ID, "drafts"):
        with pytest.raises(ValueError, match="names drafts"):
            tasks.write_description(str(hive), task_id, _description())
    assert tasks.read_description(str(hive), DRAFT_ID) is None


def _no_phase(*args, **kwargs):
    raise AssertionError("a draft reached a phase")


def test_run_task_cycle_refuses_a_draft_id(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path)
    _set_of_three(tmp_path / "hive")
    monkeypatch.setattr(core, "dispatch_phase", _no_phase)

    assert core.open_cycle(cfg, DRAFT_ID, "proj", description=_description()) is None
    assert core.run_task_cycle(cfg, DRAFT_ID, "proj", description=_description()) is CycleOutcome.BLOCKED
    assert core.run_task_cycle(cfg, "drafts", "proj") is CycleOutcome.BLOCKED


def test_queue_plan_refuses_a_draft_id(tmp_path):
    cfg = _make_config(tmp_path)
    _set_of_three(tmp_path / "hive")

    with pytest.raises(queue_mod.QueueError, match=f"task {DRAFT_ID} has no description"):
        queue_mod.plan(cfg, "proj", [DRAFT_ID], 1000)


def _cli(monkeypatch, tmp_path, *argv):
    monkeypatch.setattr(sys, "argv", ["dispatch", "--config", str(_write_config(tmp_path)), *argv])
    cli_mod.main()


def test_run_task_on_a_draft_id_never_reaches_a_phase(tmp_path, monkeypatch):
    _set_of_three(tmp_path / "hive")
    monkeypatch.setattr(core, "dispatch_phase", _no_phase)

    with pytest.raises(SystemExit) as exc:
        _cli(monkeypatch, tmp_path, "run-task", "--task-id", DRAFT_ID, "--project", "proj",
             "--description", _description())
    assert exc.value.code == cli_mod.EXIT_BLOCKED


def test_run_queue_on_a_draft_id_never_reaches_a_cycle(tmp_path, monkeypatch):
    _set_of_three(tmp_path / "hive")
    monkeypatch.setattr(core, "run_task_cycle", _no_phase)
    monkeypatch.setattr(queue_mod, "run", _no_phase)

    with pytest.raises(SystemExit) as exc:
        _cli(monkeypatch, tmp_path, "run-queue", "--project", "proj", "--task-id", DRAFT_ID,
             "--limit", "1000000", "--yes")
    assert exc.value.code == 2  # parser.error: the queue is refused before it is shown


def test_an_accepted_draft_runs_under_its_task_id(tmp_path):
    """The other side of (4): once accepted, the queue takes it like any task."""
    cfg = _make_config(tmp_path)
    _set_of_three(tmp_path / "hive")
    ids = tasks.accept_drafts(cfg.hive_tasks_dir, DRAFT_ID)

    queue = queue_mod.plan(cfg, "proj", list(ids.values()), 1000)
    assert [e.task_id for e in queue.entries] == ["T-001", "T-002", "T-003"]


# The draft-specs run, over a faked phase.


def _phase_that_drafts(calls, write=_set_of_three, success=True, no_account=False):
    def fake(cfg, task_id, slug, role, prompt=None, model=None, effort=None, **kwargs):
        calls.append({"task_id": task_id, "slug": slug, "role": role, "prompt": prompt, "model": model})
        if write is not None:
            write(Path(cfg.hive_tasks_dir))
        return DispatchResult(
            success=success, session_id="s-1", result_text="drafted", account="cuenta1",
            no_account=no_account,
        )
    return fake


@pytest.fixture
def fixed_draft_id(monkeypatch):
    monkeypatch.setattr(tasks, "new_draft_id", lambda now=None: DRAFT_ID)


def test_run_draft_specs_runs_one_arquitecto_phase(tmp_path, monkeypatch, fixed_draft_id):
    cfg = _make_config(tmp_path, role_model={DRAFT_ROLE: "claude-sonnet-5-5"})
    calls: list[dict] = []
    monkeypatch.setattr(core, "dispatch_phase", _phase_that_drafts(calls))

    draft_id, result = core.run_draft_specs(cfg, "proj", "docs/plans/x.md")

    assert draft_id == DRAFT_ID and result.success
    [call] = calls
    assert call["task_id"] == DRAFT_ID and call["role"] == "arquitecto"
    assert call["model"] == "claude-sonnet-5-5"
    drafts_dir = tasks.draft_set_dir(cfg.hive_tasks_dir, DRAFT_ID)
    assert "draft mode" in call["prompt"] and "docs/plans/x.md" in call["prompt"]
    assert drafts_dir in call["prompt"]
    for section in ("Goal", "Done when", "Surface", "Out of scope"):
        assert section in call["prompt"]
    assert _task(tmp_path / "hive", DRAFT_ID).status == "drafted"
    # A drafted run is still not a task: no description, nothing to run.
    assert tasks.read_description(cfg.hive_tasks_dir, DRAFT_ID) is None


class _Cleanup:
    """What the draft run did to the checkout on its way out."""

    def __init__(self):
        self.removed: list[str] = []
        self.restored: list[str | None] = []
        self.dirty: list[str] = []
        self.remove_error: Exception | None = None


@pytest.fixture(autouse=True)
def cleanup(monkeypatch):
    rec = _Cleanup()

    def remove(container, projects_root, slug, task_id):
        rec.removed.append(task_id)
        if rec.remove_error:
            raise rec.remove_error
        return ["arquitecto"]

    monkeypatch.setattr(core.docker_exec, "remove_task_worktrees", remove)
    monkeypatch.setattr(core.docker_exec, "read_owner", lambda container, path: "1000:1000")
    monkeypatch.setattr(core.docker_exec, "restore_owner", lambda container, path, owner: rec.restored.append(owner))
    monkeypatch.setattr(core.docker_exec, "dirty_paths", lambda container, workdir: list(rec.dirty))
    return rec


def test_run_draft_specs_asks_for_a_checkout_of_the_base_and_removes_it(tmp_path, monkeypatch, fixed_draft_id, cleanup):
    cfg = _make_config(tmp_path)
    seen: list[dict] = []
    inner = _phase_that_drafts([])

    def fake(*args, **kwargs):
        seen.append(kwargs)
        assert cleanup.removed == []  # still there while the phase runs
        return inner(*args, **kwargs)

    monkeypatch.setattr(core, "dispatch_phase", fake)

    core.run_draft_specs(cfg, "proj", "docs/plans/x.md")

    assert seen[0]["at_base"] is True
    assert cleanup.removed == [DRAFT_ID] and cleanup.restored == ["1000:1000"]


def test_run_draft_specs_removes_the_checkout_after_a_failed_phase(tmp_path, monkeypatch, fixed_draft_id, cleanup):
    monkeypatch.setattr(core, "dispatch_phase", _phase_that_drafts([], success=False, write=None))

    _, result = core.run_draft_specs(_make_config(tmp_path), "proj", "docs/plans/x.md")

    assert not result.success
    assert cleanup.removed == [DRAFT_ID] and cleanup.restored == ["1000:1000"]


def test_run_draft_specs_removes_the_checkout_after_an_exception(tmp_path, monkeypatch, fixed_draft_id, cleanup):
    def boom(*args, **kwargs):
        raise RuntimeError("phase blew up")
    monkeypatch.setattr(core, "dispatch_phase", boom)

    with pytest.raises(RuntimeError, match="phase blew up"):
        core.run_draft_specs(_make_config(tmp_path), "proj", "docs/plans/x.md")

    assert cleanup.removed == [DRAFT_ID] and cleanup.restored == ["1000:1000"]


def test_run_draft_specs_leaves_the_lock_holders_checkout_alone(tmp_path, monkeypatch, fixed_draft_id, cleanup):
    def held(*args, **kwargs):
        raise context_transfer.LockHeldError(DRAFT_ID, "someone-else")
    monkeypatch.setattr(core, "dispatch_phase", held)

    draft_id, result = core.run_draft_specs(_make_config(tmp_path), "proj", "docs/plans/x.md")

    assert result is None
    assert cleanup.removed == []


def test_a_failed_cleanup_is_a_warning_not_the_runs_error(tmp_path, monkeypatch, fixed_draft_id, cleanup, caplog):
    monkeypatch.setattr(core, "dispatch_phase", _phase_that_drafts([]))
    cleanup.remove_error = OSError("disk on fire")

    _, result = core.run_draft_specs(_make_config(tmp_path), "proj", "docs/plans/x.md")

    assert result.success
    assert "disk on fire" in caplog.text
    assert cleanup.restored == ["1000:1000"]


def test_what_a_draft_phase_left_modified_is_named_when_it_is_discarded(
    tmp_path, monkeypatch, fixed_draft_id, cleanup, caplog,
):
    monkeypatch.setattr(core, "dispatch_phase", _phase_that_drafts([]))
    cleanup.dirty = ["src/app.py"]

    core.run_draft_specs(_make_config(tmp_path), "proj", "docs/plans/x.md")

    assert "src/app.py" in caplog.text and "discarded" in caplog.text
    assert cleanup.removed == [DRAFT_ID]


@pytest.mark.parametrize("plan", ["/etc/passwd", "../other/plan.md"])
def test_run_draft_specs_refuses_a_plan_outside_the_project(tmp_path, monkeypatch, plan):
    monkeypatch.setattr(core, "dispatch_phase", _no_phase)
    with pytest.raises(ValueError, match="inside the project"):
        core.run_draft_specs(_make_config(tmp_path), "proj", plan)


def test_cli_draft_specs_lists_the_drafts(tmp_path, monkeypatch, capsys, fixed_draft_id):
    monkeypatch.setattr(core, "dispatch_phase", _phase_that_drafts([]))

    _cli(monkeypatch, tmp_path, "draft-specs", "--project", "proj", "--plan", "docs/plans/x.md")

    out = capsys.readouterr().out
    assert "D1: First." in out
    assert "D3: Third. (depends on D1, D2)" in out
    assert f"accept-drafts {DRAFT_ID}" in out
    assert list(context_transfer.list_task_ids(str(tmp_path / "hive"))) == [DRAFT_ID]


def test_cli_draft_specs_refuses_drafts_that_break_the_rules(tmp_path, monkeypatch, capsys, fixed_draft_id):
    monkeypatch.setattr(
        core, "dispatch_phase",
        _phase_that_drafts([], write=lambda hive: _draft(hive, "D1", surface=None)),
    )

    with pytest.raises(SystemExit) as exc:
        _cli(monkeypatch, tmp_path, "draft-specs", "--project", "proj", "--plan", "docs/plans/x.md")

    assert exc.value.code == cli_mod.EXIT_BLOCKED
    err = capsys.readouterr().err
    assert "draft D1: is missing its `## Surface` section" in err
    assert "--only" in err


@pytest.mark.parametrize(
    "fake, code",
    [
        (dict(success=False, no_account=True, write=None), cli_mod.EXIT_HELD),
        (dict(success=False, write=None), cli_mod.EXIT_BLOCKED),
    ],
)
def test_cli_draft_specs_exits_on_a_phase_that_did_not_finish(tmp_path, monkeypatch, fixed_draft_id, fake, code):
    monkeypatch.setattr(core, "dispatch_phase", _phase_that_drafts([], **fake))

    with pytest.raises(SystemExit) as exc:
        _cli(monkeypatch, tmp_path, "draft-specs", "--project", "proj", "--plan", "docs/plans/x.md")
    assert exc.value.code == code


def test_cli_draft_specs_exits_when_the_lock_is_held(tmp_path, monkeypatch, fixed_draft_id):
    def held(*args, **kwargs):
        raise context_transfer.LockHeldError(DRAFT_ID, "someone-else")
    monkeypatch.setattr(core, "dispatch_phase", held)

    with pytest.raises(SystemExit) as exc:
        _cli(monkeypatch, tmp_path, "draft-specs", "--project", "proj", "--plan", "docs/plans/x.md")
    assert exc.value.code == cli_mod.EXIT_BLOCKED


def test_cli_accept_drafts_prints_the_new_ids(tmp_path, monkeypatch, capsys):
    _set_of_three(tmp_path / "hive")

    _cli(monkeypatch, tmp_path, "accept-drafts", DRAFT_ID, "--only", "D1", "D2")
    assert capsys.readouterr().out.splitlines() == ["D1 -> T-001", "D2 -> T-002"]

    _cli(monkeypatch, tmp_path, "accept-drafts", DRAFT_ID)
    assert capsys.readouterr().out.splitlines() == ["D3 -> T-003"]


def test_cli_accept_drafts_refuses_with_every_problem(tmp_path, monkeypatch, capsys):
    _draft(tmp_path / "hive", "D1", ["D9"], out_of_scope=None)

    with pytest.raises(SystemExit) as exc:
        _cli(monkeypatch, tmp_path, "accept-drafts", DRAFT_ID)

    assert exc.value.code == cli_mod.EXIT_BLOCKED
    err = capsys.readouterr().err
    assert "`## Out of scope`" in err and "D9" in err and "nothing was accepted" in err


def test_cli_accept_drafts_refuses_a_task_id(tmp_path, monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        _cli(monkeypatch, tmp_path, "accept-drafts", "T-001")
    assert exc.value.code == cli_mod.EXIT_BLOCKED
    assert "not a draft id" in capsys.readouterr().err


# The model key.


def test_the_draft_model_key_is_accepted(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_ONLY_YAML + f"\nrole_model:\n  {DRAFT_ROLE}: claude-sonnet-5-5\n")

    cfg = load_config(str(config_path))

    assert cfg.model_for(DRAFT_ROLE) == "claude-sonnet-5-5"
    assert cfg.model_for("arquitecto") == cfg.default_model


def test_the_draft_model_falls_back_to_the_arquitecto_s(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_ONLY_YAML + "\nrole_model:\n  arquitecto: claude-opus-5-5\n")

    assert load_config(str(config_path)).model_for(DRAFT_ROLE) == "claude-opus-5-5"
    assert _make_config(tmp_path).model_for(DRAFT_ROLE) == "opus"
