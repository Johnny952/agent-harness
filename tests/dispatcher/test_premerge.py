"""The pre-merge check against real git repositories.

Every other docker_exec test fakes the commands and answers them by hand, which
proves the calls but not what git does with them. A merge that conflicts, or
one git accepts and the tests reject, is a claim about git's behaviour: so here
the in-container command runs on the host instead, in a throwaway repository,
and the assertions read the refs back out of it.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import dispatcher.dispatcher as dispatcher_mod
from dispatcher import docker_exec as docker_exec_mod
from dispatcher import gates
from dispatcher.config import AccountConfig, Config
from dispatcher.context_transfer import read_task_file, task_file_path
from dispatcher.tasks import handoff

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

_CONTAINER = "agent-cuenta1"
_BRANCH = "agent/task/task-1"
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


def _git(cwd, *args) -> str:
    proc = _local_exec(_CONTAINER, str(cwd), ["git", *args])
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _commit(cwd, name, text, message) -> str:
    (Path(cwd) / name).write_text(text)
    _git(cwd, "add", name)
    _git(cwd, "commit", "-q", "-m", message)
    return _git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A project on `main` with a task branch checked out in its writer worktree.

    Laid out the way the dispatcher lays it out: `projects/<slug>` is the
    shared checkout, `worktrees/<task>/work` the writers' one on the branch.
    """
    monkeypatch.setattr(docker_exec_mod, "run_docker_exec", _local_exec)
    projects = tmp_path / "projects"
    project = projects / "myproj"
    project.mkdir(parents=True)
    _git(project, "init", "-q", "-b", "main")
    (project / ".git" / "info" / "exclude").write_text("worktrees/\n")
    _commit(project, "shared.txt", "one\ntwo\nthree\n", "start")
    writer = project / "worktrees" / "task-1" / "work"
    _git(project, "worktree", "add", "-q", "-b", _BRANCH, str(writer))
    return dict(projects=str(projects), project=project, writer=writer)


def _sha(cwd, rev) -> str:
    return _git(cwd, "rev-parse", rev)


def _premerge(repo):
    return docker_exec_mod.premerge_base(_CONTAINER, repo["projects"], "myproj", "task-1")


def _diverge(repo, *, conflict: bool) -> tuple[str, str]:
    """A commit on the task branch and another on main; returns (tip, base)."""
    tip = _commit(repo["writer"], "shared.txt", "one\nTASK\nthree\n", "task work")
    if conflict:
        base = _commit(repo["project"], "shared.txt", "one\nMAIN\nthree\n", "main work")
    else:
        base = _commit(repo["project"], "other.txt", "from main\n", "main work")
    return tip, base


def test_a_clean_premerge_builds_the_merge_without_moving_anything(repo) -> None:
    tip, base = _diverge(repo, conflict=False)

    outcome = _premerge(repo)

    assert outcome.clean
    assert (outcome.base, outcome.base_commit, outcome.tip) == ("main", base, tip)
    # The merge is a real one: both histories are its parents, the task's
    # commit is still the task's commit, and nothing was rebased.
    parents = _git(repo["project"], "rev-list", "--parents", "-n", "1", outcome.merged_tip).split()
    assert parents[1:] == [tip, base]
    assert (Path(outcome.workdir) / "other.txt").exists()
    # Built aside: the branch and the base are where they were.
    assert _sha(repo["project"], _BRANCH) == tip
    assert _sha(repo["project"], "main") == base


def test_adopting_a_clean_premerge_moves_the_branch_and_its_writer_checkout(repo) -> None:
    tip, base = _diverge(repo, conflict=False)
    outcome = _premerge(repo)

    docker_exec_mod.adopt_premerge(_CONTAINER, repo["projects"], "myproj", "task-1", outcome)
    docker_exec_mod.drop_premerge_worktree(_CONTAINER, repo["projects"], "myproj", "task-1")

    assert _sha(repo["project"], _BRANCH) == outcome.merged_tip
    assert (repo["writer"] / "other.txt").read_text() == "from main\n"
    assert _sha(repo["project"], "main") == base
    assert not Path(outcome.workdir).exists()


def test_adopting_without_a_writer_checkout_moves_only_the_ref(repo) -> None:
    tip, base = _diverge(repo, conflict=False)
    _git(repo["project"], "worktree", "remove", str(repo["writer"]))
    outcome = _premerge(repo)

    docker_exec_mod.adopt_premerge(_CONTAINER, repo["projects"], "myproj", "task-1", outcome)

    assert _sha(repo["project"], _BRANCH) == outcome.merged_tip


def test_adopting_refuses_when_the_branch_moved_since_the_premerge(repo) -> None:
    """update-ref is told the old tip, so work committed in between is never lost."""
    _diverge(repo, conflict=False)
    _git(repo["project"], "worktree", "remove", str(repo["writer"]))
    outcome = _premerge(repo)
    _git(repo["project"], "branch", "-f", _BRANCH, "main")

    with pytest.raises(RuntimeError, match="could not move"):
        docker_exec_mod.adopt_premerge(_CONTAINER, repo["projects"], "myproj", "task-1", outcome)


def test_a_conflicting_premerge_is_aborted_and_moves_nothing(repo) -> None:
    tip, base = _diverge(repo, conflict=True)

    outcome = _premerge(repo)

    assert outcome.conflicted
    assert outcome.conflicts == ("shared.txt",)
    assert base[:7] in outcome.detail and "shared.txt" in outcome.detail
    assert _sha(repo["project"], _BRANCH) == tip
    assert _sha(repo["project"], "main") == base
    assert not Path(docker_exec_mod.premerge_worktree_path(
        repo["projects"], "myproj", "task-1",
    )).exists()
    # The writers' checkout never saw the merge: no half-merged files there.
    assert _git(repo["writer"], "status", "--porcelain") == ""
    assert (repo["writer"] / "shared.txt").read_text() == "one\nTASK\nthree\n"


def test_a_branch_that_already_holds_the_base_needs_no_premerge(repo) -> None:
    tip = _commit(repo["writer"], "task.txt", "x\n", "task work")

    outcome = _premerge(repo)

    assert outcome.up_to_date
    assert outcome.merged_tip == tip
    assert _sha(repo["project"], _BRANCH) == tip


# The check end to end: real git, the dispatcher's own pre_merge_check, and
# a test gate faked only at gates.run, the one piece that needs a container.


def _config(tmp_path, projects) -> Config:
    return Config(
        accounts=[AccountConfig(name="cuenta1", container=_CONTAINER)],
        quota_threshold_pct=90,
        quota_cooldown_seconds=1800,
        heartbeat_ttl_seconds=120,
        heartbeat_interval_seconds=1,
        projects_root=projects,
        hive_tasks_dir=str(tmp_path / "hive"),
        state_dir=str(tmp_path / "state"),
        collector_url="http://127.0.0.1:8787",
        default_model="opus",
        permission_mode="acceptEdits",
        allowed_tools=[],
        max_revision_rounds=3,
        escalate_effort_after_round=2,
        escalated_effort="high",
        phase_timeout_seconds=7200,
        merge_on_done=True,
        mapping_enabled=False,
        mapping_model="sonnet",
        mapping_max_turns=40,
        gates_enabled=True,
        gates_test_timeout_seconds=900,
    )


def _gate(monkeypatch, findings) -> list:
    """gates.run, answering `findings` and recording what it was run on."""
    seen = []

    def fake_run(container, workdir, project_dir, *, test_timeout_seconds, test_log_path=None):
        seen.append(dict(workdir=workdir, has_base=(Path(workdir) / "other.txt").exists()))
        return gates.Report(list(findings))

    monkeypatch.setattr(dispatcher_mod.gates, "run", fake_run)
    return seen


def _red():
    return [gates.Finding(gates.TESTS_RUN, "`pytest` failed (exit 1)", gates.BLOCKING)]


def test_a_merge_git_accepts_but_the_tests_reject_lands_nothing(
    tmp_path, repo, monkeypatch,
) -> None:
    cfg = _config(tmp_path, repo["projects"])
    handoff(cfg.hive_tasks_dir, "task-1", "done", "## auditor\n\napproved")
    tip, base = _diverge(repo, conflict=False)
    seen = _gate(monkeypatch, _red())

    verdict = dispatcher_mod.pre_merge_check(cfg, _CONTAINER, "task-1", "myproj")

    assert not verdict.passed
    # The gate ran on the merged result, not on the branch alone.
    assert seen == [dict(
        workdir=docker_exec_mod.premerge_worktree_path(repo["projects"], "myproj", "task-1"),
        has_base=True,
    )]
    assert _sha(repo["project"], _BRANCH) == tip
    assert _sha(repo["project"], "main") == base
    assert not (repo["writer"] / "other.txt").exists()
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert task.status == dispatcher_mod.CONFLICT_STATUS
    assert "`tests-run`" in task.body and base in task.body


def test_a_conflict_through_the_check_names_the_files_and_the_base(
    tmp_path, repo, monkeypatch,
) -> None:
    cfg = _config(tmp_path, repo["projects"])
    handoff(cfg.hive_tasks_dir, "task-1", "done", "## auditor\n\napproved")
    tip, base = _diverge(repo, conflict=True)
    seen = _gate(monkeypatch, [])

    verdict = dispatcher_mod.pre_merge_check(cfg, _CONTAINER, "task-1", "myproj")

    assert not verdict.passed
    assert seen == []
    assert _sha(repo["project"], _BRANCH) == tip
    assert _sha(repo["project"], "main") == base
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert task.status == dispatcher_mod.CONFLICT_STATUS
    assert "shared.txt" in task.body and base in task.body


def test_a_clean_green_check_keeps_the_merge_and_the_merge_lands_it(
    tmp_path, repo, monkeypatch,
) -> None:
    cfg = _config(tmp_path, repo["projects"])
    handoff(cfg.hive_tasks_dir, "task-1", "done", "## auditor\n\napproved")
    tip, base = _diverge(repo, conflict=False)
    _gate(monkeypatch, [])

    verdict = dispatcher_mod.pre_merge_check(cfg, _CONTAINER, "task-1", "myproj")
    merged = docker_exec_mod.merge_task_branch(_CONTAINER, repo["projects"], "myproj", "task-1")

    assert verdict.passed
    assert merged.status == docker_exec_mod.MERGED
    head = _sha(repo["project"], "main")
    assert _git(repo["project"], "merge-base", "--is-ancestor", tip, head) == ""
    assert (repo["project"] / "shared.txt").read_text() == "one\nTASK\nthree\n"
    assert (repo["project"] / "other.txt").read_text() == "from main\n"
    assert read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1")).status == "done"


# The check at review (ADR 274, `docs/plans/review.md` step 8): the same
# merge of the base and the same gate, run when a task enters review and on
# every task still there after another one merges. It tests and keeps
# nothing: the branch stays at the reviewed tip either way.


def _awaiting(cfg, task_id="task-1", project="myproj") -> None:
    """A finished, unmerged task waiting for review, its card naming `project`."""
    from dispatcher.context_transfer import write_task_file

    handoff(cfg.hive_tasks_dir, task_id, "awaiting_review", "## auditor\n\napproved")
    path = task_file_path(cfg.hive_tasks_dir, task_id)
    task = read_task_file(path)
    task.project = project
    write_task_file(path, task)


def _status(cfg, task_id="task-1") -> str:
    return read_task_file(task_file_path(cfg.hive_tasks_dir, task_id)).status


def _premerge_gone(repo, task_id="task-1") -> bool:
    return not Path(docker_exec_mod.premerge_worktree_path(
        repo["projects"], "myproj", task_id,
    )).exists()


def test_the_review_check_tests_a_clean_merge_and_keeps_nothing(
    tmp_path, repo, monkeypatch,
) -> None:
    cfg = _config(tmp_path, repo["projects"])
    _awaiting(cfg)
    tip, base = _diverge(repo, conflict=False)
    seen = _gate(monkeypatch, [])

    verdict = dispatcher_mod.review_check(cfg, _CONTAINER, "task-1", "myproj")

    assert verdict.passed
    # The gate ran on the base merged in, not on the branch alone...
    assert seen == [dict(
        workdir=docker_exec_mod.premerge_worktree_path(repo["projects"], "myproj", "task-1"),
        has_base=True,
    )]
    # ...and the merge was only tested: the reviewed tip is still the tip,
    # the writer's checkout never saw it, and the scratch checkout is gone.
    assert _sha(repo["project"], _BRANCH) == tip
    assert _sha(repo["project"], "main") == base
    assert not (repo["writer"] / "other.txt").exists()
    assert _premerge_gone(repo)
    assert _status(cfg) == "awaiting_review"


def test_the_review_check_moves_a_conflict_to_conflict_and_says_so(
    tmp_path, repo, monkeypatch,
) -> None:
    cfg = _config(tmp_path, repo["projects"])
    _awaiting(cfg)
    tip, base = _diverge(repo, conflict=True)
    seen = _gate(monkeypatch, [])

    verdict = dispatcher_mod.review_check(cfg, _CONTAINER, "task-1", "myproj")

    assert not verdict.passed
    assert seen == []
    assert _sha(repo["project"], _BRANCH) == tip
    assert _sha(repo["project"], "main") == base
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert task.status == dispatcher_mod.CONFLICT_STATUS
    note = task.body.split(dispatcher_mod.REVIEW_CHECK_HEADING)[-1]
    assert "shared.txt" in note and base in note
    # Not a merge that was refused: nothing was being merged.
    assert "merge-task" not in note


def test_the_review_check_moves_a_red_gate_to_conflict(
    tmp_path, repo, monkeypatch,
) -> None:
    cfg = _config(tmp_path, repo["projects"])
    _awaiting(cfg)
    tip, base = _diverge(repo, conflict=False)
    _gate(monkeypatch, _red())

    verdict = dispatcher_mod.review_check(cfg, _CONTAINER, "task-1", "myproj")

    assert not verdict.passed
    assert _sha(repo["project"], _BRANCH) == tip
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert task.status == dispatcher_mod.CONFLICT_STATUS
    assert "`tests-run`" in task.body and base in task.body
    assert "gates-review.log" in task.body
    assert _premerge_gone(repo)


def test_the_review_check_names_the_task_that_merged(
    tmp_path, repo, monkeypatch,
) -> None:
    cfg = _config(tmp_path, repo["projects"])
    _awaiting(cfg)
    _diverge(repo, conflict=True)
    _gate(monkeypatch, [])

    dispatcher_mod.review_check(cfg, _CONTAINER, "task-1", "myproj", merged_task="task-0")

    note = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1")).body
    assert "task-0" in note.split(dispatcher_mod.REVIEW_CHECK_HEADING)[-1]


def test_entering_review_runs_the_check_when_it_is_on(
    tmp_path, repo, monkeypatch,
) -> None:
    import dataclasses

    cfg = dataclasses.replace(_config(tmp_path, repo["projects"]), review_conflict_check=True)
    handoff(cfg.hive_tasks_dir, "task-1", "done", "## auditor\n\napproved")
    tip, _ = _diverge(repo, conflict=True)
    _gate(monkeypatch, [])

    dispatcher_mod._await_review(cfg, "task-1", "myproj")

    assert _status(cfg) == dispatcher_mod.CONFLICT_STATUS
    assert _sha(repo["project"], _BRANCH) == tip


def test_entering_review_runs_no_check_when_it_is_off(
    tmp_path, repo, monkeypatch,
) -> None:
    cfg = _config(tmp_path, repo["projects"])
    assert cfg.review_conflict_check is False
    handoff(cfg.hive_tasks_dir, "task-1", "done", "## auditor\n\napproved")
    _diverge(repo, conflict=True)
    # Recorded, not raised: the check swallows what it raises, so a throwing
    # stub would pass with the switch ignored.
    checked = []
    monkeypatch.setattr(
        docker_exec_mod, "premerge_base", lambda *a, **k: checked.append(a),
    )

    dispatcher_mod._await_review(cfg, "task-1", "myproj")

    assert checked == []
    assert _status(cfg) == "awaiting_review"


def _second_branch(repo, task_id, name, text) -> str:
    """Another task branch off main, with one commit, checked out in its own writer."""
    writer = repo["project"] / "worktrees" / task_id / "work"
    _git(repo["project"], "worktree", "add", "-q", "-b", f"agent/task/{task_id}", str(writer), "main")
    return _commit(writer, name, text, f"{task_id} work")


def test_a_merge_rechecks_every_task_still_waiting_for_review(
    tmp_path, repo, monkeypatch,
) -> None:
    import dataclasses

    cfg = dataclasses.replace(_config(tmp_path, repo["projects"]), review_conflict_check=True)
    # task-1 waits for review, editing the line task-0 edits too.
    tip = _commit(repo["writer"], "shared.txt", "one\nTASK\nthree\n", "task work")
    _awaiting(cfg, "task-1")
    _second_branch(repo, "task-0", "shared.txt", "one\nZERO\nthree\n")
    handoff(cfg.hive_tasks_dir, "task-0", "done", "## auditor\n\napproved")
    # Left alone: a task in its cycle, one of another project, one merged.
    handoff(cfg.hive_tasks_dir, "task-2", "in_progress", "## arquitecto\n\nplan")
    _awaiting(cfg, "task-3", project="otherproj")
    _gate(monkeypatch, [])
    checked = []
    real = docker_exec_mod.premerge_base

    def spy(container, projects_root, slug, task_id):
        checked.append((slug, task_id))
        return real(container, projects_root, slug, task_id)

    monkeypatch.setattr(docker_exec_mod, "premerge_base", spy)

    assert dispatcher_mod._merge_task_branch(cfg, "task-0", "myproj")

    # task-0's own pre-merge, then the sweep over the one task it concerns.
    assert checked == [("myproj", "task-0"), ("myproj", "task-1")]
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert task.status == dispatcher_mod.CONFLICT_STATUS
    note = task.body.split(dispatcher_mod.REVIEW_CHECK_HEADING)[-1]
    assert "task-0" in note and "shared.txt" in note
    assert _sha(repo["project"], _BRANCH) == tip
    assert _status(cfg, "task-2") == "in_progress"
    assert _status(cfg, "task-3") == "awaiting_review"


def test_the_recheck_skips_the_task_that_just_merged(tmp_path, repo, monkeypatch) -> None:
    import dataclasses

    cfg = dataclasses.replace(_config(tmp_path, repo["projects"]), review_conflict_check=True)
    # An approved task can still read awaiting_review, unmerged, when its
    # merge's sweep runs: only its name keeps it out.
    _awaiting(cfg, "task-0")
    _awaiting(cfg, "task-1")
    checked = []
    monkeypatch.setattr(
        dispatcher_mod, "_review_check_locked",
        lambda cfg, task_id, slug, *, merged_task: checked.append(task_id),
    )

    assert dispatcher_mod.recheck_awaiting_review(cfg, "myproj", "task-0") == []
    assert checked == ["task-1"]


def test_a_merge_rechecks_nothing_when_the_check_is_off(
    tmp_path, repo, monkeypatch,
) -> None:
    cfg = _config(tmp_path, repo["projects"])
    _commit(repo["writer"], "shared.txt", "one\nTASK\nthree\n", "task work")
    _awaiting(cfg, "task-1")
    _second_branch(repo, "task-0", "shared.txt", "one\nZERO\nthree\n")
    handoff(cfg.hive_tasks_dir, "task-0", "done", "## auditor\n\napproved")
    _gate(monkeypatch, [])

    assert dispatcher_mod._merge_task_branch(cfg, "task-0", "myproj")

    assert _status(cfg, "task-1") == "awaiting_review"


def test_the_recheck_never_raises_and_skips_a_held_base_lock(
    tmp_path, repo, monkeypatch,
) -> None:
    import dataclasses

    cfg = dataclasses.replace(_config(tmp_path, repo["projects"]), review_conflict_check=True)
    _awaiting(cfg, "task-1")
    _diverge(repo, conflict=True)

    def held(self):
        raise dispatcher_mod.tasks.BaseLockHeldError(
            "myproj", {"owner": "merge-task"}, "review check",
        )

    monkeypatch.setattr(dispatcher_mod.tasks.BaseLock, "acquire", held)

    lines = dispatcher_mod.recheck_awaiting_review(cfg, "myproj", "task-0")

    assert any("locked by merge-task" in line for line in lines)
    assert _status(cfg) == "awaiting_review"


# --- the base merge before an implementador (ADR 339) ----------------------


def _write_window_merge(tmp_path, repo):
    return dispatcher_mod.merge_base_at_write_window(
        _config(tmp_path, repo["projects"]), "task-1", "myproj",
    )


def _commit_round(repo):
    return docker_exec_mod.commit_worktree(
        _CONTAINER, str(repo["writer"]), "round 1", "implementador (cuenta1)",
        "implementador@ia-harness.invalid",
    )


def test_a_conflicting_base_merge_is_left_in_progress_and_the_round_commit_is_the_merge(
    tmp_path, repo,
) -> None:
    tip, base = _diverge(repo, conflict=True)

    move = _write_window_merge(tmp_path, repo)

    assert move.in_progress and move.conflicts == ("shared.txt",)
    writer = str(repo["writer"])
    assert docker_exec_mod.merge_in_progress(_CONTAINER, writer)
    assert "<<<<<<<" in (repo["writer"] / "shared.txt").read_text()
    # Nothing is committed while the markers are there.
    assert docker_exec_mod.conflicted_with_markers(_CONTAINER, writer) == ("shared.txt",)
    assert _sha(repo["writer"], "HEAD") == tip

    # The implementador's edit resolves it; no git command of its own.
    (repo["writer"] / "shared.txt").write_text("one\nTASK and MAIN\nthree\n")
    assert docker_exec_mod.conflicted_with_markers(_CONTAINER, writer) == ()
    assert _commit_round(repo)

    parents = _git(repo["writer"], "rev-list", "--parents", "-n", "1", "HEAD").split()
    assert parents[1:] == [tip, base]
    assert "<<<<<<<" not in _git(repo["writer"], "show", "HEAD:shared.txt")
    assert not docker_exec_mod.merge_in_progress(_CONTAINER, writer)


def test_a_file_still_holding_a_marker_is_not_committed(tmp_path, repo) -> None:
    tip, _ = _diverge(repo, conflict=True)
    _write_window_merge(tmp_path, repo)
    writer = str(repo["writer"])
    (repo["writer"] / "extra.txt").write_text("new work\n")

    # The guard dispatch_phase runs before its commit names the file.
    assert docker_exec_mod.conflicted_with_markers(_CONTAINER, writer) == ("shared.txt",)

    # Whatever the guard's caller does, nothing here staged or committed.
    assert _sha(repo["writer"], "HEAD") == tip
    assert docker_exec_mod.merge_in_progress(_CONTAINER, writer)


def test_a_conflict_that_cannot_be_kept_in_progress_is_aborted_and_reported(
    tmp_path, repo,
) -> None:
    tip, _ = _diverge(repo, conflict=True)
    (repo["writer"] / "scratch.txt").write_text("uncommitted\n")

    move = _write_window_merge(tmp_path, repo)

    assert move.conflicts == ("shared.txt",) and not move.in_progress
    assert not docker_exec_mod.merge_in_progress(_CONTAINER, str(repo["writer"]))
    assert _sha(repo["writer"], "HEAD") == tip


def test_a_leftover_merge_is_aborted_at_cycle_open(tmp_path, repo) -> None:
    tip, _ = _diverge(repo, conflict=True)
    _write_window_merge(tmp_path, repo)
    writer = str(repo["writer"])
    assert docker_exec_mod.merge_in_progress(_CONTAINER, writer)

    assert docker_exec_mod.abort_writer_merge(_CONTAINER, repo["projects"], "myproj", "task-1")

    assert not docker_exec_mod.merge_in_progress(_CONTAINER, writer)
    assert _sha(repo["writer"], "HEAD") == tip
    assert not docker_exec_mod.abort_writer_merge(_CONTAINER, repo["projects"], "myproj", "task-1")
