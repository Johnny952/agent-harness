"""Resolving a conflict with the integrador, against real git repositories.

`docs/plans/review.md` step 9 and ADR 277. Like test_premerge.py, the
in-container command runs on the host in a throwaway repository, so the
merge, the conflict and the commit that finishes it are git's own. The model
calls are the one thing faked: `dispatch_phase` is replaced by a function
that edits the writer checkout the way each role would and returns its
handoff, so what is checked is what the dispatcher does with what a phase
left behind.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import dispatcher.dispatcher as dispatcher_mod
from dispatcher import docker_exec as docker_exec_mod
from dispatcher import gates
from dispatcher import handoff as handoff_mod
from dispatcher import project_docs
from dispatcher.config import AccountConfig, Config
from dispatcher.context_transfer import read_task_file, task_file_path
from dispatcher.tasks import handoff, write_description

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


def _sha(cwd, rev) -> str:
    return _git(cwd, "rev-parse", rev)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A project on `main` with a task branch checked out in its writer worktree."""
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


def _diverge(repo, *, conflict: bool) -> tuple[str, str]:
    """A commit on the task branch and another on main; returns (tip, base)."""
    tip = _commit(repo["writer"], "shared.txt", "one\nTASK\nthree\n", "task work")
    if conflict:
        base = _commit(repo["project"], "shared.txt", "one\nMAIN\nthree\n", "main work")
    else:
        base = _commit(repo["project"], "other.txt", "from main\n", "main work")
    return tip, base


def _config(tmp_path, projects, **overrides) -> Config:
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
        **overrides,
    )


def _in_conflict(cfg, why="merging main in conflicts in shared.txt") -> None:
    """A task the review check moved to `conflict`, with a description to prompt from."""
    write_description(
        cfg.hive_tasks_dir, "task-1", "## Goal\n\nSay TASK.\n\n## Done when\n\nIt does.\n",
    )
    handoff(cfg.hive_tasks_dir, "task-1", "awaiting_review", "## auditor\n\napproved")
    handoff(
        cfg.hive_tasks_dir, "task-1", dispatcher_mod.CONFLICT_STATUS,
        f"{dispatcher_mod.REVIEW_CHECK_HEADING}\n\nChecked as the task entered review: {why}.",
    )


def _gate(monkeypatch, *answers) -> list:
    """gates.run answering each of `answers` in turn (the last one repeats)."""
    seen = []

    def fake_run(container, workdir, project_dir, *, test_timeout_seconds, test_log_path=None):
        findings = answers[min(len(seen), len(answers) - 1)]
        seen.append(dict(workdir=workdir, text=(Path(workdir) / "shared.txt").read_text()))
        return gates.Report(list(findings))

    monkeypatch.setattr(dispatcher_mod.gates, "run", fake_run)
    return seen


def _red(detail="`pytest` failed (exit 1)"):
    return [gates.Finding(gates.TESTS_RUN, detail, gates.BLOCKING)]


def _resolved(**extra):
    return {
        "status": "done",
        "resolutions": [{"path": "shared.txt", "how": "kept both lines"}],
        "blocked_reason": "",
        **extra,
    }


def _phases(monkeypatch, repo, actions: dict) -> list:
    """dispatch_phase, running `actions[role](writer)` and returning its handoff."""
    calls = []

    def fake_dispatch(cfg, task_id, slug, role, prompt, resume_session_id=None, model=None,
                      effort=None, round_num=None, max_turns=None, at_base=False,
                      only_account=None):
        calls.append(dict(role=role, prompt=prompt, model=model, effort=effort))
        payload = actions[role](repo["writer"])
        return dispatcher_mod.DispatchResult(
            success=payload is not None, session_id=None, result_text="",
            account="cuenta1", handoff=payload,
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch)
    return calls


def _approve(writer):
    return {"status": "done", "verdict": "APPROVED"}


def _resolve(text="one\nTASK\nMAIN\nthree\n"):
    def action(writer):
        (Path(writer) / "shared.txt").write_text(text)
        return _resolved()
    return action


def _resolve_conflict(cfg):
    return dispatcher_mod.resolve_conflict(cfg, _CONTAINER, "task-1", "myproj")


def _task(cfg):
    return read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))


def _writer_untouched(repo, tip) -> None:
    """Back where it was: the branch at its tip, no merge in progress, nothing dirty."""
    assert _sha(repo["project"], _BRANCH) == tip
    assert _git(repo["writer"], "status", "--porcelain", "--untracked-files=all") == ""
    assert not (Path(_git(repo["writer"], "rev-parse", "--git-dir")) / "MERGE_HEAD").exists()


def test_a_resolved_conflict_is_a_merge_commit_and_the_task_goes_back_to_review(
    tmp_path, repo, monkeypatch,
) -> None:
    cfg = _config(tmp_path, repo["projects"])
    tip, base = _diverge(repo, conflict=True)
    _in_conflict(cfg)
    seen = _gate(monkeypatch, [])
    calls = _phases(monkeypatch, repo, {"integrador": _resolve(), "revisor": _approve})

    outcome = _resolve_conflict(cfg)

    assert outcome.resolved, outcome.detail
    head = _sha(repo["project"], _BRANCH)
    parents = _git(repo["project"], "rev-list", "--parents", "-n", "1", head).split()
    # A merge, never a rebase: the task's commit and the base are its parents.
    assert parents[1:] == [tip, base]
    assert _sha(repo["project"], "main") == base
    assert (repo["writer"] / "shared.txt").read_text() == "one\nTASK\nMAIN\nthree\n"
    # The gate ran on the resolution, the revisor reviewed it, and an edit
    # inside the conflicting hunk needs no auditor.
    assert [s["text"] for s in seen] == ["one\nTASK\nMAIN\nthree\n"]
    assert [c["role"] for c in calls] == ["integrador", "revisor"]
    assert "shared.txt" in calls[0]["prompt"]
    assert "remerge-diff" in calls[1]["prompt"]
    task = _task(cfg)
    assert task.status == "awaiting_review"
    assert "kept both lines" in task.body
    assert "does not carry over" in task.body


def test_the_integrador_runs_on_the_implementadors_model_unless_configured(
    tmp_path, repo, monkeypatch,
) -> None:
    cfg = _config(tmp_path, repo["projects"], role_model={"implementador": "sonnet-x"})
    _diverge(repo, conflict=True)
    _in_conflict(cfg)
    _gate(monkeypatch, [])
    calls = _phases(monkeypatch, repo, {"integrador": _resolve(), "revisor": _approve})

    _resolve_conflict(cfg)

    assert calls[0]["model"] == "sonnet-x"


def test_a_blocked_integrador_leaves_the_task_in_conflict_and_the_branch_alone(
    tmp_path, repo, monkeypatch,
) -> None:
    cfg = _config(tmp_path, repo["projects"])
    tip, _ = _diverge(repo, conflict=True)
    _in_conflict(cfg)
    _gate(monkeypatch, [])

    def blocked(writer):
        return {
            "status": "blocked", "resolutions": [],
            "blocked_reason": "main renamed the setting this task reads",
        }

    calls = _phases(monkeypatch, repo, {"integrador": blocked, "revisor": _approve})

    outcome = _resolve_conflict(cfg)

    assert not outcome.resolved
    assert [c["role"] for c in calls] == ["integrador"]
    _writer_untouched(repo, tip)
    task = _task(cfg)
    assert task.status == dispatcher_mod.CONFLICT_STATUS
    assert "main renamed the setting this task reads" in task.body
    assert "request changes" in task.body


def test_a_write_outside_the_conflicting_files_fails_the_phase(
    tmp_path, repo, monkeypatch,
) -> None:
    cfg = _config(tmp_path, repo["projects"])
    tip, _ = _diverge(repo, conflict=True)
    _in_conflict(cfg)
    _gate(monkeypatch, [])

    def overreach(writer):
        (Path(writer) / "shared.txt").write_text("one\nTASK\nMAIN\nthree\n")
        (Path(writer) / "extra.txt").write_text("not asked for\n")
        return _resolved()

    calls = _phases(monkeypatch, repo, {"integrador": overreach, "revisor": _approve})

    outcome = _resolve_conflict(cfg)

    assert not outcome.resolved
    assert "extra.txt" in outcome.detail
    assert [c["role"] for c in calls] == ["integrador"]
    _writer_untouched(repo, tip)
    task = _task(cfg)
    assert task.status == dispatcher_mod.CONFLICT_STATUS
    assert "extra.txt" in task.body


def test_markers_left_in_a_file_fail_the_phase(tmp_path, repo, monkeypatch) -> None:
    cfg = _config(tmp_path, repo["projects"])
    tip, _ = _diverge(repo, conflict=True)
    _in_conflict(cfg)
    _gate(monkeypatch, [])

    def half_done(writer):
        return _resolved()

    _phases(monkeypatch, repo, {"integrador": half_done, "revisor": _approve})

    outcome = _resolve_conflict(cfg)

    assert not outcome.resolved
    assert "marker" in outcome.detail
    _writer_untouched(repo, tip)
    assert _task(cfg).status == dispatcher_mod.CONFLICT_STATUS


def test_a_resolution_the_tests_reject_is_undone(tmp_path, repo, monkeypatch) -> None:
    cfg = _config(tmp_path, repo["projects"])
    tip, _ = _diverge(repo, conflict=True)
    _in_conflict(cfg)
    _gate(monkeypatch, _red())
    calls = _phases(monkeypatch, repo, {"integrador": _resolve(), "revisor": _approve})

    outcome = _resolve_conflict(cfg)

    assert not outcome.resolved
    assert [c["role"] for c in calls] == ["integrador"]
    _writer_untouched(repo, tip)
    task = _task(cfg)
    assert task.status == dispatcher_mod.CONFLICT_STATUS
    assert "`tests-run`" in task.body


def test_a_revisor_that_requests_changes_undoes_the_resolution(
    tmp_path, repo, monkeypatch,
) -> None:
    cfg = _config(tmp_path, repo["projects"])
    tip, _ = _diverge(repo, conflict=True)
    _in_conflict(cfg)
    _gate(monkeypatch, [])

    def changes(writer):
        return {"status": "done", "verdict": "CHANGES_REQUESTED",
                "findings": ["dropped the base's line"]}

    _phases(monkeypatch, repo, {"integrador": _resolve(), "revisor": changes})

    outcome = _resolve_conflict(cfg)

    assert not outcome.resolved
    _writer_untouched(repo, tip)
    assert _task(cfg).status == dispatcher_mod.CONFLICT_STATUS


def test_an_edit_outside_the_conflicting_hunks_runs_the_auditor(
    tmp_path, repo, monkeypatch,
) -> None:
    cfg = _config(tmp_path, repo["projects"])
    _diverge(repo, conflict=True)
    _in_conflict(cfg)
    _gate(monkeypatch, [])
    calls = _phases(monkeypatch, repo, {
        "integrador": _resolve("ONE\nTASK\nMAIN\nthree\n"),
        "revisor": _approve,
        "auditor": lambda writer: {"status": "done"},
    })

    outcome = _resolve_conflict(cfg)

    assert outcome.resolved, outcome.detail
    assert [c["role"] for c in calls] == ["integrador", "revisor", "auditor"]


def test_a_red_gate_on_a_clean_merge_hands_the_named_files_to_the_integrador(
    tmp_path, repo, monkeypatch,
) -> None:
    cfg = _config(tmp_path, repo["projects"])
    tip, base = _diverge(repo, conflict=False)
    _in_conflict(cfg, why="the test gate went red on main merged in")
    seen = _gate(monkeypatch, _red("`pytest` failed (exit 1): shared.txt:2 assert"), [])
    calls = _phases(monkeypatch, repo, {
        "integrador": _resolve("one\nTASK fixed\nthree\n"),
        "revisor": _approve,
        "auditor": lambda writer: {"status": "done"},
    })

    outcome = _resolve_conflict(cfg)

    assert outcome.resolved, outcome.detail
    assert len(seen) == 2
    head = _sha(repo["project"], _BRANCH)
    parents = _git(repo["project"], "rev-list", "--parents", "-n", "1", head).split()
    assert parents[1:] == [tip, base]
    assert (repo["writer"] / "other.txt").read_text() == "from main\n"
    # A fix the failure asked for is not a resolution of a hunk: audited.
    assert [c["role"] for c in calls] == ["integrador", "revisor", "auditor"]
    assert "shared.txt" in calls[0]["prompt"]
    assert _task(cfg).status == "awaiting_review"


def test_a_red_gate_fix_may_not_touch_a_file_the_failure_does_not_name(
    tmp_path, repo, monkeypatch,
) -> None:
    cfg = _config(tmp_path, repo["projects"])
    tip, _ = _diverge(repo, conflict=False)
    _in_conflict(cfg, why="the test gate went red on main merged in")
    _gate(monkeypatch, _red("`pytest` failed (exit 1): shared.txt:2 assert"), [])

    def wrong_file(writer):
        (Path(writer) / "other.txt").write_text("changed\n")
        return _resolved()

    _phases(monkeypatch, repo, {"integrador": wrong_file, "revisor": _approve})

    outcome = _resolve_conflict(cfg)

    assert not outcome.resolved
    assert "other.txt" in outcome.detail
    _writer_untouched(repo, tip)


def test_a_merge_that_is_now_clean_and_green_needs_no_phase(
    tmp_path, repo, monkeypatch,
) -> None:
    cfg = _config(tmp_path, repo["projects"])
    tip, base = _diverge(repo, conflict=False)
    _in_conflict(cfg)
    _gate(monkeypatch, [])
    calls = _phases(monkeypatch, repo, {})

    outcome = _resolve_conflict(cfg)

    assert outcome.resolved, outcome.detail
    assert calls == []
    head = _sha(repo["project"], _BRANCH)
    parents = _git(repo["project"], "rev-list", "--parents", "-n", "1", head).split()
    assert parents[1:] == [tip, base]
    assert _task(cfg).status == "awaiting_review"


def test_a_task_that_is_not_in_conflict_is_refused(tmp_path, repo, monkeypatch) -> None:
    cfg = _config(tmp_path, repo["projects"])
    tip, _ = _diverge(repo, conflict=True)
    handoff(cfg.hive_tasks_dir, "task-1", "awaiting_review", "## auditor\n\napproved")
    calls = _phases(monkeypatch, repo, {})

    outcome = _resolve_conflict(cfg)

    assert not outcome.resolved
    assert "conflict" in outcome.detail
    assert calls == []
    _writer_untouched(repo, tip)


def test_a_dirty_writer_checkout_is_refused(tmp_path, repo, monkeypatch) -> None:
    cfg = _config(tmp_path, repo["projects"])
    _diverge(repo, conflict=True)
    _in_conflict(cfg)
    (repo["writer"] / "left.txt").write_text("uncommitted\n")
    calls = _phases(monkeypatch, repo, {})

    outcome = _resolve_conflict(cfg)

    assert not outcome.resolved
    assert "uncommitted" in outcome.detail
    assert calls == []
    assert (repo["writer"] / "left.txt").exists()


# The pieces around it: the outside-hunk check, the role's schema and prompt,
# its worktree, its model, and the CLI verb.


def test_the_outside_hunk_check_reads_the_markers() -> None:
    marked = "a\n<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> main\nb\n"

    assert not dispatcher_mod._edits_outside_hunks(marked, "a\nx\ny\nb\n")
    assert not dispatcher_mod._edits_outside_hunks(marked, "a\nb\n")
    assert dispatcher_mod._edits_outside_hunks(marked, "A\nx\nb\n")
    assert dispatcher_mod._edits_outside_hunks(marked, "a\nx\nB\n")
    two = "a\n<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> main\nmid\n<<<<<<< HEAD\np\n=======\nq\n>>>>>>> main\nz\n"
    assert not dispatcher_mod._edits_outside_hunks(two, "a\nx\nmid\nq\nz\n")
    assert dispatcher_mod._edits_outside_hunks(two, "a\nx\nMID\nq\nz\n")
    # No markers to anchor to (a file the failure named, a delete conflict).
    assert dispatcher_mod._edits_outside_hunks(None, "a\n")
    assert dispatcher_mod._edits_outside_hunks("a\nb\n", "a\nb\nc\n")


def test_the_integrador_reports_how_each_file_was_resolved() -> None:
    schema = handoff_mod.schema_for("integrador")

    assert schema is not None
    assert {"resolutions", "blocked_reason"} <= set(schema["required"])
    item = schema["properties"]["resolutions"]["items"]
    assert set(item["required"]) == {"path", "how"}
    rendered = handoff_mod.render({
        "status": "blocked",
        "resolutions": [{"path": "a.py", "how": "kept both"}],
        "blocked_reason": "the two sides disagree on the design",
    })
    assert "`a.py` — kept both" in rendered
    assert "the two sides disagree on the design" in rendered
    assert handoff_mod.budget_for("integrador") < handoff_mod.budget_for("implementador")


def test_the_integrador_prompt_names_its_limits() -> None:
    duties = project_docs.duties("integrador", "task-1")

    assert "blocked" in duties
    assert "rebase" in duties
    assert "resolutions" in duties


def test_the_integrador_works_in_the_writers_checkout_and_never_commits_itself(
    monkeypatch,
) -> None:
    added = []
    monkeypatch.setattr(docker_exec_mod, "ensure_worktrees_ignored", lambda c, p: True)
    monkeypatch.setattr(
        docker_exec_mod, "_add_writer_worktree",
        lambda c, p, t, b: added.append(b) or "/writer",
    )

    path = docker_exec_mod.create_worktree("c", "/projects", "s", "t", "integrador")

    assert path == "/writer" and added == ["agent/task/t"]
    assert docker_exec_mod.INTEGRADOR_ROLE not in docker_exec_mod.WRITER_ROLES


def test_the_integrador_model_falls_back_to_the_implementadors(tmp_path) -> None:
    cfg = _config(tmp_path, str(tmp_path), role_model={"implementador": "impl-model"})
    assert cfg.model_for("integrador") == "impl-model"
    own = _config(tmp_path, str(tmp_path), role_model={
        "implementador": "impl-model", "integrador": "int-model",
    })
    assert own.model_for("integrador") == "int-model"
    assert _config(tmp_path, str(tmp_path)).model_for("integrador") == "opus"


def test_role_model_and_role_effort_accept_the_integrador(tmp_path) -> None:
    from dispatcher import config as config_mod

    assert config_mod._load_role_model({"role_model": {"integrador": "m"}}) == {"integrador": "m"}
    assert config_mod._load_role_effort({"role_effort": {"integrador": "low"}}) == {
        "integrador": "low",
    }


def test_resolve_conflict_cli_runs_the_resolution(tmp_path, monkeypatch, capsys) -> None:
    from dispatcher import cli as cli_mod

    cfg = _config(tmp_path, str(tmp_path / "projects"))
    seen = []
    monkeypatch.setattr(cli_mod, "load_config", lambda path: cfg)
    monkeypatch.setattr(cli_mod, "cleanup_container", lambda c: _CONTAINER)
    monkeypatch.setattr(cli_mod.docker_exec, "read_owner", lambda *a, **k: None)
    monkeypatch.setattr(cli_mod.docker_exec, "restore_owner", lambda *a, **k: None)
    monkeypatch.setattr(
        cli_mod, "resolve_conflict",
        lambda c, container, task_id, slug: seen.append((container, task_id, slug))
        or dispatcher_mod.ResolveOutcome(False, "task T-1: blocked"),
    )
    monkeypatch.setattr(
        sys, "argv",
        ["dispatch", "--config", "config.yaml", "resolve-conflict", "--task-id", "T-1", "--project", "myproj"],
    )

    with pytest.raises(SystemExit) as exc:
        cli_mod.main()

    assert exc.value.code == 1
    assert seen == [(_CONTAINER, "T-1", "myproj")]
    assert "blocked" in capsys.readouterr().err
