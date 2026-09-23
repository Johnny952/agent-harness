import datetime as dt
import os
import time

import pytest

import dispatcher.dispatcher as dispatcher_mod
from dispatcher import gates, handoff, project_docs, role_skills
from dispatcher.config import AccountConfig, Config
from dispatcher.context_transfer import (
    LockHeldError,
    acquire_lock,
    is_lock_expired,
    list_task_ids,
    read_task_file,
    scratch_dir,
    set_kanban_issue_id,
    task_file_path,
    write_task_file,
)
from dispatcher.docker_exec import ClaudeResult
from dispatcher.state_machine import AccountState, get_state, set_state
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
        heartbeat_ttl_seconds=120,
        heartbeat_interval_seconds=1,
        projects_root=str(tmp_path / "projects"),
        hive_tasks_dir=str(tmp_path / "hive"),
        state_dir=str(tmp_path / "state"),
        vibe_kanban=None,
        collector_url="http://127.0.0.1:8787",
        default_model="opus",
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

    outcome = dispatcher_mod.docker_exec.MergeOutcome(
        dispatcher_mod.docker_exec.MERGED, "main", "merged agent/task/task-1 into main"
    )

    def __init__(self):
        self.commits = []
        self.restored = []
        self.review_cleanups = []
        self.merges = []

    def commit_worktree(self, container, workdir, message, author_name, author_email):
        self.commits.append(
            dict(
                container=container,
                workdir=workdir,
                message=message,
                author_name=author_name,
                author_email=author_email,
            )
        )
        return True

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

#: Over the revisor's 3072-byte budget by a wide margin, and over it in the
#: payload rather than in the prose — the prose is what the CLI replaces with
#: a placeholder once a schema is in play.
_FAT_HANDOFF = {"status": "complete", "verdict": "APPROVED", "risks": ["r" * 4000]}
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
    assert "3072" in retry["prompt"]
    assert result.handoff == _LEAN_HANDOFF


def test_dispatch_phase_takes_the_retry_even_if_it_is_still_over_budget(tmp_path, monkeypatch) -> None:
    """Shorter is the win; exactly-in-budget is not worth a third call."""
    cfg = _make_config(tmp_path)
    still_fat = {"status": "complete", "verdict": "APPROVED", "risks": ["r" * 3500]}
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
    """The budgets are guesses until there is data. This line is the data, so
    it is written even on the path where nothing can be done about it."""
    cfg = _make_config(tmp_path)
    _phase_recorder(monkeypatch, [
        ClaudeResult(session_id=None, result_text="", raw={"is_error": False, "structured_output": _FAT_HANDOFF}),
    ])

    with caplog.at_level("WARNING"):
        dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review the thing")

    assert any("over its 3072-byte budget" in r.getMessage() for r in caplog.records)


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
    fat = {"status": "complete", "changed": ["dispatcher/gates.py"], "risks": ["r" * 5000]}
    calls = _phase_recorder(monkeypatch, [
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF}),
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": fat}),
        ClaudeResult(session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _LEAN_HANDOFF}),
    ])
    _gate_recorder(monkeypatch, [_one_finding(gates.ASK), gates.Report()])

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "implementador", "build the thing")

    assert len(calls) == 3
    assert "4096" in calls[2]["prompt"], "the third call is the shrink, not another gate retry"
    assert result.handoff == _LEAN_HANDOFF


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
    # cuenta1 rate-limits and joins `tried`; the recheck that follows also
    # recovers cuenta1 (now COOLING_DOWN) alongside cuenta3, which was
    # already cooling from a prior call. Picking idle[0] without excluding
    # `tried` would hand the retry straight back to cuenta1 and immediately
    # fail closed instead of trying cuenta3. cuenta2 stays over-threshold
    # through the recheck, proving the pick isn't just "any recovered
    # account" either.
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


def _recording_dispatch_phase(calls, failing=()):
    """Records every phase's role, model and turn budget, and approves once."""

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
        "/data/.hive/tasks/task-1.md",
        _DESCRIPTION,
        "/data/.hive/scratch/task-1",
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
