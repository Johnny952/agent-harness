"""The bounded `--resume` for a phase that ended without its handoff.

Its own file because test_dispatcher.py turns the check off for every fake
there that answers in prose (`_phases_may_end_in_prose`).
"""

import pytest

import dispatcher.dispatcher as dispatcher_mod
from dispatcher import handoff
from dispatcher.docker_exec import ClaudeResult
from dispatcher.state_machine import AccountState, get_state
from tests.dispatcher.test_dispatcher import _make_config, _split_ctx, fake_git  # noqa: F401 (autouse)

_HANDOFF = {"status": "done"}


def _prose(session_id="sess-1"):
    return ClaudeResult(session_id=session_id, result_text="Progress so far: ...", raw={"is_error": False})


def _handed_off():
    return ClaudeResult(
        session_id="sess-1", result_text="", raw={"is_error": False, "structured_output": _HANDOFF},
    )


class _Recorder:
    def __init__(self):
        self.reasons = []

    def record(self, reason, result):
        self.reasons.append(reason)


@pytest.fixture
def calls(monkeypatch):
    """The resumes asked for, answered from a script set by each test."""
    state = {"script": [], "calls": []}

    def fake_exec_claude(container, workdir, prompt, **kwargs):
        state["calls"].append({"prompt": prompt, **kwargs})
        return state["script"].pop(0)

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    return state


def _resume(cfg, result, role="implementador"):
    recorder = _Recorder()
    out = dispatcher_mod._resume_missing_handoff(
        cfg, "agent-cuenta1", "/work", role, result, "claude-opus-5-5", "medium", recorder,
    )
    return out, recorder


def test_a_phase_with_its_handoff_is_not_resumed(tmp_path, calls):
    out, recorder = _resume(_make_config(tmp_path), _handed_off())
    assert out.raw["structured_output"] == _HANDOFF
    assert calls["calls"] == [] and recorder.reasons == []


def test_a_phase_that_stopped_short_is_resumed_until_it_hands_off(tmp_path, calls):
    calls["script"] = [_prose(), _handed_off()]
    out, recorder = _resume(_make_config(tmp_path), _prose())

    assert handoff.parse(out) == _HANDOFF
    assert recorder.reasons == ["handoff-resume", "handoff-resume"]
    first = calls["calls"][0]
    assert first["prompt"] == handoff.continue_prompt()
    assert first["resume_session_id"] == "sess-1"
    assert first["effort"] == "medium" and first["model"] == "claude-opus-5-5"
    assert first["json_schema"] == handoff.schema_for("implementador")


def test_the_resumes_are_bounded_and_then_the_phase_fails(tmp_path, calls):
    calls["script"] = [_prose(), _prose(), _prose()]
    out, recorder = _resume(_make_config(tmp_path, max_handoff_resumes=2), _prose())

    assert len(calls["calls"]) == 2
    assert recorder.reasons == ["handoff-resume", "handoff-resume"]
    assert not dispatcher_mod._exec_succeeded(out)
    assert out.session_id == "sess-1"


def test_zero_resumes_fails_the_phase_at_once(tmp_path, calls):
    out, _ = _resume(_make_config(tmp_path, max_handoff_resumes=0), _prose())
    assert calls["calls"] == []
    assert not dispatcher_mod._exec_succeeded(out)


def test_no_session_means_nothing_to_resume(tmp_path, calls):
    out, _ = _resume(_make_config(tmp_path), _prose(session_id=None))
    assert calls["calls"] == []
    assert not dispatcher_mod._exec_succeeded(out)


def test_a_failed_phase_is_left_to_the_callers_failure_path(tmp_path, calls):
    failed = ClaudeResult(session_id="sess-1", result_text="boom", raw={"is_error": True})
    out, _ = _resume(_make_config(tmp_path), failed)
    assert out is failed and calls["calls"] == []


def test_a_rate_limited_resume_is_returned_for_the_failover(tmp_path, calls, monkeypatch):
    limited = ClaudeResult(session_id="sess-1", result_text="limited", raw={"is_error": False})
    calls["script"] = [limited]
    monkeypatch.setattr(dispatcher_mod, "is_rate_limit_error", lambda r: r is limited)
    out, _ = _resume(_make_config(tmp_path), _prose())
    assert out is limited and len(calls["calls"]) == 1


def test_a_role_with_no_schema_is_never_resumed(tmp_path, calls):
    out, _ = _resume(_make_config(tmp_path), _prose(), role="unknown-role")
    assert dispatcher_mod._exec_succeeded(out) and calls["calls"] == []


def test_every_role_that_hands_off_is_told_the_phase_runs_unattended():
    for role in ("cartografo", "arquitecto", "implementador", "revisor", "auditor"):
        assert dispatcher_mod._phase_system_prompt(role).endswith(handoff.UNATTENDED_PHASE)
    # The cartografo has no skills, so the paragraph is all it gets.
    assert dispatcher_mod._phase_system_prompt("cartografo") == handoff.UNATTENDED_PHASE
    assert dispatcher_mod._phase_system_prompt("unknown-role") is None


def test_dispatch_phase_succeeds_once_the_resume_brings_the_handoff(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path)
    prompts = []

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, **kwargs):
        if "usage" in prompt.lower():
            return ClaudeResult(
                session_id=None,
                result_text=(
                    "Current session: 10% used · resets later\n"
                    "Current week (all models): 10% used · resets later"
                ),
                raw={},
            )
        prompts.append(prompt)
        return _handed_off() if resume_session_id else _prose()

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    result = dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "arquitecto", "do the thing")

    assert result.success is True
    assert prompts[-1] == handoff.continue_prompt() and len(prompts) == 2
    assert get_state(cfg.state_dir, "cuenta1") == AccountState.IDLE


def test_every_call_of_a_phase_runs_on_its_roles_model(tmp_path, monkeypatch):
    """`role_model` reaches `--model` on the phase's first call and on the
    resume that brings its handoff, so one phase never spans two models
    (`docs/decisions.md` ADR 65)."""
    cfg = _make_config(tmp_path, role_model={"implementador": "claude-sonnet-5-5"})
    models = []

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, **kwargs):
        if "usage" in prompt.lower():
            return ClaudeResult(
                session_id=None,
                result_text=(
                    "Current session: 10% used · resets later\n"
                    "Current week (all models): 10% used · resets later"
                ),
                raw={},
            )
        models.append((resume_session_id, kwargs["model"]))
        return _handed_off() if resume_session_id else _prose()

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )

    dispatcher_mod.run_phase(_split_ctx(cfg), "implementador", round_num=1)

    assert models == [(None, "claude-sonnet-5-5"), ("sess-1", "claude-sonnet-5-5")]
