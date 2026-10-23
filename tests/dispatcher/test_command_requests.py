"""A phase files the command it was denied instead of working around it
(`docs/plans/command-requests.md`, decision 2; `docs/decisions.md` ADR 117)."""

import pytest

import dispatcher.dispatcher as dispatcher_mod
from dispatcher import context_transfer, handoff, project_docs, tasks
from dispatcher.config import PHASE_ROLES, AccountConfig
from dispatcher.context_transfer import read_task_file, task_file_path
from dispatcher.docker_exec import ClaudeResult
from tests.dispatcher.test_dispatcher import (  # noqa: F401 - fixtures used by name
    _DESCRIPTION,
    _fake_index,
    _make_config,
    _phases_may_end_in_prose,
    _recording_dispatch_phase,
    fake_git,
)


def _request(argv=("uv", "sync"), kind="agent", name="", cwd="", reason="the deps are missing"):
    return {"argv": list(argv), "reason": reason, "target": {"kind": kind, "name": name}, "cwd": cwd}


def _implementador_return(requests):
    return {
        "summary": "stopped for a command", "decisions": [], "files_changed": [],
        "unverified": [], "subagents_running": [], "resolved_debt": [],
        "command_requests": requests,
    }


def _mapper_return(requests):
    return {
        "summary": "stopped for a command", "decisions": [], "files_changed": [],
        "unverified": [], "subagents_running": [], "command_requests": requests,
    }


# --- the schema -----------------------------------------------------------


@pytest.mark.parametrize("role", ["implementador", project_docs.MAPPER_ROLE])
def test_the_roles_that_run_commands_have_the_field_and_must_fill_it(role) -> None:
    schema = handoff.schema_for(role)
    field = schema["properties"]["command_requests"]
    assert "command_requests" in schema["required"]
    assert field["maxItems"] == 3
    item = field["items"]
    assert set(item["required"]) == {"argv", "reason", "target", "cwd"}
    assert item["additionalProperties"] is False
    assert item["properties"]["target"]["properties"]["kind"]["enum"] == ["agent", "dind", "service"]


@pytest.mark.parametrize(
    "role", sorted(PHASE_ROLES - {"implementador", project_docs.MAPPER_ROLE}),
)
def test_the_other_roles_have_no_field_to_file_one_in(role) -> None:
    schema = handoff.schema_for(role)
    assert schema is None or "command_requests" not in schema["properties"]
    assert not handoff.files_command_requests(role)


def test_only_the_roles_that_can_file_are_told_how() -> None:
    assert handoff.files_command_requests("implementador")
    assert handoff.files_command_requests(project_docs.MAPPER_ROLE)
    assert "command_requests" in handoff.COMMAND_REQUESTS_PROMPT


# --- the check ------------------------------------------------------------


@pytest.mark.parametrize(
    "request_",
    [
        _request(kind="agent"),
        _request(kind="dind", argv=("docker", "compose", "up", "-d")),
        _request(kind="service", name="db", argv=("psql", "-c", "select 1")),
        _request(cwd="backend/api"),
    ],
)
def test_a_well_formed_request_is_accepted(request_) -> None:
    assert handoff.command_request_problems(request_) == []
    accepted, refused = handoff.command_requests({"command_requests": [request_]})
    assert accepted == [request_]
    assert refused == []


@pytest.mark.parametrize(
    "request_, problem",
    [
        ("uv sync", "not an object"),
        ({**_request(), "argv": []}, "argv is not a non-empty list"),
        ({**_request(), "argv": "uv sync"}, "argv is not a non-empty list"),
        (_request(argv=("uv", "")), "argv has an item that is not a non-empty string"),
        ({**_request(), "argv": ["uv", 3]}, "argv has an item that is not a non-empty string"),
        ({**_request(), "reason": None}, "reason is not a string"),
        (_request(kind="host"), "target kind is 'host'"),
        ({**_request(), "target": "agent"}, "target kind is None"),
        (_request(kind="service", name=""), "a service target has no name"),
        (_request(kind="service", name="   "), "a service target has no name"),
        ({**_request(), "cwd": None}, "cwd is not a string"),
        (_request(cwd="/etc"), "is absolute"),
        (_request(cwd="src/../.."), "has a .. segment"),
    ],
)
def test_each_malformed_request_is_refused_with_its_reason(request_, problem) -> None:
    accepted, refused = handoff.command_requests({"command_requests": [request_]})
    assert accepted == []
    assert len(refused) == 1
    assert refused[0]["request"] == request_
    assert problem in refused[0]["reason"]


def test_every_problem_of_one_request_is_named() -> None:
    bad = {"argv": [], "reason": 1, "target": {"kind": "x"}, "cwd": "/"}
    problems = handoff.command_request_problems(bad)
    assert len(problems) == 4


def test_requests_past_the_limit_are_refused_and_the_first_three_kept() -> None:
    filed = [_request(argv=("step", str(n))) for n in range(5)]
    accepted, refused = handoff.command_requests({"command_requests": filed})
    assert accepted == filed[:3]
    assert [item["request"] for item in refused] == filed[3:]
    assert all(item["reason"] == "over the limit of 3 requests" for item in refused)


@pytest.mark.parametrize(
    "payload", [None, {}, {"command_requests": []}, {"command_requests": "uv sync"}],
)
def test_no_list_files_nothing(payload) -> None:
    assert handoff.command_requests(payload) == ([], [])


def test_the_rendered_handoff_shows_each_verdict() -> None:
    payload = _implementador_return(
        [_request(kind="service", name="db", argv=("psql",)), _request(kind="host")],
    )
    text = handoff.render(payload)
    assert "Command requests" in text
    assert "`psql` on service db" in text
    assert "refused: target kind is 'host'" in text


# --- the record -----------------------------------------------------------


def test_the_envelope_carries_both_lists_back(tmp_path) -> None:
    hive = str(tmp_path)
    good, bad = _request(), _request(cwd="/abs")
    accepted, refused = handoff.command_requests({"command_requests": [good, bad]})
    context_transfer.save_handoff(
        hive, "task-1", "implementador", _implementador_return([good, bad]),
        command_requests={"accepted": accepted, "refused": refused},
    )
    envelope = context_transfer.read_handoff_envelope(hive, "task-1", "implementador")
    assert envelope["command_requests"]["accepted"] == [good]
    assert envelope["command_requests"]["refused"][0]["request"] == bad
    assert "is absolute" in envelope["command_requests"]["refused"][0]["reason"]


def test_a_role_that_cannot_file_writes_no_key(tmp_path) -> None:
    hive = str(tmp_path)
    context_transfer.save_handoff(hive, "task-1", "auditor", {"summary": "ok"})
    envelope = context_transfer.read_handoff_envelope(hive, "task-1", "auditor")
    assert "command_requests" not in envelope


# --- the cycle ------------------------------------------------------------


def _task(cfg):
    return read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))


def test_an_implementador_request_blocks_the_cycle_before_any_review(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    calls = []
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase",
        _recording_dispatch_phase(calls, handoffs={"implementador": _implementador_return([_request()])}),
    )

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome == dispatcher_mod.CycleOutcome.BLOCKED
    assert [c["role"] for c in calls] == ["arquitecto", "implementador"]
    task = _task(cfg)
    assert task.status == "blocked"
    assert task.blocked_reason == handoff.COMMAND_REQUEST
    envelope = context_transfer.read_handoff_envelope(cfg.hive_tasks_dir, "task-1", "implementador")
    worktree = f"{cfg.projects_root}/myproj/worktrees/task-1/work"
    resolved = {"kind": "agent", "container": "agent-cuenta1", "cwd": worktree}
    assert envelope["command_requests"] == {"accepted": [{**_request(), "resolved": resolved}], "refused": []}


def test_only_refused_requests_do_not_block(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    calls = []
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase",
        _recording_dispatch_phase(
            calls, handoffs={"implementador": _implementador_return([_request(cwd="/root")])},
        ),
    )

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome != dispatcher_mod.CycleOutcome.BLOCKED
    assert "revisor" in [c["role"] for c in calls]
    assert _task(cfg).blocked_reason is None
    envelope = context_transfer.read_handoff_envelope(cfg.hive_tasks_dir, "task-1", "implementador")
    assert envelope["command_requests"]["accepted"] == []
    assert len(envelope["command_requests"]["refused"]) == 1


@pytest.mark.parametrize("argv", [("git", "merge", "main"), ("git", "merge", "origin/main"), ("git", "pull")])
def test_a_phases_base_merge_request_is_denied_with_the_note_and_the_cycle_goes_on(
    tmp_path, monkeypatch, argv,
) -> None:
    """ADR 340: the dispatcher owns the base merge; asking for it never blocks."""
    cfg = _make_config(tmp_path)
    calls = []
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase",
        _recording_dispatch_phase(
            calls, handoffs={"implementador": _implementador_return([_request(argv=argv)])},
        ),
    )

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome != dispatcher_mod.CycleOutcome.BLOCKED
    assert "revisor" in [c["role"] for c in calls]
    assert _task(cfg).blocked_reason is None
    envelope = context_transfer.read_handoff_envelope(cfg.hive_tasks_dir, "task-1", "implementador")
    assert envelope["command_requests"]["accepted"] == []
    [refused] = envelope["command_requests"]["refused"]
    assert "dispatcher owns the base merge" in refused["reason"]


def test_a_mapping_request_blocks_before_the_arquitecto(tmp_path, monkeypatch) -> None:
    cfg = _make_config(
        tmp_path, mapping_enabled=True,
        accounts=[AccountConfig(name="cuenta1", container="agent-cuenta1", dind="dind-cuenta1")],
    )
    _fake_index(monkeypatch, exists=False)
    calls = []
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase",
        _recording_dispatch_phase(
            calls, handoffs={project_docs.MAPPER_ROLE: _mapper_return([_request(kind="dind", argv=("docker", "ps"))])},
        ),
    )

    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert outcome == dispatcher_mod.CycleOutcome.BLOCKED
    assert [c["role"] for c in calls] == [project_docs.MAPPER_ROLE]
    assert _task(cfg).blocked_reason == handoff.COMMAND_REQUEST


def test_a_mapping_with_only_refused_requests_goes_on(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path, mapping_enabled=True)
    _fake_index(monkeypatch, exists=False)
    calls = []
    monkeypatch.setattr(
        dispatcher_mod, "dispatch_phase",
        _recording_dispatch_phase(
            calls, handoffs={project_docs.MAPPER_ROLE: _mapper_return([_request(kind="service")])},
        ),
    )

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    assert [c["role"] for c in calls][:2] == [project_docs.MAPPER_ROLE, "arquitecto"]
    assert _task(cfg).blocked_reason is None


def test_the_prompt_tells_only_the_filing_roles(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    calls = []
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _recording_dispatch_phase(calls))

    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    for call in calls:
        told = handoff.COMMAND_REQUESTS_PROMPT in call["prompt"]
        assert told == (call["role"] == "implementador"), call["role"]


# --- the task file key ----------------------------------------------------


def test_the_key_is_absent_unless_set(tmp_path) -> None:
    hive = str(tmp_path)
    path = task_file_path(hive, "task-1")
    tasks.acquire_lock(hive, "task-1", owner="cuenta1")
    assert "blocked_reason" not in open(path).read()

    tasks.mark_blocked(hive, "task-1")
    assert "blocked_reason" not in open(path).read()

    tasks.mark_blocked(hive, "task-1", reason=handoff.COMMAND_REQUEST)
    assert "blocked_reason: command_request" in open(path).read()
    assert read_task_file(path).blocked_reason == handoff.COMMAND_REQUEST


def test_taking_the_task_again_clears_the_reason(tmp_path) -> None:
    hive = str(tmp_path)
    path = task_file_path(hive, "task-1")
    tasks.acquire_lock(hive, "task-1", owner="cuenta1")
    tasks.mark_blocked(hive, "task-1", reason=handoff.COMMAND_REQUEST)

    tasks.acquire_lock(hive, "task-1", owner="cuenta1")

    assert read_task_file(path).blocked_reason is None
    assert "blocked_reason" not in open(path).read()


def test_a_block_with_no_reason_clears_a_stale_one(tmp_path) -> None:
    hive = str(tmp_path)
    path = task_file_path(hive, "task-1")
    tasks.acquire_lock(hive, "task-1", owner="cuenta1")
    tasks.mark_blocked(hive, "task-1", reason=handoff.COMMAND_REQUEST)

    tasks.mark_blocked(hive, "task-1")

    assert read_task_file(path).blocked_reason is None


# --- the gates ------------------------------------------------------------


def test_no_gates_run_after_a_filed_request(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path, gates_enabled=True)

    def must_not_gate(*args, **kwargs):
        raise AssertionError("the gates ran after a filed command request")

    monkeypatch.setattr(dispatcher_mod, "_gate_report", must_not_gate)
    result = ClaudeResult(
        session_id="s", result_text="",
        raw={"is_error": False, "structured_output": _implementador_return([_request()])},
    )

    out, report = dispatcher_mod._run_gates(
        cfg, "agent-cuenta1", "/w", f"{cfg.projects_root}/p", "task-1", "implementador", result,
        None, None, None, None, account="cuenta1",
    )

    assert out is result
    assert report is None


def test_the_gates_run_when_step_3_refuses_every_request(tmp_path, monkeypatch) -> None:
    # The cycle does not block on a request the denylist refused (ADR 118), so
    # the branch goes on to the revisor and has to be graded first.
    cfg = _make_config(tmp_path, gates_enabled=True)
    graded = []

    def gate(*args, **kwargs):
        graded.append(True)
        return None

    monkeypatch.setattr(dispatcher_mod, "_gate_report", gate)
    result = ClaudeResult(
        session_id="s", result_text="",
        raw={"is_error": False, "structured_output": _implementador_return([_request(argv=("sudo", "ls"))])},
    )

    dispatcher_mod._run_gates(
        cfg, "agent-cuenta1", "/w", f"{cfg.projects_root}/p", "task-1", "implementador", result,
        None, None, None, None, account="cuenta1",
    )

    assert graded == [True]
