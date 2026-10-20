import datetime as dt
import json
import os
import time
from pathlib import Path

import pytest

import dispatcher.dispatcher as dispatcher_mod
from dispatcher import board_mcp, debt, gates, handoff, learnings, project_docs, role_skills, subagents, tasks
from dispatcher.config import PHASE_ROLES, AccountConfig, Config
from dispatcher.context_transfer import (
    LockHeldError,
    is_lock_expired,
    list_task_ids,
    read_handoff,
    read_resolved_debt,
    read_task_file,
    save_handoff,
    scratch_dir,
    task_file_path,
    usage_log_path,
    write_task_file,
)
from dispatcher.tasks import acquire_lock, set_resolved_debt
from dispatcher.docker_exec import ClaudeResult
from dispatcher.state_machine import (
    AccountState,
    get_busy_since,
    get_current_task,
    get_last_probe,
    get_rate_limited_at,
    get_state,
    record_rate_limit,
    set_state,
)


@pytest.fixture(autouse=True)
def _phases_may_end_in_prose(monkeypatch):
    """Most fakes here answer "phase done" with no structured return, which a
    real phase now gets resumed for and then failed on. That rule has its own
    tests in test_handoff_resume.py; the tests here are about everything else,
    so the check passes their results through as they are."""
    monkeypatch.setattr(
        dispatcher_mod, "_resume_missing_handoff",
        lambda cfg, container, workdir, role, result, *args: result,
    )


# run_task_cycle refuses to dispatch a task with no description (it would
# spend four phases of quota on roles told nothing but an id), so every
# cycle test has to supply one.
_GOAL = "Add a /healthz endpoint that returns 200."
_DESCRIPTION = f"## Goal\n\n{_GOAL}\n\n## Done when\n\nGET /healthz answers 200.\n"


def _make_config(tmp_path, **overrides):
    defaults = dict(
        accounts=[AccountConfig(name="cuenta1", container="agent-cuenta1")],
        quota_threshold_pct=90,
        quota_cooldown_seconds=1800,
        heartbeat_ttl_seconds=120,
        heartbeat_interval_seconds=1,
        projects_root=str(tmp_path / "projects"),
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
        merge_on_done=False,
        mapping_enabled=False,
        mapping_model="sonnet",
        mapping_max_turns=40,
        # Off here although it ships on: the gates reach into the worktree
        # through a real `docker exec`, which every other fake in this file
        # exists to avoid. The tests that want them turn them on and fake
        # `gates.run` with them.
        gates_enabled=False,
        gates_test_timeout_seconds=900,
    )
    defaults.update(overrides)
    return Config(**defaults)


class _FakeGit:
    """Records the git/ownership calls dispatch_phase makes through docker_exec.

    Those three shell out to a real `docker exec`, so without this every
    dispatch test would go looking for a live container.
    """

    owner = "1000:1000"

    #: What a phase's worktree is holding when it returns. Clean is the
    #: ordinary case, and the only one every other test cares about.
    dirty = ()

    outcome = dispatcher_mod.docker_exec.MergeOutcome(
        dispatcher_mod.docker_exec.MERGED, "main", "merged agent/task/task-1 into main"
    )

    def __init__(self):
        self.commits = []
        self.status_checks = []
        self.restored = []
        self.review_cleanups = []
        self.merges = []

    def commit_worktree(
        self, container, workdir, message, author_name, author_email, paths=None,
        excludes=None,
    ):
        self.commits.append(
            dict(
                container=container,
                workdir=workdir,
                message=message,
                author_name=author_name,
                author_email=author_email,
                paths=paths,
                excludes=excludes,
            )
        )
        return True

    def dirty_paths(self, container, workdir):
        self.status_checks.append((container, workdir))
        return list(self.dirty)

    #: What `left_behind` finds after a commit, and the patch `diff_of` makes
    #: of it. Empty is the ordinary case: nothing is recorded.
    left_outside = ()
    left_excluded = ()
    patch = "diff --git a/x b/x\n"
    diff_error = None

    def left_behind(self, container, workdir, excludes=None):
        return dispatcher_mod.docker_exec.LeftBehind(
            outside=list(self.left_outside), excluded=list(self.left_excluded),
        )

    def diff_of(self, container, workdir, paths):
        if self.diff_error is not None:
            raise self.diff_error
        return self.patch

    def read_owner(self, container, path):
        return self.owner

    def restore_owner(self, container, path, owner):
        self.restored.append((path, owner))

    def remove_review_worktrees(self, container, projects_root, slug, task_id):
        self.review_cleanups.append((container, projects_root, slug, task_id))
        return ["revisor", "auditor"]

    def merge_task_branch(self, container, projects_root, slug, task_id):
        self.merges.append((container, projects_root, slug, task_id))
        return self.outcome

    #: What pushing the base comes to. Pushed is the ordinary case; a test
    #: that wants the refusal, or a push that raises, sets one of these.
    push_outcome = dispatcher_mod.docker_exec.PushOutcome(
        dispatcher_mod.docker_exec.PUSHED, "main", "pushed main to origin/main at ccccccc"
    )
    push_error = None

    def push_base(self, container, projects_root, slug, credentials_path=None, timeout=None, keys_dir=None):
        self.pushes = getattr(self, "pushes", [])
        self.pushes.append((container, projects_root, slug))
        if self.push_error is not None:
            raise self.push_error
        return self.push_outcome

    #: What merging the base into the task branch comes to. Up to date is the
    #: ordinary case: nothing to merge, nothing to gate, the merge goes on.
    premerge = dispatcher_mod.docker_exec.PreMergeOutcome(
        dispatcher_mod.docker_exec.UP_TO_DATE, "main", "b" * 40, "a" * 40,
        "agent/task/task-1 already holds main",
        merged_tip="a" * 40,
    )

    def premerge_base(self, container, projects_root, slug, task_id, author_name=None, author_email=None):
        self.premerges = getattr(self, "premerges", [])
        self.premerges.append((container, projects_root, slug, task_id))
        return self.premerge

    def adopt_premerge(self, container, projects_root, slug, task_id, outcome):
        self.adopted = getattr(self, "adopted", [])
        self.adopted.append(outcome)

    def drop_premerge_worktree(self, container, projects_root, slug, task_id):
        self.premerge_drops = getattr(self, "premerge_drops", [])
        self.premerge_drops.append(task_id)

    def move_staged_run_skills(self, container, workdir):
        self.staged_moves = getattr(self, "staged_moves", [])
        self.staged_moves.append((container, workdir))
        return ()


@pytest.fixture(autouse=True)
def fake_git(monkeypatch):
    fake = _FakeGit()
    monkeypatch.setattr(dispatcher_mod.docker_exec, "commit_worktree", fake.commit_worktree)
    monkeypatch.setattr(dispatcher_mod.docker_exec, "dirty_paths", fake.dirty_paths)
    monkeypatch.setattr(dispatcher_mod.docker_exec, "left_behind", fake.left_behind)
    monkeypatch.setattr(dispatcher_mod.docker_exec, "diff_of", fake.diff_of)
    monkeypatch.setattr(dispatcher_mod.docker_exec, "read_owner", fake.read_owner)
    monkeypatch.setattr(dispatcher_mod.docker_exec, "restore_owner", fake.restore_owner)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "remove_review_worktrees", fake.remove_review_worktrees
    )
    monkeypatch.setattr(dispatcher_mod.docker_exec, "merge_task_branch", fake.merge_task_branch)
    monkeypatch.setattr(dispatcher_mod.docker_exec, "push_base", fake.push_base)
    monkeypatch.setattr(dispatcher_mod.docker_exec, "premerge_base", fake.premerge_base)
    monkeypatch.setattr(dispatcher_mod.docker_exec, "adopt_premerge", fake.adopt_premerge)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "drop_premerge_worktree", fake.drop_premerge_worktree
    )
    monkeypatch.setattr(
        dispatcher_mod.project_docs, "move_staged_run_skills", fake.move_staged_run_skills
    )
    return fake


def test_dispatch_phase_success(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        if "usage" in prompt.lower():
            return ClaudeResult(
                session_id=None,
                result_text=(
                    "Current session: 10% used · resets later\n"
                    "Current week (all models): 10% used · resets later"
                ),
                raw={},
            )
        return ClaudeResult(session_id="sess-1", result_text="phase done", raw={"is_error": False})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    assert result.success is True
    assert result.account == "cuenta1"
    assert result.session_id == "sess-1"
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.IDLE


def test_dispatch_phase_passes_configured_timeout_to_phase_exec(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path, phase_timeout_seconds=999)
    captured = {}

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        if "usage" in prompt.lower():
            return ClaudeResult(
                session_id=None,
                result_text=(
                    "Current session: 10% used · resets later\n"
                    "Current week (all models): 10% used · resets later"
                ),
                raw={},
            )
        captured["timeout_seconds"] = timeout_seconds
        return ClaudeResult(session_id="sess-1", result_text="phase done", raw={"is_error": False})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    assert captured["timeout_seconds"] == 999


def test_dispatch_phase_delivers_the_role_skills_to_the_phase_call(tmp_path, monkeypatch) -> None:
    """The set is chosen here, from the role, so that every dispatch path gets
    the same one. The usage probe is a separate call and must stay bare: the
    skills are always-on cost, and the probe reads a number."""
    cfg = _make_config(tmp_path)
    captured = {}

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        if "usage" in prompt.lower():
            captured["probe"] = kwargs
            return ClaudeResult(
                session_id=None,
                result_text=(
                    "Current session: 10% used · resets later\n"
                    "Current week (all models): 10% used · resets later"
                ),
                raw={},
            )
        captured["phase"] = kwargs
        return ClaudeResult(session_id="sess-1", result_text="phase done", raw={"is_error": False})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert captured["phase"]["plugin_dirs"] == role_skills.plugin_dirs("revisor")
    assert captured["phase"]["append_system_prompt"].startswith(role_skills.system_prompt("revisor"))
    assert captured["phase"]["append_system_prompt"].endswith(handoff.UNATTENDED_PHASE)
    assert "blocking-review" in captured["phase"]["append_system_prompt"]
    assert not captured["probe"].get("plugin_dirs")
    assert not captured["probe"].get("append_system_prompt")


_USAGE_TEXT = (
    "Current session: 10% used · resets later\n"
    "Current week (all models): 10% used · resets later"
)

#: Over the revisor's budget by a wide margin, and over it in the payload
#: rather than in the prose — the prose is what the CLI replaces with a
#: placeholder once a schema is in play. Sized off the budget rather than
#: written down, so that moving a budget cannot quietly turn these tests into
#: tests of a handoff that now fits.
_FAT_HANDOFF = {
    "status": "complete", "verdict": "APPROVED",
    "risks": ["r" * (handoff.budget_for("revisor") + 1024)],
}
_LEAN_HANDOFF = {"status": "complete", "verdict": "APPROVED", "risks": ["see docs/adr/0007.md#risks"]}


def _phase_recorder(monkeypatch, phase_results):
    """Answers the usage probe, then hands out `phase_results` in order.

    Returns the list of kwargs each phase call was made with, so a test can
    tell the first call from the retry by its `resume_session_id`.
    """
    calls = []
    pending = list(phase_results)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        if "usage" in prompt.lower():
            return ClaudeResult(session_id=None, result_text=_USAGE_TEXT, raw={})
        calls.append(dict(prompt=prompt, resume_session_id=resume_session_id, **kwargs))
        return pending.pop(0)

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )
    return calls

#: A real session and a real subagent, from the D1 run written up in
#: docs/ROADMAP.md. The session ids have to be uuid-shaped: everywhere else in
#: this file `sess-1` is enough, but the harvest refuses anything that is not
#: a session id, so a test using `sess-1` would prove nothing.
_SESSION_UUID = "85e10326-e62c-48de-be2f-9a7c92741789"
_OTHER_SESSION_UUID = "23ea98a7-6dc7-4ed6-abb9-9e73c3dfc6b4"
_THIRD_SESSION_UUID = "7c3f1e42-5d6a-4b8c-9e01-2f3a4b5c6d7e"
_SUBAGENT = subagents.Subagent(
    id="a0af9044f9cb2c8db", description="slow count", agent_type="general-purpose"
)


def _fake_subagents(monkeypatch, agents=(_SUBAGENT,)):
    """Disk, with `agents` on it. Returns what each lookup asked for."""
    asked = []

    def fake_of_session(container, session_id):
        asked.append((container, session_id))
        return list(agents) if session_id else []

    monkeypatch.setattr(dispatcher_mod.subagents, "of_session", fake_of_session)
    return asked


def test_dispatch_phase_asks_the_phase_for_the_roles_handoff_schema(tmp_path, monkeypatch) -> None:
    """The schema is chosen here, from the role, for the same reason the
    skills are. The usage probe stays bare: it reads a number, and a schema
    is charged on the call that carries it."""
    cfg = _make_config(tmp_path)
    probe = {}
    calls = []

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        if "usage" in prompt.lower():
            probe.update(kwargs)
            return ClaudeResult(session_id=None, result_text=_USAGE_TEXT, raw={})
        calls.append(kwargs)
        return ClaudeResult(session_id="sess-1", result_text="ok", raw={"is_error": False})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert calls[0]["json_schema"] == handoff.schema_for("revisor")
    assert "verdict" in calls[0]["json_schema"]["properties"]
    assert probe.get("json_schema") is None


def test_dispatch_phase_lets_the_phase_write_and_read_where_it_was_sent(tmp_path, monkeypatch) -> None:
    """The two halves of one measured failure (43 of 62 tool calls denied, not
    one file written): with no permission mode there is nobody to answer a
    prompt so the CLI denies, and with no extra directory the file tools
    refuse the task file and the learnings the role prompt sends the phase to.
    The probe gets neither — it runs a built-in, uses no tools and touches no
    files."""
    cfg = _make_config(tmp_path)
    probe = {}
    calls = []

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        if "usage" in prompt.lower():
            probe.update(kwargs)
            return ClaudeResult(session_id=None, result_text=_USAGE_TEXT, raw={})
        calls.append(kwargs)
        return ClaudeResult(session_id="sess-1", result_text="ok", raw={"is_error": False})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert calls[0]["permission_mode"] == "acceptEdits"
    assert calls[0]["add_dirs"] == [str(tmp_path)]
    assert probe.get("permission_mode") is None
    assert not probe.get("add_dirs")


def test_the_opened_directory_is_the_hive_root_and_not_the_tasks_dir(tmp_path, monkeypatch) -> None:
    """`hive_tasks_dir` points at the tasks folder, but a role is also told to
    read the learnings index and to file a trap in its inbox — siblings of it.
    Opening their shared parent covers all three, and stops there: reaching
    another phase's worktree is what the worktrees exist to prevent."""
    cfg = _make_config(tmp_path, hive_tasks_dir=str(tmp_path / "hive" / "tasks"))
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="ok", raw={"is_error": False}),
    ])

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert calls[0]["add_dirs"] == [str(tmp_path / "hive")]
    assert calls[0]["add_dirs"] != [cfg.hive_tasks_dir]
    assert learnings.root_dir(cfg.hive_tasks_dir).startswith(calls[0]["add_dirs"][0])


def test_a_null_permission_mode_reaches_the_phase_as_no_mode(tmp_path, monkeypatch) -> None:
    """The config key is what makes the unflagged call reproducible, so it has
    to survive the trip: a default the dispatcher re-applies on the way past
    would be a default nobody can measure against."""
    cfg = _make_config(tmp_path, permission_mode=None)
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="ok", raw={"is_error": False}),
    ])

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert calls[0]["permission_mode"] is None


def test_dispatch_phase_hands_the_phase_the_commands_it_may_run(tmp_path, monkeypatch) -> None:
    """The third of the measured denials, and the one the mode does not cover:
    `acceptEdits` allows the file tools but refuses to run a program, so every
    `node --test` a phase attempted was denied and no phase could prove its
    own work. The phase cannot lift this itself — writing its own
    `.claude/settings.local.json` is refused too — so it arrives as a flag.
    The probe gets nothing: it runs a built-in and uses no tools."""
    cfg = _make_config(tmp_path, allowed_tools=["Bash(node --test*)"])
    probe = {}
    calls = []

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        if "usage" in prompt.lower():
            probe.update(kwargs)
            return ClaudeResult(session_id=None, result_text=_USAGE_TEXT, raw={})
        calls.append(kwargs)
        return ClaudeResult(session_id="sess-1", result_text="ok", raw={"is_error": False})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert calls[0]["allowed_tools"] == ["Bash(node --test*)"]
    assert not probe.get("allowed_tools")


def test_a_harness_that_named_no_commands_grants_none(tmp_path, monkeypatch) -> None:
    """The default has to reach the phase as an empty list rather than as some
    convenience set the dispatcher fills in: a grant nobody wrote down is a
    grant nobody can audit, and this one lets a phase run programs."""
    cfg = _make_config(tmp_path)
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="ok", raw={"is_error": False}),
    ])

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert calls[0]["allowed_tools"] == []


def test_dispatch_phase_returns_the_parsed_handoff(tmp_path, monkeypatch) -> None:
    """`result_text` is the CLI's placeholder once a schema validated; the
    payload is the phase's actual answer, so it travels on the result."""
    cfg = _make_config(tmp_path)
    _phase_recorder(monkeypatch, [
        ClaudeResult(
            session_id="sess-1",
            result_text="Structured output provided successfully",
            raw={"is_error": False, "structured_output": _LEAN_HANDOFF},
        ),
    ])

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert result.success is True
    assert result.handoff == _LEAN_HANDOFF


def test_dispatch_phase_handoff_is_none_when_the_phase_answered_in_prose(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="I had a look and it is fine", raw={"is_error": False}),
    ])

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert result.handoff is None
    assert result.result_text == "I had a look and it is fine"


def _budget_warnings(caplog):
    """The WARNINGs about a budget, and only those.

    A phase logs other things at WARNING — a session id the subagent lookup
    does not recognise, for one — and a test about what the budget says should
    not fail or pass on those.
    """
    return [
        r for r in caplog.records
        if r.levelname == "WARNING" and "budget" in r.getMessage()
    ]


def test_dispatch_phase_asks_once_for_a_shorter_handoff_when_over_budget(tmp_path, monkeypatch) -> None:
    """The retry resumes the same session — it is a rewrite of an answer that
    session already has — and carries the schema but not the skills: the
    session read those on the first call, and this one does no role work."""
    cfg = _make_config(tmp_path)
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _FAT_HANDOFF}),
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF}),
    ])

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert len(calls) == 2, "over budget should cost exactly one resume, never a loop"
    retry = calls[1]
    assert retry["resume_session_id"] == "sess-1"
    assert retry["json_schema"] == handoff.schema_for("revisor")
    assert not retry.get("plugin_dirs")
    assert not retry.get("append_system_prompt")
    assert str(handoff.budget_for("revisor")) in retry["prompt"]
    assert result.handoff == _LEAN_HANDOFF


def test_the_shrink_retry_restates_the_permission_flags(tmp_path, monkeypatch) -> None:
    """A `--resume` is a new `claude` process and inherits no flags from the
    one it resumes. The retry rewrites the handoff, which is a write — so
    without them restated it would be denied for exactly the same reason the
    first call would have been."""
    cfg = _make_config(tmp_path)
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _FAT_HANDOFF}),
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF}),
    ])

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert calls[1]["resume_session_id"] == "sess-1"
    assert calls[1]["permission_mode"] == "acceptEdits"
    assert calls[1]["add_dirs"] == [str(tmp_path)]


def test_dispatch_phase_takes_the_retry_even_if_it_is_still_over_budget(tmp_path, monkeypatch) -> None:
    """Shorter is the win; exactly-in-budget is not worth a third call."""
    cfg = _make_config(tmp_path)
    still_fat = {
        "status": "complete", "verdict": "APPROVED",
        "risks": ["r" * (handoff.budget_for("revisor") + 256)],
    }
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _FAT_HANDOFF}),
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": still_fat}),
    ])

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert len(calls) == 2
    assert result.handoff == still_fat


def test_dispatch_phase_keeps_the_long_handoff_when_the_retry_fails(tmp_path, monkeypatch) -> None:
    """A phase that did its work and answered at length has not failed. The
    retry is a formatting errand: losing it costs readability, and failing the
    phase over it would throw the work away and fail the account over."""
    cfg = _make_config(tmp_path)
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _FAT_HANDOFF}),
        ClaudeResult(session_id=None, result_text="boom", raw={}),
    ])

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert len(calls) == 2
    assert result.success is True
    assert result.handoff == _FAT_HANDOFF


def test_dispatch_phase_keeps_the_long_handoff_when_the_retry_is_rate_limited(tmp_path, monkeypatch) -> None:
    """Same reasoning, and one more: a rate-limited retry must not be read as
    the phase itself hitting the limit, or the account would cool down over a
    call that was only reformatting."""
    cfg = _make_config(tmp_path)
    _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _FAT_HANDOFF}),
        ClaudeResult(session_id="sess-1", result_text="Claude AI usage limit reached", raw={"is_error": True}),
    ])

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert result.success is True
    assert result.handoff == _FAT_HANDOFF
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.IDLE


def test_dispatch_phase_does_not_retry_without_a_session_to_resume(tmp_path, monkeypatch) -> None:
    """`--resume` needs a session id. Without one there is nothing to ask, and
    the clamp in the handoff body still bounds what reaches the task file."""
    cfg = _make_config(tmp_path)
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id=None, result_text="", raw={"is_error": False, "structured_output": _FAT_HANDOFF}),
    ])

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert len(calls) == 1
    assert result.handoff == _FAT_HANDOFF


def test_dispatch_phase_does_not_retry_a_handoff_within_budget(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF}),
    ])

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert len(calls) == 1


def test_dispatch_phase_does_not_retry_a_failed_phase(tmp_path, monkeypatch) -> None:
    """A crash has no handoff to shorten, and the phase is about to fail over
    to another account — spending a call on its wording first would delay
    that for nothing."""
    cfg = _make_config(tmp_path)
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="x" * 9000, raw={}),
    ])

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert len(calls) == 1
    assert result.success is False


def test_dispatch_phase_logs_an_overage_it_cannot_retry(tmp_path, monkeypatch, caplog) -> None:
    """Nothing can be done about this one, and that is why it warns: the long
    handoff goes into the task file for every later phase to read."""
    cfg = _make_config(tmp_path)
    _phase_recorder(monkeypatch, [
        ClaudeResult(session_id=None, result_text="", raw={"is_error": False, "structured_output": _FAT_HANDOFF}),
    ])

    with caplog.at_level("WARNING"):
        dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    budget = handoff.budget_for("revisor")
    assert any(
        f"over its {budget}-byte budget" in r.getMessage() and "no session to resume" in r.getMessage()
        for r in caplog.records
    )


def test_dispatch_phase_does_not_pay_for_a_resume_over_a_small_overage(tmp_path, monkeypatch, caplog) -> None:
    """A `--resume` costs about what a small phase costs. Half a line over is
    not worth one, and the handoff is taken as it came."""
    cfg = _make_config(tmp_path)
    budget = handoff.budget_for("revisor")
    # A line and a half over, which is the size of the smallest overages on
    # record: 46, 67, 170 and 280 bytes across four dispatched runs.
    barely = {"status": "complete", "verdict": "APPROVED", "risks": ["r" * (budget + 128)]}
    overage = handoff.over_budget("revisor", ClaudeResult(
        session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": barely},
    ))
    assert overage, "the fixture has to actually be over budget for the test to mean anything"
    assert not handoff.worth_shrinking("revisor", overage), "and it has to be inside the margin"
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": barely}),
    ])

    with caplog.at_level("INFO"):
        result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert len(calls) == 1, "an overage inside the margin should not buy a model call"
    assert result.handoff == barely
    assert any("kept as it is" in r.getMessage() for r in caplog.records)
    assert not _budget_warnings(caplog), (
        "a handoff this close to its budget is not something to warn about"
    )


def test_dispatch_phase_warns_only_about_the_handoff_that_lands(tmp_path, monkeypatch, caplog) -> None:
    """A first draft over budget that the rewrite brings back under it is the
    system working. The size still gets logged, because it is what the budgets
    are tuned from — but at INFO, so a WARNING keeps meaning that something
    over budget went into the task file."""
    cfg = _make_config(tmp_path)
    _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _FAT_HANDOFF}),
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF}),
    ])

    with caplog.at_level("INFO"):
        dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert not _budget_warnings(caplog)
    messages = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    budget = handoff.budget_for("revisor")
    assert any(f"over its {budget}-byte budget" in m for m in messages), "the tuning data survives"
    assert any(f"inside its {budget}-byte budget" in m for m in messages)


def test_dispatch_phase_warns_when_a_rewrite_is_still_over_budget(tmp_path, monkeypatch, caplog) -> None:
    """Asked, answered, still long. That survived an edit, so it is evidence
    about the budget rather than about the role, and it is worth a line."""
    cfg = _make_config(tmp_path)
    budget = handoff.budget_for("revisor")
    still_fat = {
        "status": "complete", "verdict": "APPROVED",
        "risks": ["r" * (budget + 1024)],
    }
    _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _FAT_HANDOFF}),
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": still_fat}),
    ])

    with caplog.at_level("INFO"):
        dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert any(
        r.levelname == "WARNING" and "still" in r.getMessage() and "after a rewrite" in r.getMessage()
        for r in caplog.records
    )


def test_dispatch_phase_warns_when_the_retry_could_not_be_made(tmp_path, monkeypatch, caplog) -> None:
    """The phase still succeeds on the first return, but the thing that went
    in the task file is over budget and nothing edited it."""
    cfg = _make_config(tmp_path)
    _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _FAT_HANDOFF}),
        ClaudeResult(session_id="sess-1", result_text="boom", raw={"is_error": True}),
    ])

    with caplog.at_level("WARNING"):
        result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert result.handoff == _FAT_HANDOFF
    assert any("could not be asked for a shorter handoff" in r.getMessage() for r in caplog.records)


def _gate_recorder(monkeypatch, reports):
    """Hands out `reports` in order and records how the gates were called.

    The gates read a real worktree through `docker exec`, and
    `tests/dispatcher/test_gates.py` is where that part is tested. What
    `dispatch_phase` owns is narrower — who gets gated, when, and what a
    finding is worth — so here the report is a given and only that is left
    visible.
    """
    calls = []
    pending = list(reports)

    def fake_run(container, workdir, project_dir, *, test_timeout_seconds, test_log_path=None):
        calls.append(
            dict(
                container=container,
                workdir=workdir,
                project_dir=project_dir,
                test_timeout_seconds=test_timeout_seconds,
                test_log_path=test_log_path,
            )
        )
        return pending.pop(0) if pending else gates.Report()

    monkeypatch.setattr(dispatcher_mod.gates, "run", fake_run)
    return calls


def _one_finding(level, detail="the test suite is red"):
    return gates.Report([gates.Finding(gate=gates.TESTS_RUN, detail=detail, level=level)])


def test_dispatch_phase_asks_the_implementador_to_answer_the_gates(tmp_path, monkeypatch) -> None:
    """One resume, in the session that just did the work, carrying the
    findings and the role's schema but no skills — that session read those
    already. Then the gates run again: a phase saying it fixed the suite is
    exactly the claim they exist to stop taking on faith."""
    cfg = _make_config(tmp_path, gates_enabled=True)
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF}),
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF}),
    ])
    gate_calls = _gate_recorder(monkeypatch, [_one_finding(gates.ASK), gates.Report()])

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "build the thing")

    assert len(calls) == 2, "findings should cost exactly one resume, never a loop"
    retry = calls[1]
    assert retry["resume_session_id"] == "sess-1"
    assert retry["json_schema"] == handoff.schema_for("implementador")
    assert not retry.get("plugin_dirs")
    assert not retry.get("append_system_prompt")
    assert "the test suite is red" in retry["prompt"]
    assert len(gate_calls) == 2, "the answer has to be checked, not believed"
    assert result.gates.findings == [], "the report that travels is the second one"


def test_dispatch_phase_does_not_resume_when_the_gates_find_nothing(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path, gates_enabled=True)
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="built it", raw={"is_error": False}),
    ])
    gate_calls = _gate_recorder(monkeypatch, [gates.Report()])

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "build the thing")

    assert len(calls) == 1
    assert len(gate_calls) == 1
    assert result.gates is not None and result.gates.render() == ""


def test_dispatch_phase_does_not_spend_a_resume_on_a_note(tmp_path, monkeypatch) -> None:
    """A NOTE is for the revisor to weigh, not for the implementador to
    answer — it rides along under the handoff and costs nothing."""
    cfg = _make_config(tmp_path, gates_enabled=True)
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="built it", raw={"is_error": False}),
    ])
    gate_calls = _gate_recorder(monkeypatch, [_one_finding(gates.NOTE, "an OpenAPI file changed and no doc did")])

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "build the thing")

    assert len(calls) == 1
    assert len(gate_calls) == 1
    assert result.gates.findings[0].level == gates.NOTE


@pytest.mark.parametrize("role", ["arquitecto", "revisor", "auditor"])
def test_dispatch_phase_leaves_the_reading_roles_ungated(tmp_path, monkeypatch, role) -> None:
    """The gates judge code against tests and docs. The roles that write
    neither have nothing to answer for, and a `docker exec` sweep of a
    worktree they only read is time spent for no finding."""
    cfg = _make_config(tmp_path, gates_enabled=True)
    _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="had a look", raw={"is_error": False}),
    ])
    gate_calls = _gate_recorder(monkeypatch, [_one_finding(gates.BLOCKING)])

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", role, "do the thing")

    assert gate_calls == []
    assert result.gates is None


def test_dispatch_phase_does_not_gate_a_phase_that_did_not_finish(tmp_path, monkeypatch) -> None:
    """Gating a crashed phase means grading a worktree nobody wrote in, and
    the phase is about to fail over to another account anyway."""
    cfg = _make_config(tmp_path, gates_enabled=True)
    _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="boom", raw={}),
    ])
    gate_calls = _gate_recorder(monkeypatch, [gates.Report()])

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "build the thing")

    assert result.success is False
    assert gate_calls == []


def test_dispatch_phase_does_not_run_the_gates_when_they_are_turned_off(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path, gates_enabled=False)
    _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="built it", raw={"is_error": False}),
    ])
    gate_calls = _gate_recorder(monkeypatch, [_one_finding(gates.BLOCKING)])

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "build the thing")

    assert gate_calls == []
    assert result.gates is None


def test_dispatch_phase_goes_to_review_ungated_when_a_gate_breaks(tmp_path, monkeypatch, caplog) -> None:
    """A check is a saving, not a dependency. One that throws — no container,
    a git that answers something new — should cost a review call, not the
    task, so the phase returns as if there were no gates at all."""
    cfg = _make_config(tmp_path, gates_enabled=True)
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="built it", raw={"is_error": False}),
    ])

    def explode(*args, **kwargs):
        raise RuntimeError("no such container: agent-cuenta1")

    monkeypatch.setattr(dispatcher_mod.gates, "run", explode)

    with caplog.at_level("ERROR"):
        result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "build the thing")

    assert result.success is True
    assert result.gates is None
    assert len(calls) == 1
    assert any("ungated" in r.getMessage() for r in caplog.records)


def test_dispatch_phase_keeps_the_first_return_when_the_gate_retry_fails(tmp_path, monkeypatch) -> None:
    """A phase that finished with findings against it beats one that answered
    them and crashed, and the report reaches the revisor either way. Nothing
    was written after the failure, so there is nothing to re-check."""
    cfg = _make_config(tmp_path, gates_enabled=True)
    _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="built it", raw={"is_error": False}),
        ClaudeResult(session_id="sess-1", result_text="boom", raw={}),
    ])
    gate_calls = _gate_recorder(monkeypatch, [_one_finding(gates.BLOCKING)])

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "build the thing")

    assert result.success is True
    assert result.result_text == "built it"
    assert len(gate_calls) == 1
    assert result.gates.blocking is True


def test_the_gate_retry_restates_the_permission_flags(tmp_path, monkeypatch) -> None:
    """Restated for the same reason as the shrink retry's, and with more at
    stake: this call exists to fix the code the gates found fault with, so a
    denied Edit turns the one call that saves a review round into a wasted
    one."""
    cfg = _make_config(tmp_path, gates_enabled=True)
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="built it", raw={"is_error": False}),
        ClaudeResult(session_id="sess-1", result_text="fixed it", raw={"is_error": False}),
    ])
    _gate_recorder(monkeypatch, [_one_finding(gates.BLOCKING), gates.Report()])

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "build the thing")

    assert calls[1]["resume_session_id"] == "sess-1"
    assert calls[1]["permission_mode"] == "acceptEdits"
    assert calls[1]["add_dirs"] == [str(tmp_path)]


def test_dispatch_phase_does_not_resume_a_gated_phase_without_a_session(tmp_path, monkeypatch) -> None:
    """`--resume` needs a session id. Without one the findings still travel —
    the revisor reads them — but no call is spent trying to ask about them."""
    cfg = _make_config(tmp_path, gates_enabled=True)
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id=None, result_text="built it", raw={"is_error": False}),
    ])
    gate_calls = _gate_recorder(monkeypatch, [_one_finding(gates.ASK)])

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "build the thing")

    assert len(calls) == 1
    assert len(gate_calls) == 1
    assert result.gates.needs_answer is True


def test_the_shrink_is_measured_against_what_the_gate_retry_wrote(tmp_path, monkeypatch) -> None:
    """The gate retry replaces the handoff, so the byte budget has to be
    enforced on what it wrote — the return that actually lands in the task
    file — and not on the one it replaced. That is why the gates run first."""
    cfg = _make_config(tmp_path, gates_enabled=True)
    budget = handoff.budget_for("implementador")
    fat = {"status": "complete", "changed": ["dispatcher/gates.py"], "risks": ["r" * (budget + 1000)]}
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF}),
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": fat}),
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF}),
    ])
    _gate_recorder(monkeypatch, [_one_finding(gates.ASK), gates.Report()])

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "build the thing")

    assert len(calls) == 3
    assert str(budget) in calls[2]["prompt"], "the third call is the shrink, not another gate retry"
    assert result.handoff == _LEAN_HANDOFF


def test_a_fresh_phase_is_not_told_to_revive_anything(tmp_path, monkeypatch) -> None:
    """A new session has no subagents of its own to revive, and SendMessage
    cannot reach another session's — so the note would be bytes paid for on
    every first call in exchange for nothing."""
    cfg = _make_config(tmp_path)
    _fake_subagents(monkeypatch)
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id=_SESSION_UUID, result_text="built it", raw={"is_error": False}),
    ])

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "build the thing")

    assert calls[0]["prompt"] == "build the thing"


def test_a_resumed_phase_is_told_what_it_left_running(tmp_path, monkeypatch) -> None:
    """The one call in a phase's life that can revive rather than respawn.
    The ids are looked up in the container the phase actually landed in, and
    not asked of the role: the Agent tool tells it not to repeat them."""
    cfg = _make_config(tmp_path)
    asked = _fake_subagents(monkeypatch)
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id=_SESSION_UUID, result_text="carried on", raw={"is_error": False}),
    ])

    dispatcher_mod.dispatch_phase(
        cfg, "task-1", "myproj", "implementador", "carry on",
        resume_session_id=_SESSION_UUID,
    )

    prompt = calls[0]["prompt"]
    assert prompt.startswith("carry on\n\n"), "the note is appended, never a replacement"
    assert "SendMessage" in prompt
    assert _SUBAGENT.id in prompt
    assert _SUBAGENT.description in prompt, "which agent is which is what makes the id usable"
    assert ("agent-cuenta1", _SESSION_UUID) in asked


def test_a_resumed_phase_is_warned_its_context_block_predates_its_own_work(tmp_path, monkeypatch) -> None:
    """The CLI re-sends the session's opening environment block verbatim on
    every resume, so a phase that changed hands mid-flight opens on a git
    status taken before its own edits — on T-006, `Status: (clean)` over three
    files it had just modified. Unlike the revive note this goes on every
    resume, because the block does: a session with nothing left running is
    handed the same stale snapshot as one that has subagents to revive."""
    cfg = _make_config(tmp_path)
    _fake_subagents(monkeypatch, agents=())
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id=_SESSION_UUID, result_text="carried on", raw={"is_error": False}),
    ])

    dispatcher_mod.dispatch_phase(
        cfg, "task-1", "myproj", "implementador", "carry on",
        resume_session_id=_SESSION_UUID,
    )

    prompt = calls[0]["prompt"]
    assert prompt.startswith("carry on\n\n"), "the note is appended, never a replacement"
    assert "git status --short" in prompt, "the one instruction that settles it"
    assert "SendMessage" not in prompt, "nothing was left running to revive"


def test_the_gate_retry_is_warned_about_the_stale_context_block_too(tmp_path, monkeypatch) -> None:
    """It resumes the session exactly as a failover does, and it is about to
    read the tree to answer findings against it. Trusting the opening snapshot
    there would undo the very fix it is being asked for."""
    cfg = _make_config(tmp_path, gates_enabled=True)
    _fake_subagents(monkeypatch, agents=())
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id=_SESSION_UUID, result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF}),
        ClaudeResult(session_id=_SESSION_UUID, result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF}),
    ])
    _gate_recorder(monkeypatch, [_one_finding(gates.ASK), gates.Report()])

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "build the thing")

    assert "git status --short" in calls[1]["prompt"]


def test_the_shrink_retry_is_not_warned_about_the_context_block(tmp_path, monkeypatch) -> None:
    """Same reason the revive note stays off it: it asks for the return it
    already has in fewer bytes. It reaches no conclusion about the tree, so
    the warning would be pure cost on the one call meant to be smaller."""
    cfg = _make_config(tmp_path)
    _fake_subagents(monkeypatch, agents=())
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id=_SESSION_UUID, result_text="", raw={"is_error": False, "structured_output": _FAT_HANDOFF}),
        ClaudeResult(session_id=_SESSION_UUID, result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF}),
    ])

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert calls[1]["resume_session_id"] == _SESSION_UUID, "it is a resume all the same"
    assert "git status --short" not in calls[1]["prompt"]


def test_a_phase_that_fails_over_twice_carries_one_note_about_where_it_landed(tmp_path, monkeypatch) -> None:
    """Each attempt builds its prompt from the untouched original. Appending
    in place would stack a note per failover, each about an account's session
    the new one cannot address anyway."""
    cfg = _make_config(
        tmp_path,
        accounts=[
            AccountConfig(name="cuenta1", container="agent-cuenta1"),
            AccountConfig(name="cuenta2", container="agent-cuenta2"),
            AccountConfig(name="cuenta3", container="agent-cuenta3"),
        ],
    )
    _fake_subagents(monkeypatch)
    calls = []
    limited = {"agent-cuenta1": _SESSION_UUID, "agent-cuenta2": _OTHER_SESSION_UUID}

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        if "usage" in prompt.lower():
            return ClaudeResult(session_id=None, result_text=_USAGE_TEXT, raw={})
        calls.append(dict(container=container, prompt=prompt, resume_session_id=resume_session_id))
        if container in limited:
            return ClaudeResult(session_id=limited[container], result_text="usage limit reached", raw={"is_error": True})
        return ClaudeResult(session_id=_THIRD_SESSION_UUID, result_text="done it", raw={"is_error": False})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "build the thing")

    assert result.success is True
    assert [call["container"] for call in calls] == ["agent-cuenta1", "agent-cuenta2", "agent-cuenta3"]
    assert calls[0]["prompt"] == "build the thing", "nothing to resume yet"
    assert calls[2]["resume_session_id"] == _OTHER_SESSION_UUID
    assert calls[2]["prompt"].count("SendMessage") == 1
    assert calls[2]["prompt"].startswith("build the thing\n\n")


def test_the_gate_retry_is_told_who_it_can_hand_the_fix_to(tmp_path, monkeypatch) -> None:
    """A phase that has to answer the gates is exactly the case the note is
    for: the subagent that wrote the code the gates are red about is still
    addressable, and briefing a fresh one costs the same work twice."""
    cfg = _make_config(tmp_path, gates_enabled=True)
    _fake_subagents(monkeypatch)
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id=_SESSION_UUID, result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF}),
        ClaudeResult(session_id=_SESSION_UUID, result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF}),
    ])
    _gate_recorder(monkeypatch, [_one_finding(gates.ASK), gates.Report()])

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "build the thing")

    retry = calls[1]["prompt"]
    assert "the test suite is red" in retry
    assert _SUBAGENT.id in retry


def test_the_shrink_retry_is_not_told_about_subagents(tmp_path, monkeypatch) -> None:
    """It resumes a session too, but it asks for the same answer in fewer
    bytes. There is no work there to hand to a subagent, so the note would be
    a cost — in the one call whose whole point is to be smaller."""
    cfg = _make_config(tmp_path)
    _fake_subagents(monkeypatch)
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id=_SESSION_UUID, result_text="", raw={"is_error": False, "structured_output": _FAT_HANDOFF}),
        ClaudeResult(session_id=_SESSION_UUID, result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF}),
    ])

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert calls[1]["resume_session_id"] == _SESSION_UUID, "it is a resume all the same"
    assert "SendMessage" not in calls[1]["prompt"]
    assert _SUBAGENT.id not in calls[1]["prompt"]


#: The return the reviewer is asked for instead: the change it wanted, handed
#: on to whoever owns the branch.
_CHANGES_REQUESTED = {
    "status": "complete",
    "verdict": "CHANGES_REQUESTED",
    "findings": ["docs/implementations/T-005.md is missing the pointers section"],
}


def test_a_reviewer_that_wrote_to_its_worktree_is_told_the_change_is_gone(
    tmp_path, monkeypatch, caplog, fake_git,
) -> None:
    """Measured on T-005: the revisor closed a gate by editing the
    implementation doc, its own grep in its own worktree agreed with it, the
    branch never saw it, and APPROVED was issued over a fix that did not
    exist. The edit cannot be rescued — what changes is that the phase is told
    and hands the change on as a finding instead of claiming it as work."""
    cfg = _make_config(tmp_path)
    _fake_subagents(monkeypatch, agents=())
    fake_git.dirty = ("docs/implementations/T-005.md",)
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id=_SESSION_UUID, result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF}),
        ClaudeResult(session_id=_SESSION_UUID, result_text="", raw={"is_error": False, "structured_output": _CHANGES_REQUESTED}),
    ])

    with caplog.at_level("WARNING"):
        result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert len(calls) == 2, "one resume, the same as the gate and shrink retries"
    assert calls[1]["resume_session_id"] == _SESSION_UUID
    assert "docs/implementations/T-005.md" in calls[1]["prompt"]
    assert result.handoff == _CHANGES_REQUESTED, "the corrected return is the one that lands"
    assert any(
        "docs/implementations/T-005.md" in r.getMessage() and "never reaches the branch" in r.getMessage()
        for r in caplog.records
    ), "the log is the record that it happened at all"


def test_a_clean_review_worktree_costs_the_phase_nothing(tmp_path, monkeypatch, fake_git) -> None:
    """The ordinary case. The CLI is refused when it writes settings, and the
    gate logs go to the scratch dir, so a review that reviewed is clean."""
    cfg = _make_config(tmp_path)
    _fake_subagents(monkeypatch, agents=())
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id=_SESSION_UUID, result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF}),
    ])

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert len(calls) == 1
    assert result.handoff == _LEAN_HANDOFF
    assert fake_git.status_checks == [
        ("agent-cuenta1", f"{cfg.projects_root}/myproj/worktrees/task-1")
    ], "asked once, answered nothing, and that is the whole cost"


def test_a_writing_phase_is_never_asked_what_it_left_behind(tmp_path, monkeypatch, fake_git) -> None:
    """A writer works in the task branch's own worktree and `_should_commit`
    commits it, so a dirty tree there is the phase having done its job. The
    question is only ever about a checkout nothing will commit."""
    cfg = _make_config(tmp_path)
    fake_git.dirty = ("src/app.py",)
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="built it", raw={"is_error": False}),
    ])

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "build the thing")

    assert len(calls) == 1
    assert fake_git.status_checks == []


def test_a_discarded_edit_is_logged_even_with_no_session_to_resume(
    tmp_path, monkeypatch, caplog, fake_git,
) -> None:
    """Without a session the phase cannot be corrected, and its handoff goes
    on overstating what it did — which is what used to happen every time. The
    warning is still more than the run said before."""
    cfg = _make_config(tmp_path)
    fake_git.dirty = ("docs/implementations/T-005.md",)
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id=None, result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF}),
    ])

    with caplog.at_level("WARNING"):
        result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert len(calls) == 1
    assert result.handoff == _LEAN_HANDOFF
    assert any(
        "no session to tell the revisor phase" in r.getMessage() for r in caplog.records
    )


def test_a_correction_that_will_not_run_leaves_the_review_standing(
    tmp_path, monkeypatch, caplog, fake_git,
) -> None:
    """The first return overstates what the phase did, but it holds the review
    itself, and throwing that away costs the round for nothing. The WARNING is
    in the log either way."""
    cfg = _make_config(tmp_path)
    _fake_subagents(monkeypatch, agents=())
    fake_git.dirty = ("docs/implementations/T-005.md",)
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id=_SESSION_UUID, result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF}),
        ClaudeResult(session_id=_SESSION_UUID, result_text="boom", raw={"is_error": True}),
    ])

    with caplog.at_level("WARNING"):
        result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert len(calls) == 2
    assert result.success is True
    assert result.handoff == _LEAN_HANDOFF
    assert any(
        "could not be told its edits were discarded" in r.getMessage() for r in caplog.records
    )


def test_the_handoff_gets_the_subagent_ids_from_disk(tmp_path, monkeypatch) -> None:
    """The phase cannot fill this field — it is told not to surface agent ids
    — so the dispatcher fills it, and what the phase did say is replaced
    rather than merged: disk is the only source that can be trusted here."""
    cfg = _make_config(tmp_path)
    _fake_subagents(monkeypatch)
    invented = dict(_LEAN_HANDOFF, subagents=[{"id": "made-up", "doing": "who knows"}])
    _phase_recorder(monkeypatch, [
        ClaudeResult(session_id=_SESSION_UUID, result_text="", raw={"is_error": False, "structured_output": invented}),
    ])

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert result.handoff["subagents"] == [{"id": _SUBAGENT.id, "doing": "slow count"}]
    assert result.handoff["verdict"] == _LEAN_HANDOFF["verdict"], "the rest of the return is untouched"


def test_the_gates_write_their_test_log_into_the_tasks_scratch_dir(tmp_path, monkeypatch) -> None:
    """The dispatcher and the agents mount `.hive` at the same path, so a
    finding that names this file names something the next round can open —
    and a reviewing worktree that gets rebuilt every round cannot take it
    away. The round is in the name because round two's failure is not round
    one's."""
    cfg = _make_config(tmp_path, gates_enabled=True)
    _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="built it", raw={"is_error": False}),
    ])
    gate_calls = _gate_recorder(monkeypatch, [gates.Report()])

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "build it", round_num=2)

    call = gate_calls[0]
    assert call["container"] == "agent-cuenta1"
    assert call["workdir"] == f"{cfg.projects_root}/myproj/worktrees/task-1"
    assert call["project_dir"] == f"{cfg.projects_root}/myproj"
    assert call["test_timeout_seconds"] == cfg.gates_test_timeout_seconds
    assert call["test_log_path"] == os.path.join(
        scratch_dir(cfg.hive_tasks_dir, "task-1"), "gates-round-2.log"
    )
    assert os.path.isdir(scratch_dir(cfg.hive_tasks_dir, "task-1")), "the gate has to be able to write it"


def test_check_quota_ok_probe_uses_fixed_usage_probe_timeout(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path, phase_timeout_seconds=999)
    captured = {}

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        captured["timeout_seconds"] = timeout_seconds
        return ClaudeResult(
            session_id=None,
            result_text=(
                "Current session: 10% used · resets later\n"
                "Current week (all models): 10% used · resets later"
            ),
            raw={},
        )

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)

    dispatcher_mod.check_quota_ok(cfg, "cuenta1")

    assert captured["timeout_seconds"] == dispatcher_mod._USAGE_PROBE_TIMEOUT_SECONDS
    assert dispatcher_mod._USAGE_PROBE_TIMEOUT_SECONDS == 120


def test_dispatch_phase_all_accounts_over_quota(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        return ClaudeResult(
            session_id=None,
            result_text=(
                "Current session: 95% used · resets later\n"
                "Current week (all models): 20% used · resets later"
            ),
            raw={},
        )

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    assert result.success is False
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.PRE_COOLDOWN


def test_check_quota_ok_returns_true_and_warns_when_usage_probe_format_drifts(tmp_path, monkeypatch, caplog) -> None:
    cfg = _make_config(tmp_path)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        return ClaudeResult(session_id=None, result_text="not the /usage format we expect at all", raw={})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        ok = dispatcher_mod.check_quota_ok(cfg, "cuenta1")

    assert ok is True
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.IDLE
    assert "cuenta1" in caplog.text


def test_reap_expired_locks_releases_stale_owner(tmp_path) -> None:
    cfg = _make_config(tmp_path)
    acquire_lock(cfg.hive_tasks_dir, "task-1", owner="cuenta1")
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert not is_lock_expired(task, cfg.heartbeat_ttl_seconds)

    import datetime as dt
    from dispatcher.context_transfer import write_task_file
    task.heartbeat = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=999)).isoformat()
    write_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"), task)

    reaped = dispatcher_mod.reap_expired_locks(cfg)

    assert reaped == ["task-1"]
    refreshed = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert refreshed.owner is None


def test_dispatch_phase_raises_lock_held_error_without_touching_worktree_when_task_owned_by_other(
    tmp_path, monkeypatch,
) -> None:
    cfg = _make_config(tmp_path)
    acquire_lock(cfg.hive_tasks_dir, "task-1", owner="otro")

    worktree_calls = []

    def spy_create_worktree(container, projects_root, slug, task_id, role):
        worktree_calls.append((container, projects_root, slug, task_id, role))
        return f"{projects_root}/{slug}/worktrees/{task_id}"

    monkeypatch.setattr(dispatcher_mod.docker_exec, "create_worktree", spy_create_worktree)

    with pytest.raises(LockHeldError):
        dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    # A task someone else holds must be refused before the worktree is ever
    # touched — see dispatch_phase's acquire_lock-before-create_worktree
    # ordering (design spec sec. 5, lock ownership enforcement).
    assert worktree_calls == []
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.IDLE
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert task.owner == "otro"


def test_dispatch_phase_takes_over_lock_with_no_heartbeat_via_ttl_pass_through(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    # heartbeat=None can only come from a hand-edited or legacy task file, not
    # a live dispatcher (acquire_lock always stamps a fresh heartbeat) —
    # reap_expired_locks/is_lock_expired both treat heartbeat=None as "not
    # expired", so the ttl_seconds pass-through into acquire_lock is the only
    # path that can ever take this lock over.
    write_task_file(
        task_file_path(cfg.hive_tasks_dir, "task-1"),
        dispatcher_mod.context_transfer.TaskFile(
            task_id="task-1", status="in_progress", owner="otro", depends_on=[], heartbeat=None, body="",
        ),
    )

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        if "usage" in prompt.lower():
            return ClaudeResult(
                session_id=None,
                result_text=(
                    "Current session: 10% used · resets later\n"
                    "Current week (all models): 10% used · resets later"
                ),
                raw={},
            )
        return ClaudeResult(session_id="sess-3", result_text="phase done", raw={"is_error": False})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    assert result.success is True
    assert result.account == "cuenta1"
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert task.owner == "cuenta1"


def _phase_seen_by(cfg, ran: list, result_text: str = "phase done", is_error: bool = False):
    """An `exec_claude` that answers /usage, and records the task's `phase`
    while the phase itself runs: what a reader of the task file sees then."""

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        if "usage" in prompt.lower():
            return ClaudeResult(
                session_id=None,
                result_text=(
                    "Current session: 10% used · resets later\n"
                    "Current week (all models): 10% used · resets later"
                ),
                raw={},
            )
        ran.append(read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1")).phase)
        return ClaudeResult(session_id="sess-9", result_text=result_text, raw={"is_error": is_error})

    return fake_exec_claude


@pytest.mark.parametrize("role", ["arquitecto", "implementador", "revisor", "auditor", "integrador"])
def test_dispatch_phase_writes_the_role_as_the_tasks_phase_while_it_runs(
    tmp_path, monkeypatch, role,
) -> None:
    """ADR 126: the role is on the task for as long as the phase holds it. A
    phase that succeeds keeps it with the lock, which the caller's `handoff`
    then drops (`tests/dispatcher/test_tasks.py`)."""
    cfg = _make_config(tmp_path)
    ran: list = []
    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", _phase_seen_by(cfg, ran))
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )
    monkeypatch.setattr(dispatcher_mod, "_record_left_behind", lambda *a, **k: None)
    monkeypatch.setattr(dispatcher_mod.docker_exec, "commit_worktree", lambda *a, **k: None)

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", role, "do the thing")

    assert result.success is True
    assert ran == [role]
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert (task.owner, task.phase) == ("cuenta1", role)


def test_dispatch_phase_clears_the_phase_when_the_phase_fails(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    ran: list = []
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "exec_claude",
        _phase_seen_by(cfg, ran, result_text="claude timed out", is_error=True),
    )
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "do the thing")

    assert result.success is False
    assert ran == ["implementador"]
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert (task.owner, task.heartbeat, task.phase) == (None, None, None)


def test_dispatch_phase_clears_the_phase_when_it_raises(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    seen: list = []

    def raising_create_worktree(container, projects_root, slug, task_id, role):
        seen.append(read_task_file(task_file_path(cfg.hive_tasks_dir, task_id)).phase)
        raise RuntimeError("boom")

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", _phase_seen_by(cfg, []))
    monkeypatch.setattr(dispatcher_mod.docker_exec, "create_worktree", raising_create_worktree)

    with pytest.raises(RuntimeError):
        dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "do the thing")

    assert seen == ["revisor"]
    assert read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1")).phase is None


def test_reap_expired_locks_clears_the_phase_a_killed_dispatcher_left(tmp_path) -> None:
    """ADR 126: what clears a stale `phase` is what clears the stale lock."""
    cfg = _make_config(tmp_path)
    acquire_lock(cfg.hive_tasks_dir, "task-1", owner="cuenta1", phase="implementador")
    path = task_file_path(cfg.hive_tasks_dir, "task-1")
    task = read_task_file(path)
    task.heartbeat = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=999)).isoformat()
    write_task_file(path, task)
    assert read_task_file(path).phase == "implementador"

    assert dispatcher_mod.reap_expired_locks(cfg) == ["task-1"]

    after = read_task_file(path)
    assert (after.status, after.owner, after.phase) == ("in_progress", None, None)


def test_dispatch_phase_exception_during_busy_window_leaves_account_idle(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        return ClaudeResult(
            session_id=None,
            result_text=(
                "Current session: 10% used · resets later\n"
                "Current week (all models): 10% used · resets later"
            ),
            raw={},
        )

    def raising_create_worktree(container, projects_root, slug, task_id, role):
        raise RuntimeError("boom")

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(dispatcher_mod.docker_exec, "create_worktree", raising_create_worktree)

    with pytest.raises(RuntimeError):
        dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    assert get_state(cfg.state_dir, "cuenta1") == AccountState.IDLE


def test_dispatch_phase_recovers_account_from_cooling_down_via_recheck(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    set_state(cfg.state_dir, "cuenta1", AccountState.COOLING_DOWN)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        if "usage" in prompt.lower():
            return ClaudeResult(
                session_id=None,
                result_text=(
                    "Current session: 5% used · resets later\n"
                    "Current week (all models): 5% used · resets later"
                ),
                raw={},
            )
        return ClaudeResult(session_id="sess-2", result_text="phase done", raw={"is_error": False})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    assert result.success is True
    assert result.account == "cuenta1"
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.IDLE


def test_recheck_cooling_accounts_recovers_to_idle_when_usage_probe_raises(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    set_state(cfg.state_dir, "cuenta1", AccountState.COOLING_DOWN)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        raise ValueError("bad /usage format")

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)

    recovered = dispatcher_mod._recheck_cooling_accounts(cfg)

    assert recovered == ["cuenta1"]
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.IDLE


def test_dispatch_phase_does_not_loop_forever_when_cooling_probe_raises_and_phase_rate_limits(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    set_state(cfg.state_dir, "cuenta1", AccountState.COOLING_DOWN)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        if "usage" in prompt.lower():
            raise ValueError("bad /usage format")
        return ClaudeResult(session_id=None, result_text="rate limit reached", raw={"is_error": True})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    assert result.success is False
    assert result.result_text == "no accounts available"


def test_dispatch_phase_picks_untried_recovered_account_after_recheck(tmp_path, monkeypatch) -> None:
    # cuenta1 rate-limits and joins `tried`; the recheck that follows finds
    # cuenta3, which was already cooling from a prior call, and recovers it.
    # Picking idle[0] without excluding `tried` would hand the retry straight
    # back to cuenta1 and immediately fail closed instead of trying cuenta3
    # — `tried` is what has to carry that, because the recheck could also
    # have recovered cuenta1 itself, and did before the refusal cooldown
    # started holding it. cuenta2 stays over-threshold through the recheck,
    # proving the pick isn't just "any recovered account" either.
    cfg = _make_config(
        tmp_path,
        accounts=[
            AccountConfig(name="cuenta1", container="agent-cuenta1"),
            AccountConfig(name="cuenta2", container="agent-cuenta2"),
            AccountConfig(name="cuenta3", container="agent-cuenta3"),
        ],
    )
    set_state(cfg.state_dir, "cuenta2", AccountState.COOLING_DOWN)
    set_state(cfg.state_dir, "cuenta3", AccountState.COOLING_DOWN)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        if "usage" in prompt.lower():
            if container == "agent-cuenta2":
                return ClaudeResult(
                    session_id=None,
                    result_text=(
                        "Current session: 95% used · resets later\n"
                        "Current week (all models): 95% used · resets later"
                    ),
                    raw={},
                )
            return ClaudeResult(
                session_id=None,
                result_text=(
                    "Current session: 5% used · resets later\n"
                    "Current week (all models): 5% used · resets later"
                ),
                raw={},
            )
        if container == "agent-cuenta1":
            return ClaudeResult(session_id="sess-a", result_text="usage limit reached", raw={"is_error": True})
        return ClaudeResult(session_id="sess-c", result_text="phase done", raw={"is_error": False})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    assert result.success is True
    assert result.account == "cuenta3"
    assert get_state(cfg.state_dir, "cuenta2") == AccountState.COOLING_DOWN


def test_check_quota_ok_parks_an_account_whose_probe_is_itself_refused(tmp_path, monkeypatch, caplog) -> None:
    """/usage is local and normally free, so a refusal to run even that is the
    one thing the probe can report that its own counters never could. Before
    the refusal was read first, parse_usage_output choked on the error text and
    the fail-open below waved the account through."""
    cfg = _make_config(tmp_path)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        return ClaudeResult(session_id=None, result_text="usage limit reached", raw={"is_error": True})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        ok = dispatcher_mod.check_quota_ok(cfg, "cuenta1")

    assert ok is False
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.COOLING_DOWN
    assert get_rate_limited_at(cfg.state_dir, "cuenta1") is not None
    assert "cuenta1" in caplog.text


def test_a_recently_refused_account_is_not_even_probed_by_the_recheck(tmp_path, monkeypatch, caplog) -> None:
    """The probe would say the account is healthy — it reads counters this
    machine wrote — and recovering it on those numbers is how the real 429 of
    2026-09-23 was thrown away. The refusal outranks them, and the probe is not
    worth running at all while it does."""
    cfg = _make_config(tmp_path)
    record_rate_limit(cfg.state_dir, "cuenta1", at=time.time() - 60)
    set_state(cfg.state_dir, "cuenta1", AccountState.COOLING_DOWN)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        raise AssertionError("the recheck should not have probed a just-refused account")

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        recovered = dispatcher_mod._recheck_cooling_accounts(cfg)

    assert recovered == []
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.COOLING_DOWN
    assert "cuenta1" in caplog.text


def test_the_recheck_takes_the_account_back_once_the_cooldown_has_passed(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    record_rate_limit(cfg.state_dir, "cuenta1", at=time.time() - cfg.quota_cooldown_seconds - 1)
    set_state(cfg.state_dir, "cuenta1", AccountState.COOLING_DOWN)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        return ClaudeResult(session_id=None, result_text=_USAGE_TEXT, raw={})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)

    recovered = dispatcher_mod._recheck_cooling_accounts(cfg)

    assert recovered == ["cuenta1"]
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.IDLE
    # The old mark must go with the recovery, or the next refusal-free pass
    # would keep measuring the cooldown from a limit that has already lifted.
    assert get_rate_limited_at(cfg.state_dir, "cuenta1") is None


def test_a_recheck_probe_that_is_itself_refused_restarts_the_cooldown(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    stale = time.time() - cfg.quota_cooldown_seconds - 1
    record_rate_limit(cfg.state_dir, "cuenta1", at=stale)
    set_state(cfg.state_dir, "cuenta1", AccountState.COOLING_DOWN)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        return ClaudeResult(session_id=None, result_text="usage limit reached", raw={"is_error": True})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)

    recovered = dispatcher_mod._recheck_cooling_accounts(cfg)

    assert recovered == []
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.COOLING_DOWN
    # Fresh evidence, so the floor holds the account on the next pass instead
    # of asking again on every call.
    assert get_rate_limited_at(cfg.state_dir, "cuenta1") > stale


def test_a_refused_phase_writes_the_refusal_down_and_the_recheck_honours_it(tmp_path, monkeypatch) -> None:
    """End to end over the defect: the phase 429s, the account is parked, and
    the recheck that follows sees healthy local numbers. It used to hand the
    work straight back to the account that had just been refused; now the pool
    comes up empty without spending a second attempt to relearn that."""
    cfg = _make_config(tmp_path)
    probes = []

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        if "usage" in prompt.lower():
            probes.append(container)
            return ClaudeResult(session_id=None, result_text=_USAGE_TEXT, raw={})
        return ClaudeResult(session_id="sess-a", result_text="usage limit reached", raw={"is_error": True})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    assert result.success is False
    assert result.result_text == "no accounts available"
    # The one failure that is not the task's, flagged as such (ADR 52).
    assert result.no_account is True
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.COOLING_DOWN
    assert get_rate_limited_at(cfg.state_dir, "cuenta1") is not None
    # One probe: the gate before the phase. The recheck never ran a second,
    # because the refusal already answered the question it would have asked.
    assert probes == ["agent-cuenta1"]


def test_a_turn_the_service_actually_served_clears_an_old_refusal(tmp_path, monkeypatch) -> None:
    """A clean /usage is not evidence the limit lifted, for the same reason the
    refusal had to be recorded at all. A served phase is."""
    cfg = _make_config(tmp_path)
    record_rate_limit(cfg.state_dir, "cuenta1", at=time.time() - cfg.quota_cooldown_seconds - 1)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        if "usage" in prompt.lower():
            return ClaudeResult(session_id=None, result_text=_USAGE_TEXT, raw={})
        return ClaudeResult(session_id="sess-1", result_text="phase done", raw={"is_error": False})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    assert result.success is True
    assert get_rate_limited_at(cfg.state_dir, "cuenta1") is None


def test_a_failover_leaves_the_whole_handover_in_the_log(tmp_path, monkeypatch, caplog) -> None:
    """The one transition that changes accounts mid-phase used to write
    nothing at all: T-006's log was shaped exactly like a clean run's, and the
    failover had to be inferred from a state file that holds only the current
    state. Each step now names itself — who had it, why it gave it up, who
    picked it up and which session they picked up."""
    cfg = _make_config(
        tmp_path,
        accounts=[
            AccountConfig(name="cuenta1", container="agent-cuenta1"),
            AccountConfig(name="cuenta2", container="agent-cuenta2"),
        ],
    )
    _fake_subagents(monkeypatch, agents=())

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        if "usage" in prompt.lower():
            return ClaudeResult(session_id=None, result_text=_USAGE_TEXT, raw={})
        if container == "agent-cuenta1":
            return ClaudeResult(session_id=_SESSION_UUID, result_text="usage limit reached", raw={"is_error": True})
        return ClaudeResult(session_id=_SESSION_UUID, result_text="carried on", raw={"is_error": False})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    with caplog.at_level("INFO", logger=dispatcher_mod.logger.name):
        result = dispatcher_mod.dispatch_phase(
            cfg, "T-006", "myproj", "arquitecto", "do the thing", round_num=1,
        )

    assert result.success is True
    assert result.account == "cuenta2"
    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "T-006" in m and "arquitecto round 1 goes to account cuenta1" in m and "new session" in m
        for m in messages
    ), "the account a phase ran on was never written down either"
    refusals = [m for m in messages if "rate-limited" in m]
    assert len(refusals) == 1, "one line per handover, not one per attempt"
    assert "cuenta1" in refusals[0]
    assert "COOLING_DOWN" in refusals[0]
    assert _SESSION_UUID in refusals[0], "the session is what the next account resumes"
    assert any(
        f"goes to account cuenta2, resuming session {_SESSION_UUID}" in m for m in messages
    )


def test_parking_an_account_over_its_quota_threshold_says_so(tmp_path, monkeypatch, caplog) -> None:
    """The pre-emptive park is the quiet twin of the reactive one: the account
    leaves the pool without having refused anything, so the numbers that took
    it out are the only explanation there will ever be."""
    cfg = _make_config(tmp_path)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        return ClaudeResult(
            session_id=None,
            result_text=(
                "Current session: 95% used · resets later\n"
                "Current week (all models): 40% used · resets later"
            ),
            raw={},
        )

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        assert dispatcher_mod.check_quota_ok(cfg, "cuenta1") is False

    parked = [r.getMessage() for r in caplog.records if "PRE_COOLDOWN" in r.getMessage()]
    assert len(parked) == 1
    assert "cuenta1" in parked[0]
    assert "95%" in parked[0] and "40%" in parked[0], "both halves, since either can be the one over"
    assert "90%" in parked[0], "the threshold it was measured against"


def test_an_account_coming_back_from_cooling_says_where_it_came_from(tmp_path, monkeypatch, caplog) -> None:
    """Recovery is a re-check on dispatch, so an account rejoins the pool at a
    moment nothing else marks. The prior state is what makes the line worth
    reading: back from COOLING_DOWN means a refusal has expired, back from
    PRE_COOLDOWN only that a number fell."""
    cfg = _make_config(tmp_path)
    set_state(cfg.state_dir, "cuenta1", AccountState.COOLING_DOWN)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        return ClaudeResult(
            session_id=None,
            result_text=(
                "Current session: 5% used · resets later\n"
                "Current week (all models): 5% used · resets later"
            ),
            raw={},
        )

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)

    with caplog.at_level("INFO", logger=dispatcher_mod.logger.name):
        assert dispatcher_mod._recheck_cooling_accounts(cfg) == ["cuenta1"]

    assert any(
        "cuenta1" in r.getMessage() and "COOLING_DOWN" in r.getMessage()
        for r in caplog.records
    )


def test_an_empty_pool_is_logged_with_the_accounts_it_tried(tmp_path, monkeypatch, caplog) -> None:
    """This return stops the task. Without the accounts named, the log cannot
    say whether the pool was empty before the phase started or emptied itself
    one account at a time while it ran."""
    cfg = _make_config(
        tmp_path,
        accounts=[
            AccountConfig(name="cuenta1", container="agent-cuenta1"),
            AccountConfig(name="cuenta2", container="agent-cuenta2"),
        ],
    )

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        return ClaudeResult(
            session_id=None,
            result_text=(
                "Current session: 95% used · resets later\n"
                "Current week (all models): 95% used · resets later"
            ),
            raw={},
        )

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)

    with caplog.at_level("ERROR", logger=dispatcher_mod.logger.name):
        result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    assert result.result_text == "no accounts available"
    exhausted = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert len(exhausted) == 1
    assert "task-1" in exhausted[0] and "arquitecto" in exhausted[0]
    assert "cuenta1, cuenta2" in exhausted[0]


def test_dispatch_phase_returns_failure_when_exec_crashes_with_empty_raw(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        if "usage" in prompt.lower():
            return ClaudeResult(
                session_id=None,
                result_text=(
                    "Current session: 10% used · resets later\n"
                    "Current week (all models): 10% used · resets later"
                ),
                raw={},
            )
        return ClaudeResult(session_id=None, result_text="", raw={})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    assert result.success is False
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.IDLE


def test_heartbeat_loop_survives_transient_refresh_exception(tmp_path, monkeypatch) -> None:
    calls = []

    def flaky_refresh_heartbeat(hive_dir, task_id):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("transient")

    monkeypatch.setattr(dispatcher_mod.tasks, "refresh_heartbeat", flaky_refresh_heartbeat)

    with dispatcher_mod._HeartbeatLoop(str(tmp_path), "task-1", interval_seconds=0.05) as loop:
        time.sleep(0.3)
        assert loop._thread.is_alive()

    assert len(calls) >= 2


def test_run_task_cycle_reaps_expired_locks_before_dispatching(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    acquire_lock(cfg.hive_tasks_dir, "task-1", owner="cuenta1")
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    task.heartbeat = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=999)).isoformat()
    write_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"), task)

    reap_calls = []
    original_reap = dispatcher_mod.reap_expired_locks

    def spying_reap(cfg_arg):
        reap_calls.append(cfg_arg)
        return original_reap(cfg_arg)

    monkeypatch.setattr(dispatcher_mod, "reap_expired_locks", spying_reap)
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase",
        lambda *a, **kw: dispatcher_mod.DispatchResult(success=False, session_id=None, result_text="stop", account=""),
    )

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert len(reap_calls) == 1
    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED


def test_run_task_cycle_blocks_without_raising_when_task_locked_by_other_owner(
    tmp_path, monkeypatch, caplog,
) -> None:
    cfg = _make_config(tmp_path)
    acquire_lock(cfg.hive_tasks_dir, "task-1", owner="otro")

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        return ClaudeResult(
            session_id=None,
            result_text=(
                "Current session: 10% used · resets later\n"
                "Current week (all models): 10% used · resets later"
            ),
            raw={},
        )

    worktree_calls = []

    def spy_create_worktree(container, projects_root, slug, task_id, role):
        worktree_calls.append((container, projects_root, slug, task_id, role))
        return f"{projects_root}/{slug}/worktrees/{task_id}"

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(dispatcher_mod.docker_exec, "create_worktree", spy_create_worktree)

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert worktree_calls == []
    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    assert "task-1" in caplog.text
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert task.owner == "otro"


def test_run_task_cycle_prompt_references_task_file(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    captured_prompts = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        captured_prompts.append(prompt)
        return dispatcher_mod.DispatchResult(success=False, session_id=None, result_text="stop", account="")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    expected_path = task_file_path(cfg.hive_tasks_dir, "task-1")
    assert captured_prompts
    assert expected_path in captured_prompts[0]


def test_run_task_cycle_embeds_the_description_in_every_role_prompt(tmp_path, monkeypatch) -> None:
    """The roles used to get a role name, a task id and a file path — the
    arquitecto had nothing to plan from. *Goal* and *Done when* go in the
    prompt whole (never truncated: a cut ask misinforms) for every phase, not
    just the first, so a revisor or auditor can check the work against what
    was actually asked for. The optional sections stay in `description.md`."""
    cfg = _make_config(tmp_path, max_revision_rounds=1)
    captured = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        captured.append((role, prompt))
        return dispatcher_mod.DispatchResult(
            success=True, session_id=None, result_text="VERDICT: APPROVED", account="cuenta1",
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    description = (
        "## Goal\n\nAdd a /healthz endpoint.\n\nIt must return 200 and no body.\n\n"
        "## Done when\n\nGET /healthz answers 200.\n\n"
        "## Steps\n\n1. Register the route first.\n"
    )
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=description)

    assert [role for role, _ in captured] == ["arquitecto", "implementador", "revisor", "auditor"]
    for role, prompt in captured:
        assert (
            "Task description:\n## Goal\n\nAdd a /healthz endpoint.\n\n"
            "It must return 200 and no body.\n\n## Done when\n\nGET /healthz answers 200.\n\n"
        ) in prompt, f"{role}'s prompt does not carry the ask"
        assert "Register the route first." not in prompt

    # The revisor's verdict instruction has to stay the last thing it reads,
    # or a description ending in prose could bury it.
    revisor_prompt = next(prompt for role, prompt in captured if role == "revisor")
    assert revisor_prompt.rstrip().endswith("That field is what the dispatcher reads to decide.")


def test_run_task_cycle_seeds_the_description_onto_the_board(tmp_path, monkeypatch) -> None:
    """Seeded once, then read from its file on a re-run: a resume (`run-task`
    with no --description after a Ctrl+C) has to find the original ask there."""
    cfg = _make_config(tmp_path, max_revision_rounds=1)
    prompts = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        prompts.append(prompt)
        return dispatcher_mod.DispatchResult(
            success=True, session_id=None, result_text="VERDICT: APPROVED", account="cuenta1",
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert tasks.read_description(cfg.hive_tasks_dir, "task-1") == _DESCRIPTION
    raw = Path(task_file_path(cfg.hive_tasks_dir, "task-1")).read_text()
    # handoff() appended four phase summaries to the body; the description
    # must not be one of them, or a re-run would stack copies of the ask. Nor
    # is it a frontmatter key any more.
    assert _GOAL not in raw
    assert "description:" not in raw

    prompts.clear()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj")

    assert prompts, "the re-run should have dispatched using the stored description"
    assert all(_DESCRIPTION in prompt for prompt in prompts)


def test_run_task_cycle_blocks_without_dispatching_when_no_description_exists(
    tmp_path, monkeypatch, caplog,
) -> None:
    """No description anywhere (no argument, no task file) means the four
    phases can only produce noise at full quota cost, so nothing is
    dispatched at all and the task goes straight to blocked."""
    cfg = _make_config(tmp_path)
    calls = []

    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase",
        lambda *a, **kw: calls.append(a) or dispatcher_mod.DispatchResult(
            success=True, session_id=None, result_text="ok", account="cuenta1",
        ),
    )

    with caplog.at_level("ERROR", logger=dispatcher_mod.logger.name):
        outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj")

    assert calls == []
    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    # Nothing is written either: a task that never dispatched has no task file.
    assert not os.path.exists(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert "description" in caplog.text


def test_run_task_cycle_blocks_when_the_stored_description_is_blank(tmp_path, monkeypatch) -> None:
    """A description file that is whitespace is as useless as none at all —
    the guard is on content, not on the file being present."""
    cfg = _make_config(tmp_path)
    path = Path(tasks.description_path(cfg.hive_tasks_dir, "task-1"))
    path.parent.mkdir(parents=True)
    path.write_text("   \n")
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", lambda *a, **kw: calls.append(a))

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj")

    assert calls == []
    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED


def test_run_task_cycle_blocks_when_the_stored_description_lost_a_section(
    tmp_path, monkeypatch, caplog,
) -> None:
    """A hand-edited `description.md` stops at the door, with the section
    named, rather than reaching a phase."""
    cfg = _make_config(tmp_path)
    path = Path(tasks.description_path(cfg.hive_tasks_dir, "task-1"))
    path.parent.mkdir(parents=True)
    path.write_text(f"## Goal\n\n{_GOAL}\n")
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", lambda *a, **kw: calls.append(a))

    with caplog.at_level("ERROR", logger=dispatcher_mod.logger.name):
        outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj")

    assert calls == []
    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    assert "`## Done when`" in caplog.text


def test_run_task_cycle_refuses_a_hive_that_was_not_migrated(
    tmp_path, monkeypatch, caplog,
) -> None:
    """Another task's file still has its description in the frontmatter, and
    its lock has expired: the reaper's rewrite of that file would be refused
    by `tasks.py`'s writer, as a traceback. The whole-hive rule says it first
    and in one line: nothing runs and nothing is written until
    `dispatch migrate` has."""
    cfg = _make_config(tmp_path)
    legacy = Path(task_file_path(cfg.hive_tasks_dir, "task-0"))
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        "---\ntask_id: task-0\nstatus: in_progress\nowner: cuenta1\ndepends_on: []\n"
        "heartbeat: '2020-01-01T00:00:00+00:00'\ndescription: An older ask.\n---\n\n"
    )
    before = legacy.read_bytes()
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", lambda *a, **kw: calls.append(a))

    with caplog.at_level("ERROR", logger=dispatcher_mod.logger.name):
        outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert calls == []
    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    assert legacy.read_bytes() == before
    assert "dispatch migrate" in caplog.text and "task-0" in caplog.text
    assert tasks.read_description(cfg.hive_tasks_dir, "task-1") is None


def test_one_unmigrated_file_no_writer_would_touch_still_stops_every_task(
    tmp_path, monkeypatch, caplog,
) -> None:
    """The whole-hive rule of `docs/business.md` *Task descriptions*, apart from
    the writer's own refusal: task-0 is `pending` and unlocked, so no reap, lock
    or handoff of task-1's cycle would ever write it, and `tasks.py`'s writer
    would never be asked. The cycle is refused all the same."""
    cfg = _make_config(tmp_path)
    legacy = Path(task_file_path(cfg.hive_tasks_dir, "task-0"))
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        "---\ntask_id: task-0\nstatus: pending\nowner: null\ndepends_on: []\n"
        "heartbeat: null\ndescription: An older ask.\n---\n\n"
    )
    before = legacy.read_bytes()
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", lambda *a, **kw: calls.append(a))

    with caplog.at_level("ERROR", logger=dispatcher_mod.logger.name):
        outcome = dispatcher_mod.run_task_cycle(
            cfg, "task-1", "myproj", description=_DESCRIPTION
        )

    assert calls == []
    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    assert legacy.read_bytes() == before
    assert list_task_ids(cfg.hive_tasks_dir) == ["task-0"]
    assert "dispatch migrate" in caplog.text and "task-0" in caplog.text


_FIVE_SECTIONS = (
    "## Goal\n\nAdd a /healthz endpoint.\n\n"
    "## Done when\n\n- GET /healthz answers 200.\n- The suite passes.\n\n"
    "## Surface\n\nThe router lives in `app/routes.py`.\n\n"
    "## Steps\n\n1. Register the route first.\n\n"
    "## Out of scope\n\nAuthentication stays as it is.\n"
)
_BOARD_PATH = "/data/.hive/board/task-1/description.md"


def _prompt_for(role: str, description: str) -> str:
    return dispatcher_mod._role_prompt(
        role, "task-1", "myproj", "/data/.hive/tasks/task-1.md", description,
        "/data/.hive/tasks/task-1", "/data/.hive/tasks",
    )


@pytest.mark.parametrize(
    "role", ["arquitecto", "implementador", "revisor", "auditor", project_docs.MAPPER_ROLE],
)
def test_the_prompt_carries_goal_and_done_when_and_not_the_optional_sections(role) -> None:
    prompt = _prompt_for(role, _FIVE_SECTIONS)

    assert (
        "## Goal\n\nAdd a /healthz endpoint.\n\n"
        "## Done when\n\n- GET /healthz answers 200.\n- The suite passes.\n\n"
    ) in prompt
    for optional_body in (
        "The router lives in `app/routes.py`.",
        "Register the route first.",
        "Authentication stays as it is.",
    ):
        assert optional_body not in prompt


@pytest.mark.parametrize("role", ["arquitecto", "implementador"])
def test_the_roles_that_plan_and_build_are_given_the_path_of_the_description(role) -> None:
    prompt = _prompt_for(role, _FIVE_SECTIONS)

    assert f"It also has Surface, Steps, Out of scope, not in this prompt: read {_BOARD_PATH}" in prompt
    # Only the sections this description has are named, so nobody opens the
    # file to find nothing.
    assert "It also has Steps, not in this prompt" in _prompt_for(
        role, _DESCRIPTION + "\n## Steps\n\n1. first\n"
    )
    bare = _prompt_for(role, _DESCRIPTION)
    assert f"it has no others. The stored text is {_BOARD_PATH}." in bare


@pytest.mark.parametrize("role", ["revisor", "auditor", project_docs.MAPPER_ROLE])
def test_the_other_roles_are_not_given_the_path_of_the_description(role) -> None:
    for description in (_FIVE_SECTIONS, _DESCRIPTION):
        prompt = _prompt_for(role, description)

        assert _BOARD_PATH not in prompt
        assert "description.md" not in prompt


def test_a_long_done_when_reaches_the_prompt_whole() -> None:
    done_when = "\n".join(f"- criterion {n} " + "x" * 90 for n in range(200))
    assert len(done_when) > 20_000

    for role in ("implementador", "revisor", project_docs.MAPPER_ROLE):
        assert done_when in _prompt_for(role, f"## Goal\n\n{_GOAL}\n\n## Done when\n\n{done_when}\n")


@pytest.mark.parametrize("role", ["arquitecto", "revisor", project_docs.MAPPER_ROLE])
def test_a_description_that_will_not_parse_is_an_error_and_not_half_a_prompt(role) -> None:
    with pytest.raises(tasks.DescriptionError, match="`## Done when`"):
        _prompt_for(role, f"## Goal\n\n{_GOAL}\n")


def test_revisor_approved_matches_verdict_line() -> None:
    assert dispatcher_mod.revisor_approved("looks good\nVERDICT: APPROVED") is True
    assert dispatcher_mod.revisor_approved("needs work\nVERDICT: CHANGES_REQUESTED") is False


def test_revisor_approved_fails_closed_on_missing_or_malformed_verdict() -> None:
    assert dispatcher_mod.revisor_approved("") is False
    assert dispatcher_mod.revisor_approved("no verdict line here") is False
    assert dispatcher_mod.revisor_approved("VERDICT: approved-ish") is False


def test_revisor_approved_true_with_trailing_blank_lines() -> None:
    assert dispatcher_mod.revisor_approved("looks good\nVERDICT: APPROVED\n\n\n") is True


def test_revisor_approved_true_with_markdown_emphasis() -> None:
    assert dispatcher_mod.revisor_approved("looks good\n**VERDICT: APPROVED**") is True


def test_revisor_approved_false_when_later_line_changes_verdict() -> None:
    assert dispatcher_mod.revisor_approved("VERDICT: APPROVED\nactually wait\nVERDICT: CHANGES_REQUESTED") is False


def test_revisor_approved_false_when_approved_is_not_the_verdict() -> None:
    assert dispatcher_mod.revisor_approved("Not VERDICT: APPROVED") is False


def test_revisor_approved_false_when_more_prose_follows_approved() -> None:
    assert dispatcher_mod.revisor_approved("VERDICT: APPROVED, pending a minor nit") is False


def test_revisor_approved_true_with_bold_label_only() -> None:
    assert dispatcher_mod.revisor_approved("looks good\n**VERDICT:** APPROVED") is True


def test_revisor_approved_true_with_trailing_code_fence() -> None:
    assert dispatcher_mod.revisor_approved("looks good\nVERDICT: APPROVED\n```") is True


def test_revisor_approved_true_with_blockquote() -> None:
    assert dispatcher_mod.revisor_approved("looks good\n> VERDICT: APPROVED") is True


def test_revisor_approved_true_with_underscore_emphasis() -> None:
    assert dispatcher_mod.revisor_approved("looks good\n__VERDICT: APPROVED__") is True


def test_revisor_approved_false_with_trailing_period() -> None:
    assert dispatcher_mod.revisor_approved("looks good\nVERDICT: APPROVED.") is False


def test_revisor_approved_reads_the_verdict_field() -> None:
    """The field is the contract now. With a schema in play the CLI can leave
    `result` holding "Structured output provided successfully", which no regex
    over the text can turn into a verdict."""
    assert dispatcher_mod.revisor_approved(
        "Structured output provided successfully", {"verdict": "APPROVED"}
    ) is True
    assert dispatcher_mod.revisor_approved(
        "Structured output provided successfully", {"verdict": "CHANGES_REQUESTED"}
    ) is False


def test_revisor_approved_lets_the_field_overrule_the_text() -> None:
    """Both present and disagreeing means the model wrote a verdict line into
    a field-carrying return. The field is the one it was asked for and the one
    the schema validated."""
    assert dispatcher_mod.revisor_approved("VERDICT: APPROVED", {"verdict": "CHANGES_REQUESTED"}) is False
    assert dispatcher_mod.revisor_approved("VERDICT: CHANGES_REQUESTED", {"verdict": "APPROVED"}) is True


def test_revisor_approved_falls_back_to_the_text_when_the_payload_has_no_verdict() -> None:
    """A payload without a verdict is not a rejection: it is a revisor that
    answered in prose, or through a path with no schema. Read the text, and
    let that path keep failing closed."""
    assert dispatcher_mod.revisor_approved("VERDICT: APPROVED", None) is True
    assert dispatcher_mod.revisor_approved("VERDICT: APPROVED", {"status": "complete"}) is True
    assert dispatcher_mod.revisor_approved("looks fine to me", {"status": "complete"}) is False


def test_is_rate_limit_error_true_on_429_status_with_unrelated_text() -> None:
    result = ClaudeResult(session_id=None, result_text="something went wrong", raw={"is_error": True, "api_error_status": 429})
    assert dispatcher_mod.is_rate_limit_error(result) is True


def test_is_rate_limit_error_true_on_429_status_as_string() -> None:
    result = ClaudeResult(session_id=None, result_text="something went wrong", raw={"is_error": True, "api_error_status": "429"})
    assert dispatcher_mod.is_rate_limit_error(result) is True


@pytest.mark.parametrize(
    "phrase",
    ["rate limit", "rate_limit", "usage limit", "hit your limit"],
)
def test_is_rate_limit_error_true_on_known_phrases(phrase) -> None:
    result = ClaudeResult(session_id=None, result_text=f"Error: {phrase} exceeded", raw={"is_error": True})
    assert dispatcher_mod.is_rate_limit_error(result) is True


def test_is_rate_limit_error_false_on_context_limit_reached() -> None:
    result = ClaudeResult(session_id=None, result_text="Context limit reached", raw={"is_error": True})
    assert dispatcher_mod.is_rate_limit_error(result) is False


def test_is_rate_limit_error_false_on_bare_quota_mention() -> None:
    result = ClaudeResult(session_id=None, result_text="over quota for this project", raw={"is_error": True})
    assert dispatcher_mod.is_rate_limit_error(result) is False


def test_is_rate_limit_error_false_when_not_an_error_even_with_429() -> None:
    result = ClaudeResult(session_id=None, result_text="rate limit", raw={"is_error": False, "api_error_status": 429})
    assert dispatcher_mod.is_rate_limit_error(result) is False


def test_run_task_cycle_keeps_handoff_tail_for_long_phase_output(tmp_path, monkeypatch) -> None:
    """A phase that answers in prose instead of the schema still gets clamped
    before it lands in the task file, tail included: that is where a prose
    revisor's verdict line is."""
    cfg = _make_config(tmp_path)
    long_body = "H" * 600 + "M" * 5000 + "T" * 1600

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        if role == "arquitecto":
            return dispatcher_mod.DispatchResult(success=True, session_id=None, result_text=long_body, account="cuenta1")
        if role == "revisor":
            return dispatcher_mod.DispatchResult(
                success=True, session_id=None, result_text="VERDICT: APPROVED", account="cuenta1",
            )
        return dispatcher_mod.DispatchResult(success=True, session_id=None, result_text="ok", account="cuenta1")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert "T" * 1500 in task.body
    assert "M" * 5000 not in task.body


def test_run_task_cycle_writes_the_rendered_handoff_into_the_task_file(tmp_path, monkeypatch) -> None:
    """What the next phase reads is the payload's sections, not the CLI's
    placeholder — the placeholder is the whole reason the payload exists."""
    cfg = _make_config(tmp_path)
    payload = {
        "status": "complete",
        "changed": ["dispatcher/handoff.py"],
        "subagents": [{"id": "ag_42", "doing": "running the suite"}],
        "paths": [{"path": "docs/implementations/task-1.md#schema", "holds": "the field list"}],
    }

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        if role == "arquitecto":
            return dispatcher_mod.DispatchResult(
                success=True, session_id=None, result_text="Structured output provided successfully",
                account="cuenta1", handoff=payload,
            )
        if role == "revisor":
            return dispatcher_mod.DispatchResult(
                success=True, session_id=None, result_text="", account="cuenta1",
                handoff={"status": "complete", "verdict": "APPROVED"},
            )
        return dispatcher_mod.DispatchResult(success=True, session_id=None, result_text="ok", account="cuenta1")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert "- dispatcher/handoff.py" in task.body
    assert "`ag_42` — running the suite" in task.body
    assert "`docs/implementations/task-1.md#schema` — the field list" in task.body
    assert "Structured output provided successfully" not in task.body


def test_run_task_cycle_skips_the_revisor_when_the_gates_blocked(tmp_path, monkeypatch) -> None:
    """Paying a revisor to read a branch whose tests fail buys a finding the
    dispatcher already has in hand. The round goes back around instead, and
    the next implementador opens on the findings in the task file."""
    cfg = _make_config(tmp_path, max_revision_rounds=2)
    roles = []
    blocked = _one_finding(gates.BLOCKING, "`npm test` failed; the log is at /data/.hive/tasks/task-1/gates-round-1.log")

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        roles.append((role, round_num))
        return dispatcher_mod.DispatchResult(
            success=True, session_id=None, result_text="VERDICT: APPROVED", account="cuenta1",
            gates=blocked if role == "implementador" and round_num == 1 else None,
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert roles == [
        ("arquitecto", None),
        ("implementador", 1),
        ("implementador", 2),
        ("revisor", 2),
        ("auditor", None),
    ]


def test_run_task_cycle_writes_the_gate_findings_under_the_handoff(tmp_path, monkeypatch) -> None:
    """Under the phase's own return, not instead of it: the role says what it
    did and the dispatcher says what it found, and the next phase to read the
    file can tell which is which."""
    cfg = _make_config(tmp_path, max_revision_rounds=1)
    found = _one_finding(gates.NOTE, "dispatcher/gates.py changed and no test did")

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        return dispatcher_mod.DispatchResult(
            success=True, session_id=None, result_text="VERDICT: APPROVED", account="cuenta1",
            handoff={"status": "complete", "changed": ["dispatcher/gates.py"]} if role == "implementador" else None,
            gates=found if role == "implementador" else None,
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert "**Dispatcher gates**" in task.body
    assert "dispatcher/gates.py changed and no test did" in task.body
    assert task.body.index("- dispatcher/gates.py") < task.body.index("**Dispatcher gates**")


def test_run_task_cycle_blocks_the_card_when_every_round_was_gated(tmp_path, monkeypatch) -> None:
    """A suite that stays red is not an approval by exhaustion: the rounds run
    out, the revisor was never asked, and the card goes to a human."""
    cfg = _make_config(tmp_path, max_revision_rounds=2)
    roles = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        roles.append(role)
        return dispatcher_mod.DispatchResult(
            success=True, session_id=None, result_text="VERDICT: APPROVED", account="cuenta1",
            gates=_one_finding(gates.BLOCKING) if role == "implementador" else None,
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert roles == ["arquitecto", "implementador", "implementador"]
    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED


def test_run_task_cycle_stops_when_the_arquitecto_blocked(tmp_path, monkeypatch, caplog) -> None:
    """An implementador handed a task nobody specified does not stop, it invents
    the missing decision. The arquitecto is the last phase that can say so
    before anything is written, so its `blocked` ends the task there, and what
    it put in `pending` is what a human has to answer to restart it."""
    cfg = _make_config(tmp_path)
    roles = []
    missing = [
        "What the board shows while `/api/tasks` is in flight",
        "Whether `warnings` renders per card or once for the page",
    ]

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        roles.append(role)
        return dispatcher_mod.DispatchResult(
            success=True, session_id=None, result_text="Structured output provided successfully",
            account="cuenta1",
            handoff={"status": "blocked", "pending": missing} if role == "arquitecto" else {"status": "complete"},
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    with caplog.at_level("WARNING"):
        outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert roles == ["arquitecto"]
    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    # The reason has to reach the operator: a block naming nothing is a task
    # nobody can restart.
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert all(item in logged for item in missing)
    # And the task file keeps it, for whoever opens the card instead of the log.
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert missing[0] in task.body


def test_run_task_cycle_releases_the_implementador_on_a_partial_arquitecto(tmp_path, monkeypatch) -> None:
    """Only `blocked` blocks. `partial` is the ordinary arquitecto — it left
    work for the next phase, which is what `pending` is for and exactly what
    the implementador is there to pick up."""
    cfg = _make_config(tmp_path)
    roles = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        roles.append(role)
        return dispatcher_mod.DispatchResult(
            success=True, session_id=None, result_text="Structured output provided successfully",
            account="cuenta1",
            handoff={"status": "partial", "pending": ["Wire the route the plan names"], "verdict": "APPROVED"},
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert roles == ["arquitecto", "implementador", "revisor", "auditor"]
    assert outcome is dispatcher_mod.CycleOutcome.FINISHED


def test_run_task_cycle_approves_on_a_verdict_field(tmp_path, monkeypatch) -> None:
    """End to end: a revisor whose text says nothing still gates the cycle,
    and the auditor runs after it."""
    cfg = _make_config(tmp_path)
    roles = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        roles.append(role)
        return dispatcher_mod.DispatchResult(
            success=True, session_id=None, result_text="Structured output provided successfully",
            account="cuenta1",
            handoff={"status": "complete", "verdict": "APPROVED"} if role == "revisor" else {"status": "complete"},
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert roles == ["arquitecto", "implementador", "revisor", "auditor"]
    assert outcome is dispatcher_mod.CycleOutcome.FINISHED


def test_run_task_cycle_rejects_on_a_verdict_field(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path, max_revision_rounds=1)
    roles = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        roles.append(role)
        return dispatcher_mod.DispatchResult(
            success=True, session_id=None, result_text="Structured output provided successfully",
            account="cuenta1",
            handoff={"status": "complete", "verdict": "CHANGES_REQUESTED"} if role == "revisor" else {"status": "complete"},
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert "auditor" not in roles
    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED


def test_run_task_cycle_gives_every_role_the_scratch_dir(tmp_path, monkeypatch) -> None:
    """The budget only works if there is somewhere to put what does not fit.
    The directory is created before the first phase, because a role told to
    write into a path that does not exist will spend a tool call on mkdir or
    quietly inline the detail instead."""
    cfg = _make_config(tmp_path, max_revision_rounds=1)
    captured = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        captured.append((role, prompt))
        return dispatcher_mod.DispatchResult(
            success=True, session_id=None, result_text="VERDICT: APPROVED", account="cuenta1",
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    scratch = scratch_dir(cfg.hive_tasks_dir, "task-1")
    assert os.path.isdir(scratch)
    for role, prompt in captured:
        assert scratch in prompt, f"{role}'s prompt does not say where detail goes"


def test_run_task_cycle_scratch_dir_is_not_mistaken_for_a_task(tmp_path, monkeypatch) -> None:
    """It sits beside `<task-id>.md` in the same directory the dispatcher
    lists tasks from; `list_task_ids` counts `.md` files, so a directory named
    after the task is invisible to it. This is the test that keeps it so."""
    cfg = _make_config(tmp_path, max_revision_rounds=1)

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        return dispatcher_mod.DispatchResult(
            success=True, session_id=None, result_text="VERDICT: APPROVED", account="cuenta1",
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert list_task_ids(cfg.hive_tasks_dir) == ["task-1"]


def _dispatch_call_kwargs(cfg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
    return dict(role=role, prompt=prompt, model=model, effort=effort, round_num=round_num)


def test_run_task_cycle_approves_on_first_round(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    calls = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        calls.append(_dispatch_call_kwargs(cfg_arg, task_id, slug, role, prompt, resume_session_id, model, effort, round_num))
        if role == "revisor":
            return dispatcher_mod.DispatchResult(
                success=True, session_id=None, result_text="VERDICT: APPROVED", account="cuenta1",
            )
        return dispatcher_mod.DispatchResult(success=True, session_id=None, result_text="ok", account="cuenta1")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    roles = [c["role"] for c in calls]
    assert roles == ["arquitecto", "implementador", "revisor", "auditor"]
    assert all(c["effort"] == "medium" for c in calls)
    assert all(c["model"] == "opus" for c in calls)
    assert outcome is dispatcher_mod.CycleOutcome.FINISHED


def test_run_task_cycle_escalates_effort_after_configured_round(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path, max_revision_rounds=3, escalate_effort_after_round=2, escalated_effort="high")
    calls = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        calls.append(_dispatch_call_kwargs(cfg_arg, task_id, slug, role, prompt, resume_session_id, model, effort, round_num))
        if role == "revisor" and "round 3" in prompt:
            return dispatcher_mod.DispatchResult(
                success=True, session_id=None, result_text="VERDICT: APPROVED", account="cuenta1",
            )
        if role == "revisor":
            return dispatcher_mod.DispatchResult(
                success=True, session_id=None, result_text="VERDICT: CHANGES_REQUESTED", account="cuenta1",
            )
        return dispatcher_mod.DispatchResult(success=True, session_id=None, result_text="ok", account="cuenta1")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    early_rounds = [
        c for c in calls
        if c["role"] in ("implementador", "revisor") and ("round 1" in c["prompt"] or "round 2" in c["prompt"])
    ]
    round3 = [c for c in calls if c["role"] in ("implementador", "revisor") and "round 3" in c["prompt"]]
    assert early_rounds and all(c["effort"] == "medium" for c in early_rounds)
    assert round3, "expected round 3 to run"
    assert all(c["effort"] == "high" for c in round3)
    assert outcome is dispatcher_mod.CycleOutcome.FINISHED


# --- effort escalation on a repeated finding (ADR 73) ------------------------


def _findings_cycle(tmp_path, monkeypatch, findings_by_round, **overrides):
    """A cycle whose revisor sends rounds 1 and 2 back and approves round 3.

    `findings_by_round` maps a round to the `findings` its review returns.
    The round-count rule is pushed out of the way by default
    (`escalate_effort_after_round: 5`) so only the signal can escalate.
    Returns each implementador round's effort, by round.
    """
    settings = {"max_revision_rounds": 3, "escalate_effort_after_round": 5, "escalated_effort": "high"}
    settings.update(overrides)
    cfg = _make_config(tmp_path, **settings)
    efforts = {}

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        current = next((n for n in (1, 2, 3) if f"round {n}" in prompt), None)
        if role == "implementador":
            efforts[current] = effort
        if role == "revisor":
            verdict = "APPROVED" if current == 3 else "CHANGES_REQUESTED"
            return dispatcher_mod.DispatchResult(
                success=True, session_id=None, result_text="ok", account="cuenta1",
                handoff={"status": "complete", "verdict": verdict, "findings": findings_by_round.get(current, [])},
            )
        return dispatcher_mod.DispatchResult(success=True, session_id=None, result_text="ok", account="cuenta1")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)
    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)
    assert outcome is dispatcher_mod.CycleOutcome.FINISHED
    return efforts


def test_run_task_cycle_escalates_round_3_when_the_same_finding_comes_back(tmp_path, monkeypatch, caplog) -> None:
    same = ["`tasks.claim` never releases the lock on an error."]
    with caplog.at_level("INFO"):
        efforts = _findings_cycle(
            tmp_path, monkeypatch,
            # Copied verbatim, give or take what the fingerprint absorbs.
            {1: same, 2: ["`Tasks.claim` never releases the lock on an error"]},
            escalate_effort_on_repeated_finding=True,
        )

    assert efforts == {1: "medium", 2: "medium", 3: "high"}
    fired = [r.getMessage() for r in caplog.records if "same finding(s) twice" in r.getMessage()]
    assert len(fired) == 1
    assert "task-1" in fired[0] and "round 3" in fired[0] and "effort high" in fired[0]
    assert "never releases the lock" in fired[0]


def test_run_task_cycle_does_not_escalate_a_repeated_finding_with_the_key_off(tmp_path, monkeypatch, caplog) -> None:
    same = ["`tasks.claim` never releases the lock on an error."]
    with caplog.at_level("INFO"):
        efforts = _findings_cycle(tmp_path, monkeypatch, {1: same, 2: same})

    assert efforts == {1: "medium", 2: "medium", 3: "medium"}
    assert not [r for r in caplog.records if "same finding(s) twice" in r.getMessage()]


def test_run_task_cycle_does_not_escalate_on_different_findings(tmp_path, monkeypatch) -> None:
    efforts = _findings_cycle(
        tmp_path, monkeypatch,
        {1: ["`tasks.claim` never releases the lock on an error."],
         2: ["The new route has no test for a missing task."]},
        escalate_effort_on_repeated_finding=True,
    )

    assert efforts == {1: "medium", 2: "medium", 3: "medium"}


def test_run_task_cycle_counts_rejected_debt_as_a_finding(tmp_path, monkeypatch) -> None:
    """A debt the revisor rejects is a finding (see `run_task_cycle`), so
    rejecting the same one twice is the same reason twice."""
    cfg = _make_config(
        tmp_path, max_revision_rounds=3, escalate_effort_after_round=5,
        escalated_effort="high", escalate_effort_on_repeated_finding=True,
    )
    efforts = {}
    ruling = {"debt": "No test for the retry path", "ruling": "rejected", "why": "It is in scope."}

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        current = next((n for n in (1, 2, 3) if f"round {n}" in prompt), None)
        if role == "implementador":
            efforts[current] = effort
        if role == "revisor":
            verdict = "APPROVED" if current == 3 else "CHANGES_REQUESTED"
            rulings = [] if current == 3 else [ruling]
            return dispatcher_mod.DispatchResult(
                success=True, session_id=None, result_text="ok", account="cuenta1",
                handoff={"status": "complete", "verdict": verdict, "findings": [], "debt_rulings": rulings},
            )
        return dispatcher_mod.DispatchResult(success=True, session_id=None, result_text="ok", account="cuenta1")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert efforts == {1: "medium", 2: "medium", 3: "high"}


def test_run_task_cycle_round_count_still_escalates_with_the_signal_on(tmp_path, monkeypatch) -> None:
    efforts = _findings_cycle(
        tmp_path, monkeypatch,
        {1: ["`tasks.claim` never releases the lock on an error."],
         2: ["The new route has no test for a missing task."]},
        escalate_effort_after_round=2, escalate_effort_on_repeated_finding=True,
    )

    assert efforts == {1: "medium", 2: "medium", 3: "high"}


def test_run_task_cycle_blocks_when_revision_rounds_exhausted(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path, max_revision_rounds=2)
    calls = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        calls.append(role)
        if role == "revisor":
            return dispatcher_mod.DispatchResult(
                success=True, session_id=None, result_text="VERDICT: CHANGES_REQUESTED", account="cuenta1",
            )
        return dispatcher_mod.DispatchResult(success=True, session_id=None, result_text="ok", account="cuenta1")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert calls.count("revisor") == 2
    assert "auditor" not in calls
    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED


def _approving_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
    """Every phase works and the revisor approves on the first round."""
    return dispatcher_mod.DispatchResult(
        success=True,
        session_id=None,
        result_text="VERDICT: APPROVED" if role == "revisor" else "ok",
        account="cuenta1",
    )


def _rejecting_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
    """Every phase works but the revisor never approves: the task ends blocked."""
    return dispatcher_mod.DispatchResult(
        success=True,
        session_id=None,
        result_text="VERDICT: CHANGES_REQUESTED" if role == "revisor" else "ok",
        account="cuenta1",
    )


def test_run_task_cycle_says_why_a_task_came_out_blocked(tmp_path, monkeypatch, fake_git, caplog) -> None:
    """Blocked is the status that needs a human, and it was the one that left
    no trace: with no issue id there is no board to write to, so the run ended
    at exit 0 with the task still `pending` and nothing anywhere saying a
    revisor had refused it three times. This line is that trace."""
    cfg = _make_config(tmp_path, max_revision_rounds=2)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _rejecting_dispatch_phase)

    with caplog.at_level("WARNING"):
        dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert any(
        "task-1" in r.getMessage() and "blocked after 2 of 2" in r.getMessage()
        for r in caplog.records
    )


def test_the_blocked_line_counts_rounds_that_ran_not_rounds_allowed(tmp_path, monkeypatch, fake_git, caplog) -> None:
    """`max_revision_rounds: 0` blocks without dispatching anybody, and the
    two readings — nobody was asked, versus the cap was spent — call for
    different fixes. The count is of rounds that actually ran, so the line
    tells them apart."""
    cfg = _make_config(tmp_path, max_revision_rounds=0)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _rejecting_dispatch_phase)

    with caplog.at_level("WARNING"):
        dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert any("blocked after 0 of 0" in r.getMessage() for r in caplog.records)


def test_a_timed_out_phase_says_so_instead_of_ending_the_run_at_exit_zero(
    tmp_path, monkeypatch, fake_git, caplog,
) -> None:
    """T-010's implementador was killed by `phase_timeout_seconds` a minute
    after it finished, and this branch threw the diagnosis away: the run ended
    at exit 0 with its last line being "goes to account cuenta1", and why had
    to be reconstructed from two timestamps and an uncommitted worktree.
    docker_exec already writes the sentence; nobody was saying it."""
    cfg = _make_config(tmp_path)

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        if role == "implementador":
            return dispatcher_mod.DispatchResult(
                success=False, session_id=None,
                result_text="claude timed out after 1800s", account="cuenta1",
            )
        return dispatcher_mod.DispatchResult(
            success=True, session_id=None, result_text="ok", account="cuenta1",
            handoff={"status": "complete"},
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    with caplog.at_level("ERROR"):
        dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    logged = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any("claude timed out after 1800s" in m for m in logged)
    # Which of the three attempts died is half the diagnosis.
    assert any("implementador (round 1)" in m and "task-1" in m for m in logged)


def test_a_phase_that_failed_without_a_word_still_logs_that_it_failed(
    tmp_path, monkeypatch, fake_git, caplog,
) -> None:
    """An empty `result_text` is the case that produced the silence to begin
    with, and interpolating it would log a line ending in a colon and nothing.
    The line says the output was missing, which is itself the finding."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase",
        lambda *a, **kw: dispatcher_mod.DispatchResult(
            success=False, session_id=None, result_text="", account="",
        ),
    )

    with caplog.at_level("ERROR"):
        dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert any(
        "no diagnosis" in r.getMessage()
        for r in caplog.records if r.levelname == "ERROR"
    )


@pytest.mark.parametrize(
    "dispatch,ended",
    [
        (_approving_dispatch_phase, dispatcher_mod.CycleOutcome.FINISHED),
        (_rejecting_dispatch_phase, dispatcher_mod.CycleOutcome.BLOCKED),
    ],
    ids=["done", "blocked"],
)
def test_run_task_cycle_drops_the_review_worktrees_when_the_task_ends(
    tmp_path, monkeypatch, fake_git, dispatch, ended,
) -> None:
    """Blocked is as terminal as done here: nobody is reading those checkouts."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", dispatch)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is ended
    assert fake_git.review_cleanups == [("agent-cuenta1", cfg.projects_root, "myproj", "task-1")]
    assert fake_git.restored == [(f"{cfg.projects_root}/myproj", "1000:1000")]


def test_run_task_cycle_drops_the_review_worktrees_after_a_failed_phase(
    tmp_path, monkeypatch, fake_git,
) -> None:
    """A cycle that stops early is over too, and left worktrees behind doing it."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase",
        lambda *a, **kw: dispatcher_mod.DispatchResult(success=False, session_id=None, result_text="stop", account=""),
    )

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert len(fake_git.review_cleanups) == 1


def test_run_task_cycle_leaves_another_owners_worktrees_alone(tmp_path, monkeypatch, fake_git) -> None:
    """The lock holder is still working in them; deleting them would break its phase."""
    cfg = _make_config(tmp_path)
    acquire_lock(cfg.hive_tasks_dir, "task-1", owner="otro")

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        return ClaudeResult(
            session_id=None,
            result_text=(
                "Current session: 10% used · resets later\n"
                "Current week (all models): 10% used · resets later"
            ),
            raw={},
        )

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert fake_git.review_cleanups == []
    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED


def test_run_task_cycle_still_finishes_when_the_cleanup_fails(tmp_path, monkeypatch, caplog) -> None:
    """Housekeeping must never turn a finished task into a traceback."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    def exploding_cleanup(container, projects_root, slug, task_id):
        raise RuntimeError("docker daemon went away")

    monkeypatch.setattr(dispatcher_mod.docker_exec, "remove_review_worktrees", exploding_cleanup)

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.FINISHED
    assert "docker daemon went away" in caplog.text


def test_run_task_cycle_cleanup_does_not_swallow_a_real_failure(tmp_path, monkeypatch) -> None:
    """The cleanup runs in a `finally`, which must not eat the exception it runs after."""
    cfg = _make_config(tmp_path)

    def exploding_dispatch_phase(*args, **kwargs):
        raise RuntimeError("phase blew up")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", exploding_dispatch_phase)

    with pytest.raises(RuntimeError, match="phase blew up"):
        dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)


def test_cleanup_container_returns_none_without_accounts(tmp_path) -> None:
    """A config with no accounts has nothing to run docker exec in."""
    assert dispatcher_mod.cleanup_container(_make_config(tmp_path, accounts=[])) is None


def test_run_task_cycle_does_not_merge_by_default(tmp_path, monkeypatch, fake_git) -> None:
    """merge_on_done is off, so a done task leaves the project's branch alone."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.FINISHED
    assert fake_git.merges == []


def test_run_task_cycle_merges_a_done_task_when_asked_to(tmp_path, monkeypatch, fake_git) -> None:
    cfg = _make_config(tmp_path, merge_on_done=True)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.FINISHED
    assert fake_git.merges == [("agent-cuenta1", cfg.projects_root, "myproj", "task-1")]
    assert fake_git.premerges == [("agent-cuenta1", cfg.projects_root, "myproj", "task-1")]


def _premerge_outcome(status, **kw):
    de = dispatcher_mod.docker_exec
    return de.PreMergeOutcome(
        status, "main", "b" * 40, "a" * 40, kw.pop("detail", f"pre-merge {status}"), **kw,
    )


def test_run_task_cycle_does_not_merge_a_branch_that_conflicts_with_the_base(
    tmp_path, monkeypatch, fake_git,
) -> None:
    """`docs/plans/autonomy.md` *Decisions*, 2: merge_on_done goes through the check."""
    cfg = _make_config(tmp_path, merge_on_done=True)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)
    fake_git.premerge = _premerge_outcome(
        dispatcher_mod.docker_exec.PREMERGE_CONFLICT, conflicts=("src/app.py", "README.md"),
    )

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.FINISHED
    assert fake_git.merges == []
    assert getattr(fake_git, "adopted", []) == []
    task = _task(cfg)
    assert task.status == dispatcher_mod.CONFLICT_STATUS
    assert task.merged is None
    assert dispatcher_mod.PRE_MERGE_HEADING in task.body
    assert "src/app.py, README.md" in task.body
    assert "b" * 40 in task.body


def test_run_task_cycle_does_not_merge_when_the_merged_result_fails_its_tests(
    tmp_path, monkeypatch, fake_git,
) -> None:
    cfg = _make_config(tmp_path, merge_on_done=True, gates_enabled=True)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)
    fake_git.premerge = _premerge_outcome(
        dispatcher_mod.docker_exec.PREMERGE_CLEAN, merged_tip="c" * 40,
        workdir=f"{cfg.projects_root}/myproj/worktrees/task-1/premerge",
    )
    gated = []

    def fake_gates(container, workdir, project_dir, *, test_timeout_seconds, test_log_path=None):
        gated.append(workdir)
        if workdir.endswith("/premerge"):
            return gates.Report([gates.Finding(gates.TESTS_RUN, "`pytest` failed (exit 1)", gates.BLOCKING)])
        return gates.Report([])

    monkeypatch.setattr(dispatcher_mod.gates, "run", fake_gates)

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert gated[-1] == f"{cfg.projects_root}/myproj/worktrees/task-1/premerge"
    assert fake_git.merges == []
    assert getattr(fake_git, "adopted", []) == []
    assert fake_git.premerge_drops == ["task-1"]
    task = _task(cfg)
    assert task.status == dispatcher_mod.CONFLICT_STATUS
    assert "`tests-run`" in task.body and "b" * 40 in task.body


def test_run_task_cycle_keeps_a_clean_green_premerge_and_then_merges(
    tmp_path, monkeypatch, fake_git,
) -> None:
    cfg = _make_config(tmp_path, merge_on_done=True, gates_enabled=True)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)
    fake_git.premerge = _premerge_outcome(
        dispatcher_mod.docker_exec.PREMERGE_CLEAN, merged_tip="c" * 40,
        workdir=f"{cfg.projects_root}/myproj/worktrees/task-1/premerge",
    )
    monkeypatch.setattr(
        dispatcher_mod.gates, "run", lambda *a, **k: gates.Report([]),
    )

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert fake_git.adopted == [fake_git.premerge]
    assert fake_git.merges == [("agent-cuenta1", cfg.projects_root, "myproj", "task-1")]
    assert _task(cfg).status == "done"


def test_a_premerge_check_that_cannot_run_merges_nothing_and_changes_no_status(
    tmp_path, monkeypatch, fake_git,
) -> None:
    cfg = _make_config(tmp_path, merge_on_done=True)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)
    fake_git.premerge = _premerge_outcome(dispatcher_mod.docker_exec.REFUSED)

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert fake_git.merges == []
    assert _task(cfg).status == "done"


def test_a_task_stopped_on_a_conflict_is_done_again_once_the_check_passes(
    tmp_path, fake_git,
) -> None:
    cfg = _make_config(tmp_path)
    tasks.handoff(cfg.hive_tasks_dir, "task-1", dispatcher_mod.CONFLICT_STATUS, "stopped")

    verdict = dispatcher_mod.pre_merge_check(cfg, "agent-cuenta1", "task-1", "myproj")

    assert verdict.passed
    assert _task(cfg).status == "done"


def test_run_task_cycle_still_drops_the_entries_its_auditor_filed(
    tmp_path, monkeypatch, fake_git,
) -> None:
    """The gate in `drop_promoted` is a no-op on the automatic path, and that is
    a claim about control flow rather than about either function: the only line
    that reaches the drop runs after `run_phase(ctx, "auditor", final=True, …)`
    returned, and that returning phase is what appended the `## auditor` section
    the gate reads. Pinned here rather than argued in a comment, so that a
    refactor reordering those lines fails a test (`docs/decisions.md` ADR 34)."""
    cfg = _make_config(tmp_path, merge_on_done=True)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)
    inbox = Path(learnings.ensure_dirs(cfg.hive_tasks_dir)) / learnings.INBOX_NAME
    filed = inbox / "task-1-trap.md"
    filed.write_text(
        "---\n"
        f"project: myproj\ntask: task-1\nphase: implementador\n"
        f"scope: {learnings.SCOPE_PROJECT}\nstatus: {learnings.UNCONFIRMED}\n"
        "when: the suite talks to a database\n"
        "---\n\n## Rule\n\nStart postgres before the suite.\n"
    )

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert "## auditor" in read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1")).body
    assert not filed.exists()
    assert (Path(learnings.dropped_dir(cfg.hive_tasks_dir)) / "task-1-trap.md").exists()


def test_run_task_cycle_does_not_merge_a_blocked_task(tmp_path, monkeypatch, fake_git) -> None:
    """Nothing approved this work, so merge_on_done has nothing to act on."""
    cfg = _make_config(tmp_path, merge_on_done=True)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _rejecting_dispatch_phase)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    assert fake_git.merges == []


def test_run_task_cycle_logs_a_refused_merge_and_still_finishes(tmp_path, monkeypatch, caplog) -> None:
    """A merge that cannot happen is a warning, not a failed task: the work is committed."""
    cfg = _make_config(tmp_path, merge_on_done=True)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    def refusing_merge(container, projects_root, slug, task_id):
        return dispatcher_mod.docker_exec.MergeOutcome(
            dispatcher_mod.docker_exec.REFUSED, "main", "has uncommitted changes"
        )

    monkeypatch.setattr(dispatcher_mod.docker_exec, "merge_task_branch", refusing_merge)

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.FINISHED
    assert "has uncommitted changes" in caplog.text


def test_run_task_cycle_survives_a_merge_that_raises(tmp_path, monkeypatch, caplog) -> None:
    cfg = _make_config(tmp_path, merge_on_done=True)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    def exploding_merge(container, projects_root, slug, task_id):
        raise RuntimeError("docker daemon went away")

    monkeypatch.setattr(dispatcher_mod.docker_exec, "merge_task_branch", exploding_merge)

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.FINISHED
    assert "docker daemon went away" in caplog.text


def test_run_task_cycle_hands_the_tree_back_after_merging(tmp_path, monkeypatch, fake_git) -> None:
    """git writes to .git/ as root here too, so the merge restores ownership as well."""
    cfg = _make_config(tmp_path, merge_on_done=True)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    project_dir = f"{cfg.projects_root}/myproj"
    # Once for the merge, once for the review-worktree cleanup that follows it.
    assert fake_git.restored == [(project_dir, "1000:1000"), (project_dir, "1000:1000")]


def _phase_exec(result):
    """A fake exec_claude that clears the usage probe and then returns `result`."""

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        if "usage" in prompt.lower():
            return ClaudeResult(
                session_id=None,
                result_text=(
                    "Current session: 10% used · resets later\n"
                    "Current week (all models): 10% used · resets later"
                ),
                raw={},
            )
        return result

    return fake_exec_claude


def _fake_worktree(monkeypatch):
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}/work",
    )


def test_dispatch_phase_commits_the_writing_phase(tmp_path, monkeypatch, fake_git) -> None:
    """Nothing used to commit, so the revisor reviewed an empty diff."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "exec_claude",
        _phase_exec(ClaudeResult(session_id="sess-1", result_text="done", raw={"is_error": False})),
    )
    _fake_worktree(monkeypatch)

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "do it", round_num=2)

    assert len(fake_git.commits) == 1
    commit = fake_git.commits[0]
    assert commit["workdir"].endswith("/worktrees/task-1/work")
    assert commit["author_name"] == "implementador (cuenta1)"
    assert commit["author_email"] == "implementador@ia-harness.invalid"
    # The role, the task, the round and the session that produced it: enough to
    # trace any commit back to the transcript that explains it.
    assert commit["message"].startswith("agent(implementador): task-1 round 2")
    assert "Account: cuenta1" in commit["message"]
    assert "Session: sess-1" in commit["message"]


def test_dispatch_phase_at_base_commits_nothing_even_when_the_phase_modified_a_file(
    tmp_path, monkeypatch, fake_git,
) -> None:
    """A draft run's arquitecto reads the base; what it leaves is discarded (ADR 94)."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "exec_claude",
        _phase_exec(ClaudeResult(session_id="sess-1", result_text="done", raw={"is_error": False})),
    )
    seen = []

    def spy_create_worktree(container, projects_root, slug, task_id, role, **kwargs):
        seen.append(kwargs)
        return f"{projects_root}/{slug}/worktrees/{task_id}/{role}"

    monkeypatch.setattr(dispatcher_mod.docker_exec, "create_worktree", spy_create_worktree)
    fake_git.dirty = ("src/app.py",)

    result = dispatcher_mod.dispatch_phase(cfg, "d-1", "myproj", "arquitecto", "draft", at_base=True)

    assert result.success is True
    assert seen == [{"at_base": True}]
    assert fake_git.commits == []


def test_dispatch_phase_without_at_base_still_commits_the_arquitecto_in_the_writer_worktree(
    tmp_path, monkeypatch, fake_git,
) -> None:
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "exec_claude",
        _phase_exec(ClaudeResult(session_id="sess-1", result_text="done", raw={"is_error": False})),
    )
    _fake_worktree(monkeypatch)

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "plan it")

    [commit] = fake_git.commits
    assert commit["workdir"].endswith("/worktrees/task-1/work")
    assert commit["author_name"] == "arquitecto (cuenta1)"


def test_dispatch_phase_holds_the_auditor_to_its_docs(tmp_path, monkeypatch, fake_git) -> None:
    """It writes, so it commits — but it runs after the revisor has approved,
    and a commit taking the whole tree would land code nobody read."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "exec_claude",
        _phase_exec(ClaudeResult(session_id="sess-1", result_text="indexed", raw={"is_error": False})),
    )
    _fake_worktree(monkeypatch)

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "auditor", "file the indexes")

    assert len(fake_git.commits) == 1
    assert fake_git.commits[0]["paths"] == ("docs",)


def test_dispatch_phase_leaves_the_other_writers_unscoped(tmp_path, monkeypatch, fake_git) -> None:
    """The work itself has no fixed shape to hold a role to."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "exec_claude",
        _phase_exec(ClaudeResult(session_id="sess-1", result_text="done", raw={"is_error": False})),
    )
    _fake_worktree(monkeypatch)

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "do it")

    assert fake_git.commits[0]["paths"] is None


@pytest.mark.parametrize("role", ["auditor", "implementador"])
def test_dispatch_phase_keeps_the_charter_out_of_every_commit(
    tmp_path, monkeypatch, fake_git, role
) -> None:
    """The scope differs per role; the exclusion does not. The auditor is the
    interesting one, because docs/ is its scope and the charter lives there."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "exec_claude",
        _phase_exec(ClaudeResult(session_id="sess-1", result_text="done", raw={"is_error": False})),
    )
    _fake_worktree(monkeypatch)

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", role, "do it")

    assert fake_git.commits[0]["excludes"] == (project_docs.CHARTER,)


def test_dispatch_phase_does_not_commit_a_reviewing_phase(tmp_path, monkeypatch, fake_git) -> None:
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "exec_claude",
        _phase_exec(ClaudeResult(session_id="sess-1", result_text="VERDICT: APPROVED", raw={"is_error": False})),
    )
    _fake_worktree(monkeypatch)

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review it")

    assert fake_git.commits == []


def test_dispatch_phase_does_not_commit_a_failed_phase(tmp_path, monkeypatch, fake_git) -> None:
    """A failed phase is retried in the same worktree, resuming the session."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "exec_claude",
        _phase_exec(ClaudeResult(session_id="sess-1", result_text="boom", raw={})),
    )
    _fake_worktree(monkeypatch)

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "do it")

    assert result.success is False
    assert fake_git.commits == []


def test_dispatch_phase_does_not_commit_a_rate_limited_phase(tmp_path, monkeypatch, fake_git) -> None:
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "exec_claude",
        _phase_exec(ClaudeResult(
            session_id="sess-1", result_text="429 rate limit exceeded", raw={"is_error": True},
        )),
    )
    _fake_worktree(monkeypatch)

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "do it")

    # The phase is retried on another account (there is none here), so its
    # half-finished tree must not be committed as if it were the round's work.
    assert result.success is False
    assert fake_git.commits == []


def test_dispatch_phase_hands_the_tree_back_to_its_owner(tmp_path, monkeypatch, fake_git) -> None:
    """The agents run as root against a bind mount owned by the host user."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "exec_claude",
        _phase_exec(ClaudeResult(session_id="sess-1", result_text="done", raw={"is_error": False})),
    )
    _fake_worktree(monkeypatch)

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "do it")

    assert fake_git.restored == [(f"{cfg.projects_root}/myproj", "1000:1000")]


def test_dispatch_phase_hands_the_tree_back_even_when_the_phase_raises(tmp_path, monkeypatch, fake_git) -> None:
    """`git worktree add` alone is enough to leave root-owned files behind."""
    cfg = _make_config(tmp_path)

    def raising_create_worktree(container, projects_root, slug, task_id, role):
        raise RuntimeError("git worktree add failed")

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", _phase_exec(None))
    monkeypatch.setattr(dispatcher_mod.docker_exec, "create_worktree", raising_create_worktree)

    with pytest.raises(RuntimeError):
        dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "do it")

    assert fake_git.restored == [(f"{cfg.projects_root}/myproj", "1000:1000")]


# --- the task file is the board's data, and nothing mirrors it (ADR 59) ----


def _approve_on_first_round(monkeypatch):
    """Every phase succeeds and the revisor approves, so the cycle reaches done."""

    def fake_dispatch_phase(cfg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        return dispatcher_mod.DispatchResult(
            success=True,
            session_id=None,
            result_text="VERDICT: APPROVED" if role == "revisor" else "ok",
            account="cuenta1",
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)


def test_a_finished_cycle_leaves_done_in_the_task_file_and_no_board_key(
    tmp_path, monkeypatch, caplog,
) -> None:
    """The run opens no card and stores no id for one: the task file ends
    `done`, carries no `kanban_issue_id`, and the log never mentions a board."""
    cfg = _make_config(tmp_path)
    _approve_on_first_round(monkeypatch)

    with caplog.at_level("INFO", logger=dispatcher_mod.logger.name):
        outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.FINISHED
    path = task_file_path(cfg.hive_tasks_dir, "task-1")
    assert read_task_file(path).status == "done"
    assert "kanban_issue_id" not in Path(path).read_text()
    assert "kanban" not in caplog.text.lower()


def test_a_cycle_drops_the_kanban_issue_id_an_older_run_left_in_the_task_file(
    tmp_path, monkeypatch,
) -> None:
    """A task seeded by `run-task --kanban-issue-id` before ADR 59 still runs,
    and its first phase's write is the one that drops the key."""
    cfg = _make_config(tmp_path)
    path = Path(task_file_path(cfg.hive_tasks_dir, "task-1"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntask_id: task-1\nstatus: pending\nowner: null\ndepends_on: []\n"
        "heartbeat: null\nkanban_issue_id: aaaabbbb-cccc-dddd-eeee-ffff00001111\n"
        "---\n\nan earlier note\n"
    )
    _approve_on_first_round(monkeypatch)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.FINISHED
    text = path.read_text()
    assert "kanban_issue_id" not in text and "aaaabbbb" not in text
    assert "an earlier note" in text


# --- the mapping phase, for a project nobody has written down yet ---------


def _recording_dispatch_phase(calls, failing=(), handoffs=None):
    """Records every phase's role, model and turn budget, and approves once.

    `handoffs` maps a role to the structured return its phase hands back, for
    the tests that care what the dispatcher does with one."""

    def fake_dispatch_phase(
        cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None,
        effort=None, round_num=None, max_turns=None, **kwargs,
    ):
        calls.append(dict(role=role, model=model, max_turns=max_turns, prompt=prompt))
        return dispatcher_mod.DispatchResult(
            success=role not in failing,
            session_id=None,
            result_text="VERDICT: APPROVED" if role == "revisor" else "ok",
            account="cuenta1",
            handoff=(handoffs or {}).get(role),
        )

    return fake_dispatch_phase


def _fake_index(monkeypatch, exists):
    """Answers the one `docker exec test -e` that decides whether to map."""
    asked = []

    def fake_path_exists(container, path):
        asked.append((container, path))
        return exists

    monkeypatch.setattr(dispatcher_mod.docker_exec, "path_exists", fake_path_exists)
    return asked


def test_run_task_cycle_does_not_map_unless_the_operator_asked(tmp_path, monkeypatch) -> None:
    """Mapping is a whole phase of quota spent shipping no code, so it is
    opt-in: a harness that never turned it on does not even pay the exec that
    would find out whether this project has docs."""
    cfg = _make_config(tmp_path)
    asked = _fake_index(monkeypatch, exists=False)
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _recording_dispatch_phase(calls))

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert asked == []
    assert calls[0]["role"] == "arquitecto"


def test_run_task_cycle_maps_a_project_that_has_no_index_first(tmp_path, monkeypatch) -> None:
    """Before the arquitecto, because what the mapper writes is what the
    arquitecto is told to read."""
    cfg = _make_config(
        tmp_path, mapping_enabled=True, mapping_model="haiku", mapping_max_turns=12,
    )
    asked = _fake_index(monkeypatch, exists=False)
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _recording_dispatch_phase(calls))

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    # Asked of the project's own checkout, not of a task worktree: the
    # checkout is the state a later task will inherit.
    assert asked == [("agent-cuenta1", f"{cfg.projects_root}/myproj/{project_docs.INDEX}")]
    assert calls[0]["role"] == project_docs.MAPPER_ROLE
    assert calls[0]["model"] == "haiku"
    assert calls[0]["max_turns"] == 12
    # The cheap model and the turn budget are the mapper's alone: the phases
    # that do the task run as they always did.
    assert calls[1]["role"] == "arquitecto"
    assert calls[1]["model"] == cfg.default_model
    assert calls[1]["max_turns"] is None


def test_run_task_cycle_runs_each_role_on_the_model_the_config_names(tmp_path, monkeypatch) -> None:
    """`role_model` reaches the `--model` each phase is dispatched with, and a
    role it does not name keeps `default_model` (`docs/decisions.md` ADR 65)."""
    cfg = _make_config(
        tmp_path, default_model="claude-opus-5-5",
        role_model={"implementador": "claude-sonnet-5-5", "revisor": "claude-sonnet-5-5"},
    )
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _recording_dispatch_phase(calls))

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert {c["role"] for c in calls} >= {"arquitecto", "implementador", "revisor", "auditor"}
    for call in calls:
        expected = (
            "claude-sonnet-5-5" if call["role"] in ("implementador", "revisor")
            else "claude-opus-5-5"
        )
        assert call["model"] == expected, call["role"]


def test_the_mappers_own_model_wins_over_role_model(tmp_path, monkeypatch) -> None:
    """The cartografo passes `mapping_model` explicitly, and an explicit model
    is the first thing the precedence looks at."""
    cfg = _make_config(
        tmp_path, mapping_enabled=True, mapping_model="haiku",
        role_model={project_docs.MAPPER_ROLE: "claude-sonnet-5-5", "arquitecto": "claude-sonnet-5-5"},
    )
    _fake_index(monkeypatch, exists=False)
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _recording_dispatch_phase(calls))

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert calls[0]["role"] == project_docs.MAPPER_ROLE
    assert calls[0]["model"] == "haiku"
    assert calls[1]["role"] == "arquitecto"
    assert calls[1]["model"] == "claude-sonnet-5-5"


def test_an_escalated_round_keeps_the_roles_model(tmp_path, monkeypatch) -> None:
    """The escalation changes the effort, never the model."""
    cfg = _make_config(
        tmp_path, escalate_effort_after_round=1, escalated_effort="max",
        role_model={"implementador": "claude-sonnet-5-5"},
    )
    seen = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, model=None, effort=None, **kwargs):
        seen.append((model, effort))
        return dispatcher_mod.DispatchResult(
            success=True, session_id=None, result_text="ok", account="cuenta1",
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    dispatcher_mod.run_phase(_split_ctx(cfg), "implementador", round_num=1)
    dispatcher_mod.run_phase(_split_ctx(cfg), "implementador", round_num=2)

    assert seen == [("claude-sonnet-5-5", cfg.effort_for("implementador")), ("claude-sonnet-5-5", "max")]


def test_run_task_cycle_does_not_remap_a_project_that_has_an_index(tmp_path, monkeypatch) -> None:
    """The map is written once and extended by the roles that follow it."""
    cfg = _make_config(tmp_path, mapping_enabled=True)
    _fake_index(monkeypatch, exists=True)
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _recording_dispatch_phase(calls))

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert project_docs.MAPPER_ROLE not in [call["role"] for call in calls]
    assert calls[0]["role"] == "arquitecto"


def test_run_task_cycle_cannot_map_without_a_container_to_ask(tmp_path, monkeypatch) -> None:
    """Enabled but unanswerable: with no account there is nothing to run
    `test -e` in, and a task must not be held up by that."""
    cfg = _make_config(tmp_path, mapping_enabled=True, accounts=[])
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _recording_dispatch_phase(calls))

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert calls[0]["role"] == "arquitecto"


def test_run_task_cycle_runs_the_task_anyway_when_the_map_fails(
    tmp_path, monkeypatch, caplog,
) -> None:
    """The map is a convenience for the phases that follow, never a gate on
    the card: an unmapped project is exactly the state the task started in."""
    cfg = _make_config(tmp_path, mapping_enabled=True, max_revision_rounds=1)
    _fake_index(monkeypatch, exists=False)
    calls = []
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase",
        _recording_dispatch_phase(calls, failing=(project_docs.MAPPER_ROLE,)),
    )

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        dispatcher_mod.run_task_cycle(
            cfg, "task-1", "myproj", description=_DESCRIPTION,
        )

    assert calls[1]["role"] == "arquitecto"
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert task.status == "done"
    # Whatever it managed still hands off — a partial map is worth having, and
    # the next phase has to know it is partial.
    assert project_docs.MAPPER_ROLE in task.body
    assert any("optional phase" in r.getMessage() for r in caplog.records)


def test_run_task_cycle_stops_when_the_map_bounces_off_another_owners_lock(
    tmp_path, monkeypatch, fake_git,
) -> None:
    """A lock held elsewhere is not the mapper's own failure to shrug off:
    the whole task belongs to another run, arquitecto included."""
    cfg = _make_config(tmp_path, mapping_enabled=True)
    _fake_index(monkeypatch, exists=False)
    calls = []

    def locked(cfg_arg, task_id, slug, role, prompt, **kwargs):
        calls.append(role)
        raise LockHeldError("task-1", "cuenta2")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", locked)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert calls == [project_docs.MAPPER_ROLE]
    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    # And the other run's worktrees are left where they are.
    assert fake_git.review_cleanups == []


#: What the CLI answers when `--max-turns` runs out, verified on 2.1.273.
_SPENT_BUDGET = ClaudeResult(
    session_id="sess-1",
    result_text="Reached maximum number of turns (12)",
    raw={"is_error": True, "subtype": "error_max_turns"},
)


def test_a_writer_that_ran_out_of_turns_still_commits() -> None:
    """The CLI reports a spent budget as an error, but nothing went wrong: the
    docs written up to that turn are real, and the writing roles share one
    worktree, so leaving them uncommitted smuggles them into the next role's
    commit under the next role's name."""
    assert dispatcher_mod._should_commit(project_docs.MAPPER_ROLE, _SPENT_BUDGET) is True


def test_a_reviewer_that_ran_out_of_turns_commits_nothing() -> None:
    """Reviewing roles never commit, whatever ended them."""
    assert dispatcher_mod._should_commit("revisor", _SPENT_BUDGET) is False


def test_a_rate_limited_phase_does_not_commit_even_at_the_turn_budget() -> None:
    """Failover resumes this phase on another account in the same worktree, so
    committing here would put the same work in history twice."""
    result = ClaudeResult(
        session_id="sess-1",
        result_text="429 rate limit exceeded",
        raw={"is_error": True, "subtype": "error_max_turns", "api_error_status": 429},
    )

    assert dispatcher_mod._should_commit("implementador", result) is False


def test_dispatch_phase_commits_the_map_a_spent_budget_left_behind(
    tmp_path, monkeypatch, fake_git,
) -> None:
    """End to end: the phase still reports failure — the run was cut off — and
    the files it wrote are still committed under its own name."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", _phase_exec(_SPENT_BUDGET))
    _fake_worktree(monkeypatch)

    result = dispatcher_mod.dispatch_phase(
        cfg, "task-1", "myproj", project_docs.MAPPER_ROLE, "map it",
    )

    assert result.success is False
    assert len(fake_git.commits) == 1
    assert fake_git.commits[0]["author_name"] == f"{project_docs.MAPPER_ROLE} (cuenta1)"


def test_the_mapper_is_told_the_task_is_context_not_its_job() -> None:
    """It runs ahead of the first phase, so there is no handoff to read — and
    the task it is shown is there to say which parts of the project matter
    first, not to be done."""
    prompt = dispatcher_mod._role_prompt(
        project_docs.MAPPER_ROLE,
        "task-1",
        "myproj",
        "/data/.hive/tasks/task-1.md",
        _DESCRIPTION,
        "/data/.hive/scratch/task-1",
        "/data/.hive/tasks",
        max_turns=12,
    )

    assert (
        f"The task about to run on this project is:\n## Goal\n\n{_GOAL}\n\n"
        "## Done when\n\nGET /healthz answers 200.\n\n"
    ) in prompt
    assert "You are not doing that task" in prompt
    assert "/data/.hive/tasks/task-1.md" not in prompt
    assert project_docs.INDEX in prompt
    assert "12 turns and no more" in prompt


def test_the_mapper_prompt_opens_with_the_generator() -> None:
    """The generator is user-invocable only: the Skill tool refuses it, and
    only the prompt's leading slash command runs it (ADR 115). No other role's
    prompt starts with it."""
    args = ("task-1", "myproj", "/data/.hive/tasks/task-1.md", _DESCRIPTION,
            "/data/.hive/scratch/task-1", "/data/.hive/tasks")

    mapper = dispatcher_mod._role_prompt(project_docs.MAPPER_ROLE, *args)

    assert mapper.startswith(f"{project_docs.mapper_lead()}\n\nRole: {project_docs.MAPPER_ROLE}.")
    assert mapper.count(project_docs.RUN_SKILL_COMMAND) == 1
    for role in ("arquitecto", "implementador", "revisor", "auditor"):
        assert project_docs.RUN_SKILL_COMMAND not in dispatcher_mod._role_prompt(role, *args)


@pytest.mark.parametrize(
    "role", ["arquitecto", "implementador", "revisor", "auditor", project_docs.MAPPER_ROLE],
)
def test_every_role_is_told_the_remote_is_not_its_business(role) -> None:
    """ADR 36 settles the base once, before the first worktree exists, because
    the four phases share one branch and one worktree. The role is the one with
    a shell in there, so it is told — and the mapper too, since it reads the
    tree before anything has been cut from it."""
    prompt = dispatcher_mod._role_prompt(
        role,
        "task-1",
        "myproj",
        "/data/.hive/tasks/task-1.md",
        _DESCRIPTION,
        "/data/.hive/scratch/task-1",
        "/data/.hive/tasks",
    )

    assert "git fetch" in prompt
    assert "git pull" in prompt
    assert "git rebase" in prompt
    assert "do not push" in prompt
    # Not a thing for the phase to fix on its own: the alternative to pulling is
    # saying so, which is the half a role would otherwise have to invent.
    assert "finding for your handoff" in prompt


@pytest.mark.parametrize(
    "role", ["arquitecto", "implementador", "revisor", "auditor", project_docs.MAPPER_ROLE],
)
def test_every_role_is_told_one_operation_per_bash_call(role) -> None:
    """T-017, T-018 and T-019 each lost turns to a Bash call refused whole for
    chaining two operations. Every role has a shell under the same permission
    check, the mapper too, so every role is told the shape before it tries one."""
    prompt = dispatcher_mod._role_prompt(
        role,
        "task-1",
        "myproj",
        "/data/.hive/tasks/task-1.md",
        _DESCRIPTION,
        "/data/.hive/scratch/task-1",
        "/data/.hive/tasks",
    )

    assert dispatcher_mod._ONE_OPERATION_PER_CALL in prompt
    assert "one operation per call" in prompt
    assert "`$?`" in prompt
    # The exception has to be named, or a role would refuse its own card's grant.
    assert "`cd <dir> && <granted command>`" in prompt
    assert "Read tool with its absolute path" in prompt


def test_run_task_cycle_hands_every_role_its_duty_to_the_project_docs(
    tmp_path, monkeypatch,
) -> None:
    """The duties are about *this* project's docs, so they ride in the
    dispatcher's prompt rather than in a vendored skill — a skill is method
    and travels between projects."""
    cfg = _make_config(tmp_path, max_revision_rounds=1)
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _recording_dispatch_phase(calls))

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    by_role = {call["role"]: call["prompt"] for call in calls}
    assert project_docs.DECISIONS in by_role["arquitecto"]
    assert project_docs.implementation_doc("task-1") in by_role["implementador"]
    assert project_docs.LEARNINGS_INDEX in by_role["auditor"]
    for role, prompt in by_role.items():
        assert "never by line number" in prompt, f"{role} may cite a line number"


# --- the shared learnings inbox, as the cycle moves it along ---------------


#: Verbatim, because that is the whole point of the entry: a later phase
#: finds this row by grepping for the message in front of it.
_SYMPTOM = "ECONNREFUSED 127.0.0.1:5432"


def _learning(cfg, name, **meta):
    """One inbox entry, written as text in the shape the phases are told to use.

    Not built through the module: what the cycle has to cope with is a file
    some agent wrote, so the tests write one the same way.
    """
    fields = dict(
        project="myproj",
        task="task-8",
        phase="implementador",
        scope=learnings.SCOPE_PROJECT,
        status=learnings.UNCONFIRMED,
        when="the suite talks to a database",
    )
    fields.update(meta)
    symptom = fields.pop("symptom", _SYMPTOM)
    root = learnings.ensure_dirs(cfg.hive_tasks_dir)
    path = os.path.join(root, learnings.INBOX_NAME, name)
    with open(path, "w") as handle:
        handle.write(
            "---\n"
            + "".join(f"{key}: {value}\n" for key, value in fields.items())
            + "---\n\n"
            f"## Symptom\n\n```\n{symptom}\n```\n\n"
            "## Why\n\nThe fixture assumed a server that nothing starts.\n\n"
            "## Rule\n\nStart postgres before the suite, not with it.\n\n"
            "## Evidence\n\n`pytest -q tests/db` in the writers' worktree.\n"
        )
    return path


def _inbox(cfg):
    return {entry.ref: entry for entry in learnings.read_inbox(cfg.hive_tasks_dir)}


def test_run_task_cycle_opens_the_inbox_before_the_first_phase(
    tmp_path, monkeypatch, fake_git,
) -> None:
    """A phase is told to file a trap the moment it hits one, which can be its
    first minute — so the directory cannot be made by whoever writes first."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert os.path.isdir(learnings.inbox_dir(cfg.hive_tasks_dir))
    assert os.path.isdir(learnings.harness_dir(cfg.hive_tasks_dir))


def test_run_task_cycle_confirms_a_trap_a_second_task_also_reported(
    tmp_path, monkeypatch, fake_git,
) -> None:
    """The confirmation pass runs no model: two tasks independently hitting
    the same error is the evidence, and the dispatcher can see it by itself."""
    cfg = _make_config(tmp_path)
    _learning(cfg, "task-8-db.md", task="task-8")
    _learning(cfg, "task-9-db.md", task="task-9")
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    statuses = {ref: entry.status for ref, entry in _inbox(cfg).items()}
    assert statuses == {
        "inbox/task-8-db.md": learnings.CONFIRMED,
        "inbox/task-9-db.md": learnings.CONFIRMED,
    }


def test_run_task_cycle_hands_every_role_the_inbox_and_the_auditor_its_own_rows(
    tmp_path, monkeypatch, fake_git,
) -> None:
    """Every role reads the inbox before debugging; only the auditor is told
    which entries it is the one to file, because only it writes the docs."""
    cfg = _make_config(tmp_path, max_revision_rounds=1)
    _learning(cfg, "task-8-db.md")
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _recording_dispatch_phase(calls))

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    by_role = {call["role"]: call["prompt"] for call in calls}
    for role, prompt in by_role.items():
        assert learnings.root_dir(cfg.hive_tasks_dir) in prompt, f"{role} is not told where"
        assert _SYMPTOM.split()[0] in prompt or "grep" in prompt, f"{role} is not told to grep"
    assert "carried_by: task-1" in by_role["auditor"]
    assert "carried_by: task-1" not in by_role["implementador"]


def test_the_entries_are_stamped_before_the_auditor_files_them(
    tmp_path, monkeypatch, fake_git,
) -> None:
    """The auditor's prompt names the entries it owns, so the stamp has to be
    on disk before that prompt is built — not after the phase returns."""
    cfg = _make_config(tmp_path, max_revision_rounds=1)
    _learning(cfg, "task-8-db.md")
    stamped = {}

    def recording(cfg_arg, task_id, slug, role, prompt, **kwargs):
        stamped[role] = {ref: entry.carried_by for ref, entry in _inbox(cfg).items()}
        return dispatcher_mod.DispatchResult(
            success=True,
            session_id=None,
            result_text="VERDICT: APPROVED" if role == "revisor" else "ok",
            account="cuenta1",
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", recording)

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert stamped["implementador"] == {"inbox/task-8-db.md": ""}
    assert stamped["auditor"] == {"inbox/task-8-db.md": "task-1"}


def test_a_blocked_task_lets_go_of_the_entries_it_never_filed(
    tmp_path, monkeypatch, fake_git,
) -> None:
    """Nobody filed them, so they have to go back to being unowned: a stamp
    left behind by a dead run would keep the next task from picking them up."""
    cfg = _make_config(tmp_path)
    _learning(cfg, "task-8-db.md", carried_by="task-1")
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _rejecting_dispatch_phase)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    entry = _inbox(cfg)["inbox/task-8-db.md"]
    assert entry.carried_by == ""
    # And the breadcrumb, so a human reading the file can see it was dropped
    # rather than never picked up.
    assert entry.meta["orphaned_from"] == ["task-1"]


def test_a_merged_task_drops_the_entries_it_filed(tmp_path, monkeypatch, fake_git) -> None:
    """They are in the project's docs on the branch that just landed, so the
    inbox copy would charge every later prompt for a row the repo has."""
    cfg = _make_config(tmp_path, merge_on_done=True)
    path = _learning(cfg, "task-8-db.md")
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert fake_git.merges == [("agent-cuenta1", cfg.projects_root, "myproj", "task-1")]
    assert not os.path.exists(path)


def test_a_refused_merge_keeps_the_entries_where_the_next_task_sees_them(
    tmp_path, monkeypatch, fake_git,
) -> None:
    """The docs are on a branch nothing has landed, so dropping the inbox copy
    would hide the trap from every task until that branch finally merges."""
    cfg = _make_config(tmp_path, merge_on_done=True)
    path = _learning(cfg, "task-8-db.md")

    def refusing_merge(container, projects_root, slug, task_id):
        return dispatcher_mod.docker_exec.MergeOutcome(
            dispatcher_mod.docker_exec.REFUSED, "main", "has uncommitted changes"
        )

    monkeypatch.setattr(dispatcher_mod.docker_exec, "merge_task_branch", refusing_merge)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert os.path.exists(path)
    # Still stamped: the task did file them, and the stamp is what stops the
    # next run from filing the same rows a second time before the merge.
    assert _inbox(cfg)["inbox/task-8-db.md"].carried_by == "task-1"


def test_a_run_that_bounced_off_another_owners_lock_leaves_its_entries_alone(
    tmp_path, monkeypatch, fake_git,
) -> None:
    """Same reason the worktrees are left alone: the run that holds the lock
    is still alive, and those entries are the ones it is about to file."""
    cfg = _make_config(tmp_path)
    _learning(cfg, "task-8-db.md", carried_by="task-1")

    def locked(cfg_arg, task_id, slug, role, prompt, **kwargs):
        raise LockHeldError("task-1", "cuenta2")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", locked)

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert _inbox(cfg)["inbox/task-8-db.md"].carried_by == "task-1"


# Debt the task declared, ruled on by the review, and named for the auditor.
# The index is the only record (ADR 59), so every test below holds on what
# the auditor is told to file.

_DEBT = {
    "origin": debt.FOUND,
    "what": "The retry loop has no test.",
    "where": "a task that changes the backoff in docker_exec",
    "why": "the fixture needs a fake clock this task does not have",
    "cost": "a regression in the backoff lands silently",
    "fix": "a fake clock in conftest, then one case per branch",
}

_MIGRATION = {
    "origin": debt.INTRODUCED,
    "what": "The migration has no down step.",
    "where": "a task that has to roll back a deploy of this schema",
    "why": "reversing the backfill needs a decision nobody has made",
    "cost": "a bad deploy is rolled forward or not at all",
    "fix": "write the down step once the backfill policy is decided",
}


class _FakeDebtIndex:
    """`docs/debt/README.md` as the dispatcher finds it, and who asked for it."""

    def __init__(self, text=""):
        self.text = text
        self.reads = []

    def read_index(self, container, workdir):
        self.reads.append((container, workdir))
        return self.text


@pytest.fixture
def debt_index(monkeypatch):
    """The index, faked: `cleanup_container` names a real container, so
    without this every test that files debt shells out to a real docker."""
    fake = _FakeDebtIndex()
    monkeypatch.setattr(dispatcher_mod.debt, "read_index", fake.read_index)
    return fake


def _debt_dispatch_phase(
    calls, declared=(), rulings=(), resolved=(), verdict="APPROVED",
):
    """Every phase succeeds; the implementador declares and the revisor rules."""

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, **kwargs):
        calls.append(dict(role=role, prompt=prompt))
        payload = {"status": "complete"}
        if role == "implementador":
            payload["debt"] = [dict(item) for item in declared]
            payload["resolved_debt"] = list(resolved)
        if role == "revisor":
            payload["verdict"] = verdict
            payload["debt_rulings"] = [dict(item) for item in rulings]
        return dispatcher_mod.DispatchResult(
            success=True,
            session_id=None,
            result_text="",
            account="cuenta1",
            handoff=payload,
        )

    return fake_dispatch_phase


def _roles(calls):
    return [call["role"] for call in calls]


def _by_role(calls):
    return {call["role"]: call["prompt"] for call in calls}


def _filed_lines(prompt):
    """The entries a filing note names, as its own bullet lines."""
    return [line for line in prompt.splitlines() if line.startswith("- `task-")]


def test_the_debt_the_review_accepted_gets_one_id_each_in_the_auditors_prompt(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """One id per accepted declaration, assigned by the dispatcher: an agent
    that numbered its own entries would number them again on a re-run."""
    cfg = _make_config(tmp_path)
    calls = []
    monkeypatch.setattr(
        dispatcher_mod,
        "dispatch_phase",
        _debt_dispatch_phase(
            calls,
            declared=(_DEBT, _MIGRATION),
            rulings=({"debt": "The retry loop has no test", "ruling": debt.ACCEPTED},),
        ),
    )

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert _filed_lines(_by_role(calls)["auditor"]) == [
        "- `task-1-D1` — The retry loop has no test.",
        "- `task-1-D2` — The migration has no down step.",
    ]
    assert outcome is dispatcher_mod.CycleOutcome.FINISHED


def test_only_the_auditor_is_handed_the_entry_ids(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """The ids are assigned before the phase runs, and handed to the one role
    that writes the index — every other role is told to propose, not file."""
    cfg = _make_config(tmp_path)
    calls = []
    monkeypatch.setattr(
        dispatcher_mod,
        "dispatch_phase",
        _debt_dispatch_phase(calls, declared=(_DEBT,)),
    )

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    by_role = _by_role(calls)
    assert "`task-1-D1`" in by_role["auditor"]
    # Nothing mirrors the entry (ADR 59): the column is kept and told `none`.
    assert "write `none` in the `card` column" in by_role["auditor"]
    assert " | ".join(debt.COLUMNS) in by_role["auditor"]
    assert "`task-1-D1`" not in by_role["implementador"]
    assert "`task-1-D1`" not in by_role["revisor"]


def test_a_task_that_declared_no_debt_says_nothing_about_debt_to_the_auditor(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """A filing note with no entries under it reads as an instruction to find
    something to file, and the auditor files only what it is handed."""
    cfg = _make_config(tmp_path)
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _debt_dispatch_phase(calls))

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert "already has its ids" not in _by_role(calls)["auditor"]
    # And nothing was read: there is no declaration to compare against.
    assert debt_index.reads == []


def test_debt_the_revisor_rejected_sends_an_approving_round_back_around(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """A rejected declaration is work the revisor says this task should have
    done, which is a finding — and a finding outranks the verdict field."""
    cfg = _make_config(tmp_path, max_revision_rounds=2)
    calls = []
    monkeypatch.setattr(
        dispatcher_mod,
        "dispatch_phase",
        _debt_dispatch_phase(
            calls,
            declared=(_DEBT,),
            rulings=({"debt": "The retry loop has no test", "ruling": debt.REJECTED},),
            verdict="APPROVED",
        ),
    )

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    # Both rounds spent, no auditor, and nothing filed: no index was read.
    assert _roles(calls).count("implementador") == 2
    assert "auditor" not in _roles(calls)
    assert debt_index.reads == []
    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED


def test_debt_the_revisor_read_as_a_block_ends_the_task_with_nothing_filed(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """Another round cannot supply a decision the task was never given, so it
    ends here rather than burning the rest of the rounds to say so again."""
    cfg = _make_config(tmp_path, max_revision_rounds=3)
    calls = []
    monkeypatch.setattr(
        dispatcher_mod,
        "dispatch_phase",
        _debt_dispatch_phase(
            calls,
            declared=(_MIGRATION,),
            rulings=({"debt": "The migration has no down step", "ruling": debt.BLOCKS},),
        ),
    )

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert _roles(calls).count("implementador") == 1
    # No auditor, so nobody was handed an id to file, and no index was read.
    assert "auditor" not in _roles(calls)
    assert debt_index.reads == []
    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED


def test_debt_the_index_already_carries_is_not_filed_a_second_time(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """An index that fills with the same entry once per task is an index
    nobody reads, and the index is what knows the entry is already there."""
    cfg = _make_config(tmp_path)
    debt_index.text = (
        "| id | what | where | fix | card |\n"
        "|---|---|---|---|---|\n"
        "| `task-4-D1` | The retry loop has no test | backoff changes | a fake clock "
        "| `card-9` |\n"
    )
    calls = []
    monkeypatch.setattr(
        dispatcher_mod,
        "dispatch_phase",
        _debt_dispatch_phase(calls, declared=(_DEBT, _MIGRATION)),
    )

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    # The entry that did get filed is the first this task files, not the
    # second: the numbering counts what was filed, not what was declared.
    assert _filed_lines(_by_role(calls)["auditor"]) == [
        "- `task-1-D1` — The migration has no down step."
    ]


def test_the_index_is_read_from_the_branch_the_task_is_being_built_on(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """A re-run of a task that already filed entries has them on its own
    branch and nowhere else; reading the checkout would file them twice."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase", _debt_dispatch_phase([], declared=(_DEBT,)),
    )

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    # Twice since T-086: once to number the entries, once after the auditor
    # to mirror what it filed as backlog items. Both from the branch.
    assert debt_index.reads == [
        ("agent-cuenta1", f"{cfg.projects_root}/myproj/worktrees/task-1/work"),
    ] * 2


def _auditor_writes_index(debt_index, inner, text):
    """`inner`, with the auditor writing `text` as the index on its branch."""

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, **kwargs):
        result = inner(cfg_arg, task_id, slug, role, prompt, **kwargs)
        if role == "auditor":
            debt_index.text = text
        return result

    return fake_dispatch_phase


_AUDITED_INDEX = (
    "| id | what | where | fix | card | impact |\n"
    "|---|---|---|---|---|---|\n"
    "| `task-1-D1` | The retry loop has no test. | a task that changes the backoff "
    "| a fake clock | none | risk |\n"
)


def _debt_items(cfg):
    from dispatcher import tasks as tasks_mod
    return {
        task_id: task for task_id, task in tasks_mod.read_all_tasks(cfg.hive_tasks_dir).items()
        if task.source == "debt"
    }


def test_the_debt_the_auditor_filed_becomes_a_backlog_item_at_its_impact(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """ADR 131: the row the auditor wrote, read back from the branch, is filed
    as a `proposed` item tagged `debt`, its priority from the row's impact."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase",
        _auditor_writes_index(
            debt_index, _debt_dispatch_phase([], declared=(_DEBT,)), _AUDITED_INDEX,
        ),
    )

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    [(item_id, item)] = _debt_items(cfg).items()
    assert item_id == "T-001"
    assert (item.status, item.priority, item.tags, item.debt_id) == (
        "proposed", "high", ["debt"], "task-1-D1",
    )
    assert item.title == "task-1-D1: The retry loop has no test."


def test_a_re_run_files_no_second_backlog_item(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    cfg = _make_config(tmp_path)
    filed = [("task-1-D1", debt.Declaration(what="The retry loop has no test."))]
    debt_index.text = _AUDITED_INDEX
    monkeypatch.setattr(dispatcher_mod, "cleanup_container", lambda cfg_arg: "agent-cuenta1")

    first = dispatcher_mod._mirror_filed_debt(cfg, "task-1", "myproj", filed)
    again = dispatcher_mod._mirror_filed_debt(cfg, "task-1", "myproj", filed)

    assert [r["created"] for r in first + again] == [True, False]
    assert list(_debt_items(cfg)) == ["T-001"]


def test_an_entry_the_index_does_not_show_is_filed_from_its_declaration(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """The auditor wrote no row (or the read failed): the item still exists,
    at the default priority, because only the auditor judges an impact."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase", _debt_dispatch_phase([], declared=(_DEBT,)),
    )

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    [item] = _debt_items(cfg).values()
    assert (item.priority, item.debt_id) == ("normal", "task-1-D1")


def test_an_entry_the_index_shows_resolved_is_not_filed(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    cfg = _make_config(tmp_path)
    debt_index.text = _AUDITED_INDEX.replace("The retry loop", "**Resolved 2026-10-18.** The retry loop")
    monkeypatch.setattr(dispatcher_mod, "cleanup_container", lambda cfg_arg: "agent-cuenta1")
    filed = [("task-1-D1", debt.Declaration(what="The retry loop has no test."))]

    assert dispatcher_mod._mirror_filed_debt(cfg, "task-1", "myproj", filed) == []
    assert _debt_items(cfg) == {}


def test_a_failure_to_file_debt_does_not_fail_the_cycle(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """The index is still the record, and `dispatch import-debt` files later."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase", _debt_dispatch_phase([], declared=(_DEBT,)),
    )

    def boom(*args, **kwargs):
        raise OSError("hive is read-only")

    monkeypatch.setattr(dispatcher_mod.tasks, "file_debt", boom)

    result = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert result is dispatcher_mod.CycleOutcome.FINISHED
    assert _debt_items(cfg) == {}


def test_a_cycle_with_no_debt_files_no_backlog_item(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _debt_dispatch_phase([]))

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert _debt_items(cfg) == {}


def test_the_entries_a_task_resolved_outlive_the_handoff_that_named_them(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """The merge can be days later, in `merge-task`, long after this handoff
    is prose in a file nobody parses, and the api serves the list."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod,
        "dispatch_phase",
        _debt_dispatch_phase([], resolved=("`task-4-D1`", "task-4-D1", "task-6-D2")),
    )

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert read_resolved_debt(cfg.hive_tasks_dir, "task-1") == ["task-4-D1", "task-6-D2"]


def test_a_merged_task_closes_nothing_for_the_debt_it_resolved(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """The auditor marks the row resolved on the branch, and that is the whole
    of it: after the merge nothing reads the index to go and close a card."""
    cfg = _make_config(tmp_path, merge_on_done=True)
    debt_index.text = (
        "| id | what | where | fix | card |\n"
        "|---|---|---|---|---|\n"
        "| `task-4-D1` | The retry loop has no test | backoff changes | a fake clock "
        "| `card-9` |\n"
    )
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase", _debt_dispatch_phase([], resolved=("task-4-D1",)),
    )

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.FINISHED
    assert fake_git.merges == [("agent-cuenta1", cfg.projects_root, "myproj", "task-1")]
    assert read_resolved_debt(cfg.hive_tasks_dir, "task-1") == ["task-4-D1"]
    assert debt_index.reads == []
    assert not hasattr(dispatcher_mod, "close_resolved_debt")


# --- _file_accepted_debt, off the handoffs rather than the results ----------
#
# The cycle calls it holding both results in memory; `run-phase --final` calls
# it holding only what earlier phases left on disk. These pin down the shape it
# has to answer for either caller: two `dict | None` and nothing else.


def test_filing_accepted_debt_gives_an_id_to_each_declaration_the_review_kept(
    tmp_path, debt_index,
) -> None:
    """The unit the cycle and the hand-resume share: declarations in, entry
    ids out, with the revisor's rulings deciding which survive."""
    cfg = _make_config(tmp_path)

    filed = dispatcher_mod._file_accepted_debt(
        cfg, "task-1", "myproj",
        {"debt": [dict(_DEBT), dict(_MIGRATION)]},
        {"debt_rulings": [{"debt": "The migration has no down step", "ruling": debt.REJECTED}]},
    )

    assert [(entry, declaration.what) for entry, declaration in filed] == [
        ("task-1-D1", _DEBT["what"])
    ]


def test_filing_accepted_debt_with_no_review_on_disk_keeps_every_declaration(
    tmp_path, debt_index,
) -> None:
    """Accept-by-default all the way down. A hand-resumed task whose revisor
    handoff was never stored has no rulings to read, and the answer to "was
    this accepted?" on a round that reached the auditor is yes."""
    cfg = _make_config(tmp_path)

    filed = dispatcher_mod._file_accepted_debt(
        cfg, "task-1", "myproj", {"debt": [dict(_DEBT), dict(_MIGRATION)]}, None,
    )

    assert [entry for entry, _ in filed] == ["task-1-D1", "task-1-D2"]


def test_filing_accepted_debt_with_nothing_declared_reads_no_index_and_files_nothing(
    tmp_path, debt_index,
) -> None:
    """A missing handoff declares nothing, which is the same answer as a
    handoff that declared nothing — and neither is worth a docker exec."""
    cfg = _make_config(tmp_path)

    assert dispatcher_mod._file_accepted_debt(cfg, "task-1", "myproj", None, None) == []
    assert dispatcher_mod._file_accepted_debt(
        cfg, "task-1", "myproj", {"debt": []}, {"debt_rulings": []},
    ) == []
    assert debt_index.reads == []


def test_filing_accepted_debt_skips_what_the_branch_index_already_carries(
    tmp_path, debt_index,
) -> None:
    """Read from the branch, not the checkout: a re-run of the closing phase
    must not file the same entry twice."""
    cfg = _make_config(tmp_path)
    debt_index.text = (
        "| id | what | where | fix | card |\n"
        "|---|---|---|---|---|\n"
        "| `task-1-D1` | The retry loop has no test | backoff changes | a fake clock "
        "| `card-9` |\n"
    )

    filed = dispatcher_mod._file_accepted_debt(
        cfg, "task-1", "myproj", {"debt": [dict(_DEBT), dict(_MIGRATION)]}, None,
    )

    assert [declaration.what for _, declaration in filed] == [_MIGRATION["what"]]
    # Numbered by what was filed, not by what was declared.
    assert [entry for entry, _ in filed] == ["task-1-D1"]


def test_filing_accepted_debt_survives_an_index_it_cannot_read(
    tmp_path, monkeypatch, caplog,
) -> None:
    """The index is read to avoid a duplicate row. Failing to read it costs a
    duplicate row, never the entry."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod.debt, "read_index",
        lambda container, workdir: (_ for _ in ()).throw(RuntimeError("no container")),
    )

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        filed = dispatcher_mod._file_accepted_debt(
            cfg, "task-1", "myproj", {"debt": [dict(_DEBT)]}, None,
        )

    assert [entry for entry, _ in filed] == ["task-1-D1"]
    assert any("no container" in r.getMessage() for r in caplog.records)


def test_filing_accepted_debt_returns_the_id_and_the_declaration_and_nothing_else(
    tmp_path, debt_index,
) -> None:
    """The shape `debt.filing_note` takes: an id and what it names. No card
    rides along (`docs/decisions.md` ADR 59)."""
    cfg = _make_config(tmp_path)

    filed = dispatcher_mod._file_accepted_debt(
        cfg, "task-1", "myproj", {"debt": [dict(_DEBT)]}, None,
    )

    assert filed == [("task-1-D1", debt.Declaration(**_DEBT))]


# --- run_single_phase -------------------------------------------------------
#
# The repair path: one phase of a task that is already under way, instead of a
# cycle that always restarts at the arquitecto. What these pin down is as much
# what it refuses to do as what it does — every step it skips is a step that
# needs a handoff from a phase this call did not run.


def _spy_learnings(monkeypatch) -> dict:
    """Watch the two calls that decide who owns the entries when a run ends."""
    seen: dict = {"carried": [], "orphaned": []}
    monkeypatch.setattr(
        dispatcher_mod.learnings, "carry",
        lambda hive_dir, project, task_id: seen["carried"].append((project, task_id)) or [],
    )
    monkeypatch.setattr(
        dispatcher_mod.learnings, "mark_orphaned",
        lambda hive_dir, task_id: seen["orphaned"].append(task_id) or [],
    )
    return seen


def test_run_single_phase_dispatches_that_phase_and_no_other(tmp_path, monkeypatch) -> None:
    """The whole point: the arquitecto and the implementador already ran and
    were already paid for, so resuming at the revisor costs one phase."""
    cfg = _make_config(tmp_path)
    calls = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        calls.append(role)
        return dispatcher_mod.DispatchResult(success=True, session_id=None, result_text="ok", account="cuenta1")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    result = dispatcher_mod.run_single_phase(
        cfg, "task-1", "myproj", "revisor", round_num=2, description=_DESCRIPTION,
    )

    assert calls == ["revisor"]
    assert result is not None and result.success


def test_run_single_phase_runs_the_round_it_was_told_it_is_on(tmp_path, monkeypatch) -> None:
    """A resumed round has to name itself. The number labels the section in
    the task file, goes into the prompt, and decides the effort — so a round 2
    dispatched without it is dispatched as though round 1 never happened."""
    cfg = _make_config(tmp_path, escalate_effort_after_round=1)
    seen: dict = {}

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        seen.update(prompt=prompt, effort=effort, round_num=round_num)
        return dispatcher_mod.DispatchResult(success=True, session_id=None, result_text="looked at it", account="cuenta1")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    dispatcher_mod.run_single_phase(
        cfg, "task-1", "myproj", "revisor", round_num=2, description=_DESCRIPTION,
    )

    assert seen["round_num"] == 2
    assert "revision round 2" in seen["prompt"]
    assert seen["effort"] == cfg.escalated_effort
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert "## revisor (round 2)" in task.body


def test_run_single_phase_hands_the_note_to_the_prompt(tmp_path, monkeypatch) -> None:
    """The operator's only channel into a resumed phase: what the dead process
    took with it, which is nowhere in the task file precisely because it died."""
    cfg = _make_config(tmp_path)
    prompts = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        prompts.append(prompt)
        return dispatcher_mod.DispatchResult(success=True, session_id=None, result_text="ok", account="cuenta1")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    dispatcher_mod.run_single_phase(
        cfg, "task-1", "myproj", "revisor", round_num=2,
        description=_DESCRIPTION, note="The host rebooted mid-round.",
    )

    assert "The host rebooted mid-round." in prompts[0]


def test_run_single_phase_leaves_the_task_pending_and_orphans_its_entries(
    tmp_path, monkeypatch,
) -> None:
    """Every phase but the last one. The task is still going, so it is
    not closed — and nobody filed these entries, so they stop naming a carrier
    that is no longer running."""
    cfg = _make_config(tmp_path)
    seen = _spy_learnings(monkeypatch)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    dispatcher_mod.run_single_phase(
        cfg, "task-1", "myproj", "revisor", round_num=2, description=_DESCRIPTION,
    )

    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert task.status == "pending"
    assert seen["carried"] == []
    assert seen["orphaned"] == ["task-1"]


def test_run_single_phase_final_closes_the_task_the_way_a_cycle_does(
    tmp_path, monkeypatch,
) -> None:
    """--final is the operator saying this phase is the last one. It buys what
    the cycle gives its auditor: the entries carried in beforehand, done in the
    task file, and no orphaning on the way out."""
    cfg = _make_config(tmp_path)
    seen = _spy_learnings(monkeypatch)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    result = dispatcher_mod.run_single_phase(
        cfg, "task-1", "myproj", "auditor", final=True, description=_DESCRIPTION,
    )

    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert task.status == "done"
    assert result is not None and result.success
    assert seen["carried"] == [("myproj", "task-1")]
    assert seen["orphaned"] == []


def test_run_single_phase_final_mirrors_the_debt_it_filed(tmp_path, monkeypatch) -> None:
    """A task closed with `run-phase --final` files its debt as backlog items
    the way a cycle does (ADR 131)."""
    cfg = _make_config(tmp_path)
    _spy_learnings(monkeypatch)
    filed = [("task-1-D1", debt.Declaration(what="The retry loop has no test."))]
    mirrored = []
    monkeypatch.setattr(dispatcher_mod, "_file_accepted_debt", lambda *a: filed)
    monkeypatch.setattr(
        dispatcher_mod, "_mirror_filed_debt",
        lambda cfg_arg, task_id, slug, entries: mirrored.append((task_id, entries)) or [],
    )
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    dispatcher_mod.run_single_phase(
        cfg, "task-1", "myproj", "auditor", final=True, description=_DESCRIPTION,
    )

    assert mirrored == [("task-1", filed)]


def test_run_single_phase_does_not_act_on_the_verdict_it_got_back(tmp_path, monkeypatch) -> None:
    """A refusing revisor is what starts another implementador round in a
    cycle. Here there is nothing to start it from, so the refusal is handed
    back to the operator and the run stops at one phase."""
    cfg = _make_config(tmp_path)
    calls = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        calls.append(role)
        return _rejecting_dispatch_phase(cfg_arg, task_id, slug, role, prompt, round_num=round_num)

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    result = dispatcher_mod.run_single_phase(
        cfg, "task-1", "myproj", "revisor", round_num=2, description=_DESCRIPTION,
    )

    assert calls == ["revisor"]
    assert result is not None and result.success


def test_run_single_phase_never_merges_even_when_the_config_says_to(
    tmp_path, monkeypatch, fake_git,
) -> None:
    """merge_on_done fires on a cycle that watched a revisor approve. A phase
    run by hand watched nothing, so the branch is the operator's to merge."""
    cfg = _make_config(tmp_path, merge_on_done=True)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    dispatcher_mod.run_single_phase(
        cfg, "task-1", "myproj", "auditor", final=True, description=_DESCRIPTION,
    )

    assert fake_git.merges == []


def test_run_single_phase_says_what_closing_by_hand_did_not_do(
    tmp_path, monkeypatch, caplog,
) -> None:
    """Invisible from the task file otherwise: a task closed this way was
    closed by a phase, not by a cycle that read a verdict."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    with caplog.at_level("INFO", logger=dispatcher_mod.logger.name):
        dispatcher_mod.run_single_phase(
            cfg, "task-1", "myproj", "auditor", final=True, description=_DESCRIPTION,
        )

    assert any(
        "closed by a single auditor phase" in r.getMessage() and "merge-task" in r.getMessage()
        for r in caplog.records
    )


def test_run_single_phase_that_did_not_land_returns_none_and_closes_nothing(
    tmp_path, monkeypatch, fake_git,
) -> None:
    """The operator is driving the cycle by hand now, so a phase that failed
    has to be an answer and not a silence: the next one would run over it."""
    cfg = _make_config(tmp_path)
    seen = _spy_learnings(monkeypatch)
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase",
        lambda *a, **kw: dispatcher_mod.DispatchResult(success=False, session_id=None, result_text="stop", account=""),
    )

    result = dispatcher_mod.run_single_phase(
        cfg, "task-1", "myproj", "auditor", final=True, description=_DESCRIPTION,
    )

    assert result is None
    # --final or not, a phase that failed hands nothing off: no `done`.
    assert not os.path.exists(task_file_path(cfg.hive_tasks_dir, "task-1"))
    # --final asked for the entries, the phase did not file them: they go back.
    assert seen["orphaned"] == ["task-1"]
    assert len(fake_git.review_cleanups) == 1


def test_run_single_phase_refuses_a_task_nobody_described(tmp_path, monkeypatch) -> None:
    """Same preamble as the cycle, same refusal: a role told a task id and
    nothing else costs a phase of quota to learn nothing."""
    cfg = _make_config(tmp_path)
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", lambda *a, **kw: calls.append(1))

    assert dispatcher_mod.run_single_phase(cfg, "task-1", "myproj", "auditor") is None
    assert calls == []
    assert not os.path.exists(task_file_path(cfg.hive_tasks_dir, "task-1"))


def test_run_single_phase_leaves_another_owners_worktrees_alone(
    tmp_path, monkeypatch, fake_git,
) -> None:
    """The reason the operator is resuming by hand is usually that something
    else died — but if it did not, its checkouts are still in use."""
    cfg = _make_config(tmp_path)
    acquire_lock(cfg.hive_tasks_dir, "task-1", owner="otro")

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        return ClaudeResult(
            session_id=None,
            result_text=(
                "Current session: 10% used · resets later\n"
                "Current week (all models): 10% used · resets later"
            ),
            raw={},
        )

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)

    result = dispatcher_mod.run_single_phase(
        cfg, "task-1", "myproj", "revisor", round_num=2, description=_DESCRIPTION,
    )

    assert result is None
    assert fake_git.review_cleanups == []
    assert read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1")).owner == "otro"


def test_run_single_phase_cleanup_does_not_swallow_a_real_failure(tmp_path, monkeypatch) -> None:
    """`result` is bound before the try for exactly this: close_cycle runs on
    the way out of a crash too, and must not turn it into its own error."""
    cfg = _make_config(tmp_path)

    def exploding_dispatch_phase(*args, **kwargs):
        raise RuntimeError("phase blew up")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", exploding_dispatch_phase)

    with pytest.raises(RuntimeError, match="phase blew up"):
        dispatcher_mod.run_single_phase(
            cfg, "task-1", "myproj", "auditor", final=True, description=_DESCRIPTION,
        )


def test_run_single_phase_stores_what_the_phase_returned(tmp_path, monkeypatch) -> None:
    """The piece everything below rests on. The prose rendering is for whoever
    reads next; the structure is for whoever has to act, and until it was
    written down it died with the process that held it."""
    cfg = _make_config(tmp_path)
    payload = {"verdict": "APPROVED", "debt_rulings": [{"debt": "no test", "ruling": debt.ACCEPTED}]}
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase",
        _recording_dispatch_phase([], handoffs={"revisor": payload}),
    )

    dispatcher_mod.run_single_phase(
        cfg, "task-1", "myproj", "revisor", round_num=2, description=_DESCRIPTION,
    )

    assert read_handoff(cfg.hive_tasks_dir, "task-1", "revisor") == payload


def test_run_single_phase_final_files_the_debt_the_stored_handoffs_declared(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """The whole point of storing them: a task driven a phase at a time closes
    with the same entries and resolved rows a cycle would have left."""
    cfg = _make_config(tmp_path)
    save_handoff(
        cfg.hive_tasks_dir, "task-1", "implementador",
        {"debt": [dict(_DEBT), dict(_MIGRATION)], "resolved_debt": ["`task-0-D3`"]},
        round_num=1,
    )
    save_handoff(
        cfg.hive_tasks_dir, "task-1", "revisor",
        {"verdict": "APPROVED",
         "debt_rulings": [{"debt": "The migration has no down step", "ruling": debt.REJECTED}]},
        round_num=1,
    )
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _recording_dispatch_phase(calls))

    dispatcher_mod.run_single_phase(
        cfg, "task-1", "myproj", "auditor", final=True, description=_DESCRIPTION,
    )

    # The revisor's ruling survived the round trip through disk: the auditor
    # is told one entry, not two.
    assert _filed_lines(calls[0]["prompt"]) == ["- `task-1-D1` — The retry loop has no test."]
    assert read_resolved_debt(cfg.hive_tasks_dir, "task-1") == ["task-0-D3"]


def test_run_single_phase_final_appends_its_filing_note_to_the_operators(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """`--note` is the operator's instruction to this phase and the filing
    note is the dispatcher's. The phase is owed both, so neither replaces the
    other."""
    cfg = _make_config(tmp_path)
    save_handoff(cfg.hive_tasks_dir, "task-1", "implementador", {"debt": [dict(_DEBT)]})
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _recording_dispatch_phase(calls))

    dispatcher_mod.run_single_phase(
        cfg, "task-1", "myproj", "auditor", final=True,
        description=_DESCRIPTION, note="The host rebooted mid-round.",
    )

    assert "The host rebooted mid-round." in calls[0]["prompt"]
    assert "`task-1-D1`" in calls[0]["prompt"]


def test_run_single_phase_final_without_stored_handoffs_files_nothing_and_says_so(
    tmp_path, monkeypatch, fake_git, caplog,
) -> None:
    """A task whose earlier phases ran before the dispatcher stored anything
    has no record to read. It closes the way this path always did — and the
    log says which of the two happened, because "no debt filed" means
    something very different when there was a record to file from."""
    cfg = _make_config(tmp_path)
    set_resolved_debt(cfg.hive_tasks_dir, "task-1", ["task-0-D3"])
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _recording_dispatch_phase(calls))

    with caplog.at_level("INFO", logger=dispatcher_mod.logger.name):
        dispatcher_mod.run_single_phase(
            cfg, "task-1", "myproj", "auditor", final=True, description=_DESCRIPTION,
        )

    assert "already has its ids" not in calls[0]["prompt"]
    # set_resolved_debt replaces, so a call that knows nothing must not make it.
    assert read_resolved_debt(cfg.hive_tasks_dir, "task-1") == ["task-0-D3"]
    assert read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1")).status == "done"
    assert any("No stored handoff was found" in r.getMessage() for r in caplog.records)


def test_run_single_phase_final_names_which_handoffs_it_closed_off(
    tmp_path, monkeypatch, fake_git, debt_index, caplog,
) -> None:
    """The other branch of the same line. Half a record is still a record, and
    the operator reading the log should not have to guess which half."""
    cfg = _make_config(tmp_path)
    save_handoff(cfg.hive_tasks_dir, "task-1", "implementador", {"debt": [dict(_DEBT)]})
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _recording_dispatch_phase([]))

    with caplog.at_level("INFO", logger=dispatcher_mod.logger.name):
        dispatcher_mod.run_single_phase(
            cfg, "task-1", "myproj", "auditor", final=True,
            description=_DESCRIPTION,
        )

    assert any(
        "implementador: found" in r.getMessage() and "revisor: missing" in r.getMessage()
        and "1 debt entr(y/ies) named for filing" in r.getMessage()
        for r in caplog.records
    )


def test_run_single_phase_that_is_not_final_files_no_debt(
    tmp_path, monkeypatch, debt_index,
) -> None:
    """A middle phase has no business closing anything out. The handoffs are
    on disk from the moment they are returned, so what keeps a revisor round
    from filing its own task's debt is --final and nothing else."""
    cfg = _make_config(tmp_path)
    save_handoff(cfg.hive_tasks_dir, "task-1", "implementador", {"debt": [dict(_DEBT)]})
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _recording_dispatch_phase(calls))

    dispatcher_mod.run_single_phase(
        cfg, "task-1", "myproj", "revisor", round_num=2, description=_DESCRIPTION,
    )

    assert "`task-1-D1`" not in calls[0]["prompt"]
    assert debt_index.reads == []


def _make_pool_config(tmp_path, **overrides):
    """The two-account pool Phase 2 is about: the operator's own console first
    in the config order and last in the picker's."""
    defaults = dict(
        accounts=[
            AccountConfig(name="cuenta1", container="agent-cuenta1", is_primary=True),
            AccountConfig(name="cuenta2", container="agent-cuenta2"),
        ],
        primary_account="cuenta1",
        reserve_pct=60,
        fallback_roles=["revisor", "auditor"],
    )
    defaults.update(overrides)
    return _make_config(tmp_path, **defaults)


def _age_busy_since(cfg, account_name, seconds) -> None:
    """Backdate an account's BUSY stamp.

    `set_state` always stamps `time.time()`, which is the whole point of it, so
    an aged stamp can only be written from outside — the same way the lock
    tests backdate a heartbeat by rewriting the card.
    """
    path = os.path.join(cfg.state_dir, f"{account_name}.json")
    with open(path) as fh:
        data = json.load(fh)
    data["busy_since"] = time.time() - seconds
    with open(path, "w") as fh:
        json.dump(data, fh)


def _age_heartbeat(cfg, task_id, seconds) -> None:
    path = task_file_path(cfg.hive_tasks_dir, task_id)
    task = read_task_file(path)
    task.heartbeat = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds)
    ).isoformat()
    write_task_file(path, task)


def test_the_picker_spends_a_worker_before_the_primary(tmp_path) -> None:
    """Both idle and the primary listed first: config order alone would spend
    the console the operator is talking to while a worker sat idle."""
    cfg = _make_pool_config(tmp_path)

    assert dispatcher_mod.pick_idle_account(cfg, role="implementador") == "cuenta2"


def test_the_picker_reads_a_primary_moved_since_the_last_pick(tmp_path) -> None:
    """The console moved the primary to cuenta2 through the overrides file:
    the next pick ranks cuenta2 last without a restart (ADR 176)."""
    overrides = tmp_path / "overrides.yaml"
    cfg = _make_pool_config(
        tmp_path, overrides_path=str(overrides), config_primary_account="cuenta1"
    )
    assert dispatcher_mod.pick_idle_account(cfg, role="implementador") == "cuenta2"

    overrides.write_text("primary_account: cuenta2\n")

    assert dispatcher_mod.pick_idle_account(cfg, role="implementador") == "cuenta1"
    assert cfg.primary_account == "cuenta2"


def test_a_pool_with_no_primary_keeps_the_config_order(tmp_path) -> None:
    """Every config written before this key existed is one of these, and it
    has to behave exactly as it did."""
    cfg = _make_config(
        tmp_path,
        accounts=[
            AccountConfig(name="cuenta1", container="agent-cuenta1"),
            AccountConfig(name="cuenta2", container="agent-cuenta2"),
        ],
    )

    assert dispatcher_mod.pick_idle_account(cfg, role="implementador") == "cuenta1"


def test_a_one_account_pool_is_not_a_fallback_decision(tmp_path) -> None:
    """With no worker to be out of quota there is nothing to fall back *from*.
    The account is simply the pool, so the role gate does not apply to it and
    an implementador runs on it like anything else — otherwise naming the only
    account as primary would take the harness out of service."""
    cfg = _make_config(
        tmp_path,
        accounts=[AccountConfig(name="cuenta1", container="agent-cuenta1", is_primary=True)],
        primary_account="cuenta1",
        fallback_roles=["revisor"],
    )

    assert dispatcher_mod.pick_idle_account(cfg, role="implementador") == "cuenta1"


def test_the_primary_takes_a_phase_its_roles_list_names(tmp_path, caplog) -> None:
    """A refusal is the one case the reserve exists for: cuenta2 was turned
    away by the service, so no amount of waiting brings it back inside the
    cooldown."""
    cfg = _make_pool_config(tmp_path)
    record_rate_limit(cfg.state_dir, "cuenta2")
    set_state(cfg.state_dir, "cuenta2", AccountState.COOLING_DOWN)

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        account = dispatcher_mod.pick_idle_account(cfg, role="revisor")

    assert account == "cuenta1"
    assert any("falls back to the primary account cuenta1" in r.getMessage() for r in caplog.records)


def test_the_primary_refuses_a_phase_outside_fallback_roles(tmp_path, caplog) -> None:
    """An implementador writes code across up to max_revision_rounds rounds and
    is the phase most likely to drain the reserve it was just handed. The pool
    is dry for it, and saying so is the answer the operator needs."""
    cfg = _make_pool_config(tmp_path)
    record_rate_limit(cfg.state_dir, "cuenta2")
    set_state(cfg.state_dir, "cuenta2", AccountState.COOLING_DOWN)

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        account = dispatcher_mod.pick_idle_account(cfg, role="implementador")

    assert account is None
    assert any("not in fallback_roles" in r.getMessage() for r in caplog.records)


def test_an_empty_fallback_roles_keeps_the_primary_out_of_everything(tmp_path) -> None:
    cfg = _make_pool_config(tmp_path, fallback_roles=[])
    record_rate_limit(cfg.state_dir, "cuenta2")
    set_state(cfg.state_dir, "cuenta2", AccountState.COOLING_DOWN)

    assert dispatcher_mod.pick_idle_account(cfg, role="revisor") is None


def test_a_caller_that_names_no_role_is_not_held_to_the_roles_list(tmp_path) -> None:
    """`role` is optional on the picker and the gate is about the phase, not
    about the account: a caller with no phase in hand has nothing to check."""
    cfg = _make_pool_config(tmp_path)
    record_rate_limit(cfg.state_dir, "cuenta2")
    set_state(cfg.state_dir, "cuenta2", AccountState.COOLING_DOWN)

    assert dispatcher_mod.pick_idle_account(cfg) == "cuenta1"


def test_the_picker_waits_out_a_worker_parked_on_a_counter(tmp_path, caplog) -> None:
    """Parked by a counter and parked by a refusal are different. cuenta2 went
    over the local threshold and `_recheck_cooling_accounts` re-probes it the
    moment nothing is IDLE, so waiting costs nothing while falling back spends
    the console."""
    cfg = _make_pool_config(tmp_path)
    set_state(cfg.state_dir, "cuenta2", AccountState.PRE_COOLDOWN)

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        account = dispatcher_mod.pick_idle_account(cfg, role="revisor")

    assert account is None
    assert any("on a counter, not on a refusal" in r.getMessage() for r in caplog.records)


def test_a_refusal_older_than_the_cooldown_is_worth_waiting_for_again(tmp_path) -> None:
    """The refusal stops being a reason to spend the reserve the moment
    quota_cooldown_seconds is up — past that the account is one probe away from
    coming back, which is the free option again."""
    cfg = _make_pool_config(tmp_path)
    record_rate_limit(
        cfg.state_dir, "cuenta2", at=time.time() - cfg.quota_cooldown_seconds - 1,
    )
    set_state(cfg.state_dir, "cuenta2", AccountState.COOLING_DOWN)

    assert dispatcher_mod.pick_idle_account(cfg, role="revisor") is None


def test_the_picker_waits_for_a_worker_that_is_running_a_phase(tmp_path, caplog) -> None:
    """The commonest reason the pool is short, and the one that always ends by
    itself."""
    cfg = _make_pool_config(tmp_path)
    set_state(cfg.state_dir, "cuenta2", AccountState.BUSY, current_task_id="task-1")

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        account = dispatcher_mod.pick_idle_account(cfg, role="revisor")

    assert account is None
    assert any("cuenta2 is running a phase" in r.getMessage() for r in caplog.records)


def test_a_worker_already_tried_this_dispatch_is_not_worth_waiting_for(tmp_path) -> None:
    """`exclude` is the dispatch's own list of accounts that just failed it, so
    a worker on it is not a worker that will come back — it is one that already
    did and was no use."""
    cfg = _make_pool_config(tmp_path)

    assert dispatcher_mod.pick_idle_account(cfg, exclude={"cuenta2"}, role="revisor") == "cuenta1"


def test_the_picker_returns_none_when_the_primary_is_out_too(tmp_path) -> None:
    cfg = _make_pool_config(tmp_path)
    set_state(cfg.state_dir, "cuenta1", AccountState.COOLING_DOWN)
    set_state(cfg.state_dir, "cuenta2", AccountState.COOLING_DOWN)

    assert dispatcher_mod.pick_idle_account(cfg, role="revisor") is None


def test_the_primary_is_held_to_the_reserve_and_a_worker_to_the_threshold(tmp_path) -> None:
    """Since ADR 48 this is the primary's *session* ceiling and its week's
    fallback, not the number its week parks on — the week's own ceiling is
    `quota.week_ceiling`'s and moves with the reset. The assertions stand
    because `_threshold_for` still answers exactly what it used to; what
    changed is how much of the rule it is."""
    cfg = _make_pool_config(tmp_path)

    assert dispatcher_mod._threshold_for(cfg, "cuenta1") == cfg.reserve_pct
    assert dispatcher_mod._threshold_for(cfg, "cuenta2") == cfg.quota_threshold_pct


def test_an_unknown_account_answers_to_the_worker_threshold(tmp_path) -> None:
    """There is exactly one primary and it is named in config, so anything the
    pool cannot identify is not it."""
    cfg = _make_pool_config(tmp_path)

    assert dispatcher_mod._threshold_for(cfg, "cuenta9") == cfg.quota_threshold_pct


def test_the_primary_parks_at_its_reserve_rather_than_the_worker_threshold(tmp_path, monkeypatch) -> None:
    """The reserve is a ceiling and not only an admission test: 70% is fine for
    a worker and past the line for the console.

    Unchanged by ADR 48, and it now exercises the fallback as well: `resets
    later` does not parse, so the week falls back to the 60% reserve exactly as
    before, and the 70% session parks the account either way. The paced path is
    `test_the_primarys_week_parks_on_the_paced_ceiling_alone`.
    """
    cfg = _make_pool_config(tmp_path)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        return ClaudeResult(
            session_id=None,
            result_text=(
                "Current session: 70% used · resets later\n"
                "Current week (all models): 20% used · resets later"
            ),
            raw={},
        )

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)

    assert dispatcher_mod.check_quota_ok(cfg, "cuenta1") is False
    assert dispatcher_mod.check_quota_ok(cfg, "cuenta2") is True


# --- a one-account pool ------------------------------------------------------
#
# What scripts/configure.sh sets up when the operator names a single account,
# with or without making it the primary. Nothing in the picker is special to
# it -- `has_workers` is what turns the fallback rules off -- so these pin the
# behaviour down for every role rather than trusting the one-liner to stay
# one.

#: Every phase the dispatcher hands an account to.
_EVERY_ROLE = ["arquitecto", "implementador", "revisor", "auditor", "cartografo"]


def test_the_role_list_here_is_every_phase_role() -> None:
    """The parametrised tests below are only "every role" while this holds."""
    from dispatcher.config import PHASE_ROLES

    assert sorted(_EVERY_ROLE) == sorted(PHASE_ROLES)


def _make_solo_config(tmp_path, *, primary: bool, **overrides):
    """One account, named primary or not, with a fallback_roles that would
    refuse most phases if it were consulted."""
    defaults = dict(
        accounts=[AccountConfig(name="solo", container="agent-solo", is_primary=primary)],
        primary_account="solo" if primary else None,
        reserve_pct=60,
        quota_threshold_pct=90,
        fallback_roles=["revisor"],
    )
    defaults.update(overrides)
    return _make_config(tmp_path, **defaults)


@pytest.mark.parametrize("primary", [True, False], ids=["primary", "no-primary"])
@pytest.mark.parametrize("role", _EVERY_ROLE)
def test_a_one_account_pool_hands_every_role_to_its_account(tmp_path, primary, role) -> None:
    cfg = _make_solo_config(tmp_path, primary=primary)

    assert dispatcher_mod.pick_idle_account(cfg, role=role) == "solo"


@pytest.mark.parametrize("primary", [True, False], ids=["primary", "no-primary"])
def test_fallback_roles_does_not_gate_a_one_account_pool(tmp_path, caplog, primary) -> None:
    """Even an empty list, which on a two-account pool keeps the primary out of
    everything: with no worker there is no fallback for it to rule on."""
    cfg = _make_solo_config(tmp_path, primary=primary, fallback_roles=[])

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        picks = [dispatcher_mod.pick_idle_account(cfg, role=role) for role in _EVERY_ROLE]

    assert picks == ["solo"] * len(_EVERY_ROLE)
    assert not any("fallback" in r.getMessage() for r in caplog.records)


def test_a_one_account_pool_that_is_parked_has_nothing_to_hand_out(tmp_path) -> None:
    cfg = _make_solo_config(tmp_path, primary=True)
    set_state(cfg.state_dir, "solo", AccountState.PRE_COOLDOWN)

    assert dispatcher_mod.pick_idle_account(cfg, role="revisor") is None


def _solo_probe(monkeypatch, session_pct):
    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        return ClaudeResult(
            session_id=None,
            result_text=(
                f"Current session: {session_pct}% used · resets later\n"
                "Current week (all models): 20% used · resets later"
            ),
            raw={},
        )

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)


@pytest.mark.parametrize(
    "primary, session_pct, parks",
    [
        # The primary is the operator's console even when it is the whole
        # pool, so it keeps its reserve: 70% is past 60.
        (True, 59, False),
        (True, 70, True),
        # Not named primary, it is a worker and answers to the threshold.
        (False, 70, False),
        (False, 95, True),
    ],
    ids=["primary-under-reserve", "primary-over-reserve", "worker-under-threshold", "worker-over-threshold"],
)
def test_a_one_account_pool_parks_at_the_ceiling_its_role_in_the_pool_sets(
    tmp_path, monkeypatch, primary, session_pct, parks,
) -> None:
    cfg = _make_solo_config(tmp_path, primary=primary)
    _solo_probe(monkeypatch, session_pct)

    assert dispatcher_mod._threshold_for(cfg, "solo") == (60 if primary else 90)
    assert dispatcher_mod.check_quota_ok(cfg, "solo") is not parks
    expected = AccountState.PRE_COOLDOWN if parks else AccountState.IDLE
    assert get_state(cfg.state_dir, "solo") == expected


@pytest.mark.parametrize("primary", [True, False], ids=["primary", "no-primary"])
def test_a_one_account_pool_runs_a_whole_cycle(tmp_path, monkeypatch, primary) -> None:
    """Each phase of the cycle asks the real picker for its account, under its
    own role, and runs on the one it gets: the cycle finishes on one account
    with a fallback_roles that names only the revisor."""
    cfg = _make_solo_config(tmp_path, primary=primary)
    calls = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        account = dispatcher_mod.pick_idle_account(cfg_arg, role=role)
        calls.append((role, account))
        text = "VERDICT: APPROVED" if role == "revisor" else "ok"
        return dispatcher_mod.DispatchResult(
            success=account is not None, session_id=None, result_text=text, account=account,
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert calls == [
        ("arquitecto", "solo"), ("implementador", "solo"), ("revisor", "solo"), ("auditor", "solo"),
    ]
    assert outcome is dispatcher_mod.CycleOutcome.FINISHED


# --- the primary's paced weekly ceiling (ADR 48) -----------------------------
#
# Every probe below is recorded text and every clock is supplied, so none of
# these tests depends on the day it runs on.

#: The probe clock these tests measure from.
_PACE_NOW = dt.datetime(2026, 10, 9, 12, 0, tzinfo=dt.timezone.utc)
#: Five days to the reset, where the ramp is 24.17% and the ceiling 19.17%.
_RESET_IN_5_DAYS = "Oct 14, 12:00pm (UTC)"
#: A day and a half, where the ramp is 73.75% and the ceiling 68.75%.
_RESET_IN_36_HOURS = "Oct 11, 12:00am (UTC)"
#: Exactly a day, the step to the flat 95% and a 90% ceiling.
_RESET_IN_1_DAY = "Oct 10, 12:00pm (UTC)"


def _pace_probe(monkeypatch, session_pct, week_pct, reset_clause, now=_PACE_NOW):
    """Hand both the probe and the clock to the dispatcher, recorded."""
    week_line = f"Current week (all models): {week_pct}% used"
    if reset_clause is not None:
        week_line += f" · resets {reset_clause}"

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        return ClaudeResult(
            session_id=None,
            result_text=f"Current session: {session_pct}% used\n{week_line}",
            raw={},
        )

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(dispatcher_mod, "_utc_now", lambda: now)


def test_the_primarys_week_parks_on_the_paced_ceiling_alone(tmp_path, monkeypatch, caplog) -> None:
    """40% of a week with five days still to run is inside the old 60% reserve
    and well past the paced ceiling, so the ceiling is the only thing parking
    this account — and the log says which ceiling, with what left of the week."""
    cfg = _make_pool_config(tmp_path)
    _pace_probe(monkeypatch, session_pct=10, week_pct=40, reset_clause=_RESET_IN_5_DAYS)

    with caplog.at_level("WARNING"):
        assert dispatcher_mod.check_quota_ok(cfg, "cuenta1") is False

    assert get_state(cfg.state_dir, "cuenta1") == AccountState.PRE_COOLDOWN
    parked = [r.getMessage() for r in caplog.records if "parking it PRE_COOLDOWN" in r.getMessage()]
    assert len(parked) == 1
    # The ceiling, the days left and the reset it read: an operator reading
    # this line can tell a paced refusal from a flat one without the config.
    assert "19.2% weekly ceiling" in parked[0]
    assert "5.0 days" in parked[0]
    assert "2026-10-14 12:00 UTC" in parked[0]


def test_the_paced_primary_is_released_when_the_ceiling_rises_past_its_week(
    tmp_path, monkeypatch
) -> None:
    """The release has no new scheduler behind it: the same recheck loop probes
    the parked account, and the ceiling it computes is simply higher the nearer
    the reset is. At five days out 40% is over the line; at a day and a half it
    is not."""
    cfg = _make_pool_config(tmp_path)
    set_state(cfg.state_dir, "cuenta1", AccountState.PRE_COOLDOWN)

    _pace_probe(monkeypatch, session_pct=10, week_pct=40, reset_clause=_RESET_IN_5_DAYS)
    assert dispatcher_mod._recheck_cooling_accounts(cfg) == []
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.PRE_COOLDOWN

    _pace_probe(monkeypatch, session_pct=10, week_pct=40, reset_clause=_RESET_IN_36_HOURS)
    assert dispatcher_mod._recheck_cooling_accounts(cfg) == ["cuenta1"]
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.IDLE


def test_the_gate_and_the_recheck_are_one_decision_about_the_ceiling(
    tmp_path, monkeypatch
) -> None:
    """The flap `_threshold_for`'s docstring warns about, pinned for a ceiling
    that moves: against the same probe and the same clock that parked it, the
    recheck must not hand the account straight back."""
    cfg = _make_pool_config(tmp_path)
    _pace_probe(monkeypatch, session_pct=10, week_pct=40, reset_clause=_RESET_IN_5_DAYS)

    assert dispatcher_mod.check_quota_ok(cfg, "cuenta1") is False
    assert dispatcher_mod._recheck_cooling_accounts(cfg) == []
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.PRE_COOLDOWN


def test_a_week_with_no_reset_clause_falls_back_to_the_reserve_and_says_so(
    tmp_path, monkeypatch, caplog
) -> None:
    """A 0% line carries no reset clause at all, so the fallback is an ordinary
    path and not a drift. It is today's behaviour exactly — 70% of a week over
    the 60% reserve still parks, which is also why a parse failure can never
    wave through something the older code refused."""
    cfg = _make_pool_config(tmp_path)
    _pace_probe(monkeypatch, session_pct=10, week_pct=70, reset_clause=None)

    with caplog.at_level("WARNING"):
        assert dispatcher_mod.check_quota_ok(cfg, "cuenta1") is False

    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "falls back to the 60% reserve" in m and "no reset clause" in m for m in messages
    )
    assert any("the 60% reserve on both windows" in m for m in messages)


def test_a_week_under_the_reserve_with_no_reset_clause_still_passes(tmp_path, monkeypatch) -> None:
    cfg = _make_pool_config(tmp_path)
    _pace_probe(monkeypatch, session_pct=10, week_pct=50, reset_clause=None)

    assert dispatcher_mod.check_quota_ok(cfg, "cuenta1") is True


def test_the_pacing_switch_off_restores_the_flat_reserve(tmp_path, monkeypatch) -> None:
    """The numbers that park a paced primary, with `pace_primary_week: false`:
    40% of a week is under the 60% reserve, so the account is admitted."""
    cfg = _make_pool_config(tmp_path, pace_primary_week=False)
    _pace_probe(monkeypatch, session_pct=10, week_pct=40, reset_clause=_RESET_IN_5_DAYS)

    assert dispatcher_mod.check_quota_ok(cfg, "cuenta1") is True


def test_a_worker_is_not_paced(tmp_path, monkeypatch) -> None:
    """A worker exists to be spent: it keeps `quota_threshold_pct` on both
    windows, whatever its reset says."""
    cfg = _make_pool_config(tmp_path)
    _pace_probe(monkeypatch, session_pct=10, week_pct=40, reset_clause=_RESET_IN_5_DAYS)

    assert dispatcher_mod.check_quota_ok(cfg, "cuenta2") is True
    assert get_state(cfg.state_dir, "cuenta2") == AccountState.IDLE


def test_the_paced_primarys_session_still_answers_to_the_reserve(tmp_path, monkeypatch) -> None:
    """The two windows are checked separately: a session over the reserve parks
    the account even with its week almost untouched and its ceiling at 90%."""
    cfg = _make_pool_config(tmp_path)
    _pace_probe(monkeypatch, session_pct=70, week_pct=1, reset_clause=_RESET_IN_1_DAY)

    assert dispatcher_mod.check_quota_ok(cfg, "cuenta1") is False
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.PRE_COOLDOWN


# --- the last probe, persisted (ADR 49) -------------------------------------
#
# Same recorded probes and supplied clock as the ADR 48 block above, read back
# out of the state file `_quota_decision` wrote them to.


def test_the_gate_records_a_paced_primarys_probe_and_both_its_ceilings(
    tmp_path, monkeypatch
) -> None:
    """Everything the park log says, kept: the probe's own numbers, the session's
    reserve, and the week's paced ceiling with the reset and days-left it was
    computed from. Recorded and then parked, so it also survived the park."""
    cfg = _make_pool_config(tmp_path)
    _pace_probe(monkeypatch, session_pct=10, week_pct=40, reset_clause=_RESET_IN_5_DAYS)

    assert dispatcher_mod.check_quota_ok(cfg, "cuenta1") is False

    assert get_state(cfg.state_dir, "cuenta1") == AccountState.PRE_COOLDOWN
    probe = get_last_probe(cfg.state_dir, "cuenta1")
    assert probe == {
        "probed_at": _PACE_NOW.timestamp(),
        "session_pct": 10,
        "week_pct": 40,
        "session_reset": None,
        "week_reset": _RESET_IN_5_DAYS,
        "exceeds": True,
        "session_ceiling_pct": 60,
        "week_ceiling": {
            "paced": True,
            "pct": pytest.approx(19.1667, abs=1e-3),
            "days_left": pytest.approx(5.0),
            "reset": "2026-10-14T12:00:00+00:00",
            "fallback_reason": None,
        },
    }
    # Plain JSON all the way down: the api serves this record as it reads it.
    assert json.loads(json.dumps(probe)) == probe


def test_the_recheck_records_its_probe_too(tmp_path, monkeypatch) -> None:
    """The release path writes the probe that released the account, so the row
    never shows the numbers that parked it after they stopped being true."""
    cfg = _make_pool_config(tmp_path)
    set_state(cfg.state_dir, "cuenta1", AccountState.PRE_COOLDOWN)
    _pace_probe(monkeypatch, session_pct=10, week_pct=40, reset_clause=_RESET_IN_36_HOURS)

    assert dispatcher_mod._recheck_cooling_accounts(cfg) == ["cuenta1"]

    probe = get_last_probe(cfg.state_dir, "cuenta1")
    assert probe["exceeds"] is False
    assert probe["week_reset"] == _RESET_IN_36_HOURS
    assert probe["week_ceiling"]["pct"] == pytest.approx(68.75)
    assert probe["week_ceiling"]["days_left"] == pytest.approx(1.5)
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.IDLE


def test_a_fallback_primarys_record_says_why_it_was_not_paced(tmp_path, monkeypatch) -> None:
    cfg = _make_pool_config(tmp_path)
    _pace_probe(monkeypatch, session_pct=10, week_pct=70, reset_clause=None)

    assert dispatcher_mod.check_quota_ok(cfg, "cuenta1") is False

    week = get_last_probe(cfg.state_dir, "cuenta1")["week_ceiling"]
    assert week == {
        "paced": True,
        "pct": 60.0,
        "days_left": None,
        "reset": None,
        "fallback_reason": "the week line carried no reset clause",
    }


@pytest.mark.parametrize(
    ("account", "overrides", "threshold"),
    [("cuenta2", {}, 90), ("cuenta1", {"pace_primary_week": False}, 60)],
    ids=["worker", "primary-with-pacing-off"],
)
def test_an_unpaced_record_carries_the_one_configured_threshold(
    tmp_path, monkeypatch, account, overrides, threshold
) -> None:
    cfg = _make_pool_config(tmp_path, **overrides)
    _pace_probe(monkeypatch, session_pct=10, week_pct=40, reset_clause=_RESET_IN_5_DAYS)

    assert dispatcher_mod.check_quota_ok(cfg, account) is True

    probe = get_last_probe(cfg.state_dir, account)
    assert (probe["week_pct"], probe["exceeds"], probe["session_ceiling_pct"]) == (
        40, False, threshold,
    )
    assert probe["week_ceiling"] == {
        "paced": False, "pct": threshold, "days_left": None, "reset": None,
        "fallback_reason": None,
    }


def test_a_failed_record_changes_neither_the_gate_nor_the_recheck(
    tmp_path, monkeypatch, caplog
) -> None:
    """The record is for the console. A write that fails must not crash the
    gate, and must not reach the recheck's fail-open `except` — which would read
    it as a failed probe and wave a parked account straight back to IDLE."""
    cfg = _make_pool_config(tmp_path)
    _pace_probe(monkeypatch, session_pct=10, week_pct=40, reset_clause=_RESET_IN_5_DAYS)

    def refuse(*args, **kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr(dispatcher_mod.state_machine, "record_probe", refuse)

    with caplog.at_level("WARNING"):
        assert dispatcher_mod.check_quota_ok(cfg, "cuenta1") is False
        assert get_state(cfg.state_dir, "cuenta1") == AccountState.PRE_COOLDOWN
        assert dispatcher_mod._recheck_cooling_accounts(cfg) == []

    assert get_state(cfg.state_dir, "cuenta1") == AccountState.PRE_COOLDOWN
    assert get_last_probe(cfg.state_dir, "cuenta1") is None
    failures = [
        r.getMessage() for r in caplog.records if "could not record the quota probe" in r.getMessage()
    ]
    assert len(failures) == 2
    assert "No space left on device" in failures[0]


def test_a_phase_with_a_live_heartbeat_is_left_alone_however_long_it_runs(tmp_path) -> None:
    """The card is the judge precisely so that a long phase is not a stale one:
    this account has been BUSY for hours and is still beating."""
    cfg = _make_config(tmp_path)
    acquire_lock(cfg.hive_tasks_dir, "task-1", owner="cuenta1")
    set_state(cfg.state_dir, "cuenta1", AccountState.BUSY, current_task_id="task-1")
    _age_busy_since(cfg, "cuenta1", 9999)

    assert dispatcher_mod.reap_stale_busy_accounts(cfg) == []
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.BUSY


def test_a_phase_whose_heartbeat_died_gives_its_account_back(tmp_path) -> None:
    cfg = _make_config(tmp_path)
    acquire_lock(cfg.hive_tasks_dir, "task-1", owner="cuenta1")
    set_state(cfg.state_dir, "cuenta1", AccountState.BUSY, current_task_id="task-1")
    _age_busy_since(cfg, "cuenta1", 9999)
    _age_heartbeat(cfg, "task-1", 999)

    assert dispatcher_mod.reap_stale_busy_accounts(cfg) == ["cuenta1"]
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.IDLE


def test_a_fresh_busy_stamp_is_a_floor_under_the_heartbeat_test(tmp_path) -> None:
    """A phase stamps BUSY before it takes its card, and the card it is about
    to take still carries the previous phase's heartbeat. Without the floor a
    second dispatcher would reap a phase that started seconds ago."""
    cfg = _make_config(tmp_path)
    acquire_lock(cfg.hive_tasks_dir, "task-1", owner="cuenta1")
    _age_heartbeat(cfg, "task-1", 999)
    set_state(cfg.state_dir, "cuenta1", AccountState.BUSY, current_task_id="task-1")

    assert dispatcher_mod.reap_stale_busy_accounts(cfg) == []
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.BUSY


def test_an_account_holding_no_card_is_judged_by_the_phase_timeout(tmp_path) -> None:
    """Nothing to read but the wall clock, so it gets the whole timeout rather
    than the heartbeat TTL — `timeout` kills the phase in the container at
    exactly that point, so past it there is nothing left to protect."""
    cfg = _make_config(tmp_path)
    set_state(cfg.state_dir, "cuenta1", AccountState.BUSY)
    _age_busy_since(cfg, "cuenta1", cfg.heartbeat_ttl_seconds + 1)

    assert dispatcher_mod.reap_stale_busy_accounts(cfg) == []

    _age_busy_since(cfg, "cuenta1", cfg.phase_timeout_seconds + 1)

    assert dispatcher_mod.reap_stale_busy_accounts(cfg) == ["cuenta1"]


def test_a_card_nobody_ever_beat_on_is_judged_by_the_phase_timeout_too(tmp_path) -> None:
    """`is_lock_expired` answers False for a card with no heartbeat, which is
    right for the lock — nothing there has gone stale — and would be a trap
    here, pinning the account BUSY for the life of the state file. The reaper
    treats a card with no heartbeat as no card at all."""
    cfg = _make_config(tmp_path)
    acquire_lock(cfg.hive_tasks_dir, "task-1", owner="cuenta1")
    path = task_file_path(cfg.hive_tasks_dir, "task-1")
    task = read_task_file(path)
    task.heartbeat = None
    write_task_file(path, task)
    assert not is_lock_expired(task, cfg.heartbeat_ttl_seconds), "the premise of this test"
    set_state(cfg.state_dir, "cuenta1", AccountState.BUSY, current_task_id="task-1")
    _age_busy_since(cfg, "cuenta1", cfg.phase_timeout_seconds + 1)

    assert dispatcher_mod.reap_stale_busy_accounts(cfg) == ["cuenta1"]


def test_a_busy_account_with_no_stamp_gets_the_clock_started(tmp_path) -> None:
    """BUSY written by hand, or by a dispatcher from before the stamp existed.
    One TTL of patience costs a dispatch; reaping on no evidence costs the
    phase. The card has to survive the restamp or the reap that follows would
    lose the account's task."""
    cfg = _make_config(tmp_path)
    path = os.path.join(cfg.state_dir, "cuenta1.json")
    os.makedirs(cfg.state_dir, exist_ok=True)
    with open(path, "w") as fh:
        json.dump({"state": "BUSY", "current_task_id": "task-1"}, fh)

    assert dispatcher_mod.reap_stale_busy_accounts(cfg) == []
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.BUSY
    assert get_busy_since(cfg.state_dir, "cuenta1") is not None
    assert get_current_task(cfg.state_dir, "cuenta1") == "task-1"


def test_the_picker_hands_out_an_account_the_reaper_just_freed(tmp_path) -> None:
    """The reaper runs inside the picker rather than on a timer, because the
    moment anyone wants an account is the moment it is worth finding out that
    one of them is only nominally busy."""
    cfg = _make_pool_config(tmp_path)
    set_state(cfg.state_dir, "cuenta2", AccountState.BUSY)
    _age_busy_since(cfg, "cuenta2", cfg.phase_timeout_seconds + 1)

    assert dispatcher_mod.pick_idle_account(cfg, role="implementador") == "cuenta2"


def _update_outcome(status, detail="the project's checkout and origin/main agree"):
    return dispatcher_mod.docker_exec.UpdateOutcome(status, detail)


def test_run_task_cycle_updates_the_project_before_the_first_phase(tmp_path, monkeypatch) -> None:
    """Once per task, and before anything has been cut from the base it moves.

    The four roles share one branch and one writer worktree, so the only safe
    moment is this one: after it, a fast-forward would move the ground under a
    revisor reading what the implementador wrote.
    """
    cfg = _make_config(tmp_path, update_project_before_task=True)
    order = []

    def fake_update(container, projects_root, slug, **kwargs):
        order.append(("update", container, projects_root, slug))
        return _update_outcome(dispatcher_mod.docker_exec.UPDATED, "fast-forwarded main 3 commit(s)")

    def fake_dispatch_phase(*args, **kwargs):
        order.append(("phase", kwargs.get("role") or args[3]))
        return _approving_dispatch_phase(*args, **kwargs)

    monkeypatch.setattr(dispatcher_mod.docker_exec, "update_project_branch", fake_update)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert order[0] == ("update", "agent-cuenta1", cfg.projects_root, "myproj")
    assert [step for step in order if step[0] == "update"] == [order[0]]
    assert [step[1] for step in order if step[0] == "phase"][0] == "arquitecto"


def test_run_task_cycle_leaves_the_remote_alone_when_the_operator_did_not_ask(tmp_path, monkeypatch) -> None:
    """Off by default. The flag is the only thing that makes a task reach the
    network, so a config that predates it behaves exactly as it did."""
    cfg = _make_config(tmp_path)

    def never(*args, **kwargs):
        raise AssertionError("the update ran without update_project_before_task")

    monkeypatch.setattr(dispatcher_mod.docker_exec, "update_project_branch", never)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.FINISHED


def test_run_task_cycle_blocks_when_the_project_diverged_from_its_remote(
    tmp_path, monkeypatch, caplog
) -> None:
    """Two histories, and picking one is a merge nobody asked this run to make
    — charter C-1. Nothing has been written yet, so stopping here costs a
    dispatch and saves a branch cut from a base that was about to be rewritten.
    """
    cfg = _make_config(tmp_path, update_project_before_task=True)
    detail = "main has 2 commit(s) ahead of origin and 3 behind: diverged"
    monkeypatch.setattr(
        dispatcher_mod.docker_exec,
        "update_project_branch",
        lambda *a, **kw: _update_outcome(dispatcher_mod.docker_exec.DIVERGED, detail),
    )
    roles = []

    def fake_dispatch_phase(*args, **kwargs):
        roles.append(kwargs.get("role") or args[3])
        return _approving_dispatch_phase(*args, **kwargs)

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    with caplog.at_level("WARNING"):
        outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert roles == []
    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    # Blocked naming nothing is a task nobody can restart.
    assert detail in "\n".join(r.getMessage() for r in caplog.records)


def test_run_task_cycle_starts_anyway_when_the_update_could_not_be_attempted(
    tmp_path, monkeypatch, caplog
) -> None:
    """No credential, an ssh:// remote, a fetch that failed: the base is then
    exactly as stale as it was before any of this existed, which is the
    behaviour every run had until now. Warned about, not stopped — but it has to
    be readable in the log afterwards, or a stale run looks like a clean one."""
    cfg = _make_config(tmp_path, update_project_before_task=True)
    detail = "no git credential file at /run/secrets/git-credentials; main left as it is"
    monkeypatch.setattr(
        dispatcher_mod.docker_exec,
        "update_project_branch",
        lambda *a, **kw: _update_outcome(dispatcher_mod.docker_exec.UNAVAILABLE, detail),
    )
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    with caplog.at_level("WARNING"):
        outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.FINISHED
    assert detail in "\n".join(r.getMessage() for r in caplog.records)


def test_run_task_cycle_does_not_warn_about_an_update_that_worked(tmp_path, monkeypatch) -> None:
    """UNCHANGED is the common answer — a clone already level with its remote,
    or one merge-task left ahead. A warning on it would train the operator to
    stop reading them."""
    cfg = _make_config(tmp_path, update_project_before_task=True)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec,
        "update_project_branch",
        lambda *a, **kw: _update_outcome(dispatcher_mod.docker_exec.UNCHANGED),
    )
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)
    records = []
    monkeypatch.setattr(dispatcher_mod.logger, "warning", lambda *a, **kw: records.append(a))

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert records == []


def test_update_project_branch_restores_ownership_around_the_update(tmp_path, monkeypatch, fake_git) -> None:
    """git writes to .git/ as root in the container, and that is the host's own
    checkout — the same restore every other git call into a container does."""
    cfg = _make_config(tmp_path, update_project_before_task=True)
    seen = {}

    def fake_update(container, projects_root, slug, credentials_path=None, **kwargs):
        seen.update(container=container, projects_root=projects_root, slug=slug,
                    credentials_path=credentials_path)
        return _update_outcome(dispatcher_mod.docker_exec.UNCHANGED)

    monkeypatch.setattr(dispatcher_mod.docker_exec, "update_project_branch", fake_update)

    outcome = dispatcher_mod.update_project_branch(cfg, "myproj")

    assert outcome.status == dispatcher_mod.docker_exec.UNCHANGED
    assert seen["credentials_path"] == cfg.git_credentials_path
    assert (f"{cfg.projects_root}/myproj", _FakeGit.owner) in fake_git.restored


def test_update_project_branch_restores_ownership_even_when_the_update_raises(
    tmp_path, monkeypatch, fake_git
) -> None:
    """A checkout left root-owned is a host the operator can no longer edit in."""
    cfg = _make_config(tmp_path, update_project_before_task=True)

    def boom(*args, **kwargs):
        raise RuntimeError("docker exec died")

    monkeypatch.setattr(dispatcher_mod.docker_exec, "update_project_branch", boom)

    with pytest.raises(RuntimeError):
        dispatcher_mod.update_project_branch(cfg, "myproj")

    assert (f"{cfg.projects_root}/myproj", _FakeGit.owner) in fake_git.restored


def test_update_project_branch_needs_no_particular_account(tmp_path, monkeypatch) -> None:
    """Every agent bind-mounts the same projects root, so this runs in
    cleanup_container's — which by then may be the only one not rate-limited."""
    cfg = _make_pool_config(tmp_path, update_project_before_task=True)
    containers = []
    monkeypatch.setattr(
        dispatcher_mod.docker_exec,
        "update_project_branch",
        lambda container, *a, **kw: containers.append(container)
        or _update_outcome(dispatcher_mod.docker_exec.UNCHANGED),
    )

    dispatcher_mod.update_project_branch(cfg, "myproj")

    assert containers == [dispatcher_mod.cleanup_container(cfg)]


def test_update_project_branch_does_nothing_without_a_container(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path, accounts=[], update_project_before_task=True)

    def never(*args, **kwargs):
        raise AssertionError("the update ran with no container to run it in")

    monkeypatch.setattr(dispatcher_mod.docker_exec, "update_project_branch", never)

    assert dispatcher_mod.update_project_branch(cfg, "myproj") is None


def _no_account_for(*roles: str):
    """Every phase works and the revisor approves, except that `roles` find
    no account free: the return dispatch_phase gives when the pool is empty."""
    def fake(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        if role in roles:
            return dispatcher_mod.DispatchResult(
                success=False, session_id=None, result_text="no accounts available",
                account="", no_account=True,
            )
        return _approving_dispatch_phase(cfg_arg, task_id, slug, role, prompt)
    return fake


def test_run_task_cycle_says_it_finished_when_the_auditor_closed_it(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.FINISHED


@pytest.mark.parametrize("role", ["arquitecto", "implementador", "revisor", "auditor"])
def test_run_task_cycle_is_held_when_no_account_takes_a_phase(tmp_path, monkeypatch, role) -> None:
    """Whichever phase it is: the run stops as for any stop, but it is held,
    which is what lets the CLI exit with a code a wait answers."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _no_account_for(role))

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.HELD
    # Stopped, not closed: the auditor's handoff is the only writer of `done`.
    path = task_file_path(cfg.hive_tasks_dir, "task-1")
    assert not os.path.exists(path) or read_task_file(path).status != "done"


def test_run_task_cycle_is_blocked_not_held_when_a_phase_fails_on_its_own(tmp_path, monkeypatch) -> None:
    """A phase that ran and failed is the task's problem, not the pool's."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase",
        lambda *a, **kw: dispatcher_mod.DispatchResult(
            success=False, session_id=None, result_text="claude timed out after 1800s", account="cuenta1",
        ),
    )

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED


def test_run_task_cycle_is_blocked_when_the_rounds_run_out(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path, max_revision_rounds=1)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _rejecting_dispatch_phase)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED


def test_run_task_cycle_is_blocked_when_there_is_no_description(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj")

    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED


def test_run_task_cycle_is_not_held_by_an_optional_map_that_found_no_account(tmp_path, monkeypatch) -> None:
    """The mapper is not a phase the task depends on: the task runs anyway, so
    the pool being empty for it says nothing about how the run ended."""
    cfg = _make_config(tmp_path, mapping_enabled=True)
    _fake_index(monkeypatch, exists=False)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _no_account_for(project_docs.MAPPER_ROLE))

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.FINISHED


def test_run_single_phase_hands_back_a_held_result_when_no_account_took_it(
    tmp_path, monkeypatch, fake_git,
) -> None:
    """Not None, which is a phase that failed: the CLI exits differently on the
    two, and `no_account` is how it tells them apart."""
    cfg = _make_config(tmp_path)
    seen = _spy_learnings(monkeypatch)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _no_account_for("auditor"))

    result = dispatcher_mod.run_single_phase(
        cfg, "task-1", "myproj", "auditor", final=True, description=_DESCRIPTION,
    )

    assert result is not None
    assert result.success is False and result.no_account is True
    # The phase never ran, so it handed nothing off and closed nothing.
    assert not os.path.exists(task_file_path(cfg.hive_tasks_dir, "task-1"))
    # Not completed: the entries --final carried go back, as for any stop.
    assert seen["orphaned"] == ["task-1"]


# --- what every `claude` call cost, recorded (ADR 53) ------------------------
#
# One line per call in the task's own usage log: the attempt itself and each of
# the three retries, on every account tried. Nothing here is ever a guessed
# number — a field the CLI did not give is `null`, never 0.


_USAGE_NOW = dt.datetime(2026, 10, 11, 9, 30, tzinfo=dt.timezone.utc)

#: A `--output-format json` object with everything on it the CLI is known to
#: print. The names are the CLI's own (`docs/ROADMAP.md` V1.1 *Record*, its
#: 2026-09-24 V5.1 row, root `README.md` item 2), because no stored `raw`
#: exists anywhere on this harness to read them off.
_FULL_RAW = {
    "is_error": False,
    "num_turns": 37,
    "duration_ms": 412_000,
    "total_cost_usd": 1.84,
    "session_id": "sess-1",
    "usage": {
        "input_tokens": 1_200,
        "output_tokens": 9_500,
        "cache_read_input_tokens": 480_000,
        "cache_creation_input_tokens": 31_000,
    },
    # Out of order on purpose: `models_used` is sorted. Values are never read.
    "modelUsage": {
        "claude-sonnet-5-5": {"costUSD": 1.0},
        "claude-haiku-5-5": {"costUSD": 0.84},
    },
}


def _usage_lines(cfg, task_id="task-1"):
    """The log as a reader gets it: a line skipped if it will not parse."""
    path = Path(usage_log_path(cfg.hive_tasks_dir, task_id))
    if not path.exists():
        return []
    records = []
    for line in path.read_text().splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def test_usage_record_keeps_every_number_a_full_result_carried(tmp_path) -> None:
    record = dispatcher_mod._usage_record(
        "T-024", "implementador", 2, "cuenta1", "phase", "claude-sonnet-5-5",
        ClaudeResult(session_id="sess-1", result_text="built it", raw=_FULL_RAW),
        _USAGE_NOW,
    )

    assert record == {
        "task_id": "T-024",
        "role": "implementador",
        "round": 2,
        "account": "cuenta1",
        "call": "phase",
        "model": "claude-sonnet-5-5",
        "session_id": "sess-1",
        "recorded_at": "2026-10-11T09:30:00+00:00",
        "measured": True,
        # Names only, never values: the first real run says on its own which
        # keys the CLI actually printed, without another probe.
        "raw_keys": [
            "duration_ms", "is_error", "modelUsage", "num_turns", "session_id",
            "total_cost_usd", "usage",
        ],
        "models_used": ["claude-haiku-5-5", "claude-sonnet-5-5"],
        "is_error": False,
        "num_turns": 37,
        "duration_ms": 412_000,
        "total_cost_usd": 1.84,
        "input_tokens": 1_200,
        "output_tokens": 9_500,
        "cache_read_input_tokens": 480_000,
        "cache_creation_input_tokens": 31_000,
        # No `permission_denials` key in the result, so not reported: null,
        # and no `permission_denials_dropped` (ADR 116).
        "permission_denials": None,
    }
    # Plain JSON all the way down: `append_usage` serialises this as it is.
    assert json.loads(json.dumps(record)) == record


def test_usage_record_of_a_result_with_no_usage_block_is_null_and_not_zero(tmp_path) -> None:
    """The difference P4 would rest on: a call whose token counts were never
    reported is unknown, and reading it as nothing spent would understate
    every median built on this log."""
    raw = {"is_error": False, "num_turns": 4, "duration_ms": 9_100}
    record = dispatcher_mod._usage_record(
        "T-024", "revisor", 1, "cuenta2", "phase", None,
        ClaudeResult(session_id="sess-2", result_text="ok", raw=raw),
        _USAGE_NOW,
    )

    assert record["model"] is None, "no model was passed, and none is filled in"
    assert record["models_used"] is None
    assert record["measured"] is True, "the CLI did answer; one of its keys is missing"
    assert record["raw_keys"] == ["duration_ms", "is_error", "num_turns"]
    assert record["num_turns"] == 4 and record["duration_ms"] == 9_100
    for key in (
        "input_tokens", "output_tokens",
        "cache_read_input_tokens", "cache_creation_input_tokens",
        "total_cost_usd",
    ):
        assert record[key] is None, key
        assert record[key] != 0


def test_usage_record_of_an_empty_result_says_it_has_no_usage(tmp_path) -> None:
    """`raw` is `{}` for a host-backstop timeout, a crash, a stdout that is not
    JSON and a JSON list. None of those cost nothing — they cost whatever ran
    before they broke, and the honest record of that is `measured: false`."""
    record = dispatcher_mod._usage_record(
        "T-024", "auditor", None, "cuenta1", "phase", "claude-opus-5-5",
        ClaudeResult(session_id=None, result_text="claude timed out after 7200s", raw={}),
        _USAGE_NOW,
    )

    assert record["measured"] is False
    assert record["models_used"] is None
    assert record["model"] == "claude-opus-5-5", "what was asked for is known even unmeasured"
    assert record["raw_keys"] == []
    assert record["session_id"] is None
    # The dispatcher's own ten are still right: this line is what says a
    # phase ran at all.
    assert record["task_id"] == "T-024"
    assert record["role"] == "auditor"
    assert record["round"] is None
    assert record["account"] == "cuenta1"
    assert record["call"] == "phase"
    assert record["recorded_at"] == "2026-10-11T09:30:00+00:00"
    for key in (
        "is_error", "num_turns", "duration_ms", "total_cost_usd",
        "input_tokens", "output_tokens",
        "cache_read_input_tokens", "cache_creation_input_tokens",
    ):
        assert record[key] is None, key


@pytest.mark.parametrize(
    "raw",
    [
        # A count the CLI printed as a string, which is what a format drift
        # looks like from here.
        {"num_turns": "37", "usage": {"input_tokens": "1200"}},
        # `usage` as something other than an object.
        {"usage": ["input_tokens"]},
        {"usage": None},
        # `True` is an `int` in Python and is not a token count.
        {"num_turns": True, "duration_ms": True},
    ],
)
def test_usage_record_refuses_a_value_that_is_not_a_number(raw) -> None:
    """Keeping the CLI's own spelling means a renamed or re-typed key shows up
    as a column that went all-null, never as a number that quietly means
    something else."""
    record = dispatcher_mod._usage_record(
        "T-024", "implementador", 1, "cuenta1", "phase", None,
        ClaudeResult(session_id="sess-1", result_text="", raw=raw),
        _USAGE_NOW,
    )

    assert record["measured"] is True
    for key in (
        "num_turns", "duration_ms", "total_cost_usd",
        "input_tokens", "output_tokens",
        "cache_read_input_tokens", "cache_creation_input_tokens",
    ):
        assert record[key] is None, key


@pytest.mark.parametrize("model_usage", [["m"], "m", None, 3])
def test_usage_record_models_used_is_null_for_a_model_usage_that_is_not_an_object(model_usage) -> None:
    record = dispatcher_mod._usage_record(
        "T-024", "implementador", 1, "cuenta1", "phase", None,
        ClaudeResult(session_id="sess-1", result_text="", raw={"modelUsage": model_usage}),
        _USAGE_NOW,
    )

    assert record["measured"] is True
    assert record["models_used"] is None


def test_a_retry_line_carries_the_model_the_phase_was_asked_for(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _FAT_HANDOFF}),
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF}),
    ])

    dispatcher_mod.dispatch_phase(
        cfg, "task-1", "myproj", "revisor", "review the thing", model="claude-x",
    )

    lines = _usage_lines(cfg)
    assert [line["call"] for line in lines] == ["phase", "shrink-retry"]
    assert [line["model"] for line in lines] == ["claude-x", "claude-x"]


def test_a_dispatch_with_no_model_records_null(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="built it", raw=_FULL_RAW),
    ])

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "build it")

    lines = _usage_lines(cfg)
    assert len(lines) == 1
    assert lines[0]["model"] is None


def test_a_dispatched_phase_leaves_one_line_naming_its_account_and_round(
    tmp_path, monkeypatch,
) -> None:
    cfg = _make_config(tmp_path)
    _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="built it", raw=_FULL_RAW),
    ])

    dispatcher_mod.dispatch_phase(
        cfg, "task-1", "myproj", "implementador", "build the thing", round_num=2,
    )

    lines = _usage_lines(cfg)
    assert len(lines) == 1
    assert lines[0]["call"] == "phase"
    assert lines[0]["account"] == "cuenta1"
    assert lines[0]["role"] == "implementador"
    assert lines[0]["round"] == 2
    assert lines[0]["num_turns"] == 37
    assert lines[0]["input_tokens"] == 1_200


def test_a_phase_that_did_not_finish_is_still_recorded(tmp_path, monkeypatch) -> None:
    """The case the record exists for as much as the clean one: a phase that
    crashed or timed out returns before `save_handoff`, so the envelope cannot
    carry it, and the account paid for it either way."""
    cfg = _make_config(tmp_path)
    _phase_recorder(monkeypatch, [
        ClaudeResult(session_id=None, result_text="claude timed out after 7200s", raw={}),
    ])

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review it")

    assert result.success is False
    lines = _usage_lines(cfg)
    assert len(lines) == 1
    assert lines[0]["call"] == "phase"
    assert lines[0]["measured"] is False
    assert lines[0]["num_turns"] is None


def test_a_failover_records_a_line_per_account_in_order(tmp_path, monkeypatch) -> None:
    """A refused attempt is a call the service served and the account paid for,
    so the log holds both halves of the handover rather than only the account
    the phase finished on."""
    cfg = _make_config(
        tmp_path,
        accounts=[
            AccountConfig(name="cuenta1", container="agent-cuenta1"),
            AccountConfig(name="cuenta2", container="agent-cuenta2"),
        ],
    )
    _fake_subagents(monkeypatch, agents=())

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        if "usage" in prompt.lower():
            return ClaudeResult(session_id=None, result_text=_USAGE_TEXT, raw={})
        if container == "agent-cuenta1":
            return ClaudeResult(
                session_id=_SESSION_UUID, result_text="usage limit reached",
                raw={"is_error": True, "num_turns": 1},
            )
        return ClaudeResult(session_id=_SESSION_UUID, result_text="carried on", raw=_FULL_RAW)

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    result = dispatcher_mod.dispatch_phase(
        cfg, "task-1", "myproj", "arquitecto", "do the thing", model="claude-x",
    )

    assert result.account == "cuenta2"
    lines = _usage_lines(cfg)
    assert [line["model"] for line in lines] == ["claude-x", "claude-x"]
    assert [(line["account"], line["call"]) for line in lines] == [
        ("cuenta1", "phase"), ("cuenta2", "phase"),
    ]
    assert lines[0]["is_error"] is True, "a 429 attempt and a clean phase are otherwise one shape"
    assert lines[1]["total_cost_usd"] == 1.84


def test_a_shrink_retry_is_recorded_as_its_own_call(tmp_path, monkeypatch) -> None:
    """Each retry replaces `result`, so anything derived from the phase's final
    return describes the last retry and not the phase. One line per call is
    what keeps the two apart."""
    cfg = _make_config(tmp_path)
    _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _FAT_HANDOFF, "num_turns": 30}),
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF, "num_turns": 2}),
    ])

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    lines = _usage_lines(cfg)
    assert [line["call"] for line in lines] == ["phase", "shrink-retry"]
    assert [line["num_turns"] for line in lines] == [30, 2]


def test_a_gate_retry_is_recorded_as_its_own_call(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path, gates_enabled=True)
    _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF, "num_turns": 30}),
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF, "num_turns": 5}),
    ])
    _gate_recorder(monkeypatch, [_one_finding(gates.ASK), gates.Report()])

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "build the thing")

    lines = _usage_lines(cfg)
    assert [line["call"] for line in lines] == ["phase", "gate-retry"]
    assert [line["num_turns"] for line in lines] == [30, 5]


def test_a_review_write_retry_is_recorded_as_its_own_call(tmp_path, monkeypatch, fake_git) -> None:
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(_FakeGit, "dirty", ("docs/implementations/task-1.md",))
    _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF, "num_turns": 20}),
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF, "num_turns": 3}),
    ])

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    lines = _usage_lines(cfg)
    assert [line["call"] for line in lines] == ["phase", "review-retry"]
    assert [line["num_turns"] for line in lines] == [20, 3]


def test_a_retry_that_was_discarded_is_still_recorded(tmp_path, monkeypatch) -> None:
    """It was thrown away as a *result* and the account still paid for it.
    Recorded before the branch that decides whether to keep it, for exactly
    that reason."""
    cfg = _make_config(tmp_path)
    _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _FAT_HANDOFF}),
        ClaudeResult(session_id="sess-1", result_text="boom", raw={}),
    ])

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert result.handoff == _FAT_HANDOFF, "the discarded retry is not the phase's answer"
    lines = _usage_lines(cfg)
    assert [line["call"] for line in lines] == ["phase", "shrink-retry"]
    assert lines[1]["measured"] is False


def test_a_usage_log_that_cannot_be_written_does_not_cost_the_phase(
    tmp_path, monkeypatch, caplog,
) -> None:
    """Bookkeeping on a path that may already be failing: a full disk must not
    be what fails a phase or changes a decision (`_record_probe`'s contract)."""
    cfg = _make_config(tmp_path)
    _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="built it", raw=_FULL_RAW),
    ])

    def boom(hive_dir, task_id, record):
        raise OSError("No space left on device")

    monkeypatch.setattr(dispatcher_mod.context_transfer, "append_usage", boom)

    with caplog.at_level("WARNING"):
        result = dispatcher_mod.dispatch_phase(
            cfg, "task-1", "myproj", "implementador", "build the thing",
        )

    assert result.success is True
    assert any(
        "implementador" in r.getMessage() and "No space left on device" in r.getMessage()
        for r in caplog.records
    )


# --- what the CLI refused, recorded (ADR 116) --------------------------------
#
# The `permission_denials` list a `claude -p` result carries, reduced to three
# keys per entry and capped, on the usage line and in the handoff envelope.


def _denial_record(raw):
    return dispatcher_mod._usage_record(
        "T-069", "implementador", 1, "cuenta1", "phase", None,
        ClaudeResult(session_id="sess-1", result_text="", raw=raw),
        _USAGE_NOW,
    )


_BASH_DENIAL = {
    "tool_name": "Bash",
    "tool_use_id": "toolu_01",
    "tool_input": {"command": "npm i", "description": "Install packages"},
}


def test_usage_record_keeps_each_denial_the_cli_reported() -> None:
    record = _denial_record({"permission_denials": [_BASH_DENIAL]})

    assert record["permission_denials"] == [_BASH_DENIAL]
    assert "permission_denials_dropped" not in record
    assert json.loads(json.dumps(record)) == record


def test_usage_record_keeps_an_empty_list_apart_from_an_absent_one() -> None:
    """`[]` is "the CLI said nothing was refused"; `null` is "it did not say".
    A reader asking whether a permission cost a phase needs the difference."""
    assert _denial_record({"permission_denials": []})["permission_denials"] == []
    assert _denial_record({"is_error": False})["permission_denials"] is None
    assert _denial_record({})["permission_denials"] is None


@pytest.mark.parametrize("value", [None, {"tool_name": "Bash"}, "Bash", 3, True])
def test_usage_record_denials_that_are_not_a_list_are_null(value) -> None:
    record = _denial_record({"permission_denials": value})

    assert record["permission_denials"] is None
    assert "permission_denials_dropped" not in record


def test_usage_record_drops_the_keys_of_a_denial_it_does_not_know() -> None:
    entry = {**_BASH_DENIAL, "reason": "not allowed", "extra": {"x": 1}}

    record = _denial_record({"permission_denials": [entry]})

    assert record["permission_denials"] == [_BASH_DENIAL]


def test_usage_record_fills_in_null_for_a_missing_id_or_input() -> None:
    record = _denial_record({"permission_denials": [{"tool_name": "Write", "tool_use_id": 7}]})

    assert record["permission_denials"] == [
        {"tool_name": "Write", "tool_use_id": None, "tool_input": None},
    ]


@pytest.mark.parametrize(
    "entry, type_name",
    [
        ("Bash", "str"),
        (["Bash"], "list"),
        (None, "NoneType"),
        ({"tool_use_id": "toolu_01"}, "dict"),
        ({"tool_name": 3, "tool_input": {}}, "dict"),
    ],
)
def test_usage_record_marks_a_malformed_denial_instead_of_dropping_it(entry, type_name) -> None:
    """Kept in place and named for what it was, so a format drift shows on the
    page instead of as a list one entry shorter."""
    record = _denial_record({"permission_denials": [_BASH_DENIAL, entry]})

    assert record["permission_denials"] == [_BASH_DENIAL, {"malformed": type_name}]


def test_usage_record_keeps_fifty_denials_and_counts_the_rest() -> None:
    denials = [
        {"tool_name": "Bash", "tool_use_id": f"toolu_{i}", "tool_input": {"command": str(i)}}
        for i in range(53)
    ]

    record = _denial_record({"permission_denials": denials})

    assert len(record["permission_denials"]) == 50
    assert record["permission_denials"][-1]["tool_use_id"] == "toolu_49"
    assert record["permission_denials_dropped"] == 3


def test_usage_record_of_exactly_fifty_denials_drops_nothing() -> None:
    denials = [{"tool_name": "Bash", "tool_use_id": None, "tool_input": None}] * 50

    record = _denial_record({"permission_denials": denials})

    assert len(record["permission_denials"]) == 50
    assert "permission_denials_dropped" not in record


def test_usage_record_cuts_every_long_string_in_a_denial_input() -> None:
    body = "x" * 1_234
    entry = {
        "tool_name": "Write",
        "tool_use_id": "toolu_02",
        "tool_input": {
            "file_path": "/repo/a.py",
            "content": body,
            "edits": [{"old_string": body, "new_string": "y" * 500}],
        },
    }

    record = _denial_record({"permission_denials": [entry]})

    kept = record["permission_denials"][0]["tool_input"]
    cut = "x" * 500 + "…[cut 734 chars]"
    assert kept["file_path"] == "/repo/a.py"
    assert kept["content"] == cut
    assert kept["edits"][0]["old_string"] == cut
    assert kept["edits"][0]["new_string"] == "y" * 500, "500 is the limit, not past it"
    # The CLI's own object is left as it was: the reduction copies.
    assert entry["tool_input"]["content"] == body


def test_usage_record_a_denial_that_will_not_reduce_is_null_and_the_line_stands(
    monkeypatch, caplog,
) -> None:
    def boom(raw):
        raise RuntimeError("unexpected shape")

    monkeypatch.setattr(dispatcher_mod, "_reduce_permission_denials", boom)

    with caplog.at_level("WARNING"):
        record = _denial_record(_FULL_RAW | {"permission_denials": [_BASH_DENIAL]})

    assert record["permission_denials"] is None
    assert record["input_tokens"] == 1_200, "the rest of the line is unharmed"
    assert "unexpected shape" in caplog.text


def test_a_denial_that_will_not_reduce_does_not_cost_the_phase(
    tmp_path, monkeypatch, caplog,
) -> None:
    cfg = _make_config(tmp_path)
    _phase_recorder(monkeypatch, [
        ClaudeResult(
            session_id="sess-1", result_text="built it",
            raw=_FULL_RAW | {"permission_denials": [_BASH_DENIAL]},
        ),
    ])

    def boom(raw):
        raise RuntimeError("unexpected shape")

    monkeypatch.setattr(dispatcher_mod, "_reduce_permission_denials", boom)

    with caplog.at_level("WARNING"):
        result = dispatcher_mod.dispatch_phase(
            cfg, "task-1", "myproj", "implementador", "build the thing",
        )

    assert result.success is True
    assert result.permission_denials is None
    lines = _usage_lines(cfg)
    assert len(lines) == 1
    assert lines[0]["permission_denials"] is None
    assert "unexpected shape" in caplog.text


def test_a_dispatched_phase_hands_back_the_denials_of_its_last_call(tmp_path, monkeypatch) -> None:
    """Each retry replaces `result`, so the handoff's denials are the call that
    answered — the retry — while each call keeps its own on its usage line."""
    cfg = _make_config(tmp_path)
    _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="", raw={
            "is_error": False, "structured_output": _FAT_HANDOFF,
            "permission_denials": [_BASH_DENIAL],
        }),
        ClaudeResult(session_id="sess-1", result_text="", raw={
            "is_error": False, "structured_output": _LEAN_HANDOFF, "permission_denials": [],
        }),
    ])

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert result.permission_denials == []
    lines = _usage_lines(cfg)
    assert [line["permission_denials"] for line in lines] == [[_BASH_DENIAL], []]


def test_a_phase_with_no_account_hands_back_no_denials() -> None:
    """No call was made, so there is nothing to have been refused or not: the
    default, which `save_handoff` writes as no key at all."""
    result = dispatcher_mod.DispatchResult(
        success=False, session_id=None, result_text="no accounts available", account="",
        no_account=True,
    )

    assert result.permission_denials is dispatcher_mod.context_transfer.PERMISSION_DENIALS_ABSENT


def test_run_single_phase_writes_the_denials_into_the_envelope(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, **kwargs):
        return dispatcher_mod.DispatchResult(
            success=True, session_id=None, result_text="VERDICT: APPROVED",
            account="cuenta1", handoff={"verdict": "APPROVED"},
            permission_denials=[_BASH_DENIAL],
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    dispatcher_mod.run_single_phase(
        cfg, "task-1", "myproj", "revisor", round_num=2, description=_DESCRIPTION,
    )

    envelope = dispatcher_mod.context_transfer.read_handoff_envelope(
        cfg.hive_tasks_dir, "task-1", "revisor",
    )
    assert envelope["permission_denials"] == [_BASH_DENIAL]
    assert envelope["handoff"] == {"verdict": "APPROVED"}


def test_run_single_phase_writes_null_denials_as_null(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, **kwargs):
        return dispatcher_mod.DispatchResult(
            success=True, session_id=None, result_text="VERDICT: APPROVED",
            account="cuenta1", handoff={"verdict": "APPROVED"}, permission_denials=None,
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    dispatcher_mod.run_single_phase(
        cfg, "task-1", "myproj", "revisor", round_num=2, description=_DESCRIPTION,
    )

    envelope = dispatcher_mod.context_transfer.read_handoff_envelope(
        cfg.hive_tasks_dir, "task-1", "revisor",
    )
    assert "permission_denials" in envelope
    assert envelope["permission_denials"] is None


def _left_behind_phase(tmp_path, monkeypatch, role="implementador"):
    cfg = _make_config(tmp_path)
    _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="built it", raw=_FULL_RAW),
    ])
    return cfg


def test_an_out_of_scope_edit_is_recorded_with_its_content(
    tmp_path, monkeypatch, fake_git, caplog,
) -> None:
    cfg = _left_behind_phase(tmp_path, monkeypatch)
    monkeypatch.setattr(_FakeGit, "left_outside", ("tests/x.py",))
    monkeypatch.setattr(_FakeGit, "patch", "--- a/tests/x.py\n+++ b/tests/x.py\n-nine\n+ten\n")

    with caplog.at_level("WARNING"):
        result = dispatcher_mod.dispatch_phase(
            cfg, "task-1", "myproj", "implementador", "build the thing", round_num=2,
        )

    assert result.success is True
    records = dispatcher_mod.context_transfer.read_left_behind(cfg.hive_tasks_dir, "task-1")
    assert [(r["role"], r["round"], r["outside"], r["excluded"]) for r in records] == [
        ("implementador", 2, ["tests/x.py"], [])
    ]
    with open(records[0]["patch"], encoding="utf-8") as fh:
        assert "+ten" in fh.read()
    assert "tests/x.py" in caplog.text and "NOT on the branch" in caplog.text


def test_an_excluded_path_is_recorded(tmp_path, monkeypatch) -> None:
    cfg = _left_behind_phase(tmp_path, monkeypatch)
    monkeypatch.setattr(_FakeGit, "left_excluded", ("docs/charter.md",))

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "build the thing")

    records = dispatcher_mod.context_transfer.read_left_behind(cfg.hive_tasks_dir, "task-1")
    assert records[0]["excluded"] == ["docs/charter.md"]
    assert records[0]["outside"] == []


def test_a_clean_phase_records_nothing_and_clears_an_earlier_record(
    tmp_path, monkeypatch,
) -> None:
    cfg = _left_behind_phase(tmp_path, monkeypatch)
    dispatcher_mod.context_transfer.save_left_behind(
        cfg.hive_tasks_dir, "task-1", "implementador", 1, ["old.py"], [], "old",
    )

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "build the thing")

    assert dispatcher_mod.context_transfer.read_left_behind(cfg.hive_tasks_dir, "task-1") == []


def test_a_failure_to_record_what_was_left_behind_does_not_fail_the_phase(
    tmp_path, monkeypatch, caplog,
) -> None:
    cfg = _left_behind_phase(tmp_path, monkeypatch)
    monkeypatch.setattr(_FakeGit, "left_outside", ("tests/x.py",))
    monkeypatch.setattr(_FakeGit, "diff_error", RuntimeError("git diff failed"))

    with caplog.at_level("WARNING"):
        result = dispatcher_mod.dispatch_phase(
            cfg, "task-1", "myproj", "implementador", "build the thing",
        )

    assert result.success is True
    assert any(
        "could not record" in r.getMessage() and "git diff failed" in r.getMessage()
        for r in caplog.records
    )


def test_left_behind_report_names_phase_paths_and_patch(tmp_path) -> None:
    cfg = _make_config(tmp_path)
    assert dispatcher_mod.left_behind_report(cfg, "task-1") == []
    patch = dispatcher_mod.context_transfer.save_left_behind(
        cfg.hive_tasks_dir, "task-1", "auditor", 2, ["tests/x.py"], ["docs/charter.md"], "p",
    )

    [line] = dispatcher_mod.left_behind_report(cfg, "task-1")

    assert "auditor phase (round 2)" in line
    assert "outside its scope: tests/x.py" in line
    assert "excluded: docs/charter.md" in line
    assert patch in line
    assert dispatcher_mod.left_behind_report(cfg, "task-1", role="revisor") == []


def test_the_automatic_merge_warns_about_what_was_left_behind(
    tmp_path, monkeypatch, caplog,
) -> None:
    cfg = _make_config(tmp_path, merge_on_done=True)
    dispatcher_mod.context_transfer.save_left_behind(
        cfg.hive_tasks_dir, "task-1", "auditor", 1, ["tests/x.py"], [], "p",
    )

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        dispatcher_mod._merge_task_branch(cfg, "task-1", "myproj")

    assert "tests/x.py" in caplog.text


def test_run_task_cycle_passes_an_effort_on_every_phase(tmp_path, monkeypatch) -> None:
    """No phase is left to the CLI's own default: each gets `default_effort`
    unless `role_effort` names its role (docs/plans/models-5-5.md, step 2)."""
    cfg = _make_config(tmp_path, default_effort="low", role_effort={"revisor": "xhigh"})
    calls = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        calls.append((role, effort))
        return _approving_dispatch_phase(cfg_arg, task_id, slug, role, prompt)

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert {role for role, _ in calls} >= {"arquitecto", "implementador", "revisor", "auditor"}
    for role, effort in calls:
        assert effort == ("xhigh" if role == "revisor" else "low"), role


def test_effort_for_escalation_wins_over_the_role_level(tmp_path) -> None:
    cfg = _make_config(
        tmp_path, escalate_effort_after_round=2, escalated_effort="max",
        default_effort="medium", role_effort={"implementador": "low"},
    )
    assert cfg.effort_for("implementador") == "low"
    assert cfg.effort_for("implementador", round_num=2) == "low"
    assert cfg.effort_for("implementador", round_num=3) == "max"
    assert cfg.effort_for("cartografo") == "medium"


def test_effort_for_without_the_signal_is_the_round_count_rule_for_every_role(tmp_path) -> None:
    """No signal, or the signal with the key off, changes nothing (ADR 73)."""
    roles = ("cartografo", "arquitecto", "implementador", "revisor", "auditor")
    settings = dict(
        escalate_effort_after_round=2, escalated_effort="max",
        default_effort="medium", role_effort={"implementador": "low", "auditor": "high"},
    )
    today = _make_config(tmp_path, **settings)
    on = _make_config(tmp_path, escalate_effort_on_repeated_finding=True, **settings)
    for role in roles:
        for round_num in (None, 1, 2, 3, 4):
            expected = (
                "max" if round_num is not None and round_num > 2
                else {"implementador": "low", "auditor": "high"}.get(role, "medium")
            )
            assert today.effort_for(role, round_num) == expected
            assert today.effort_for(role, round_num, repeated_finding=True) == expected
            assert on.effort_for(role, round_num) == expected
            assert on.effort_for(role, round_num, repeated_finding=True) == "max"


def test_model_for_falls_back_to_the_default_model(tmp_path) -> None:
    cfg = _make_config(
        tmp_path, default_model="claude-opus-5-5",
        role_model={"implementador": "claude-sonnet-5-5"},
    )
    assert cfg.model_for("implementador") == "claude-sonnet-5-5"
    assert cfg.model_for("auditor") == "claude-opus-5-5"


# --- merged recorded, dependencies gated, epics closed, blocked marked ---------
# `docs/plans/task-split.md` sections 3 and 4, `docs/implementations/T-030.md`.

_SPLIT = [
    handoff.SubTask(title="The api", surface="api/", description=_DESCRIPTION, after=()),
    handoff.SubTask(title="The screen", surface="front/", description=_DESCRIPTION, after=(1,)),
]


def _task(cfg, task_id="task-1"):
    return read_task_file(task_file_path(cfg.hive_tasks_dir, task_id))


def _epic(cfg) -> None:
    """`task-0` split into `task-0-S1` and `task-0-S2`, the second after the first."""
    tasks.handoff(cfg.hive_tasks_dir, "task-0", new_status="pending", body="## arquitecto")
    tasks.file_split(cfg.hive_tasks_dir, "task-0", _SPLIT)


def _hive_bytes(cfg) -> dict:
    root = Path(cfg.hive_tasks_dir).parent
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*")) if path.is_file() and "learnings" not in path.parts
    }


def test_a_cycle_that_merges_records_it_on_the_task(tmp_path, monkeypatch, fake_git) -> None:
    cfg = _make_config(tmp_path, merge_on_done=True)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    task = _task(cfg)
    assert task.merged["into"] == "main"
    assert task.status == "done"


def test_a_cycle_whose_merge_was_refused_records_nothing(tmp_path, monkeypatch, fake_git) -> None:
    cfg = _make_config(tmp_path, merge_on_done=True)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)
    fake_git.outcome = dispatcher_mod.docker_exec.MergeOutcome(
        dispatcher_mod.docker_exec.REFUSED, "main", "has uncommitted changes"
    )

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.FINISHED
    assert _task(cfg).merged is None


def test_a_cycle_that_does_not_merge_records_nothing(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert _task(cfg).merged is None


def test_merging_the_last_child_closes_the_epic_and_any_other_does_not(
    tmp_path, monkeypatch, fake_git,
) -> None:
    cfg = _make_config(tmp_path, merge_on_done=True)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)
    _epic(cfg)

    assert dispatcher_mod.run_task_cycle(cfg, "task-0-S1", "myproj") is dispatcher_mod.CycleOutcome.FINISHED
    assert _task(cfg, "task-0-S1").merged["into"] == "main"
    assert _task(cfg, "task-0").status == "epic"

    assert dispatcher_mod.run_task_cycle(cfg, "task-0-S2", "myproj") is dispatcher_mod.CycleOutcome.FINISHED
    assert _task(cfg, "task-0").status == "done"


def _merge(status, target="main"):
    return dispatcher_mod.docker_exec.MergeOutcome(status, target, "detail")


def test_record_merge_keeps_the_first_record_when_the_branch_was_already_in(tmp_path) -> None:
    """Running `merge-task` again is how a record that was never written gets
    written, and it must not move the date of one that was."""
    cfg = _make_config(tmp_path)
    tasks.handoff(cfg.hive_tasks_dir, "task-1", new_status="done", body="## auditor")
    up_to_date = _merge(dispatcher_mod.docker_exec.UP_TO_DATE)

    recorded, problem = dispatcher_mod.record_merge(cfg, "task-1", up_to_date)
    assert (recorded, problem) == (["task task-1: recorded as merged into main"], None)
    first = _task(cfg).merged

    assert dispatcher_mod.record_merge(cfg, "task-1", up_to_date) == ([], None)
    assert _task(cfg).merged == first
    # A merge that happened is a new fact, and replaces it.
    dispatcher_mod.record_merge(cfg, "task-1", _merge(dispatcher_mod.docker_exec.MERGED, "release"))
    assert _task(cfg).merged["into"] == "release"


def test_record_merge_repairs_an_epic_whose_close_never_happened(tmp_path) -> None:
    cfg = _make_config(tmp_path)
    _epic(cfg)
    tasks.mark_merged(cfg.hive_tasks_dir, "task-0-S1", "main")
    tasks.mark_merged(cfg.hive_tasks_dir, "task-0-S2", "main")

    recorded, problem = dispatcher_mod.record_merge(
        cfg, "task-0-S2", _merge(dispatcher_mod.docker_exec.UP_TO_DATE)
    )

    assert (recorded, problem) == (["epic task-0: every sub-task is merged or dropped, so it is done"], None)
    assert _task(cfg, "task-0").status == "done"


def test_record_merge_reports_what_it_could_not_write_and_does_not_raise(tmp_path) -> None:
    cfg = _make_config(tmp_path)
    os.makedirs(cfg.hive_tasks_dir)
    merged = _merge(dispatcher_mod.docker_exec.MERGED)

    recorded, problem = dispatcher_mod.record_merge(cfg, "task-1", merged)
    assert recorded == [] and "no task file" in problem
    assert os.listdir(cfg.hive_tasks_dir) == []

    Path(task_file_path(cfg.hive_tasks_dir, "task-1")).write_text(
        "---\ntask_id: task-1\nstatus: done\ndescription: old\n---\n\n"
    )
    recorded, problem = dispatcher_mod.record_merge(cfg, "task-1", merged)
    assert recorded == [] and tasks.MIGRATE_COMMAND in problem


def _start_epic_run(cfg, limit=None) -> None:
    """*Start epic* on `task-0` as the board service queues it (ADR NNN)."""
    from dispatcher import actions, epic_runs
    actions.start_epic_run(
        cfg.state_dir, "task-0", "myproj", ["task-0-S1"],
        offset=lambda tid: epic_runs.usage_offset(cfg.hive_tasks_dir, tid),
        spent=lambda run: epic_runs.spend(cfg.hive_tasks_dir, run)[0],
        limit=limit, actor="test",
    )


def _queued_starts(cfg) -> list[str]:
    from dispatcher import actions
    entries, _ = actions.read_all(cfg.state_dir)
    return [e.record["task_id"] for e in entries if e.state == "pending"]


def test_record_merge_queues_a_started_epics_next_task_once(tmp_path) -> None:
    cfg = _make_config(tmp_path)
    _epic(cfg)
    _start_epic_run(cfg)
    merged = _merge(dispatcher_mod.docker_exec.MERGED)

    recorded, problem = dispatcher_mod.record_merge(cfg, "task-0-S1", merged)

    assert problem is None
    assert recorded[-1] == "epic task-0: queued task-0-S2"
    assert _queued_starts(cfg) == ["task-0-S1", "task-0-S2"]
    # `merge-task` run again records nothing new and queues nothing twice.
    up_to_date = _merge(dispatcher_mod.docker_exec.UP_TO_DATE)
    assert dispatcher_mod.record_merge(cfg, "task-0-S1", up_to_date) == ([], None)
    assert _queued_starts(cfg) == ["task-0-S1", "task-0-S2"]


def test_record_merge_queues_nothing_for_a_stopped_or_unstarted_epic(tmp_path) -> None:
    from dispatcher import actions
    cfg = _make_config(tmp_path)
    _epic(cfg)
    dispatcher_mod.record_merge(cfg, "task-0-S1", _merge(dispatcher_mod.docker_exec.MERGED))
    assert _queued_starts(cfg) == []

    _start_epic_run(cfg)
    actions.create_epic_cancels(cfg.state_dir, "task-0", actor="test")
    tasks.mark_merged(cfg.hive_tasks_dir, "task-0-S1", "release")
    recorded, problem = dispatcher_mod.record_merge(
        cfg, "task-0-S1", _merge(dispatcher_mod.docker_exec.MERGED)
    )
    assert problem is None and not any(line.startswith("epic") for line in recorded)
    assert "task-0-S2" not in _queued_starts(cfg)


def test_record_merge_does_not_queue_a_task_already_started(tmp_path) -> None:
    from dispatcher import actions
    cfg = _make_config(tmp_path)
    _epic(cfg)
    _start_epic_run(cfg)
    tasks.mark_merged(cfg.hive_tasks_dir, "task-0-S1", "main")
    actions.create_starts(cfg.state_dir, "myproj", ["task-0-S2"], actor="test")

    recorded, _ = dispatcher_mod.record_merge(
        cfg, "task-0-S1", _merge(dispatcher_mod.docker_exec.UP_TO_DATE)
    )

    assert not any(line.startswith("epic") for line in recorded)
    assert _queued_starts(cfg).count("task-0-S2") == 1


def test_record_merge_pauses_an_epic_at_its_limit(tmp_path) -> None:
    from dispatcher import actions, context_transfer
    cfg = _make_config(tmp_path)
    _epic(cfg)
    _start_epic_run(cfg, limit=10)
    context_transfer.append_usage(cfg.hive_tasks_dir, "task-0-S1", {
        "input_tokens": 15, "output_tokens": 5,
        "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
    })

    recorded, problem = dispatcher_mod.record_merge(
        cfg, "task-0-S1", _merge(dispatcher_mod.docker_exec.MERGED)
    )

    assert problem is None
    assert recorded[-1] == "epic task-0: paused, its limit is reached"
    assert _queued_starts(cfg) == ["task-0-S1"]
    assert actions.read_epic_run(cfg.state_dir, "task-0")["paused"] == actions.PAUSED_LIMIT


def _refused_by_open_cycle(cfg, caplog, task_id="task-1", description=None) -> str:
    """Open a cycle that must be refused, and check nothing was written to the hive."""
    before = _hive_bytes(cfg)
    with caplog.at_level("ERROR", logger=dispatcher_mod.logger.name):
        ctx = dispatcher_mod.open_cycle(cfg, task_id, "myproj", description=description)
    assert ctx is None
    assert _hive_bytes(cfg) == before
    return caplog.text


def _waiting_on(cfg, *deps) -> None:
    tasks.handoff(
        cfg.hive_tasks_dir, "task-1", new_status="pending", body="", depends_on=list(deps)
    )


def test_open_cycle_refuses_a_task_whose_dependency_has_no_file(tmp_path, caplog) -> None:
    cfg = _make_config(tmp_path)
    _waiting_on(cfg, "task-404")

    logged = _refused_by_open_cycle(cfg, caplog, description=_DESCRIPTION)

    assert "task task-1 cannot start: task-404 has no task file" in logged
    # Refused before the supplied description was written.
    assert tasks.read_description(cfg.hive_tasks_dir, "task-1") is None


def test_open_cycle_refuses_a_dependency_that_is_done_and_not_merged(tmp_path, caplog) -> None:
    cfg = _make_config(tmp_path)
    tasks.handoff(cfg.hive_tasks_dir, "task-a", new_status="done", body="## auditor")
    _waiting_on(cfg, "task-a", "task-404")

    logged = _refused_by_open_cycle(cfg, caplog, description=_DESCRIPTION)

    assert "task task-1 cannot start: task-a is not merged; task-404 has no task file" in logged


def test_open_cycle_accepts_a_dependency_that_is_merged(tmp_path) -> None:
    cfg = _make_config(tmp_path)
    tasks.handoff(cfg.hive_tasks_dir, "task-a", new_status="done", body="## auditor")
    tasks.mark_merged(cfg.hive_tasks_dir, "task-a", "main")
    _waiting_on(cfg, "task-a")

    assert dispatcher_mod.open_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION) is not None


@pytest.mark.parametrize("status", tasks.BACKLOG_STATUSES)
def test_open_cycle_refuses_a_backlog_task(tmp_path, caplog, status) -> None:
    """ADR 127: a proposed, deferred or dropped task is accepted first."""
    cfg = _make_config(tmp_path)
    tasks.handoff(cfg.hive_tasks_dir, "task-1", new_status=status, body="")

    logged = _refused_by_open_cycle(cfg, caplog, description=_DESCRIPTION)

    assert f"task task-1 cannot start: it is in the backlog (status {status})" in logged
    assert tasks.read_description(cfg.hive_tasks_dir, "task-1") is None


def test_open_cycle_refuses_a_bad_priority_or_source(tmp_path, caplog) -> None:
    cfg = _make_config(tmp_path)
    tasks.handoff(cfg.hive_tasks_dir, "task-1", new_status="pending", body="")
    path = Path(task_file_path(cfg.hive_tasks_dir, "task-1"))
    path.write_text(path.read_text().replace("status: pending\n", "status: pending\npriority: urgent\n"))

    logged = _refused_by_open_cycle(cfg, caplog, description=_DESCRIPTION)

    assert "task task-1 cannot start: its priority is 'urgent'" in logged


@pytest.mark.parametrize("status", ["pending", "blocked", "in_progress"])
def test_open_cycle_still_opens_a_board_task(tmp_path, status) -> None:
    """Only the backlog is refused: a blocked retry and a resume come through here."""
    cfg = _make_config(tmp_path)
    tasks.handoff(cfg.hive_tasks_dir, "task-1", new_status=status, body="")
    path = Path(task_file_path(cfg.hive_tasks_dir, "task-1"))
    path.write_text(
        path.read_text().replace(
            f"status: {status}\n", f"status: {status}\npriority: high\nsource: operator\n"
        )
    )

    assert dispatcher_mod.open_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION) is not None


def test_run_task_cycle_is_blocked_by_an_unmet_dependency_and_marks_nothing(
    tmp_path, monkeypatch,
) -> None:
    cfg = _make_config(tmp_path)
    _waiting_on(cfg, "task-404")
    roles = []
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase", _recording_dispatch_phase(roles)
    )

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    assert roles == []
    assert _task(cfg).status == "pending"


def test_open_cycle_refuses_a_child_of_a_malformed_epic_with_the_reasons(tmp_path, caplog) -> None:
    cfg = _make_config(tmp_path)
    _epic(cfg)
    assert dispatcher_mod.open_cycle(cfg, "task-0-S1", "myproj") is not None
    # By hand: the parent stops being an epic and forgets one of its children.
    path = task_file_path(cfg.hive_tasks_dir, "task-0")
    parent = read_task_file(path)
    parent.status, parent.split_into = "pending", ["task-0-S2", "task-0-S9"]
    write_task_file(path, parent)

    logged = _refused_by_open_cycle(cfg, caplog, task_id="task-0-S1")

    assert "task task-0-S1 cannot start: " in logged
    for reason in (
        "its epic task-0 has status pending, not epic",
        "epic task-0 does not name task-0-S1 in its split_into",
        "epic task-0: task-0-S9 has no task file",
        "task-0-S2: depends_on names task-0-S1, which is not a sibling",
    ):
        assert reason in logged


def test_open_cycle_refuses_a_child_whose_epic_has_no_file(tmp_path, caplog) -> None:
    cfg = _make_config(tmp_path)
    _epic(cfg)
    os.remove(task_file_path(cfg.hive_tasks_dir, "task-0"))

    logged = _refused_by_open_cycle(cfg, caplog, task_id="task-0-S1")

    assert "task task-0-S1 cannot start: its epic task-0 has no task file" in logged


def test_a_cycle_that_ran_out_of_rounds_leaves_the_task_blocked(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path, max_revision_rounds=1)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _rejecting_dispatch_phase)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    task = _task(cfg)
    assert (task.status, task.owner, task.heartbeat) == ("blocked", None, None)
    assert "## revisor" in task.body  # the trail stays


def test_a_cycle_the_arquitecto_blocked_leaves_the_task_blocked(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase",
        _recording_dispatch_phase([], handoffs={"arquitecto": {"status": "blocked"}}),
    )

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    assert _task(cfg).status == "blocked"


def test_a_phase_that_failed_after_one_that_landed_leaves_the_task_blocked(
    tmp_path, monkeypatch,
) -> None:
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase", _recording_dispatch_phase([], failing=("implementador",))
    )

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    assert _task(cfg).status == "blocked"


def test_a_blocked_task_runs_again_and_can_finish(tmp_path, monkeypatch) -> None:
    """Running it again is the operator's decision to retry."""
    cfg = _make_config(tmp_path, max_revision_rounds=1)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _rejecting_dispatch_phase)
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)
    assert _task(cfg).status == "blocked"
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj")

    assert outcome is dispatcher_mod.CycleOutcome.FINISHED
    assert _task(cfg).status == "done"


def test_a_held_cycle_does_not_mark_the_task_blocked(tmp_path, monkeypatch) -> None:
    """Held is a wait for an account, even after phases of this run took the lock."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _no_account_for("implementador"))

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.HELD
    assert _task(cfg).status == "pending"


def test_a_cycle_that_bounced_off_another_owners_lock_does_not_mark_it_blocked(
    tmp_path, monkeypatch,
) -> None:
    """Even when an earlier phase of this run did take the lock: marking would
    release the lock of the run that holds the task now."""
    cfg = _make_config(tmp_path)

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, **kwargs):
        if role == "implementador":
            acquire_lock(cfg.hive_tasks_dir, task_id, owner="otro")
            raise LockHeldError(task_id, "otro")
        return _approving_dispatch_phase(cfg_arg, task_id, slug, role, prompt)

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    task = _task(cfg)
    assert (task.status, task.owner) == ("in_progress", "otro")


def test_a_run_that_took_the_task_since_keeps_its_lock_when_this_one_blocks(
    tmp_path, monkeypatch,
) -> None:
    """No phase of this run bounced, but the file has an owner at the end: a
    second run took the task after this one's last phase let go of it."""
    cfg = _make_config(tmp_path, max_revision_rounds=1)

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, **kwargs):
        return _rejecting_dispatch_phase(cfg_arg, task_id, slug, role, prompt)

    real_handoff = tasks.handoff

    def handoff_then_taken(hive, task_id, **kwargs):
        real_handoff(hive, task_id, **kwargs)
        if "revisor" in kwargs["body"]:
            acquire_lock(hive, task_id, owner="otro")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)
    monkeypatch.setattr(dispatcher_mod.tasks, "handoff", handoff_then_taken)

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    task = _task(cfg)
    assert (task.status, task.owner) == ("in_progress", "otro")


def test_a_diverged_base_does_not_mark_the_task_blocked(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path, update_project_before_task=True)
    tasks.handoff(cfg.hive_tasks_dir, "task-1", new_status="pending", body="## arquitecto")
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "update_project_branch",
        lambda *a, **kw: _update_outcome(dispatcher_mod.docker_exec.DIVERGED, "diverged"),
    )

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    assert _task(cfg).status == "pending"


def test_a_blocked_cycle_with_no_task_file_creates_none(tmp_path, monkeypatch) -> None:
    """The first phase failed, so no handoff ever wrote the file."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase", _recording_dispatch_phase([], failing=("arquitecto",))
    )

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    assert not os.path.exists(task_file_path(cfg.hive_tasks_dir, "task-1"))


def test_a_single_phase_that_failed_does_not_mark_the_task_blocked(tmp_path, monkeypatch) -> None:
    """`run-phase` is the operator driving the task by hand; only a cycle marks."""
    cfg = _make_config(tmp_path)
    tasks.handoff(cfg.hive_tasks_dir, "task-1", new_status="pending", body="## arquitecto")
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase", _recording_dispatch_phase([], failing=("implementador",))
    )

    result = dispatcher_mod.run_single_phase(
        cfg, "task-1", "myproj", "implementador", description=_DESCRIPTION
    )

    assert result is None
    assert _task(cfg).status == "pending"


# --- the split, switched on ------------------------------------------------
# `docs/plans/task-split.md` section 8 step 4, `docs/decisions.md` ADR 60.

def _split_payload(subtasks=_SPLIT, status="complete") -> dict:
    """The arquitecto's return carrying `subtasks` as its split."""
    return {
        "status": status,
        "pending": ["a decision nobody made"] if status == "blocked" else [],
        "split": [
            {
                "title": subtask.title, "surface": subtask.surface,
                "description": subtask.description, "after": list(subtask.after),
            }
            for subtask in subtasks
        ],
    }


def _splitting_cycle(cfg, monkeypatch, payload, task_id="task-1", description=_DESCRIPTION):
    """Run a cycle whose arquitecto returns `payload`; the outcome and the roles dispatched."""
    calls = []
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase",
        _recording_dispatch_phase(calls, handoffs={"arquitecto": payload}),
    )
    outcome = dispatcher_mod.run_task_cycle(cfg, task_id, "myproj", description=description)
    return outcome, [call["role"] for call in calls]


def _split_ctx(cfg, task_id="task-1"):
    return dispatcher_mod.CycleContext(
        cfg=cfg, task_id=task_id, slug="myproj", description=_DESCRIPTION,
        task_file=task_file_path(cfg.hive_tasks_dir, task_id),
        scratch_dir=scratch_dir(cfg.hive_tasks_dir, task_id), harness="",
    )


def _with_status(cfg, status, task_id="task-1", **fields) -> None:
    tasks.handoff(cfg.hive_tasks_dir, task_id, new_status="pending", body="## arquitecto")
    path = task_file_path(cfg.hive_tasks_dir, task_id)
    task = read_task_file(path)
    task.status = status
    for name, value in fields.items():
        setattr(task, name, value)
    write_task_file(path, task)


def test_a_cycle_whose_arquitecto_split_files_the_epic_and_stops(
    tmp_path, monkeypatch, caplog,
) -> None:
    cfg = _make_config(tmp_path)
    closed = []
    real_close = dispatcher_mod.close_cycle
    monkeypatch.setattr(
        dispatcher_mod, "close_cycle",
        lambda ctx, completed: (closed.append(completed), real_close(ctx, completed))[1],
    )

    with caplog.at_level("INFO", logger=dispatcher_mod.logger.name):
        outcome, roles = _splitting_cycle(cfg, monkeypatch, _split_payload())

    assert outcome is dispatcher_mod.CycleOutcome.SPLIT
    assert roles == ["arquitecto"]
    parent = _task(cfg)
    # Not a block: the status is the `epic` that `file_split` wrote.
    assert (parent.status, parent.owner) == ("epic", None)
    assert parent.split_into == ["task-1-S1", "task-1-S2"]
    first, second = _task(cfg, "task-1-S1"), _task(cfg, "task-1-S2")
    assert (first.status, first.epic, first.depends_on) == ("pending", "task-1", [])
    assert (second.status, second.epic, second.depends_on) == ("pending", "task-1", ["task-1-S1"])
    assert "Part of epic task-1." in tasks.read_description(cfg.hive_tasks_dir, "task-1-S1")
    assert "task task-1 is now an epic: task-1-S1, task-1-S2; run them in that order" in caplog.text
    # The cleanup of a block, not of a finished task.
    assert closed == [False]


def test_a_blocked_arquitecto_that_also_split_is_blocked_and_files_nothing(
    tmp_path, monkeypatch,
) -> None:
    """A block wins: it names a decision nobody made, and a split filed over
    it would hand that hole to every sub-task."""
    cfg = _make_config(tmp_path)

    outcome, roles = _splitting_cycle(cfg, monkeypatch, _split_payload(status="blocked"))

    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    assert roles == ["arquitecto"]
    assert list_task_ids(cfg.hive_tasks_dir) == ["task-1"]
    assert (_task(cfg).status, _task(cfg).split_into) == ("blocked", [])


@pytest.mark.parametrize(
    "subtasks,reason",
    [
        (_SPLIT[:1], "a split has 2 to 4 sub-tasks, this one has 1"),
        (
            [_SPLIT[0], handoff.SubTask("The screen", "front/", "## Goal\n\nA screen.\n", (1,))],
            "sub-task 2: its description",
        ),
    ],
    ids=["split_problems", "SplitError"],
)
def test_a_split_that_cannot_be_filed_blocks_before_the_implementador(
    tmp_path, monkeypatch, caplog, subtasks, reason,
) -> None:
    cfg = _make_config(tmp_path)

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        outcome, roles = _splitting_cycle(cfg, monkeypatch, _split_payload(subtasks))

    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    assert roles == ["arquitecto"]
    assert list_task_ids(cfg.hive_tasks_dir) == ["task-1"]
    assert tasks.read_description(cfg.hive_tasks_dir, "task-1-S1") is None
    assert (_task(cfg).status, _task(cfg).split_into) == ("blocked", [])
    assert "task task-1: the split cannot be filed: " in caplog.text
    assert reason in caplog.text


def test_a_sub_task_that_splits_again_is_blocked_and_files_nothing(
    tmp_path, monkeypatch, caplog,
) -> None:
    cfg = _make_config(tmp_path)
    _epic(cfg)

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        outcome, roles = _splitting_cycle(
            cfg, monkeypatch, _split_payload(), task_id="task-0-S1", description=None
        )

    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    assert roles == ["arquitecto"]
    assert sorted(list_task_ids(cfg.hive_tasks_dir)) == ["task-0", "task-0-S1", "task-0-S2"]
    assert _task(cfg, "task-0-S1").status == "blocked"
    assert "a task inside an epic cannot become an epic" in caplog.text


def test_a_cycle_with_an_empty_split_goes_on_to_the_implementador(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)

    outcome, roles = _splitting_cycle(cfg, monkeypatch, {"status": "complete", "split": []})

    assert outcome is dispatcher_mod.CycleOutcome.FINISHED
    assert roles == ["arquitecto", "implementador", "revisor", "auditor"]


@pytest.mark.parametrize("status", ["done", "epic"])
def test_file_split_refuses_a_parent_that_is_done_or_an_epic(tmp_path, caplog, status) -> None:
    cfg = _make_config(tmp_path)
    _with_status(cfg, status)
    before = _hive_bytes(cfg)

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        outcome = dispatcher_mod._file_split(_split_ctx(cfg), _SPLIT)

    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    assert _hive_bytes(cfg) == before
    assert (
        f"task task-1 is {status}; only a pending, in_progress or blocked task can be split"
        in caplog.text
    )


def test_file_split_refuses_a_parent_a_live_run_still_holds(tmp_path, caplog) -> None:
    """That run's next handoff would write `pending` over the `epic`."""
    cfg = _make_config(tmp_path)
    tasks.handoff(cfg.hive_tasks_dir, "task-1", new_status="pending", body="## arquitecto")
    acquire_lock(cfg.hive_tasks_dir, "task-1", "cuenta2")
    before = _hive_bytes(cfg)

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        outcome = dispatcher_mod._file_split(_split_ctx(cfg), _SPLIT)

    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    assert _hive_bytes(cfg) == before
    assert "task task-1 is locked by cuenta2" in caplog.text


@pytest.mark.parametrize("status", ["pending", "blocked", "in_progress"])
def test_file_split_files_a_parent_no_run_holds(tmp_path, status) -> None:
    """`in_progress` with no heartbeat is what a reaped lock leaves: the run
    that died before filing, which `file-split` exists to recover."""
    cfg = _make_config(tmp_path)
    _with_status(cfg, status)

    outcome = dispatcher_mod._file_split(_split_ctx(cfg), _SPLIT)

    assert outcome is dispatcher_mod.CycleOutcome.SPLIT
    assert (_task(cfg).status, _task(cfg).split_into) == ("epic", ["task-1-S1", "task-1-S2"])


def test_file_split_on_a_task_with_no_file_is_blocked_and_creates_none(tmp_path, caplog) -> None:
    cfg = _make_config(tmp_path)

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        outcome = dispatcher_mod._file_split(_split_ctx(cfg), _SPLIT)

    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    assert list_task_ids(cfg.hive_tasks_dir) == []
    assert "task task-1 has no task file" in caplog.text


def test_run_task_cycle_does_not_dispatch_an_epic(tmp_path, monkeypatch, caplog) -> None:
    """Refused in `open_cycle`, before an account or the lock, so T-030's mark
    cannot turn the `epic` into `blocked`."""
    cfg = _make_config(tmp_path)
    _epic(cfg)
    before = _hive_bytes(cfg)
    picked = []
    monkeypatch.setattr(
        dispatcher_mod, "pick_idle_account", lambda *args, **kwargs: picked.append(args)
    )

    with caplog.at_level("ERROR", logger=dispatcher_mod.logger.name):
        outcome, roles = _splitting_cycle(cfg, monkeypatch, _split_payload(), task_id="task-0")

    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    assert roles == [] and picked == []
    assert _hive_bytes(cfg) == before
    assert _task(cfg, "task-0").status == "epic"
    assert (
        "task task-0 is an epic, split into task-0-S1, task-0-S2: run its sub-tasks, not the epic"
        in caplog.text
    )


def test_open_cycle_names_an_epic_with_no_sub_tasks_as_such(tmp_path, caplog) -> None:
    cfg = _make_config(tmp_path)
    _with_status(cfg, "epic")
    tasks.write_description(cfg.hive_tasks_dir, "task-1", _DESCRIPTION)

    logged = _refused_by_open_cycle(cfg, caplog)

    assert "task task-1 is an epic, split into no sub-tasks" in logged


def test_a_single_phase_on_an_epic_is_refused_and_keeps_it_an_epic(tmp_path, monkeypatch) -> None:
    """`tasks.handoff` writes `pending` over any status, so `run-phase` must
    not reach it."""
    cfg = _make_config(tmp_path)
    _epic(cfg)
    before = _hive_bytes(cfg)
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _recording_dispatch_phase(calls))

    result = dispatcher_mod.run_single_phase(
        cfg, "task-0", "myproj", "arquitecto", description=_DESCRIPTION
    )

    assert result is None and calls == []
    assert _hive_bytes(cfg) == before


def test_a_single_arquitecto_phase_that_split_files_nothing(tmp_path, monkeypatch) -> None:
    """`run-phase` proposes; `file-split` files."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase",
        _recording_dispatch_phase([], handoffs={"arquitecto": _split_payload()}),
    )

    result = dispatcher_mod.run_single_phase(
        cfg, "task-1", "myproj", "arquitecto", description=_DESCRIPTION
    )

    assert handoff.split_of(result.handoff) == _SPLIT
    assert list_task_ids(cfg.hive_tasks_dir) == ["task-1"]
    assert (_task(cfg).status, _task(cfg).split_into) == ("pending", [])


def _saved_split(cfg, payload) -> None:
    """A task whose arquitecto phase landed with `payload`, as `run_phase` leaves it."""
    tasks.write_description(cfg.hive_tasks_dir, "task-1", _DESCRIPTION)
    tasks.handoff(cfg.hive_tasks_dir, "task-1", new_status="pending", body="## arquitecto")
    save_handoff(cfg.hive_tasks_dir, "task-1", "arquitecto", payload)


def test_file_saved_split_files_the_split_a_phase_proposed_and_only_once(
    tmp_path, monkeypatch, caplog,
) -> None:
    cfg = _make_config(tmp_path)
    _saved_split(cfg, _split_payload())

    def no_model(*args, **kwargs):
        raise AssertionError("file-split runs no model")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", no_model)

    assert dispatcher_mod.file_saved_split(cfg, "task-1", "myproj") is dispatcher_mod.CycleOutcome.SPLIT
    assert (_task(cfg).status, _task(cfg).split_into) == ("epic", ["task-1-S1", "task-1-S2"])
    assert _task(cfg, "task-1-S2").depends_on == ["task-1-S1"]

    # The parent is an epic now, so filing again is refused, not repeated.
    before = _hive_bytes(cfg)
    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        again = dispatcher_mod.file_saved_split(cfg, "task-1", "myproj")
    assert again is dispatcher_mod.CycleOutcome.BLOCKED
    assert _hive_bytes(cfg) == before
    assert "task task-1 is epic" in caplog.text


@pytest.mark.parametrize(
    "payload,reason",
    [
        (None, "no saved arquitecto handoff at "),
        (_split_payload(status="blocked"), "its arquitecto blocked, and a block wins over a split"),
        ({"status": "complete", "split": []}, "its arquitecto handoff has no split"),
        (_split_payload(_SPLIT[:1]), "the split cannot be filed: a split has 2 to 4 sub-tasks"),
    ],
    ids=["missing", "blocked", "empty", "invalid"],
)
def test_file_saved_split_refuses_what_it_cannot_file(tmp_path, caplog, payload, reason) -> None:
    cfg = _make_config(tmp_path)
    _saved_split(cfg, {"status": "complete"})
    if payload is None:
        os.remove(dispatcher_mod.context_transfer.handoff_path(cfg.hive_tasks_dir, "task-1", "arquitecto"))
    else:
        save_handoff(cfg.hive_tasks_dir, "task-1", "arquitecto", payload)
    before = _hive_bytes(cfg)

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        outcome = dispatcher_mod.file_saved_split(cfg, "task-1", "myproj")

    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    assert _hive_bytes(cfg) == before
    assert _task(cfg).status == "pending"  # only a cycle marks a task blocked
    assert reason in caplog.text


_AUTH_EXPIRED_TEXT = "Failed to authenticate: OAuth session expired and could not be refreshed"


def test_is_auth_error_true_on_the_cli_message_seen_on_t055() -> None:
    result = ClaudeResult(session_id=None, result_text=_AUTH_EXPIRED_TEXT, raw={"is_error": True})
    assert dispatcher_mod.is_auth_error(result) is True


def test_is_auth_error_true_on_401_status_with_unrelated_text() -> None:
    result = ClaudeResult(session_id=None, result_text="something went wrong", raw={"is_error": True, "api_error_status": 401})
    assert dispatcher_mod.is_auth_error(result) is True


def test_is_auth_error_false_when_not_an_error() -> None:
    result = ClaudeResult(session_id=None, result_text=_AUTH_EXPIRED_TEXT, raw={"is_error": False})
    assert dispatcher_mod.is_auth_error(result) is False


@pytest.mark.parametrize(
    "result",
    [
        ClaudeResult(session_id=None, result_text="usage limit reached", raw={"is_error": True, "api_error_status": 429}),
        ClaudeResult(session_id=None, result_text="Context limit reached", raw={"is_error": True}),
        ClaudeResult(session_id=None, result_text="forbidden", raw={"is_error": True, "api_error_status": 403}),
    ],
)
def test_is_auth_error_false_on_a_refusal_that_is_not_about_the_login(result) -> None:
    assert dispatcher_mod.is_auth_error(result) is False


def test_check_quota_ok_parks_an_account_whose_login_has_expired(tmp_path, monkeypatch, caplog) -> None:
    """Before this, the probe's error text fell into the unreadable-report
    fail-open and the account was waved through to a phase it could not run
    (T-055, 2026-10-15)."""
    cfg = _make_config(tmp_path)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        return ClaudeResult(session_id=None, result_text=_AUTH_EXPIRED_TEXT, raw={"is_error": True})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        ok = dispatcher_mod.check_quota_ok(cfg, "cuenta1")

    assert ok is False
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.AUTH_EXPIRED
    # Not a rate limit: nothing about the quota was learned.
    assert get_rate_limited_at(cfg.state_dir, "cuenta1") is None
    assert "docker exec -it agent-cuenta1 claude" in caplog.text


def test_dispatch_phase_hands_a_phase_refused_on_login_to_the_next_account(tmp_path, monkeypatch) -> None:
    cfg = _make_config(
        tmp_path,
        accounts=[
            AccountConfig(name="cuenta1", container="agent-cuenta1"),
            AccountConfig(name="cuenta2", container="agent-cuenta2"),
        ],
    )
    resumed = []

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        if "usage" in prompt.lower():
            return ClaudeResult(session_id=None, result_text=_USAGE_TEXT, raw={})
        if container == "agent-cuenta1":
            return ClaudeResult(session_id="sess-a", result_text=_AUTH_EXPIRED_TEXT, raw={"is_error": True})
        resumed.append(resume_session_id)
        return ClaudeResult(session_id="sess-a", result_text="phase done", raw={"is_error": False})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    assert result.success is True
    assert result.account == "cuenta2"
    assert resumed == ["sess-a"]
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.AUTH_EXPIRED
    assert get_state(cfg.state_dir, "cuenta2") == AccountState.IDLE


def test_an_expired_login_is_never_re_probed_back_into_the_pool(tmp_path, monkeypatch) -> None:
    """/usage reads local counters and would say the account is healthy; only
    the operator's re-login and `release` bring it back."""
    cfg = _make_config(tmp_path)
    set_state(cfg.state_dir, "cuenta1", AccountState.AUTH_EXPIRED)

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        raise AssertionError("an AUTH_EXPIRED account should not be probed")

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)

    assert dispatcher_mod._recheck_cooling_accounts(cfg) == []
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.AUTH_EXPIRED
    assert dispatcher_mod.pick_idle_account(cfg) is None


def test_an_expired_worker_does_not_hold_the_primary_back(tmp_path) -> None:
    """A worker on a counter comes back by itself and is worth waiting for; a
    worker on an expired login does not, so the fallback goes ahead."""
    cfg = _make_config(
        tmp_path,
        accounts=[
            AccountConfig(name="cuenta1", container="agent-cuenta1", is_primary=True),
            AccountConfig(name="cuenta2", container="agent-cuenta2"),
        ],
        primary_account="cuenta1",
    )
    set_state(cfg.state_dir, "cuenta2", AccountState.AUTH_EXPIRED)

    assert dispatcher_mod.pick_idle_account(cfg, role="revisor") == "cuenta1"


# --- the automatic approve (docs/plans/autonomy.md A-3, ADR 77) -------------

_AA_DESCRIPTION = (
    f"## Goal\n\n{_GOAL}\n\n## Surface\n\n`src/app.py` and `tests/`.\n\n"
    "## Done when\n\nGET /healthz answers 200.\n"
)


def _aa_dispatch_phase(
    *, gate_report=gates.Report([]), revisor=None, auditor=None,
):
    """Every phase works; the revisor and the auditor return structured handoffs."""
    revisor = {"status": "complete", "verdict": "APPROVED"} if revisor is None else revisor
    auditor = {"status": "complete"} if auditor is None else auditor

    def fake(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None,
             effort=None, round_num=None, **kwargs):
        payload = {"revisor": revisor, "auditor": auditor}.get(role)
        return dispatcher_mod.DispatchResult(
            success=True,
            session_id=None,
            result_text="VERDICT: APPROVED" if role == "revisor" else "ok",
            account="cuenta1",
            handoff=payload,
            gates=gate_report if role == "implementador" else None,
        )

    return fake


@pytest.fixture
def aa_git(monkeypatch, fake_git):
    """`fake_git`, plus the two reads only the automatic approve makes."""
    fake_git.changed = ["src/app.py", "tests/test_app.py", "docs/decisions.md"]
    fake_git.head = "c" * 40

    def branch_changed_paths(container, projects_root, slug, task_id):
        return None if fake_git.changed is None else list(fake_git.changed)

    monkeypatch.setattr(dispatcher_mod.docker_exec, "branch_changed_paths", branch_changed_paths)
    monkeypatch.setattr(dispatcher_mod.docker_exec, "head_commit", lambda container, path: fake_git.head)
    return fake_git


def _aa_config(tmp_path, **overrides):
    return _make_config(tmp_path, auto_approve={"myproj": True}, **overrides)


def _aa_run(cfg, monkeypatch, *, description=_AA_DESCRIPTION, authorized=True, **phase):
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _aa_dispatch_phase(**phase))
    return dispatcher_mod.run_task_cycle(
        cfg, "task-1", "myproj", description=description, authorized=authorized,
    )


def test_auto_approve_merges_when_every_condition_holds(tmp_path, monkeypatch, aa_git) -> None:
    cfg = _aa_config(tmp_path)

    outcome = _aa_run(cfg, monkeypatch)

    assert outcome is dispatcher_mod.CycleOutcome.FINISHED
    assert aa_git.merges == [("agent-cuenta1", cfg.projects_root, "myproj", "task-1")]
    task = _task(cfg)
    assert task.status == "done"
    assert task.merged["into"] == "main"
    assert dispatcher_mod.auto_approve.HEADING in task.body
    assert "Approved and merged into main at " + "c" * 40 in task.body
    for name in dispatcher_mod.auto_approve.CONDITIONS:
        assert f"`{name}`" in task.body


def _assert_refused_on(cfg, fake_git, condition) -> None:
    assert fake_git.merges == []
    task = _task(cfg)
    assert task.merged is None
    assert dispatcher_mod.auto_approve.HEADING in task.body
    assert "Not merged: 1 condition(s) failed" in task.body
    assert f"- `{condition}`:" in task.body


@pytest.mark.parametrize(
    ("condition", "phase"),
    [
        ("gates", dict(gate_report=None)),
        ("gates", dict(gate_report=gates.Report([gates.Finding(gates.TESTS_IN_DIFF, "no test", gates.ASK)]))),
        ("revisor", dict(revisor={"status": "complete", "verdict": "APPROVED",
                                  "findings": ["the endpoint has no timeout"]})),
        ("auditor", dict(auditor={"status": "partial"})),
        ("auditor", dict(auditor={"status": "complete", "pending": ["update the README"]})),
        ("auditor", dict(auditor={"status": "complete", "debt": [{"what": "the index is stale"}]})),
    ],
)
def test_auto_approve_refuses_on_a_phase_condition_alone(
    tmp_path, monkeypatch, aa_git, condition, phase,
) -> None:
    cfg = _aa_config(tmp_path)

    outcome = _aa_run(cfg, monkeypatch, **phase)

    assert outcome is dispatcher_mod.CycleOutcome.FINISHED
    _assert_refused_on(cfg, aa_git, condition)
    assert _task(cfg).status == "done"


def test_auto_approve_refuses_when_debt_was_filed_this_cycle(tmp_path, monkeypatch, aa_git) -> None:
    cfg = _aa_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod, "_file_accepted_debt",
        lambda *args, **kwargs: [("D-0007", debt.Declaration(what="cache the lookup"))],
    )

    _aa_run(cfg, monkeypatch)

    _assert_refused_on(cfg, aa_git, "auditor")
    assert "D-0007" in _task(cfg).body


def test_auto_approve_refuses_a_branch_that_conflicts_with_its_base(tmp_path, monkeypatch, aa_git) -> None:
    cfg = _aa_config(tmp_path)
    aa_git.premerge = _premerge_outcome(
        dispatcher_mod.docker_exec.PREMERGE_CONFLICT, conflicts=("src/app.py",),
    )

    _aa_run(cfg, monkeypatch)

    _assert_refused_on(cfg, aa_git, "pre-merge")
    # The pre-merge check still moves a conflicting task to `conflict`.
    assert _task(cfg).status == dispatcher_mod.CONFLICT_STATUS


@pytest.mark.parametrize(
    ("condition", "changed"),
    [
        ("surface", ["src/app.py", "src/other.py"]),
        ("protected", ["src/app.py", "tests/.env"]),
    ],
)
def test_auto_approve_refuses_on_the_changed_paths_alone(
    tmp_path, monkeypatch, aa_git, condition, changed,
) -> None:
    cfg = _aa_config(tmp_path)
    aa_git.changed = changed

    _aa_run(cfg, monkeypatch)

    _assert_refused_on(cfg, aa_git, condition)


def test_auto_approve_refuses_a_path_the_project_protects(tmp_path, monkeypatch, aa_git) -> None:
    cfg = _aa_config(tmp_path, protected_paths={"myproj": ["tests/fixtures/"]})
    aa_git.changed = ["src/app.py", "tests/fixtures/users.json"]

    _aa_run(cfg, monkeypatch)

    _assert_refused_on(cfg, aa_git, "protected")


def test_auto_approve_refuses_a_task_with_no_surface(tmp_path, monkeypatch, aa_git) -> None:
    """No Surface section is the whole repo, which is never approved by itself."""
    cfg = _aa_config(tmp_path)

    _aa_run(cfg, monkeypatch, description=_DESCRIPTION)

    _assert_refused_on(cfg, aa_git, "surface")


def test_auto_approve_refuses_when_the_changed_paths_cannot_be_read(tmp_path, monkeypatch, aa_git) -> None:
    cfg = _aa_config(tmp_path)
    aa_git.changed = None

    _aa_run(cfg, monkeypatch)

    assert aa_git.merges == []
    body = _task(cfg).body
    assert "Not merged: 2 condition(s) failed" in body
    assert "- `surface`: the changed paths could not be read" in body
    assert "- `protected`: the changed paths could not be read" in body


def test_auto_approve_refuses_a_cycle_nobody_authorized(tmp_path, monkeypatch, aa_git) -> None:
    cfg = _aa_config(tmp_path)

    _aa_run(cfg, monkeypatch, authorized=False)

    _assert_refused_on(cfg, aa_git, "authorized")


def test_auto_approve_wins_over_merge_on_done(tmp_path, monkeypatch, aa_git) -> None:
    """ADR 77: a project with both on merges only what the check allows."""
    cfg = _aa_config(tmp_path, merge_on_done=True)
    aa_git.changed = ["src/app.py", "tests/.env.test"]

    _aa_run(cfg, monkeypatch)

    _assert_refused_on(cfg, aa_git, "protected")


def test_auto_approve_records_a_merge_that_did_not_land(tmp_path, monkeypatch, aa_git) -> None:
    cfg = _aa_config(tmp_path)
    aa_git.outcome = dispatcher_mod.docker_exec.MergeOutcome(
        dispatcher_mod.docker_exec.REFUSED, "main", "the project has uncommitted changes",
    )

    _aa_run(cfg, monkeypatch)

    task = _task(cfg)
    assert task.merged is None
    assert task.status == "done"
    assert "but the merge did not land: the project has uncommitted changes" in task.body


def test_a_project_without_auto_approve_merges_exactly_as_before(tmp_path, monkeypatch, aa_git) -> None:
    """Off for the project: `merge_on_done` alone decides, with no check and no note."""
    cfg = _make_config(tmp_path, merge_on_done=True, auto_approve={"other": True})
    aa_git.changed = ["config.yaml"]

    _aa_run(cfg, monkeypatch, description=_DESCRIPTION, authorized=False, gate_report=None)

    assert aa_git.merges == [("agent-cuenta1", cfg.projects_root, "myproj", "task-1")]
    task = _task(cfg)
    assert task.merged["into"] == "main"
    assert dispatcher_mod.auto_approve.HEADING not in task.body


def test_a_project_without_auto_approve_and_merge_on_done_off_never_merges(
    tmp_path, monkeypatch, aa_git,
) -> None:
    cfg = _make_config(tmp_path)

    _aa_run(cfg, monkeypatch)

    assert aa_git.merges == []
    assert dispatcher_mod.auto_approve.HEADING not in _task(cfg).body


# --- the base push after an automatic approve (A-4, ADR 78) ---


def test_auto_approve_pushes_the_base_after_the_merge(tmp_path, monkeypatch, aa_git) -> None:
    cfg = _aa_config(tmp_path)

    _aa_run(cfg, monkeypatch)

    assert aa_git.merges == [("agent-cuenta1", cfg.projects_root, "myproj", "task-1")]
    assert aa_git.pushes == [("agent-cuenta1", cfg.projects_root, "myproj")]
    task = _task(cfg)
    assert task.status == "done"
    assert "Pushed: pushed main to origin/main at ccccccc." in task.body


def test_a_refused_push_leaves_the_task_done_and_says_why(tmp_path, monkeypatch, aa_git) -> None:
    de = dispatcher_mod.docker_exec
    cfg = _aa_config(tmp_path)
    aa_git.push_outcome = de.PushOutcome(
        de.PUSH_REFUSED, "main",
        "pushing main to origin/main was refused; main keeps the merge locally and is not reset: rejected",
    )

    outcome = _aa_run(cfg, monkeypatch)

    assert outcome is dispatcher_mod.CycleOutcome.FINISHED
    task = _task(cfg)
    assert task.status == "done"
    assert task.merged["into"] == "main"
    assert "The push did not land: pushing main to origin/main was refused" in task.body
    assert "nothing was reset" in task.body


def test_on_approve_is_handed_the_refused_push(tmp_path, monkeypatch, aa_git) -> None:
    """The queue (A-5) stops on this; the outcome alone cannot say it."""
    de = dispatcher_mod.docker_exec
    cfg = _aa_config(tmp_path)
    aa_git.push_outcome = de.PushOutcome(de.PUSH_REFUSED, "main", "rejected")
    seen = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _aa_dispatch_phase())

    outcome = dispatcher_mod.run_task_cycle(
        cfg, "task-1", "myproj", description=_AA_DESCRIPTION, on_approve=seen.append,
    )

    assert outcome is dispatcher_mod.CycleOutcome.FINISHED
    assert len(seen) == 1 and seen[0].merged and seen[0].push.refused


def test_may_start_phase_false_holds_the_cycle_before_the_phase(tmp_path, monkeypatch, fake_git) -> None:
    """The queue's limit (ADR 79): no new phase starts, nothing is marked blocked."""
    cfg = _make_config(tmp_path)
    started = []

    def fake(cfg_arg, task_id, slug, role, prompt, **kwargs):
        started.append(role)
        return dispatcher_mod.DispatchResult(
            success=True, session_id=None, result_text="ok", account="cuenta1",
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake)
    outcome = dispatcher_mod.run_task_cycle(
        cfg, "task-1", "myproj", description=_DESCRIPTION,
        may_start_phase=lambda: not started,
    )

    assert outcome is dispatcher_mod.CycleOutcome.HELD
    assert started == ["arquitecto"]
    assert _task(cfg).status != "blocked"


def test_a_push_that_raises_is_a_refusal_not_a_crash(tmp_path, monkeypatch, aa_git) -> None:
    cfg = _aa_config(tmp_path)
    aa_git.push_error = RuntimeError("docker went away")

    _aa_run(cfg, monkeypatch)

    task = _task(cfg)
    assert task.status == "done"
    assert task.merged["into"] == "main"
    assert "The push did not land: the push could not run: docker went away" in task.body


def test_a_base_with_no_upstream_is_said_and_not_a_failure(tmp_path, monkeypatch, aa_git) -> None:
    de = dispatcher_mod.docker_exec
    cfg = _aa_config(tmp_path)
    aa_git.push_outcome = de.PushOutcome(
        de.NO_UPSTREAM, "main", "main tracks no upstream branch; nothing pushed",
    )

    _aa_run(cfg, monkeypatch)

    body = _task(cfg).body
    assert "Not pushed: main tracks no upstream branch; nothing pushed." in body
    assert "did not land" not in body


def test_auto_approve_does_not_push_a_merge_that_did_not_land(tmp_path, monkeypatch, aa_git) -> None:
    cfg = _aa_config(tmp_path)
    aa_git.outcome = dispatcher_mod.docker_exec.MergeOutcome(
        dispatcher_mod.docker_exec.REFUSED, "main", "the project has uncommitted changes",
    )

    _aa_run(cfg, monkeypatch)

    assert getattr(aa_git, "pushes", []) == []


def test_auto_approve_refused_by_a_condition_never_pushes(tmp_path, monkeypatch, aa_git) -> None:
    cfg = _aa_config(tmp_path)
    aa_git.changed = ["config.yaml"]

    _aa_run(cfg, monkeypatch)

    assert aa_git.merges == []
    assert getattr(aa_git, "pushes", []) == []


def test_merge_on_done_merges_and_never_pushes(tmp_path, monkeypatch, fake_git) -> None:
    """ADR 78: only an approve pushes; `merge_on_done` stays a local merge."""
    cfg = _make_config(tmp_path, merge_on_done=True)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert fake_git.merges == [("agent-cuenta1", cfg.projects_root, "myproj", "task-1")]
    assert getattr(fake_git, "pushes", []) == []


# --- the base lock around every base operation (B-1, ADR 80) -----------------


def _base_lock_record(cfg, slug="myproj"):
    """The project's base lock as it stands, None when nobody holds it."""
    path = dispatcher_mod.tasks.base_lock_path(cfg.state_dir, slug)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _hold_the_base(cfg, slug="myproj"):
    """Another live holder takes the project's base lock, and nobody waits."""
    dispatcher_mod.tasks.acquire_base_lock(
        cfg.state_dir, slug, "elsewhere:1:feedface", "merge", ttl_seconds=cfg.heartbeat_ttl_seconds,
    )


def _recording_under_the_lock(cfg, seen, name, real):
    """Wrap `real` so each call records the base lock held while it ran."""

    def wrapped(*args, **kwargs):
        seen.append((name, _base_lock_record(cfg)))
        return real(*args, **kwargs)

    return wrapped


def test_update_project_branch_holds_the_base_lock_for_the_update(
    tmp_path, monkeypatch, fake_git,
) -> None:
    cfg = _make_config(tmp_path, update_project_before_task=True)
    seen = []
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "update_project_branch",
        _recording_under_the_lock(
            cfg, seen, "update", lambda *a, **kw: _update_outcome(dispatcher_mod.docker_exec.UNCHANGED),
        ),
    )
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "read_owner",
        _recording_under_the_lock(cfg, seen, "read_owner", fake_git.read_owner),
    )
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "restore_owner",
        _recording_under_the_lock(cfg, seen, "restore_owner", fake_git.restore_owner),
    )

    dispatcher_mod.update_project_branch(cfg, "myproj")

    # The owner is read and restored inside the same window as the update.
    assert [name for name, _ in seen] == ["read_owner", "update", "restore_owner"]
    assert all(record is not None and record["operation"] == "update" for _, record in seen)
    assert len({record["owner"] for _, record in seen}) == 1
    assert _base_lock_record(cfg) is None


def test_update_project_branch_lets_go_of_the_lock_when_the_update_raises(
    tmp_path, monkeypatch, fake_git,
) -> None:
    cfg = _make_config(tmp_path, update_project_before_task=True)

    def boom(*args, **kwargs):
        raise RuntimeError("docker exec died")

    monkeypatch.setattr(dispatcher_mod.docker_exec, "update_project_branch", boom)

    with pytest.raises(RuntimeError):
        dispatcher_mod.update_project_branch(cfg, "myproj")

    assert _base_lock_record(cfg) is None


def test_run_task_cycle_blocks_when_the_base_is_locked(tmp_path, monkeypatch, caplog) -> None:
    """A task cut from a base another operation is moving starts on ground
    nobody can name; refused after the wait, it stops before any phase runs."""
    cfg = _make_config(tmp_path, update_project_before_task=True)
    monkeypatch.setattr(dispatcher_mod, "BASE_LOCK_WAIT_SECONDS", 0)
    _hold_the_base(cfg)

    def never(*args, **kwargs):
        raise AssertionError("the update ran under another holder's lock")

    monkeypatch.setattr(dispatcher_mod.docker_exec, "update_project_branch", never)
    roles = []

    def fake_dispatch_phase(*args, **kwargs):
        roles.append(kwargs.get("role") or args[3])
        return _approving_dispatch_phase(*args, **kwargs)

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    with caplog.at_level("WARNING"):
        outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome is dispatcher_mod.CycleOutcome.BLOCKED
    assert roles == []
    assert "the base is locked by elsewhere:1:feedface" in caplog.text
    # The other holder's lock is left as it was.
    assert _base_lock_record(cfg)["owner"] == "elsewhere:1:feedface"


def test_dispatch_phase_holds_the_base_lock_for_the_mapping_commit(
    tmp_path, monkeypatch, fake_git,
) -> None:
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "exec_claude",
        _phase_exec(ClaudeResult(session_id="sess-1", result_text="mapped", raw={"is_error": False})),
    )
    _fake_worktree(monkeypatch)
    seen = []
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "commit_worktree",
        _recording_under_the_lock(cfg, seen, "commit", fake_git.commit_worktree),
    )

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", project_docs.MAPPER_ROLE, "map it")

    assert [name for name, _ in seen] == ["commit"]
    assert seen[0][1]["operation"] == "mapping commit"
    assert _base_lock_record(cfg) is None


def test_the_mapping_phase_moves_the_staged_skill_under_the_lock_before_its_commit(
    tmp_path, monkeypatch, fake_git,
) -> None:
    """A phase cannot write under .claude/, so the skill is staged and moved
    by the dispatcher, inside the mapping commit's lock (ADR 115)."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "exec_claude",
        _phase_exec(ClaudeResult(session_id="sess-1", result_text="mapped", raw={"is_error": False})),
    )
    _fake_worktree(monkeypatch)
    seen = []
    monkeypatch.setattr(
        dispatcher_mod.project_docs, "move_staged_run_skills",
        _recording_under_the_lock(cfg, seen, "move", fake_git.move_staged_run_skills),
    )
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "commit_worktree",
        _recording_under_the_lock(cfg, seen, "commit", fake_git.commit_worktree),
    )

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", project_docs.MAPPER_ROLE, "map it")

    assert [name for name, _ in seen] == ["move", "commit"]
    assert all(lock["operation"] == "mapping commit" for _, lock in seen)
    assert len(fake_git.staged_moves) == 1


@pytest.mark.parametrize("role", ["arquitecto", "implementador", "auditor", "revisor"])
def test_no_other_phase_moves_a_staged_skill(tmp_path, monkeypatch, fake_git, role) -> None:
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "exec_claude",
        _phase_exec(ClaudeResult(session_id="sess-1", result_text="done", raw={"is_error": False})),
    )
    _fake_worktree(monkeypatch)

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", role, "do it")

    assert getattr(fake_git, "staged_moves", []) == []


def test_only_the_cartografo_gets_mapping_allowed_tools(tmp_path) -> None:
    cfg = _make_config(
        tmp_path, allowed_tools=["Bash(git status*)"], mapping_allowed_tools=["Bash(make*)"],
    )

    flags = dispatcher_mod._phase_permission_flags(cfg, project_docs.MAPPER_ROLE)

    assert flags["allowed_tools"] == ["Bash(git status*)", "Bash(make*)"]
    assert flags["permission_mode"] == "acceptEdits"
    assert cfg.allowed_tools == ["Bash(git status*)"]


@pytest.mark.parametrize(
    "role",
    sorted(PHASE_ROLES - {project_docs.MAPPER_ROLE}),
)
def test_every_other_role_keeps_exactly_allowed_tools(tmp_path, role) -> None:
    cfg = _make_config(
        tmp_path, allowed_tools=["Bash(git status*)"], mapping_allowed_tools=["Bash(make*)"],
    )
    plain = _make_config(tmp_path, allowed_tools=["Bash(git status*)"])

    flags = dispatcher_mod._phase_permission_flags(cfg, role)

    assert flags == dispatcher_mod._phase_permission_flags(plain, role)
    # The board's own names follow for a role that has them (ADR 128); that
    # grant is the board service's table, not mapping_allowed_tools.
    assert flags["allowed_tools"] == ["Bash(git status*)", *board_mcp.allowed_tools(role)]


def test_dispatch_phase_takes_no_base_lock_for_a_task_branch_commit(
    tmp_path, monkeypatch, fake_git,
) -> None:
    """The writing phases commit on the task's own branch, not the base."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "exec_claude",
        _phase_exec(ClaudeResult(session_id="sess-1", result_text="done", raw={"is_error": False})),
    )
    _fake_worktree(monkeypatch)
    seen = []
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "commit_worktree",
        _recording_under_the_lock(cfg, seen, "commit", fake_git.commit_worktree),
    )

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "do it")

    assert seen == [("commit", None)]


def test_the_automatic_merge_holds_the_base_lock_from_the_check_to_the_merge(
    tmp_path, monkeypatch, fake_git,
) -> None:
    """The pre-merge verdict is about the base as it stood; let it move before
    the merge and the verdict is about a base that is gone."""
    cfg = _make_config(tmp_path, merge_on_done=True)
    seen = []
    for name in ("read_owner", "premerge_base", "merge_task_branch", "restore_owner"):
        monkeypatch.setattr(
            dispatcher_mod.docker_exec, name,
            _recording_under_the_lock(cfg, seen, name, getattr(fake_git, name)),
        )

    assert dispatcher_mod._merge_task_branch(cfg, "task-1", "myproj") is True

    assert [name for name, _ in seen] == ["read_owner", "premerge_base", "merge_task_branch", "restore_owner"]
    assert all(record is not None and record["operation"] == "merge" for _, record in seen)
    assert len({record["owner"] for _, record in seen}) == 1
    assert _base_lock_record(cfg) is None


def test_the_automatic_merge_is_refused_while_the_base_is_locked(
    tmp_path, monkeypatch, fake_git, caplog,
) -> None:
    cfg = _make_config(tmp_path, merge_on_done=True)
    monkeypatch.setattr(dispatcher_mod, "BASE_LOCK_WAIT_SECONDS", 0)
    _hold_the_base(cfg)

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        assert dispatcher_mod._merge_task_branch(cfg, "task-1", "myproj") is False

    assert fake_git.merges == []
    assert fake_git.restored == []
    assert "not merged" in caplog.text and "the base is locked" in caplog.text


def test_auto_approve_holds_one_base_lock_for_the_check_the_merge_and_the_push(
    tmp_path, monkeypatch, aa_git,
) -> None:
    cfg = _aa_config(tmp_path)
    seen = []
    for name in ("premerge_base", "merge_task_branch", "push_base"):
        monkeypatch.setattr(
            dispatcher_mod.docker_exec, name,
            _recording_under_the_lock(cfg, seen, name, getattr(aa_git, name)),
        )

    _aa_run(cfg, monkeypatch)

    assert [name for name, _ in seen] == ["premerge_base", "merge_task_branch", "push_base"]
    assert all(record is not None and record["operation"] == "merge" for _, record in seen)
    # One window, not three: nothing can move the base between them.
    assert len({record["owner"] for _, record in seen}) == 1
    assert _base_lock_record(cfg) is None


def test_auto_approve_is_refused_while_the_base_is_locked(tmp_path, monkeypatch, aa_git) -> None:
    cfg = _aa_config(tmp_path)
    monkeypatch.setattr(dispatcher_mod, "BASE_LOCK_WAIT_SECONDS", 0)
    _hold_the_base(cfg)

    outcome = _aa_run(cfg, monkeypatch)

    assert outcome is dispatcher_mod.CycleOutcome.FINISHED
    assert aa_git.merges == []
    assert getattr(aa_git, "pushes", []) == []
    task = _task(cfg)
    assert task.merged is None
    assert dispatcher_mod.auto_approve.HEADING in task.body
    assert "- `pre-merge`:" in task.body
    assert "the base is locked by elsewhere:1:feedface" in task.body
