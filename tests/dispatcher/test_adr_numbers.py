"""ADR numbers handed out per project, and the merges of the logs that hold them.

ADR 81: the append-only logs merge by union, each task's arquitecto is given
a block of ADR numbers from a counter in `state_dir`, and the pre-merge check
stops a merge that leaves one number (or one heading) in the log twice.
"""

import json
import shutil
import threading
from pathlib import Path

import pytest

import dispatcher.dispatcher as dispatcher_mod
from dispatcher import adr_numbers, gates
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


def _log(*numbers: int) -> str:
    return "# Decisions\n\n" + "".join(
        f"## ADR {n} — decision {n}\n\nBody {n}.\n\n" for n in numbers
    )


# -- the counter -------------------------------------------------------------


def test_the_first_block_starts_past_the_highest_number_even_with_gaps(tmp_path) -> None:
    block = adr_numbers.reserve(str(tmp_path), "proj", "task-1", _log(1, 2, 7))

    assert (block.first, block.last) == (8, 10)


def test_a_struck_through_heading_still_holds_its_number(tmp_path) -> None:
    text = _log(1, 2) + "## ~~ADR 9 — withdrawn~~\n\nSuperseded.\n"

    assert adr_numbers.highest(text) == 9
    assert adr_numbers.reserve(str(tmp_path), "proj", "task-1", text).first == 10


def test_a_heading_inside_fenced_code_is_not_an_adr() -> None:
    text = _log(3) + "```\n## ADR 40 — an example\n```\n"

    assert adr_numbers.adr_numbers(text) == [3]


def test_a_project_without_a_log_starts_at_one(tmp_path) -> None:
    block = adr_numbers.reserve(str(tmp_path), "proj", "task-1", None)

    assert (block.first, block.last) == (1, 3)


def test_two_reservations_never_overlap(tmp_path) -> None:
    text = _log(1, 2, 3)

    one = adr_numbers.reserve(str(tmp_path), "proj", "task-1", text)
    two = adr_numbers.reserve(str(tmp_path), "proj", "task-2", text)

    assert (one.first, one.last) == (4, 6)
    assert (two.first, two.last) == (7, 9)


def test_concurrent_reservations_never_overlap(tmp_path) -> None:
    blocks: list[adr_numbers.Block] = []
    lock = threading.Lock()

    def take(i: int) -> None:
        block = adr_numbers.reserve(str(tmp_path), "proj", f"task-{i}", _log(1))
        with lock:
            blocks.append(block)

    threads = [threading.Thread(target=take, args=(i,)) for i in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    numbers = [n for block in blocks for n in range(block.first, block.last + 1)]
    assert len(numbers) == len(set(numbers)) == 36
    assert min(numbers) == 2


def test_a_rerun_of_the_same_task_reuses_its_block(tmp_path) -> None:
    first = adr_numbers.reserve(str(tmp_path), "proj", "task-1", _log(1))
    adr_numbers.reserve(str(tmp_path), "proj", "task-2", _log(1))

    again = adr_numbers.reserve(str(tmp_path), "proj", "task-1", _log(1, 2, 3))

    assert again == first


def test_a_number_appended_by_hand_past_the_counter_is_stepped_over(tmp_path) -> None:
    adr_numbers.reserve(str(tmp_path), "proj", "task-1", _log(1))  # 2..4

    later = adr_numbers.reserve(str(tmp_path), "proj", "task-2", _log(1, 2, 20))

    assert later.first == 21


def test_projects_count_apart_and_the_state_is_a_json_file(tmp_path) -> None:
    adr_numbers.reserve(str(tmp_path), "one", "task-1", None)

    other = adr_numbers.reserve(str(tmp_path), "two", "task-1", None)

    assert other.first == 1
    data = json.loads((tmp_path / "adr" / "one.json").read_text())
    assert data == {"next": 4, "blocks": {"task-1": [1, 3]}}
    # Written by replace: no temporary file is left beside it.
    assert sorted(p.name for p in (tmp_path / "adr").iterdir()) == [
        "one.json", "one.lock", "two.json", "two.lock",
    ]


def test_a_block_holds_at_least_one_number(tmp_path) -> None:
    with pytest.raises(ValueError):
        adr_numbers.reserve(str(tmp_path), "proj", "task-1", None, size=0)


def test_the_prompt_line_names_the_block() -> None:
    assert adr_numbers.Block(12, 14).prompt_line().startswith("Your ADR numbers are 12 to 14")


# -- the arquitecto's note ---------------------------------------------------


def test_the_arquitecto_note_reserves_from_the_projects_log(tmp_path, monkeypatch) -> None:
    project = tmp_path / "projects" / "myproj"
    (project / "docs").mkdir(parents=True)
    (project / "docs" / "decisions.md").write_text(_log(1, 5))
    monkeypatch.setattr(docker_exec_mod, "run_docker_exec", _local_exec)
    cfg = _config(tmp_path, str(tmp_path / "projects"))

    note = dispatcher_mod._adr_block_note(cfg, "task-1", "myproj")
    again = dispatcher_mod._adr_block_note(cfg, "task-1", "myproj")

    assert note == again
    assert note.startswith("Your ADR numbers are 6 to 8")


def test_the_arquitecto_note_is_empty_when_the_reservation_fails(tmp_path, monkeypatch) -> None:
    def broken(*args, **kwargs):
        raise RuntimeError("no container")

    monkeypatch.setattr(docker_exec_mod, "run_docker_exec", broken)
    cfg = _config(tmp_path, str(tmp_path / "projects"))

    assert dispatcher_mod._adr_block_note(cfg, "task-1", "myproj") == ""


# -- the duplicate gate ------------------------------------------------------


def test_a_clean_log_has_no_duplicates() -> None:
    assert gates.decisions_duplicates(_log(1, 2, 4)) == []


def test_two_adrs_given_one_number_are_a_duplicate() -> None:
    text = _log(1, 2) + "## ADR 2 — another decision\n\nOther.\n"

    assert gates.decisions_duplicates(text) == ["ADR 2"]


def test_one_heading_appended_twice_is_a_duplicate() -> None:
    text = "# Decisions\n\n## Notes\n\nA.\n\n## Notes\n\nB.\n"

    assert gates.decisions_duplicates(text) == ["## Notes"]


def test_the_gate_fails_on_a_duplicate_number(tmp_path, monkeypatch) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "decisions.md").write_text(_log(1, 3, 3))
    monkeypatch.setattr(docker_exec_mod, "run_docker_exec", _local_exec)

    findings = gates.decisions_log(_CONTAINER, str(tmp_path))

    assert [(f.gate, f.level) for f in findings] == [(gates.DECISIONS_LOG, gates.BLOCKING)]
    assert "ADR 3" in findings[0].detail


def test_the_gate_passes_without_a_log(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(docker_exec_mod, "run_docker_exec", _local_exec)

    assert gates.decisions_log(_CONTAINER, str(tmp_path)) == []


def test_this_repositorys_own_log_has_no_duplicates() -> None:
    """The repo's decisions.md is itself under the scheme (ADR 81)."""
    text = (_REPO_ROOT / "docs" / "decisions.md").read_text()

    assert gates.decisions_duplicates(text) == []


def test_this_repository_marks_its_logs_union() -> None:
    attributes = (_REPO_ROOT / ".gitattributes").read_text().splitlines()

    for path in ("docs/decisions.md", "docs/ROADMAP.md",
                 "docs/implementations/**", "docs/learnings/**"):
        assert f"{path} merge=union" in attributes


# -- union merges against real git --------------------------------------------

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

_ATTRIBUTES = "docs/decisions.md merge=union\n"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """test_premerge's layout, with a union-merged decisions log on main."""
    monkeypatch.setattr(docker_exec_mod, "run_docker_exec", _local_exec)
    projects = tmp_path / "projects"
    project = projects / "myproj"
    (project / "docs").mkdir(parents=True)
    _git(project, "init", "-q", "-b", "main")
    (project / ".git" / "info" / "exclude").write_text("worktrees/\n")
    (project / ".gitattributes").write_text(_ATTRIBUTES)
    _git(project, "add", ".gitattributes")
    _commit(project, "docs/decisions.md", _log(1, 2), "start")
    writer = project / "worktrees" / "task-1" / "work"
    _git(project, "worktree", "add", "-q", "-b", _BRANCH, str(writer))
    return dict(projects=str(projects), project=project, writer=writer)


def _append(cwd, entry: str, message: str) -> str:
    path = Path(cwd) / "docs" / "decisions.md"
    return _commit(cwd, "docs/decisions.md", path.read_text() + entry, message)


@needs_git
def test_a_union_merge_keeps_both_appends(repo) -> None:
    _append(repo["writer"], "## ADR 3 — from the task\n\nTask.\n", "task adr")
    _append(repo["project"], "## ADR 6 — from main\n\nMain.\n", "main adr")

    proc = _local_exec(_CONTAINER, str(repo["writer"]), ["git", "merge", "-q", "--no-edit", "main"])

    assert proc.returncode == 0, proc.stderr + proc.stdout
    merged = (repo["writer"] / "docs" / "decisions.md").read_text()
    assert adr_numbers.adr_numbers(merged) == [1, 2, 3, 6]
    assert gates.decisions_duplicates(merged) == []


@needs_git
def test_without_the_attribute_the_same_appends_conflict(repo) -> None:
    """The control: what the union attribute is there to prevent."""
    _commit(repo["project"], ".gitattributes", "", "drop the attribute")
    _git(repo["writer"], "merge", "-q", "--no-edit", "main")
    _append(repo["writer"], "## ADR 3 — from the task\n\nTask.\n", "task adr")
    _append(repo["project"], "## ADR 6 — from main\n\nMain.\n", "main adr")

    proc = _local_exec(_CONTAINER, str(repo["writer"]), ["git", "merge", "-q", "--no-edit", "main"])

    assert proc.returncode != 0


@needs_git
def test_a_clean_union_merge_with_one_number_twice_goes_to_conflict(
    tmp_path, repo, monkeypatch,
) -> None:
    cfg = _config(tmp_path, repo["projects"])
    handoff(cfg.hive_tasks_dir, "task-1", "done", "## auditor\n\napproved")
    tip = _append(repo["writer"], "## ADR 3 — from the task\n\nTask.\n", "task adr")
    base = _append(repo["project"], "## ADR 3 — from main\n\nMain.\n", "main adr")
    seen = _gate(monkeypatch, [])

    verdict = dispatcher_mod.pre_merge_check(cfg, _CONTAINER, "task-1", "myproj")

    assert not verdict.passed
    assert seen == []  # stopped before the test gate
    assert _sha(repo["project"], _BRANCH) == tip
    assert _sha(repo["project"], "main") == base
    assert not Path(docker_exec_mod.premerge_worktree_path(
        repo["projects"], "myproj", "task-1",
    )).exists()
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert task.status == dispatcher_mod.CONFLICT_STATUS
    assert "ADR 3" in task.body and base in task.body


@needs_git
def test_a_clean_union_merge_with_distinct_numbers_passes(tmp_path, repo, monkeypatch) -> None:
    cfg = _config(tmp_path, repo["projects"])
    handoff(cfg.hive_tasks_dir, "task-1", "done", "## auditor\n\napproved")
    _append(repo["writer"], "## ADR 3 — from the task\n\nTask.\n", "task adr")
    _append(repo["project"], "## ADR 6 — from main\n\nMain.\n", "main adr")
    seen = _gate(monkeypatch, [])

    verdict = dispatcher_mod.pre_merge_check(cfg, _CONTAINER, "task-1", "myproj")

    assert verdict.passed
    assert len(seen) == 1
    merged = (repo["writer"] / "docs" / "decisions.md").read_text()
    assert adr_numbers.adr_numbers(merged) == [1, 2, 3, 6]
