"""The staged run skill's move into `.claude/skills/`, on a real tree (ADR 115).

A phase is refused every write under `.claude/`, so the mapper stages the
generated skill under `docs/run-skill/` and the dispatcher moves it before the
mapping commit. The move is a claim about what `find`, `mv` and git do with
the files, so the in-container commands run on the host here, in a throwaway
directory, and the assertions read the tree and the commit back.
"""

import logging
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from dispatcher import docker_exec, project_docs

_CONTAINER = "agent-cuenta1"
_ISOLATED = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def _local_exec(container, workdir, command, env=None, timeout=None):
    """`docker exec -w workdir container command`, run on the host instead."""
    assert container == _CONTAINER
    return subprocess.run(
        command, cwd=workdir, env={**os.environ, **_ISOLATED, **(env or {})},
        capture_output=True, text=True, timeout=timeout,
    )


@pytest.fixture(autouse=True)
def local_exec(monkeypatch):
    monkeypatch.setattr(docker_exec, "run_docker_exec", _local_exec)


def _stage(work: Path, name: str, files: dict[str, str]) -> Path:
    staged = work / project_docs.RUN_SKILL_STAGING / name
    for rel, text in files.items():
        (staged / rel).parent.mkdir(parents=True, exist_ok=True)
        (staged / rel).write_text(text)
    return staged


_SKILL = {
    "SKILL.md": "---\nname: run-myproj\n---\nRun `node .claude/skills/run-myproj/driver.mjs`.\n",
    "driver.mjs": "console.log('up')\n",
    "lib/helper.mjs": "export {}\n",
}


def test_a_staged_skill_lands_whole_and_the_staging_dir_is_gone(tmp_path: Path) -> None:
    """The generator writes more than SKILL.md, so the directory moves as one."""
    _stage(tmp_path, "run-myproj", _SKILL)

    moved = project_docs.move_staged_run_skills(_CONTAINER, str(tmp_path))

    assert moved == ("run-myproj",)
    target = tmp_path / project_docs.RUN_SKILLS_DIR / "run-myproj"
    for rel, text in _SKILL.items():
        assert (target / rel).read_text() == text
    assert not (tmp_path / project_docs.RUN_SKILL_STAGING).exists()


@pytest.mark.parametrize("name", ["myproj", "Run-Myproj", "run-my_proj", "run-"])
def test_a_dir_not_named_like_a_skill_is_not_moved(tmp_path: Path, caplog, name: str) -> None:
    staged = tmp_path / project_docs.RUN_SKILL_STAGING / name
    staged.mkdir(parents=True, exist_ok=True)
    (staged / "SKILL.md").write_text("x\n")

    with caplog.at_level(logging.WARNING):
        moved = project_docs.move_staged_run_skills(_CONTAINER, str(tmp_path))

    assert moved == ()
    assert not (tmp_path / ".claude").exists()
    assert "is not a run-skill directory" in caplog.text
    # Never committed either: the staging dir is removed with it in it.
    assert not (tmp_path / project_docs.RUN_SKILL_STAGING).exists()


def test_a_file_staged_beside_the_dirs_is_not_moved(tmp_path: Path, caplog) -> None:
    (tmp_path / project_docs.RUN_SKILL_STAGING).mkdir(parents=True)
    (tmp_path / project_docs.RUN_SKILL_STAGING / "run-loose").write_text("x\n")

    with caplog.at_level(logging.WARNING):
        moved = project_docs.move_staged_run_skills(_CONTAINER, str(tmp_path))

    assert moved == ()
    assert not (tmp_path / ".claude").exists()
    assert "is not a run-skill directory" in caplog.text


def test_an_existing_skill_is_never_overwritten(tmp_path: Path, caplog) -> None:
    existing = tmp_path / project_docs.RUN_SKILLS_DIR / "run-myproj"
    existing.mkdir(parents=True)
    (existing / "SKILL.md").write_text("the owner's own\n")
    _stage(tmp_path, "run-myproj", _SKILL)

    with caplog.at_level(logging.WARNING):
        moved = project_docs.move_staged_run_skills(_CONTAINER, str(tmp_path))

    assert moved == ()
    assert (existing / "SKILL.md").read_text() == "the owner's own\n"
    assert not (existing / "driver.mjs").exists()
    assert "never overwritten" in caplog.text
    assert not (tmp_path / project_docs.RUN_SKILL_STAGING).exists()


def test_one_bad_dir_does_not_stop_a_good_one(tmp_path: Path) -> None:
    _stage(tmp_path, "run-myproj", _SKILL)
    _stage(tmp_path, "not-a-skill", {"SKILL.md": "x\n"})

    moved = project_docs.move_staged_run_skills(_CONTAINER, str(tmp_path))

    assert moved == ("run-myproj",)
    assert sorted(p.name for p in (tmp_path / project_docs.RUN_SKILLS_DIR).iterdir()) == [
        "run-myproj",
    ]


def test_with_nothing_staged_nothing_happens(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.js").write_text("x\n")

    moved = project_docs.move_staged_run_skills(_CONTAINER, str(tmp_path))

    assert moved == ()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["src"]


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_the_mapping_commit_includes_the_moved_skill(tmp_path: Path) -> None:
    """The cartografo commits the whole tree bar the charter (no scope), so
    nothing in the dispatcher keeps `.claude/` out of its commit."""
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=work, check=True, env={**os.environ, **_ISOLATED})
    (work / "README").write_text("x\n")
    subprocess.run(["git", "add", "README"], cwd=work, check=True, env={**os.environ, **_ISOLATED})
    subprocess.run(["git", "commit", "-q", "-m", "start"], cwd=work, check=True, env={**os.environ, **_ISOLATED})
    (work / project_docs.INDEX).parent.mkdir(parents=True)
    (work / project_docs.INDEX).write_text("---\ntest: npm test\n---\n")
    _stage(work, "run-myproj", _SKILL)

    project_docs.move_staged_run_skills(_CONTAINER, str(work))
    committed = docker_exec.commit_worktree(
        _CONTAINER, str(work), "map", "cartografo (cuenta1)", "cartografo@ia-harness.invalid",
        paths=project_docs.commit_scope(project_docs.MAPPER_ROLE),
        excludes=project_docs.commit_excludes(),
    )

    assert committed is True
    names = subprocess.run(
        ["git", "show", "--name-only", "--format=", "HEAD"], cwd=work,
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert sorted(names) == sorted(
        [project_docs.INDEX]
        + [f"{project_docs.RUN_SKILLS_DIR}/run-myproj/{rel}" for rel in _SKILL]
    )
    assert not any(name.startswith(project_docs.RUN_SKILL_STAGING) for name in names)
