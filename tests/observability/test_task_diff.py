"""`GET /api/tasks/<id>/diff`, against temporary git checkouts (ADR NNN).

The mounts and the client are `test_api.py`'s `_harness`; each project
directory it makes is turned into a real repository here, with the task's
work on `docker_exec.task_branch`, so what is exercised is the `git` this
route runs and not a stand-in for it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from dispatcher import docker_exec
from observability.api import app as api_app
from tests.observability.test_api import _get, _harness, _task

PATH = "/api/tasks/T-1/diff"


def _git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


def _commit(repo: Path, name: str, text: str, message: str) -> str:
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _repo(harness, slug: str = "ia-harness") -> Path:
    """`slug`'s checkout as a repository on `main`, one commit in."""
    repo = Path(harness.projects_root) / slug
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "a.txt", "one\ntwo\n", "base")
    return repo


def _branch_with_work(repo: Path, task_id: str = "T-1") -> str:
    """The task branch off `main` with one change of its own; its tip."""
    _git(repo, "checkout", "-q", "-b", docker_exec.task_branch(task_id))
    tip = _commit(repo, "a.txt", "one\nTWO\nthree\n", "work")
    _git(repo, "checkout", "-q", "main")
    return tip


def test_the_diff_is_the_task_branch_against_its_merge_base_with_the_checkouts_branch(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1", status="done")
    repo = _repo(harness)
    fork = _git(repo, "rev-parse", "HEAD")
    tip = _branch_with_work(repo)
    # A commit on the base after the fork is not the task's change: the
    # three-dot diff leaves it out, as a merge would.
    _commit(repo, "b.txt", "later\n", "base moved")

    body = _get(harness, PATH).get_json()

    assert body["warnings"] == []
    data = body["data"]
    assert {k: data[k] for k in ("task_id", "project", "branch", "base", "truncated")} == {
        "task_id": "T-1",
        "project": "ia-harness",
        "branch": "agent/task/T-1",
        "base": "main",
        "truncated": False,
    }
    assert (data["commit"], data["merge_base"]) == (tip, fork)
    assert "diff --git a/a.txt b/a.txt" in data["diff"]
    assert "-two\n+TWO\n+three\n" in data["diff"]
    assert "b.txt" not in data["diff"]


def test_the_tasks_project_picks_the_checkout_and_project_overrides_it(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path, projects=("ia-harness", "other"))
    _task(harness.tasks_dir, "T-1", project="other")
    _repo(harness, "ia-harness")
    tip = _branch_with_work(_repo(harness, "other"))

    named_by_task = _get(harness, PATH).get_json()
    overridden = _get(harness, f"{PATH}?project=ia-harness").get_json()

    assert (named_by_task["data"]["project"], named_by_task["data"]["commit"]) == ("other", tip)
    assert overridden["data"] is None
    assert "no branch agent/task/T-1" in overridden["warnings"][0]


def test_a_task_naming_no_project_among_several_checkouts_is_a_400(tmp_path: Path) -> None:
    harness = _harness(tmp_path, projects=("ia-harness", "other"))
    _task(harness.tasks_dir, "T-1")

    resp = _get(harness, PATH)

    assert resp.status_code == 400
    assert "project is required" in resp.get_json()["error"]


def test_no_task_branch_is_data_null_and_says_why(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    _repo(harness)

    body = _get(harness, PATH).get_json()

    assert body["data"] is None
    assert body["warnings"] == [
        f"{harness.projects_root}/ia-harness: no branch agent/task/T-1: "
        "the task has not committed there"
    ]


def test_a_branch_with_no_change_of_its_own_is_an_empty_diff_not_null(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    repo = _repo(harness)
    _git(repo, "branch", docker_exec.task_branch("T-1"))

    body = _get(harness, PATH).get_json()

    assert body["data"]["diff"] == ""
    assert body["warnings"] == []


def test_a_detached_checkout_has_no_base_and_says_so(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    repo = _repo(harness)
    _branch_with_work(repo)
    _git(repo, "checkout", "-q", "--detach", "main")

    body = _get(harness, PATH).get_json()

    assert body["data"] is None
    assert "no branch checked out to diff agent/task/T-1 against" in body["warnings"][0]


def test_a_directory_that_is_not_a_repository_is_not_answered_by_one_that_holds_it(
    tmp_path: Path,
) -> None:
    # `GIT_CEILING_DIRECTORIES`: without it git walks up from the project
    # directory, and a parent repository would be read as the project's.
    _git(tmp_path, "init", "-q", "-b", "main")
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")

    body = _get(harness, PATH).get_json()

    assert body["data"] is None
    assert "no branch checked out" in body["warnings"][0]


def test_a_diff_past_the_cap_is_cut_at_a_whole_line_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_app, "MAX_DIFF_BYTES", 200)
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    repo = _repo(harness)
    _git(repo, "checkout", "-q", "-b", docker_exec.task_branch("T-1"))
    _commit(repo, "big.txt", "".join(f"line {n}\n" for n in range(100)), "big")
    _git(repo, "checkout", "-q", "main")

    body = _get(harness, PATH).get_json()

    assert body["data"]["truncated"] is True
    assert len(body["data"]["diff"].encode()) <= 200
    assert body["data"]["diff"].endswith("\n")
    assert "over 200 bytes" in body["warnings"][0]


def test_no_git_in_the_image_is_data_null_and_never_a_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    _repo(harness)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    resp = _get(harness, PATH)

    assert resp.status_code == 200
    assert resp.get_json()["data"] is None
    assert "git is not installed" in resp.get_json()["warnings"][0]


@pytest.mark.parametrize("task_id", ["T-404", "..", "a b"])
def test_an_unknown_task_is_a_404(tmp_path: Path, task_id: str) -> None:
    harness = _harness(tmp_path)

    resp = _get(harness, f"/api/tasks/{task_id}/diff")

    assert resp.status_code == 404


def test_an_unknown_project_is_a_404(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")

    resp = _get(harness, f"{PATH}?project=nope")

    assert resp.status_code == 404
    assert "no project 'nope'" in resp.get_json()["error"]
