"""The debt index is append-only and merges by union (ADR 297).

A resolution is a row of its own, the reader folds it into its entry, and a
row both sides edited in place - what a union would keep twice - stops the
merge. The merges here run in real git, with the control that conflicts
without the attribute.
"""

import shutil
from pathlib import Path

import pytest

import dispatcher.dispatcher as dispatcher_mod
from dispatcher import debt, gates
from dispatcher import docker_exec as docker_exec_mod
from dispatcher.context_transfer import read_task_file, task_file_path
from dispatcher.tasks import handoff
from tests.dispatcher.test_premerge import (
    _BRANCH,
    _CONTAINER,
    _commit,
    _config,
    _gate,
    _git,
    _local_exec,
    _sha,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HEADER = "| id | what | where | fix | card | impact |\n|---|---|---|---|---|---|\n"
_INDEX = "docs/debt/README.md"
_LOG = "docs/debt/log.md"


def _row(entry: str, what: str = "Do the thing.", where: str = "A task that bites.",
         fix: str = "Fix it.", impact: str = "cost") -> str:
    return f"| `{entry}` | {what} | {where} | {fix} | none | {impact} |\n"


def _resolution(entry: str, task: str, fix: str = "Taken as written.") -> str:
    return _row(entry, f"**Resolved 2026-10-22 by {task}.**", "", fix, "none")


# -- the fold ----------------------------------------------------------------

def test_a_resolution_row_folds_into_its_entry() -> None:
    text = _HEADER + _row("T-1-D1", fix="Lift it.") + _row("T-1-D2") + _resolution("T-1-D1", "T-2")

    rows = debt.index_rows(text)

    assert [row["id"] for row in rows] == ["T-1-D1", "T-1-D2"]
    first = rows[0]
    assert first["resolved"] is True
    assert first["what"] == "**Resolved 2026-10-22 by T-2.** Do the thing."
    assert first["where"] == "A task that bites."
    assert first["fix"] == "Lift it. Taken as written."
    assert first["impact"] == "none"
    assert rows[1]["resolved"] is False
    assert [row["id"] for row in debt.open_rows(text)] == ["T-1-D2"]


def test_a_resolution_row_with_no_fix_leaves_the_entrys_fix() -> None:
    text = _HEADER + _row("T-1-D1", fix="Lift it.") + _resolution("T-1-D1", "T-2", fix="")

    assert debt.index_rows(text)[0]["fix"] == "Lift it."


def test_a_resolution_row_with_no_entry_is_returned_as_written() -> None:
    rows = debt.index_rows(_HEADER + _resolution("T-9-D1", "T-9"))

    assert [(row["id"], row["resolved"]) for row in rows] == [("T-9-D1", True)]


def test_a_repeated_row_that_is_not_a_resolution_is_kept() -> None:
    text = _HEADER + _row("T-1-D1") + _row("T-1-D1", what="Another.")

    assert [row["what"] for row in debt.index_rows(text)] == ["Do the thing.", "Another."]


def test_a_row_resolved_in_place_is_not_a_resolution_row() -> None:
    text = _HEADER + _row("T-1-D1", what="**Resolved 2026-10-22 by T-2.** Old.")

    rows = debt.index_rows(text)

    assert len(rows) == 1 and rows[0]["resolved"] is True and rows[0]["what"].endswith("Old.")
    assert debt.duplicate_ids(text) == []


def test_duplicate_ids_names_a_repeat_and_ignores_resolution_rows() -> None:
    text = (_HEADER + _row("T-1-D1") + _row("T-1-D2") + _row("T-1-D1", what="Other.")
            + _resolution("T-1-D2", "T-2") + _resolution("T-1-D2", "T-3"))

    assert debt.duplicate_ids(text) == ["T-1-D1"]


# -- the gate ----------------------------------------------------------------

def test_the_gate_blocks_on_a_repeated_entry(tmp_path, monkeypatch) -> None:
    (tmp_path / "docs" / "debt").mkdir(parents=True)
    (tmp_path / _INDEX).write_text(_HEADER + _row("T-1-D1") + _row("T-1-D1", what="Other."))
    monkeypatch.setattr(docker_exec_mod, "run_docker_exec", _local_exec)

    findings = gates.debt_index(_CONTAINER, str(tmp_path))

    assert [(f.gate, f.level) for f in findings] == [(gates.DEBT_INDEX_GATE, gates.BLOCKING)]
    assert "T-1-D1" in findings[0].detail


def test_the_gate_passes_a_clean_index_and_a_missing_one(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(docker_exec_mod, "run_docker_exec", _local_exec)
    assert gates.debt_index(_CONTAINER, str(tmp_path)) == []

    (tmp_path / "docs" / "debt").mkdir(parents=True)
    (tmp_path / _INDEX).write_text(_HEADER + _row("T-1-D1") + _resolution("T-1-D1", "T-2"))
    assert gates.debt_index(_CONTAINER, str(tmp_path)) == []


# -- this repository ---------------------------------------------------------

def test_this_repositorys_own_index_has_no_repeated_entry() -> None:
    text = (_REPO_ROOT / _INDEX).read_text()

    assert debt.duplicate_ids(text) == []
    assert debt.index_rows(text)


def test_this_repositorys_index_ends_with_its_table() -> None:
    last = (_REPO_ROOT / _INDEX).read_text().rstrip().splitlines()[-1]

    assert last.startswith("|")


def test_this_repository_marks_the_debt_files_union() -> None:
    attributes = (_REPO_ROOT / ".gitattributes").read_text().splitlines()

    assert f"{_INDEX} merge=union" in attributes
    assert f"{_LOG} merge=union" in attributes


# -- merges against real git -------------------------------------------------

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

_ATTRIBUTES = f"{_INDEX} merge=union\n{_LOG} merge=union\n"
_START = _HEADER + _row("T-1-D1") + _row("T-1-D2")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A project on `main` holding two adjacent open rows and a log, with a task branch."""
    monkeypatch.setattr(docker_exec_mod, "run_docker_exec", _local_exec)
    projects = tmp_path / "projects"
    project = projects / "myproj"
    (project / "docs" / "debt").mkdir(parents=True)
    _git(project, "init", "-q", "-b", "main")
    (project / ".git" / "info" / "exclude").write_text("worktrees/\n")
    (project / ".gitattributes").write_text(_ATTRIBUTES)
    _git(project, "add", ".gitattributes")
    (project / _LOG).write_text("# Debt log\n\nT-0 filed nothing.\n")
    _git(project, "add", _LOG)
    _commit(project, _INDEX, _START, "start")
    writer = project / "worktrees" / "task-1" / "work"
    _git(project, "worktree", "add", "-q", "-b", _BRANCH, str(writer))
    return dict(projects=str(projects), project=project, writer=writer)


def _append(cwd, name: str, entry: str, message: str) -> str:
    path = Path(cwd) / name
    return _commit(cwd, name, path.read_text() + entry, message)


def _resolve_on_each_side(repo) -> str:
    """The task resolves D1 and main resolves D2, each with its log paragraph."""
    _append(repo["writer"], _INDEX, _resolution("T-1-D1", "T-3"), "task resolves D1")
    _append(repo["writer"], _LOG, "\nT-3 resolved `T-1-D1`.\n", "task log")
    base = _append(repo["project"], _INDEX, _resolution("T-1-D2", "T-4"), "main resolves D2")
    _append(repo["project"], _LOG, "\nT-4 resolved `T-1-D2`.\n", "main log")
    return base


def _merge(repo):
    return _local_exec(_CONTAINER, str(repo["writer"]), ["git", "merge", "-q", "--no-edit", "main"])


@needs_git
def test_two_branches_that_resolve_different_rows_merge_without_conflict(repo) -> None:
    _resolve_on_each_side(repo)

    proc = _merge(repo)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    merged = (repo["writer"] / _INDEX).read_text()
    assert debt.duplicate_ids(merged) == []
    rows = debt.index_rows(merged)
    assert [(row["id"], row["resolved"]) for row in rows] == [("T-1-D1", True), ("T-1-D2", True)]
    assert merged.count("| `T-1-D1` |") == 2 and merged.count("| `T-1-D2` |") == 2
    log = (repo["writer"] / _LOG).read_text()
    assert "T-3 resolved" in log and "T-4 resolved" in log and log.count("T-0 filed") == 1


@needs_git
def test_without_the_attribute_the_same_appends_conflict(repo) -> None:
    """The control: what the union attribute is there to prevent."""
    _commit(repo["project"], ".gitattributes", "", "drop the attributes")
    _git(repo["writer"], "merge", "-q", "--no-edit", "main")
    _resolve_on_each_side(repo)

    assert _merge(repo).returncode != 0


@needs_git
def test_two_in_place_edits_of_one_row_merge_to_a_repeat_the_gate_names(repo) -> None:
    _commit(repo["writer"], _INDEX, _HEADER + _row("T-1-D1", what="Task's wording.") + _row("T-1-D2"), "task")
    _commit(repo["project"], _INDEX, _HEADER + _row("T-1-D1", what="Main's wording.") + _row("T-1-D2"), "main")

    proc = _merge(repo)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert debt.duplicate_ids((repo["writer"] / _INDEX).read_text()) == ["T-1-D1"]
    findings = gates.debt_index(_CONTAINER, str(repo["writer"]))
    assert [f.gate for f in findings] == [gates.DEBT_INDEX_GATE]


@needs_git
def test_a_clean_union_merge_with_a_repeated_row_goes_to_conflict(tmp_path, repo, monkeypatch) -> None:
    cfg = _config(tmp_path, repo["projects"])
    handoff(cfg.hive_tasks_dir, "task-1", "done", "## auditor\n\napproved")
    tip = _commit(repo["writer"], _INDEX, _HEADER + _row("T-1-D1", what="Task's.") + _row("T-1-D2"), "task")
    base = _commit(repo["project"], _INDEX, _HEADER + _row("T-1-D1", what="Main's.") + _row("T-1-D2"), "main")
    seen = _gate(monkeypatch, [])

    verdict = dispatcher_mod.pre_merge_check(cfg, _CONTAINER, "task-1", "myproj")

    assert not verdict.passed
    assert seen == []  # stopped before the test gate
    assert _sha(repo["project"], _BRANCH) == tip
    assert _sha(repo["project"], "main") == base
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert task.status == dispatcher_mod.CONFLICT_STATUS
    assert "T-1-D1" in task.body


@needs_git
def test_a_clean_union_merge_of_two_resolutions_passes(tmp_path, repo, monkeypatch) -> None:
    cfg = _config(tmp_path, repo["projects"])
    handoff(cfg.hive_tasks_dir, "task-1", "done", "## auditor\n\napproved")
    _resolve_on_each_side(repo)
    seen = _gate(monkeypatch, [])

    verdict = dispatcher_mod.pre_merge_check(cfg, _CONTAINER, "task-1", "myproj")

    assert verdict.passed
    assert len(seen) == 1
