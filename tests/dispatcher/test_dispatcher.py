import datetime as dt
import json
import os
import time
from pathlib import Path

import pytest

import dispatcher.dispatcher as dispatcher_mod
from dispatcher import debt, gates, handoff, learnings, project_docs, role_skills, subagents
from dispatcher.config import AccountConfig, Config
from dispatcher.context_transfer import (
    LockHeldError,
    acquire_lock,
    is_lock_expired,
    list_task_ids,
    read_handoff,
    read_resolved_debt,
    read_task_file,
    save_handoff,
    scratch_dir,
    set_kanban_issue_id,
    set_resolved_debt,
    task_file_path,
    write_task_file,
)
from dispatcher.docker_exec import ClaudeResult
from dispatcher.state_machine import (
    AccountState,
    get_busy_since,
    get_current_task,
    get_rate_limited_at,
    get_state,
    record_rate_limit,
    set_state,
)
from dispatcher.vibe_kanban_client import NullKanbanClient


# run_task_cycle refuses to dispatch a task with no description (it would
# spend four phases of quota on roles told nothing but an id), so every
# cycle test has to supply one.
_DESCRIPTION = "Add a /healthz endpoint that returns 200."


#: Vibe Kanban assigns every id itself, so a card's uuid is only ever
#: something the board answered with — never something the harness picked.
_ISSUE_ID = "0e1d2c3b-4a59-6878-9706-5a4b3c2d1e0f"


class _FakeKanban:
    """A board that answers, and remembers what the run put on it."""

    enabled = True

    def __init__(self, issue_id=_ISSUE_ID):
        self.issue_id = issue_id
        self.created = []
        self.statuses = []

    def create_issue(self, title, description=None):
        self.created.append((title, description))
        return self.issue_id

    def set_status(self, issue_id, status):
        self.statuses.append((issue_id, status))


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
        vibe_kanban=None,
        local_board=None,
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


@pytest.fixture(autouse=True)
def fake_git(monkeypatch):
    fake = _FakeGit()
    monkeypatch.setattr(dispatcher_mod.docker_exec, "commit_worktree", fake.commit_worktree)
    monkeypatch.setattr(dispatcher_mod.docker_exec, "dirty_paths", fake.dirty_paths)
    monkeypatch.setattr(dispatcher_mod.docker_exec, "read_owner", fake.read_owner)
    monkeypatch.setattr(dispatcher_mod.docker_exec, "restore_owner", fake.restore_owner)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "remove_review_worktrees", fake.remove_review_worktrees
    )
    monkeypatch.setattr(dispatcher_mod.docker_exec, "merge_task_branch", fake.merge_task_branch)
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
    assert captured["phase"]["append_system_prompt"] == role_skills.system_prompt("revisor")
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

    monkeypatch.setattr(dispatcher_mod.context_transfer, "refresh_heartbeat", flaky_refresh_heartbeat)

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

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert len(reap_calls) == 1
    assert (_ISSUE_ID, "blocked") in kanban.statuses


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

    kanban = _FakeKanban()
    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert worktree_calls == []
    assert (_ISSUE_ID, "blocked") in kanban.statuses
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

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    expected_path = task_file_path(cfg.hive_tasks_dir, "task-1")
    assert captured_prompts
    assert expected_path in captured_prompts[0]


def test_run_task_cycle_embeds_the_description_in_every_role_prompt(tmp_path, monkeypatch) -> None:
    """The roles used to get a role name, a task id and a file path — the
    arquitecto had nothing to plan from. The description goes in the prompt
    whole (never truncated: a cut ask misinforms) for every phase, not just
    the first, so a revisor or auditor can check the work against what was
    actually asked for."""
    cfg = _make_config(tmp_path, max_revision_rounds=1)
    captured = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        captured.append((role, prompt))
        return dispatcher_mod.DispatchResult(
            success=True, session_id=None, result_text="VERDICT: APPROVED", account="cuenta1",
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    description = "Add a /healthz endpoint.\n\nIt must return 200 and no body."
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=description)

    assert [role for role, _ in captured] == ["arquitecto", "implementador", "revisor", "auditor"]
    for role, prompt in captured:
        assert description in prompt, f"{role}'s prompt does not carry the description"

    # The revisor's verdict instruction has to stay the last thing it reads,
    # or a description ending in prose could bury it.
    revisor_prompt = next(prompt for role, prompt in captured if role == "revisor")
    assert revisor_prompt.rstrip().endswith("That field is what the dispatcher reads to decide.")


def test_run_task_cycle_seeds_the_description_into_the_task_file(tmp_path, monkeypatch) -> None:
    """Seeded once, then read from the file on a re-run: the roles open the
    task file for handoff context anyway, and a resume (`run-task` with no
    --description after a Ctrl+C) has to find the original ask there."""
    cfg = _make_config(tmp_path, max_revision_rounds=1)
    prompts = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        prompts.append(prompt)
        return dispatcher_mod.DispatchResult(
            success=True, session_id=None, result_text="VERDICT: APPROVED", account="cuenta1",
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert task.description == _DESCRIPTION
    # handoff() appended four phase summaries to the body; the description
    # must not be one of them, or a re-run would stack copies of the ask.
    assert _DESCRIPTION not in task.body

    prompts.clear()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban())

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

    kanban = _FakeKanban()
    with caplog.at_level("ERROR", logger=dispatcher_mod.logger.name):
        dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban)

    assert calls == []
    # Nothing reaches the board either: with no ask there is nothing to name a
    # card after, and a task that never dispatched has nothing to show.
    assert kanban.created == []
    assert kanban.statuses == []
    assert "description" in caplog.text


def test_run_task_cycle_blocks_when_the_stored_description_is_blank(tmp_path, monkeypatch) -> None:
    """A task file whose description is whitespace is as useless as none at
    all — the guard is on content, not on the key being present."""
    cfg = _make_config(tmp_path)
    write_task_file(
        task_file_path(cfg.hive_tasks_dir, "task-1"),
        dispatcher_mod.context_transfer.TaskFile(
            task_id="task-1", status="pending", owner=None, depends_on=[],
            heartbeat=None, body="", description="   \n",
        ),
    )
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", lambda *a, **kw: calls.append(a))

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban)

    assert calls == []
    assert kanban.created == []


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

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

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

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

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

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

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

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

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

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert roles == ["arquitecto", "implementador", "implementador"]
    assert (_ISSUE_ID, "blocked") in kanban.statuses


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

    kanban = _FakeKanban()
    with caplog.at_level("WARNING"):
        dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert roles == ["arquitecto"]
    assert (_ISSUE_ID, "blocked") in kanban.statuses
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

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert roles == ["arquitecto", "implementador", "revisor", "auditor"]
    assert (_ISSUE_ID, "done") in kanban.statuses


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

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert roles == ["arquitecto", "implementador", "revisor", "auditor"]
    assert (_ISSUE_ID, "done") in kanban.statuses


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

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert "auditor" not in roles
    assert (_ISSUE_ID, "blocked") in kanban.statuses


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

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

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

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

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

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    roles = [c["role"] for c in calls]
    assert roles == ["arquitecto", "implementador", "revisor", "auditor"]
    assert all(c["effort"] is None for c in calls)
    assert all(c["model"] == "opus" for c in calls)
    assert (_ISSUE_ID, "done") in kanban.statuses


def test_run_task_cycle_completes_when_kanban_status_updates_always_raise(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    calls = []

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None, effort=None, round_num=None, **kwargs):
        calls.append(role)
        if role == "revisor":
            return dispatcher_mod.DispatchResult(
                success=True, session_id=None, result_text="VERDICT: APPROVED", account="cuenta1",
            )
        return dispatcher_mod.DispatchResult(success=True, session_id=None, result_text="ok", account="cuenta1")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", fake_dispatch_phase)

    class _RaisingKanban:
        enabled = True

        def create_issue(self, title, description=None):
            return _ISSUE_ID

        def set_status(self, issue_id, status):
            raise RuntimeError("kanban is down")

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _RaisingKanban(), description=_DESCRIPTION)

    assert calls == ["arquitecto", "implementador", "revisor", "auditor"]
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert task.status == "done"


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

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    early_rounds = [
        c for c in calls
        if c["role"] in ("implementador", "revisor") and ("round 1" in c["prompt"] or "round 2" in c["prompt"])
    ]
    round3 = [c for c in calls if c["role"] in ("implementador", "revisor") and "round 3" in c["prompt"]]
    assert early_rounds and all(c["effort"] is None for c in early_rounds)
    assert round3, "expected round 3 to run"
    assert all(c["effort"] == "high" for c in round3)
    assert (_ISSUE_ID, "done") in kanban.statuses


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

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert calls.count("revisor") == 2
    assert "auditor" not in calls
    assert (_ISSUE_ID, "blocked") in kanban.statuses
    assert (_ISSUE_ID, "done") not in kanban.statuses


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
        dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

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
        dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

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
        dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

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
        dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

    assert any(
        "no diagnosis" in r.getMessage()
        for r in caplog.records if r.levelname == "ERROR"
    )


@pytest.mark.parametrize(
    "dispatch,status",
    [(_approving_dispatch_phase, "done"), (_rejecting_dispatch_phase, "blocked")],
    ids=["done", "blocked"],
)
def test_run_task_cycle_drops_the_review_worktrees_when_the_task_ends(
    tmp_path, monkeypatch, fake_git, dispatch, status,
) -> None:
    """Blocked is as terminal as done here: nobody is reading those checkouts."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", dispatch)

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert (_ISSUE_ID, status) in kanban.statuses
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

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

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

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert fake_git.review_cleanups == []
    assert (_ISSUE_ID, "blocked") in kanban.statuses


def test_run_task_cycle_still_finishes_when_the_cleanup_fails(tmp_path, monkeypatch, caplog) -> None:
    """Housekeeping must never turn a finished task into a traceback."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    def exploding_cleanup(container, projects_root, slug, task_id):
        raise RuntimeError("docker daemon went away")

    monkeypatch.setattr(dispatcher_mod.docker_exec, "remove_review_worktrees", exploding_cleanup)

    kanban = _FakeKanban()
    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert (_ISSUE_ID, "done") in kanban.statuses
    assert "docker daemon went away" in caplog.text


def test_run_task_cycle_cleanup_does_not_swallow_a_real_failure(tmp_path, monkeypatch) -> None:
    """The cleanup runs in a `finally`, which must not eat the exception it runs after."""
    cfg = _make_config(tmp_path)

    def exploding_dispatch_phase(*args, **kwargs):
        raise RuntimeError("phase blew up")

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", exploding_dispatch_phase)

    with pytest.raises(RuntimeError, match="phase blew up"):
        dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)


def test_cleanup_container_returns_none_without_accounts(tmp_path) -> None:
    """A config with no accounts has nothing to run docker exec in."""
    assert dispatcher_mod.cleanup_container(_make_config(tmp_path, accounts=[])) is None


def test_run_task_cycle_does_not_merge_by_default(tmp_path, monkeypatch, fake_git) -> None:
    """merge_on_done is off, so a done task leaves the project's branch alone."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert (_ISSUE_ID, "done") in kanban.statuses
    assert fake_git.merges == []


def test_run_task_cycle_merges_a_done_task_when_asked_to(tmp_path, monkeypatch, fake_git) -> None:
    cfg = _make_config(tmp_path, merge_on_done=True)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert (_ISSUE_ID, "done") in kanban.statuses
    assert fake_git.merges == [("agent-cuenta1", cfg.projects_root, "myproj", "task-1")]


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

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

    assert "## auditor" in read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1")).body
    assert not filed.exists()
    assert (Path(learnings.dropped_dir(cfg.hive_tasks_dir)) / "task-1-trap.md").exists()


def test_run_task_cycle_does_not_merge_a_blocked_task(tmp_path, monkeypatch, fake_git) -> None:
    """Nothing approved this work, so merge_on_done has nothing to act on."""
    cfg = _make_config(tmp_path, merge_on_done=True)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _rejecting_dispatch_phase)

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert (_ISSUE_ID, "blocked") in kanban.statuses
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

    kanban = _FakeKanban()
    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert (_ISSUE_ID, "done") in kanban.statuses
    assert "has uncommitted changes" in caplog.text


def test_run_task_cycle_survives_a_merge_that_raises(tmp_path, monkeypatch, caplog) -> None:
    cfg = _make_config(tmp_path, merge_on_done=True)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    def exploding_merge(container, projects_root, slug, task_id):
        raise RuntimeError("docker daemon went away")

    monkeypatch.setattr(dispatcher_mod.docker_exec, "merge_task_branch", exploding_merge)

    kanban = _FakeKanban()
    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert (_ISSUE_ID, "done") in kanban.statuses
    assert "docker daemon went away" in caplog.text


def test_run_task_cycle_hands_the_tree_back_after_merging(tmp_path, monkeypatch, fake_git) -> None:
    """git writes to .git/ as root here too, so the merge restores ownership as well."""
    cfg = _make_config(tmp_path, merge_on_done=True)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

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


# --- the board, for a harness that was given one --------------------------


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


def test_run_task_cycle_opens_a_card_for_a_task_that_has_none(tmp_path, monkeypatch) -> None:
    """Creating the issue is the only way a task id becomes a uuid, and the
    uuid has to outlive the run: a later phase after a restart must move this
    same card rather than open a second one."""
    cfg = _make_config(tmp_path)
    _approve_on_first_round(monkeypatch)

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert kanban.created == [(f"task-1: {_DESCRIPTION}", _DESCRIPTION)]
    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert task.kanban_issue_id == _ISSUE_ID
    assert (_ISSUE_ID, "done") in kanban.statuses


def test_run_task_cycle_reuses_the_card_the_task_already_names(tmp_path, monkeypatch) -> None:
    seeded = "aaaabbbb-cccc-dddd-eeee-ffff00001111"
    cfg = _make_config(tmp_path)
    set_kanban_issue_id(cfg.hive_tasks_dir, "task-1", seeded)
    _approve_on_first_round(monkeypatch)

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    # `run-task --kanban-issue-id`, or an earlier run: either way the board
    # already has this task on it and a second card would split its history.
    assert kanban.created == []
    assert {issue for issue, _ in kanban.statuses} == {seeded}


def test_run_task_cycle_blocks_the_card_a_task_with_no_description_names(
    tmp_path, monkeypatch,
) -> None:
    """The unaskable task still owes the board an answer, once it has a card:
    blocked is what a human has to come and look at."""
    cfg = _make_config(tmp_path)
    set_kanban_issue_id(cfg.hive_tasks_dir, "task-1", _ISSUE_ID)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", lambda *a, **kw: pytest.fail("dispatched"))

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban)

    assert kanban.created == []
    assert kanban.statuses == [(_ISSUE_ID, "blocked")]


def test_run_task_cycle_runs_the_phases_when_the_board_refuses_a_card(
    tmp_path, monkeypatch, caplog,
) -> None:
    """A board is a visibility aid, not dispatch state: one that can't be
    reached costs a warning and the run goes on without a card."""
    cfg = _make_config(tmp_path)
    _approve_on_first_round(monkeypatch)

    class _RefusingKanban(_FakeKanban):
        def create_issue(self, title, description=None):
            raise RuntimeError("vibe-kanban: not logged in")

    kanban = _RefusingKanban()
    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1")).status == "done"
    # No card, so no status to move — and one warning, not one per phase.
    assert kanban.statuses == []
    assert sum("runs without a card" in r.getMessage() for r in caplog.records) == 1


def test_run_task_cycle_without_a_board_says_nothing_about_one(
    tmp_path, monkeypatch, caplog,
) -> None:
    """`vibe_kanban` is optional, so most runs have no board at all. Such a run
    must not carry a warning per phase about something it was never asked for."""
    cfg = _make_config(tmp_path)
    _approve_on_first_round(monkeypatch)

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        dispatcher_mod.run_task_cycle(
            cfg, "task-1", "myproj", NullKanbanClient(), description=_DESCRIPTION,
        )

    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert task.status == "done"
    assert task.kanban_issue_id is None
    assert "kanban" not in caplog.text.lower()


def test_issue_title_leads_with_the_task_id_and_the_first_line_of_the_ask() -> None:
    # The id lines the card up with a task file by eye; the ask is what a human
    # recognises without opening it.
    title = dispatcher_mod._issue_title("task-1", "\n  Add /healthz.\nThen document it.\n")

    assert title == "task-1: Add /healthz."


def test_issue_title_stops_at_a_glance() -> None:
    title = dispatcher_mod._issue_title("task-1", "W" * 400)

    assert len(title) == dispatcher_mod._ISSUE_TITLE_MAX
    assert title.endswith("\u2026")


def test_issue_title_of_an_ask_with_no_line_is_just_the_task_id() -> None:
    assert dispatcher_mod._issue_title("task-1", "   ") == "task-1"


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

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

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

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

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


def test_run_task_cycle_does_not_remap_a_project_that_has_an_index(tmp_path, monkeypatch) -> None:
    """The map is written once and extended by the roles that follow it."""
    cfg = _make_config(tmp_path, mapping_enabled=True)
    _fake_index(monkeypatch, exists=True)
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _recording_dispatch_phase(calls))

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

    assert project_docs.MAPPER_ROLE not in [call["role"] for call in calls]
    assert calls[0]["role"] == "arquitecto"


def test_run_task_cycle_cannot_map_without_a_container_to_ask(tmp_path, monkeypatch) -> None:
    """Enabled but unanswerable: with no account there is nothing to run
    `test -e` in, and a task must not be held up by that."""
    cfg = _make_config(tmp_path, mapping_enabled=True, accounts=[])
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _recording_dispatch_phase(calls))

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

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
            cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION,
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

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert calls == [project_docs.MAPPER_ROLE]
    assert (_ISSUE_ID, "blocked") in kanban.statuses
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

    assert _DESCRIPTION in prompt
    assert "You are not doing that task" in prompt
    assert "/data/.hive/tasks/task-1.md" not in prompt
    assert project_docs.INDEX in prompt
    assert "12 turns and no more" in prompt


def test_run_task_cycle_hands_every_role_its_duty_to_the_project_docs(
    tmp_path, monkeypatch,
) -> None:
    """The duties are about *this* project's docs, so they ride in the
    dispatcher's prompt rather than in a vendored skill — a skill is method
    and travels between projects."""
    cfg = _make_config(tmp_path, max_revision_rounds=1)
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _recording_dispatch_phase(calls))

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

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

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

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

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

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

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

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

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

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

    kanban = _FakeKanban()
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert (_ISSUE_ID, "blocked") in kanban.statuses
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

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

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

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

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

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _FakeKanban(), description=_DESCRIPTION)

    assert _inbox(cfg)["inbox/task-8-db.md"].carried_by == "task-1"


# Debt the task declared, ruled on by the review, mirrored onto the board.
# The index is the source of truth and the board is the viewing aid, so every
# test below has to hold on both halves: what the auditor is told to file, and
# what a human looking at the backlog ends up seeing.

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


class _DebtBoard(_FakeKanban):
    """A board that answers with a different id per card.

    The task's own card is opened first and keeps `_ISSUE_ID`, so a run can
    still be checked on the status it set; every debt card after it gets an id
    of its own, which is what the auditor is handed and what a merge closes.
    """

    def create_issue(self, title, description=None):
        self.created.append((title, description))
        return self.issue_id if len(self.created) == 1 else f"card-{len(self.created) - 1}"

    @property
    def debt_cards(self):
        return self.created[1:]


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
    calls, board=None, declared=(), rulings=(), resolved=(), verdict="APPROVED",
):
    """Every phase succeeds; the implementador declares and the revisor rules."""

    def fake_dispatch_phase(cfg_arg, task_id, slug, role, prompt, **kwargs):
        calls.append(
            dict(
                role=role,
                prompt=prompt,
                cards=len(board.created) if board is not None else 0,
            )
        )
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


def test_the_debt_the_review_accepted_becomes_one_card_each_on_the_board(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """One card per accepted declaration, opened by the dispatcher: an agent
    that can create tasks can assign itself work, and a re-run of the phase
    would open the same card twice."""
    cfg = _make_config(tmp_path)
    kanban = _DebtBoard()
    calls = []
    monkeypatch.setattr(
        dispatcher_mod,
        "dispatch_phase",
        _debt_dispatch_phase(
            calls,
            board=kanban,
            declared=(_DEBT, _MIGRATION),
            rulings=({"debt": "The retry loop has no test", "ruling": debt.ACCEPTED},),
        ),
    )

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    titles = [title for title, _ in kanban.debt_cards]
    assert titles == [
        "[debt] The retry loop has no test.",
        "[debt] The migration has no down step.",
    ]
    # The card carries the declaration whole — a human triaging the backlog
    # should not have to clone the repo to know what they are approving — and
    # names the entry that stays the source of truth.
    first = kanban.debt_cards[0][1]
    assert "**Fix:** a fake clock in conftest, then one case per branch" in first
    assert "`task-1-D1`" in first and project_docs.DEBT_INDEX in first
    assert "`task-1-D2`" in kanban.debt_cards[1][1]
    assert (_ISSUE_ID, "done") in kanban.statuses


def test_a_debt_card_opens_before_the_auditor_writes_the_row_that_points_at_it(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """That order is the whole trick: the card exists first, so the auditor
    can write a row that already points at it and stay the only writer."""
    cfg = _make_config(tmp_path)
    kanban = _DebtBoard()
    calls = []
    monkeypatch.setattr(
        dispatcher_mod,
        "dispatch_phase",
        _debt_dispatch_phase(calls, board=kanban, declared=(_DEBT,)),
    )

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    seen = {call["role"]: call["cards"] for call in calls}
    # One card through the whole review: the task's own.
    assert seen["implementador"] == 1 and seen["revisor"] == 1
    assert seen["auditor"] == 2


def test_only_the_auditor_is_handed_the_entry_and_card_ids(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """The ids are assigned before the phase runs, and handed to the one role
    that writes the index — every other role is told to propose, not file."""
    cfg = _make_config(tmp_path)
    kanban = _DebtBoard()
    calls = []
    monkeypatch.setattr(
        dispatcher_mod,
        "dispatch_phase",
        _debt_dispatch_phase(calls, board=kanban, declared=(_DEBT,)),
    )

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    by_role = _by_role(calls)
    assert "`task-1-D1`" in by_role["auditor"]
    assert "`card-1`" in by_role["auditor"]
    assert " | ".join(debt.COLUMNS) in by_role["auditor"]
    assert "`task-1-D1`" not in by_role["implementador"]
    assert "`task-1-D1`" not in by_role["revisor"]


def test_a_task_that_declared_no_debt_says_nothing_about_debt_to_the_auditor(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """A filing note with no entries under it reads as an instruction to find
    something to file, and the auditor files only what it is handed."""
    cfg = _make_config(tmp_path)
    kanban = _DebtBoard()
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _debt_dispatch_phase(calls, board=kanban))

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert kanban.debt_cards == []
    assert "already has its ids" not in _by_role(calls)["auditor"]
    # And nothing was read: there is no declaration to compare against.
    assert debt_index.reads == []


def test_debt_the_revisor_rejected_sends_an_approving_round_back_around(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """A rejected declaration is work the revisor says this task should have
    done, which is a finding — and a finding outranks the verdict field."""
    cfg = _make_config(tmp_path, max_revision_rounds=2)
    kanban = _DebtBoard()
    calls = []
    monkeypatch.setattr(
        dispatcher_mod,
        "dispatch_phase",
        _debt_dispatch_phase(
            calls,
            board=kanban,
            declared=(_DEBT,),
            rulings=({"debt": "The retry loop has no test", "ruling": debt.REJECTED},),
            verdict="APPROVED",
        ),
    )

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    # Both rounds spent, no auditor, and nothing filed or carded.
    assert _roles(calls).count("implementador") == 2
    assert "auditor" not in _roles(calls)
    assert kanban.debt_cards == []
    assert (_ISSUE_ID, "blocked") in kanban.statuses


def test_debt_the_revisor_read_as_a_block_ends_the_task_with_no_card(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """Another round cannot supply a decision the task was never given, so it
    ends here rather than burning the rest of the rounds to say so again."""
    cfg = _make_config(tmp_path, max_revision_rounds=3)
    kanban = _DebtBoard()
    calls = []
    monkeypatch.setattr(
        dispatcher_mod,
        "dispatch_phase",
        _debt_dispatch_phase(
            calls,
            board=kanban,
            declared=(_MIGRATION,),
            rulings=({"debt": "The migration has no down step", "ruling": debt.BLOCKS},),
        ),
    )

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert _roles(calls).count("implementador") == 1
    assert "auditor" not in _roles(calls)
    assert kanban.debt_cards == []
    assert (_ISSUE_ID, "blocked") in kanban.statuses


def test_debt_the_index_already_carries_gets_no_second_card(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """A board that fills with the same entry once per task is a board nobody
    reads, and the index is what knows the entry is already there."""
    cfg = _make_config(tmp_path)
    debt_index.text = (
        "| id | what | where | fix | card |\n"
        "|---|---|---|---|---|\n"
        "| `task-4-D1` | The retry loop has no test | backoff changes | a fake clock "
        "| `card-9` |\n"
    )
    kanban = _DebtBoard()
    calls = []
    monkeypatch.setattr(
        dispatcher_mod,
        "dispatch_phase",
        _debt_dispatch_phase(calls, board=kanban, declared=(_DEBT, _MIGRATION)),
    )

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert [title for title, _ in kanban.debt_cards] == ["[debt] The migration has no down step."]
    # And the entry that did get filed is the first this task files, not the
    # second: the numbering counts what was filed, not what was declared.
    assert "`task-1-D1`" in _by_role(calls)["auditor"]


def test_the_index_is_read_from_the_branch_the_task_is_being_built_on(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """A re-run of a task that already filed entries has them on its own
    branch and nowhere else; reading the checkout would card them twice."""
    cfg = _make_config(tmp_path)
    kanban = _DebtBoard()
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase", _debt_dispatch_phase([], declared=(_DEBT,)),
    )

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert debt_index.reads == [
        ("agent-cuenta1", f"{cfg.projects_root}/myproj/worktrees/task-1/work"),
    ]


def test_a_project_with_no_board_files_its_debt_anyway(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """`vibe_kanban` is optional, and the index is the source of truth — so a
    project without a board loses the cards and nothing else."""
    cfg = _make_config(tmp_path)
    calls = []
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase", _debt_dispatch_phase(calls, declared=(_DEBT,)),
    )

    dispatcher_mod.run_task_cycle(
        cfg, "task-1", "myproj", NullKanbanClient(), description=_DESCRIPTION,
    )

    note = _by_role(calls)["auditor"]
    assert "`task-1-D1`" in note
    assert "no card (this project has no board)" in note


def test_the_entries_a_task_resolved_outlive_the_handoff_that_named_them(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """The merge that closes these cards can be days later, in `merge-task`,
    long after this handoff is prose in a file nobody parses."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod,
        "dispatch_phase",
        _debt_dispatch_phase([], resolved=("`task-4-D1`", "task-4-D1", "task-6-D2")),
    )

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", _DebtBoard(), description=_DESCRIPTION)

    assert read_resolved_debt(cfg.hive_tasks_dir, "task-1") == ["task-4-D1", "task-6-D2"]


def test_a_merged_task_closes_the_cards_of_the_debt_it_resolved(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """Only after the merge: the row is marked resolved on the branch, so a
    branch that never lands leaves the board exactly as it was."""
    cfg = _make_config(tmp_path, merge_on_done=True)
    debt_index.text = (
        "| id | what | where | fix | card |\n"
        "|---|---|---|---|---|\n"
        "| `task-4-D1` | The retry loop has no test | backoff changes | a fake clock "
        "| `card-9` |\n"
    )
    kanban = _DebtBoard()
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase", _debt_dispatch_phase([], resolved=("task-4-D1",)),
    )

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert ("card-9", "done") in kanban.statuses
    # Read from the merged checkout this time, not the branch: the card id was
    # written by whichever task filed the entry, which is not this one.
    assert debt_index.reads == [("agent-cuenta1", f"{cfg.projects_root}/myproj")]


def test_a_refused_merge_leaves_the_resolved_cards_open(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """Nothing landed, so the debt is not resolved anywhere a later clone can
    see — closing the card would be the board lying about the repo."""
    cfg = _make_config(tmp_path, merge_on_done=True)
    debt_index.text = (
        "| id | what | where | fix | card |\n"
        "|---|---|---|---|---|\n"
        "| `task-4-D1` | The retry loop has no test | backoff | a fake clock | `card-9` |\n"
    )

    def refusing_merge(container, projects_root, slug, task_id):
        return dispatcher_mod.docker_exec.MergeOutcome(
            dispatcher_mod.docker_exec.REFUSED, "main", "has uncommitted changes"
        )

    monkeypatch.setattr(dispatcher_mod.docker_exec, "merge_task_branch", refusing_merge)
    kanban = _DebtBoard()
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase", _debt_dispatch_phase([], resolved=("task-4-D1",)),
    )

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", kanban, description=_DESCRIPTION)

    assert ("card-9", "done") not in kanban.statuses
    assert debt_index.reads == []


def test_close_resolved_debt_leaves_a_resolved_entry_with_no_card_alone(
    tmp_path, monkeypatch, debt_index,
) -> None:
    """Either the id is wrong or the row never got a card. Both are for a
    human to look at, and neither is worth failing a merge that succeeded."""
    cfg = _make_config(tmp_path)
    dispatcher_mod.context_transfer.set_resolved_debt(cfg.hive_tasks_dir, "task-1", ["task-4-D1"])
    debt_index.text = (
        "| id | what | where | fix | card |\n"
        "|---|---|---|---|---|\n"
        "| `task-4-D1` | The retry loop has no test | backoff | a fake clock | - |\n"
    )
    kanban = _DebtBoard()

    assert dispatcher_mod.close_resolved_debt(cfg, kanban, "task-1", "myproj") == []
    assert kanban.statuses == []


def test_close_resolved_debt_does_not_read_the_index_for_a_project_with_no_board(
    tmp_path, monkeypatch, debt_index,
) -> None:
    """There is nothing to close, and the read costs a `docker exec` per merge
    on every project that never configured a board."""
    cfg = _make_config(tmp_path)
    dispatcher_mod.context_transfer.set_resolved_debt(cfg.hive_tasks_dir, "task-1", ["task-4-D1"])

    assert dispatcher_mod.close_resolved_debt(cfg, NullKanbanClient(), "task-1", "myproj") == []
    assert debt_index.reads == []


# --- _file_accepted_debt, off the handoffs rather than the results ----------
#
# The cycle calls it holding both results in memory; `run-phase --final` calls
# it holding only what earlier phases left on disk. These pin down the shape it
# has to answer for either caller: two `dict | None` and nothing else.


def test_filing_accepted_debt_opens_one_card_per_declaration_the_review_kept(
    tmp_path, debt_index,
) -> None:
    """The unit the cycle and the hand-resume share: declarations in, entries
    and cards out, with the revisor's rulings deciding which survive."""
    cfg = _make_config(tmp_path)
    kanban = _DebtBoard()
    kanban.create_issue("the task's own card")

    filed = dispatcher_mod._file_accepted_debt(
        cfg, kanban, "task-1", "myproj",
        {"debt": [dict(_DEBT), dict(_MIGRATION)]},
        {"debt_rulings": [{"debt": "The migration has no down step", "ruling": debt.REJECTED}]},
    )

    assert [entry for entry, _, _ in filed] == ["task-1-D1"]
    assert [card for _, card, _ in filed] == ["card-1"]
    assert [title for title, _ in kanban.debt_cards] == ["[debt] The retry loop has no test."]


def test_filing_accepted_debt_with_no_review_on_disk_keeps_every_declaration(
    tmp_path, debt_index,
) -> None:
    """Accept-by-default all the way down. A hand-resumed task whose revisor
    handoff was never stored has no rulings to read, and the answer to "was
    this accepted?" on a round that reached the auditor is yes."""
    cfg = _make_config(tmp_path)
    kanban = _DebtBoard()
    kanban.create_issue("the task's own card")

    filed = dispatcher_mod._file_accepted_debt(
        cfg, kanban, "task-1", "myproj", {"debt": [dict(_DEBT), dict(_MIGRATION)]}, None,
    )

    assert [entry for entry, _, _ in filed] == ["task-1-D1", "task-1-D2"]
    assert len(kanban.debt_cards) == 2


def test_filing_accepted_debt_with_nothing_declared_reads_no_index_and_files_nothing(
    tmp_path, debt_index,
) -> None:
    """A missing handoff declares nothing, which is the same answer as a
    handoff that declared nothing — and neither is worth a docker exec."""
    cfg = _make_config(tmp_path)
    kanban = _DebtBoard()

    assert dispatcher_mod._file_accepted_debt(cfg, kanban, "task-1", "myproj", None, None) == []
    assert dispatcher_mod._file_accepted_debt(
        cfg, kanban, "task-1", "myproj", {"debt": []}, {"debt_rulings": []},
    ) == []
    assert kanban.created == []
    assert debt_index.reads == []


def test_filing_accepted_debt_skips_what_the_branch_index_already_carries(
    tmp_path, debt_index,
) -> None:
    """Read from the branch, not the checkout: a re-run of the closing phase
    must not card the same entry twice."""
    cfg = _make_config(tmp_path)
    debt_index.text = (
        "| id | what | where | fix | card |\n"
        "|---|---|---|---|---|\n"
        "| `task-1-D1` | The retry loop has no test | backoff changes | a fake clock "
        "| `card-9` |\n"
    )
    kanban = _DebtBoard()
    kanban.create_issue("the task's own card")

    filed = dispatcher_mod._file_accepted_debt(
        cfg, kanban, "task-1", "myproj", {"debt": [dict(_DEBT), dict(_MIGRATION)]}, None,
    )

    assert [declaration.what for _, _, declaration in filed] == [_MIGRATION["what"]]
    # Numbered by what was filed, not by what was declared.
    assert [entry for entry, _, _ in filed] == ["task-1-D1"]


def test_filing_accepted_debt_survives_an_index_it_cannot_read(
    tmp_path, monkeypatch, caplog,
) -> None:
    """The index is read to avoid a duplicate card. Failing to read it costs a
    duplicate card, never the entry."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(
        dispatcher_mod.debt, "read_index",
        lambda container, workdir: (_ for _ in ()).throw(RuntimeError("no container")),
    )
    kanban = _DebtBoard()
    kanban.create_issue("the task's own card")

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        filed = dispatcher_mod._file_accepted_debt(
            cfg, kanban, "task-1", "myproj", {"debt": [dict(_DEBT)]}, None,
        )

    assert [entry for entry, _, _ in filed] == ["task-1-D1"]
    assert any("no container" in r.getMessage() for r in caplog.records)


def test_filing_accepted_debt_files_the_entry_even_when_the_board_refuses(
    tmp_path, debt_index, caplog,
) -> None:
    """The index is the source of truth and the board is the aid: a project
    whose board is down loses the cards, not the debt."""
    cfg = _make_config(tmp_path)

    class _BrokenBoard(_FakeKanban):
        def create_issue(self, title, description=None):
            raise RuntimeError("board is down")

    with caplog.at_level("WARNING", logger=dispatcher_mod.logger.name):
        filed = dispatcher_mod._file_accepted_debt(
            cfg, _BrokenBoard(), "task-1", "myproj", {"debt": [dict(_DEBT)]}, None,
        )

    assert filed == [("task-1-D1", None, debt.Declaration(**_DEBT))]
    assert any("task-1-D1" in r.getMessage() for r in caplog.records)


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
        cfg, "task-1", "myproj", _FakeKanban(), "revisor", round_num=2, description=_DESCRIPTION,
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
        cfg, "task-1", "myproj", _FakeKanban(), "revisor", round_num=2, description=_DESCRIPTION,
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
        cfg, "task-1", "myproj", _FakeKanban(), "revisor", round_num=2,
        description=_DESCRIPTION, note="The host rebooted mid-round.",
    )

    assert "The host rebooted mid-round." in prompts[0]


def test_run_single_phase_leaves_the_task_pending_and_orphans_its_entries(
    tmp_path, monkeypatch,
) -> None:
    """Every phase but the last one. The task is still going, so the card is
    not closed — and nobody filed these entries, so they stop naming a carrier
    that is no longer running."""
    cfg = _make_config(tmp_path)
    seen = _spy_learnings(monkeypatch)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    kanban = _FakeKanban()
    dispatcher_mod.run_single_phase(
        cfg, "task-1", "myproj", kanban, "revisor", round_num=2, description=_DESCRIPTION,
    )

    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert task.status == "pending"
    assert (_ISSUE_ID, "done") not in kanban.statuses
    assert seen["carried"] == []
    assert seen["orphaned"] == ["task-1"]


def test_run_single_phase_final_closes_the_task_the_way_a_cycle_does(
    tmp_path, monkeypatch,
) -> None:
    """--final is the operator saying this phase is the last one. It buys what
    the cycle gives its auditor: the entries carried in beforehand, done in the
    task file and on the board, and no orphaning on the way out."""
    cfg = _make_config(tmp_path)
    seen = _spy_learnings(monkeypatch)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    kanban = _FakeKanban()
    dispatcher_mod.run_single_phase(
        cfg, "task-1", "myproj", kanban, "auditor", final=True, description=_DESCRIPTION,
    )

    task = read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))
    assert task.status == "done"
    assert (_ISSUE_ID, "done") in kanban.statuses
    assert seen["carried"] == [("myproj", "task-1")]
    assert seen["orphaned"] == []


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
        cfg, "task-1", "myproj", _FakeKanban(), "revisor", round_num=2, description=_DESCRIPTION,
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
        cfg, "task-1", "myproj", _FakeKanban(), "auditor", final=True, description=_DESCRIPTION,
    )

    assert fake_git.merges == []


def test_run_single_phase_says_what_closing_by_hand_did_not_do(
    tmp_path, monkeypatch, caplog,
) -> None:
    """Invisible from the task file otherwise: a card closed this way was
    closed by a phase, not by a cycle that read a verdict."""
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _approving_dispatch_phase)

    with caplog.at_level("INFO", logger=dispatcher_mod.logger.name):
        dispatcher_mod.run_single_phase(
            cfg, "task-1", "myproj", _FakeKanban(), "auditor", final=True, description=_DESCRIPTION,
        )

    assert any(
        "closed by a single auditor phase" in r.getMessage() and "merge-task" in r.getMessage()
        for r in caplog.records
    )


def test_run_single_phase_that_did_not_land_blocks_the_card_and_returns_none(
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

    kanban = _FakeKanban()
    result = dispatcher_mod.run_single_phase(
        cfg, "task-1", "myproj", kanban, "auditor", final=True, description=_DESCRIPTION,
    )

    assert result is None
    assert (_ISSUE_ID, "blocked") in kanban.statuses
    assert (_ISSUE_ID, "done") not in kanban.statuses
    # --final asked for the entries, the phase did not file them: they go back.
    assert seen["orphaned"] == ["task-1"]
    assert len(fake_git.review_cleanups) == 1


def test_run_single_phase_refuses_a_task_nobody_described(tmp_path, monkeypatch) -> None:
    """Same preamble as the cycle, same refusal: a role told a task id and
    nothing else costs a phase of quota to learn nothing."""
    cfg = _make_config(tmp_path)
    set_kanban_issue_id(cfg.hive_tasks_dir, "task-1", _ISSUE_ID)
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", lambda *a, **kw: calls.append(1))

    kanban = _FakeKanban()
    assert dispatcher_mod.run_single_phase(cfg, "task-1", "myproj", kanban, "auditor") is None
    assert calls == []
    assert kanban.statuses == [(_ISSUE_ID, "blocked")]


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

    kanban = _FakeKanban()
    result = dispatcher_mod.run_single_phase(
        cfg, "task-1", "myproj", kanban, "revisor", round_num=2, description=_DESCRIPTION,
    )

    assert result is None
    assert fake_git.review_cleanups == []
    assert (_ISSUE_ID, "blocked") in kanban.statuses
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
            cfg, "task-1", "myproj", _FakeKanban(), "auditor", final=True, description=_DESCRIPTION,
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
        cfg, "task-1", "myproj", _FakeKanban(), "revisor", round_num=2, description=_DESCRIPTION,
    )

    assert read_handoff(cfg.hive_tasks_dir, "task-1", "revisor") == payload


def test_run_single_phase_final_files_the_debt_the_stored_handoffs_declared(
    tmp_path, monkeypatch, fake_git, debt_index,
) -> None:
    """The whole point of storing them: a task driven a phase at a time closes
    with the same cards, entries and resolved rows a cycle would have left."""
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

    kanban = _DebtBoard()
    dispatcher_mod.run_single_phase(
        cfg, "task-1", "myproj", kanban, "auditor", final=True, description=_DESCRIPTION,
    )

    # The revisor's ruling survived the round trip through disk: one card, not two.
    assert [title for title, _ in kanban.debt_cards] == ["[debt] The retry loop has no test."]
    # And the auditor is told the entry it owns, so its row points at the card.
    assert "`task-1-D1`" in calls[0]["prompt"]
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
        cfg, "task-1", "myproj", _DebtBoard(), "auditor", final=True,
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
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _recording_dispatch_phase([]))

    kanban = _DebtBoard()
    with caplog.at_level("INFO", logger=dispatcher_mod.logger.name):
        dispatcher_mod.run_single_phase(
            cfg, "task-1", "myproj", kanban, "auditor", final=True, description=_DESCRIPTION,
        )

    assert kanban.debt_cards == []
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
            cfg, "task-1", "myproj", _DebtBoard(), "auditor", final=True,
            description=_DESCRIPTION,
        )

    assert any(
        "implementador: found" in r.getMessage() and "revisor: missing" in r.getMessage()
        and "1 debt card(s) filed" in r.getMessage()
        for r in caplog.records
    )


def test_run_single_phase_that_is_not_final_files_no_debt(
    tmp_path, monkeypatch, debt_index,
) -> None:
    """A middle phase has no business closing anything out. The handoffs are
    on disk from the moment they are returned, so what keeps a revisor round
    from carding its own task's debt is --final and nothing else."""
    cfg = _make_config(tmp_path)
    save_handoff(cfg.hive_tasks_dir, "task-1", "implementador", {"debt": [dict(_DEBT)]})
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _recording_dispatch_phase([]))

    kanban = _DebtBoard()
    dispatcher_mod.run_single_phase(
        cfg, "task-1", "myproj", kanban, "revisor", round_num=2, description=_DESCRIPTION,
    )

    assert kanban.debt_cards == []
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
    a worker and past the line for the console."""
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
