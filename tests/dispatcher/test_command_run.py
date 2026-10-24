"""The operator's verdict on a filed command request, and the resume after it
(`docs/plans/command-requests.md` step 4; `docs/decisions.md` ADR 119)."""

import json
import subprocess
import sys

import pytest

import dispatcher.dispatcher as dispatcher_mod
from dispatcher import cli, command_run, context_transfer, handoff, project_docs
from dispatcher.config import AccountConfig
from dispatcher.context_transfer import read_task_file, task_file_path
from tests.dispatcher.test_command_requests import _implementador_return, _mapper_return, _request
from tests.dispatcher.test_dispatcher import (  # noqa: F401 - fixtures used by name
    _DESCRIPTION,
    _fake_index,
    _make_config,
    fake_git,
)


class _Phases:
    """A `dispatch_phase` stand-in: every call recorded, each role's return queued.

    `returns` maps a role to the handoffs its calls hand back in turn; the
    last one repeats. Every call answers in session `sess-<role>-<n>` on
    cuenta1, so the envelope carries what a resume needs.
    """

    def __init__(self, returns, no_account_on_resume=False):
        self.returns = {role: list(items) for role, items in returns.items()}
        self.calls = []
        self.no_account_on_resume = no_account_on_resume

    def __call__(
        self, cfg_arg, task_id, slug, role, prompt, resume_session_id=None, model=None,
        effort=None, round_num=None, max_turns=None, only_account=None, **kwargs,
    ):
        self.calls.append(dict(
            role=role, prompt=prompt, resume_session_id=resume_session_id,
            only_account=only_account, round_num=round_num, model=model, max_turns=max_turns,
        ))
        if resume_session_id is not None and self.no_account_on_resume:
            return dispatcher_mod.DispatchResult(
                success=False, session_id=None, result_text="no account", account="",
                no_account=True,
            )
        queue = self.returns.get(role) or [None]
        payload = queue.pop(0) if len(queue) > 1 else queue[0]
        return dispatcher_mod.DispatchResult(
            success=True,
            session_id=f"sess-{role}-{len(self.calls)}",
            result_text="VERDICT: APPROVED" if role == "revisor" else "ok",
            account="cuenta1",
            handoff=payload,
        )

    def roles(self):
        return [call["role"] for call in self.calls]


class _FakeExec:
    """`docker_exec.run_docker_exec` without docker: records each call, answers as told."""

    def __init__(self, returncode=0, stdout="", stderr="", timeout=False):
        self.returncode, self.stdout, self.stderr, self.timeout = returncode, stdout, stderr, timeout
        self.calls = []

    def __call__(self, container, workdir, command, env=None, timeout=None):
        self.calls.append(dict(container=container, workdir=workdir, command=command, timeout=timeout))
        if self.timeout:
            raise subprocess.TimeoutExpired(["docker", "exec"], timeout, output=self.stdout.encode())
        return subprocess.CompletedProcess(
            ["docker", "exec", container, *command], self.returncode, self.stdout, self.stderr,
        )


def _task(cfg):
    return read_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"))


def _worktree(cfg):
    return f"{cfg.projects_root}/myproj/worktrees/task-1/work"


def _blocked(tmp_path, monkeypatch, requests=None, **cfg_overrides):
    """A task the implementador blocked on `requests`; the next implementador returns clean."""
    cfg = _make_config(tmp_path, **cfg_overrides)
    phases = _Phases({"implementador": [
        _implementador_return(requests if requests is not None else [_request()]),
        _implementador_return([]),
    ]})
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", phases)
    outcome = dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)
    assert outcome == dispatcher_mod.CycleOutcome.BLOCKED
    assert _task(cfg).blocked_reason == handoff.COMMAND_REQUEST
    phases.calls.clear()
    return cfg, phases


def _exec(monkeypatch, **kwargs):
    fake = _FakeExec(**kwargs)
    monkeypatch.setattr(command_run.docker_exec, "run_docker_exec", fake)
    return fake


def _log(cfg, n=1):
    with open(context_transfer.command_log_path(cfg.hive_tasks_dir, "task-1", n)) as handle:
        return handle.read()


# --- run --------------------------------------------------------------------


def test_run_with_exit_zero_runs_the_argv_and_resumes_the_session(tmp_path, monkeypatch) -> None:
    cfg, phases = _blocked(tmp_path, monkeypatch)
    fake = _exec(monkeypatch, stdout="Resolved 12 packages\n")

    outcome = command_run.run_command_request(cfg, "task-1", "myproj", 1, "run")

    assert fake.calls == [dict(
        container="agent-cuenta1", workdir=_worktree(cfg), command=["uv", "sync"],
        timeout=command_run.COMMAND_TIMEOUT_SECONDS,
    )]
    log = _log(cfg)
    assert 'argv: ["uv", "sync"]' in log
    assert f"cwd: {_worktree(cfg)}" in log
    assert "exit: 0" in log
    assert log.endswith("Resolved 12 packages\n")
    resume = phases.calls[0]
    assert resume["role"] == "implementador"
    assert resume["resume_session_id"] == "sess-implementador-2"
    assert resume["only_account"] == "cuenta1"
    assert resume["round_num"] == 1
    assert '["uv", "sync"]' in resume["prompt"]
    assert "exit code: 0" in resume["prompt"]
    assert "Resolved 12 packages" in resume["prompt"]
    # The cycle after the phase: the revisor and the auditor, as a cycle runs them.
    assert phases.roles() == ["implementador", "revisor", "auditor"]
    assert outcome == dispatcher_mod.CycleOutcome.FINISHED
    verdict = _task(cfg).command_verdicts
    assert len(verdict) == 1
    assert verdict[0]["n"] == 1 and verdict[0]["verdict"] == "run" and verdict[0]["by"] == "operator"
    assert verdict[0]["role"] == "implementador" and verdict[0]["at"]


def test_run_with_a_nonzero_exit_tells_the_phase_the_code(tmp_path, monkeypatch) -> None:
    cfg, phases = _blocked(tmp_path, monkeypatch)
    _exec(monkeypatch, returncode=2, stdout="partial\n", stderr="error: no lockfile\n")

    command_run.run_command_request(cfg, "task-1", "myproj", 1, "run")

    log = _log(cfg)
    assert "exit: 2" in log
    assert "partial\nerror: no lockfile\n" in log
    assert "exit code: 2" in phases.calls[0]["prompt"]
    assert "error: no lockfile" in phases.calls[0]["prompt"]


def test_a_timeout_is_recorded_as_one_with_no_exit_code(tmp_path, monkeypatch) -> None:
    cfg, phases = _blocked(tmp_path, monkeypatch)
    _exec(monkeypatch, timeout=True, stdout="still resolving\n")

    command_run.run_command_request(cfg, "task-1", "myproj", 1, "run")

    log = _log(cfg)
    assert f"timeout: after {command_run.COMMAND_TIMEOUT_SECONDS}s, no exit code" in log
    assert "exit:" not in log
    assert "still resolving" in log
    prompt = phases.calls[0]["prompt"]
    assert "timed out" in prompt and "exit code" in prompt
    assert "exit code:" not in prompt


def test_output_over_the_cap_is_truncated_and_the_phase_gets_the_path(tmp_path, monkeypatch) -> None:
    cfg, phases = _blocked(tmp_path, monkeypatch)
    big = "x" * (command_run.OUTPUT_CAP_BYTES + 1000)
    _exec(monkeypatch, stdout=big)

    command_run.run_command_request(cfg, "task-1", "myproj", 1, "run")

    log = _log(cfg)
    assert "x" * command_run.OUTPUT_CAP_BYTES in log
    assert "x" * (command_run.OUTPUT_CAP_BYTES + 1) not in log
    assert "1000 more bytes dropped" in log
    path = context_transfer.command_log_path(cfg.hive_tasks_dir, "task-1", 1)
    prompt = phases.calls[0]["prompt"]
    assert path in prompt
    assert "xxxx" not in prompt


def test_a_service_runs_through_its_dinds_compose(tmp_path, monkeypatch) -> None:
    account = AccountConfig(name="cuenta1", container="agent-cuenta1", dind="dind-cuenta1")
    monkeypatch.setattr(
        dispatcher_mod.command_targets, "running_services", lambda dind, project_dir: ["db"],
    )
    cfg, _ = _blocked(
        tmp_path, monkeypatch, [_request(kind="service", name="db", argv=("psql", "-c", "select 1"))],
        accounts=[account],
    )
    fake = _exec(monkeypatch, stdout="1\n")

    command_run.run_command_request(cfg, "task-1", "myproj", 1, "run")

    assert fake.calls[0]["container"] == "dind-cuenta1"
    assert fake.calls[0]["command"] == ["docker", "compose", "exec", "-T", "db", "psql", "-c", "select 1"]
    assert "service: db" in _log(cfg)


def test_no_shell_the_argv_is_a_list_and_a_semicolon_stays_literal(tmp_path, monkeypatch) -> None:
    cfg, _ = _blocked(tmp_path, monkeypatch, [_request(argv=("echo", "a;rm -rf x"))])
    fake = _exec(monkeypatch, stdout="a;rm -rf x\n")

    command_run.run_command_request(cfg, "task-1", "myproj", 1, "run")

    command = fake.calls[0]["command"]
    assert command == ["echo", "a;rm -rf x"]
    assert not {"sh", "bash", "-c"} & set(command)


def test_no_shell_down_to_subprocess(monkeypatch) -> None:
    """The real `run_docker_exec`, with only `subprocess.run` faked."""
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(command_run.docker_exec.subprocess, "run", fake_run)
    request = {"argv": ["echo", "a;rm -rf x"], "resolved": {"kind": "agent", "container": "c", "cwd": "/w"}}
    command_run.docker_exec.run_docker_exec("c", "/w", command_run._command_for(request["argv"], request["resolved"]))

    assert isinstance(seen["args"], list)
    assert seen["args"][-2:] == ["echo", "a;rm -rf x"]
    assert not seen["kwargs"].get("shell")


# --- deny -------------------------------------------------------------------


def test_deny_with_a_comment_resumes_with_it_and_runs_nothing(tmp_path, monkeypatch) -> None:
    cfg, phases = _blocked(tmp_path, monkeypatch)
    fake = _exec(monkeypatch)

    command_run.run_command_request(cfg, "task-1", "myproj", 1, "deny", comment="use the lockfile")

    assert fake.calls == []
    prompt = phases.calls[0]["prompt"]
    assert "denied" in prompt and "use the lockfile" in prompt
    assert phases.calls[0]["resume_session_id"] == "sess-implementador-2"
    verdict = _task(cfg).command_verdicts[0]
    assert verdict["verdict"] == "deny" and verdict["comment"] == "use the lockfile"


def _resumed_effort_and_model(tmp_path, monkeypatch, **cfg_overrides):
    """Block an implementador round on a request, resume it, return the resume's call."""
    efforts = []
    real = _Phases.__call__

    def with_effort(self, *args, effort=None, **kwargs):
        efforts.append(effort)
        return real(self, *args, effort=effort, **kwargs)

    monkeypatch.setattr(_Phases, "__call__", with_effort)
    cfg, phases = _blocked(tmp_path, monkeypatch, **cfg_overrides)
    efforts.clear()
    _exec(monkeypatch)
    command_run.run_command_request(cfg, "task-1", "myproj", 1, "deny")
    return cfg, phases, efforts


def test_a_resume_of_a_round_the_round_count_escalated_runs_on_the_escalated_model(tmp_path, monkeypatch) -> None:
    cfg, phases, efforts = _resumed_effort_and_model(
        tmp_path, monkeypatch, escalate_effort_after_round=0, escalated_effort="max",
        escalated_model="claude-opus-5-5",
    )

    assert phases.calls[0]["resume_session_id"] is not None
    assert phases.calls[0]["model"] == "claude-opus-5-5"
    assert efforts[0] == "max"


def test_a_resume_with_the_escalated_model_unset_keeps_the_default(tmp_path, monkeypatch) -> None:
    cfg, phases, efforts = _resumed_effort_and_model(
        tmp_path, monkeypatch, escalate_effort_after_round=0, escalated_effort="max",
    )

    assert phases.calls[0]["model"] == cfg.default_model


def test_a_resume_reads_the_repeated_finding_from_the_envelope(tmp_path, monkeypatch) -> None:
    """The filing round ran on the signal, which the cycle no longer remembers."""
    cfg, phases = _blocked(
        tmp_path, monkeypatch, escalated_model="claude-opus-5-5",
        escalate_effort_after_round=5, escalate_effort_on_repeated_finding=True,
    )
    envelope = context_transfer.read_handoff_envelope(cfg.hive_tasks_dir, "task-1", "implementador")
    assert envelope["model"] == cfg.default_model and "repeated_finding" not in envelope
    context_transfer.save_handoff(
        cfg.hive_tasks_dir, "task-1", "implementador", envelope["handoff"], round_num=2,
        command_requests=envelope["command_requests"], session_id=envelope["session_id"],
        account=envelope["account"], repeated_finding=True,
    )
    _exec(monkeypatch)

    command_run.run_command_request(cfg, "task-1", "myproj", 1, "deny")

    assert phases.calls[0]["model"] == "claude-opus-5-5"


def test_deny_without_a_comment_says_so(tmp_path, monkeypatch) -> None:
    cfg, phases = _blocked(tmp_path, monkeypatch)
    _exec(monkeypatch)

    command_run.run_command_request(cfg, "task-1", "myproj", 1, "deny")

    assert command_run.DENIED_NO_COMMENT in phases.calls[0]["prompt"]


# --- refusals ---------------------------------------------------------------


def _refused(cfg, n, verdict, match):
    with pytest.raises(command_run.VerdictRefused, match=match):
        command_run.run_command_request(cfg, "task-1", "myproj", n, verdict)


def test_a_task_that_is_not_blocked_is_refused(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _Phases({}))
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)
    fake = _exec(monkeypatch)

    _refused(cfg, 1, "run", "not blocked on a command request")
    assert fake.calls == []


def test_a_task_blocked_for_another_reason_is_refused(tmp_path, monkeypatch) -> None:
    cfg, phases = _blocked(tmp_path, monkeypatch)
    dispatcher_mod.tasks.mark_blocked(cfg.hive_tasks_dir, "task-1")
    fake = _exec(monkeypatch)

    _refused(cfg, 1, "run", "not blocked on a command request")
    assert fake.calls == [] and phases.calls == []


@pytest.mark.parametrize("n", [0, 2, 5])
def test_an_index_out_of_range_is_refused(tmp_path, monkeypatch, n) -> None:
    cfg, phases = _blocked(tmp_path, monkeypatch)
    fake = _exec(monkeypatch)

    _refused(cfg, n, "run", "out of range")
    assert fake.calls == [] and phases.calls == [] and _task(cfg).command_verdicts == []


def test_a_request_refused_at_filing_cannot_be_run(tmp_path, monkeypatch) -> None:
    cfg, phases = _blocked(tmp_path, monkeypatch, [_request(), _request(cwd="/etc")])
    fake = _exec(monkeypatch)

    _refused(cfg, 2, "run", "refused at filing")
    assert fake.calls == [] and phases.calls == []


def test_a_request_that_no_longer_passes_its_check_is_refused(tmp_path, monkeypatch) -> None:
    account = AccountConfig(name="cuenta1", container="agent-cuenta1", dind="dind-cuenta1")
    cfg, phases = _blocked(
        tmp_path, monkeypatch, [_request(kind="dind", argv=("docker", "ps"))], accounts=[account],
    )
    # The dind is gone from the config since the request was filed.
    cfg.accounts[0] = AccountConfig(name="cuenta1", container="agent-cuenta1")
    fake = _exec(monkeypatch)

    _refused(cfg, 1, "run", "no longer passes its check")
    assert fake.calls == [] and phases.calls == [] and _task(cfg).command_verdicts == []


def test_deny_does_not_need_the_check_to_pass(tmp_path, monkeypatch) -> None:
    account = AccountConfig(name="cuenta1", container="agent-cuenta1", dind="dind-cuenta1")
    cfg, phases = _blocked(
        tmp_path, monkeypatch, [_request(kind="dind", argv=("docker", "ps"))], accounts=[account],
    )
    cfg.accounts[0] = AccountConfig(name="cuenta1", container="agent-cuenta1")
    _exec(monkeypatch)

    command_run.run_command_request(cfg, "task-1", "myproj", 1, "deny")

    assert phases.calls[0]["resume_session_id"] is not None


def test_an_unknown_verdict_is_refused(tmp_path, monkeypatch) -> None:
    cfg, _ = _blocked(tmp_path, monkeypatch)
    _refused(cfg, 1, "approve", "not one of")


def test_a_second_verdict_on_the_same_block_is_refused(tmp_path, monkeypatch) -> None:
    """The resume was held, so the task is still blocked on the same request."""
    cfg = _make_config(tmp_path)
    phases = _Phases({"implementador": [_implementador_return([_request()])]}, no_account_on_resume=True)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", phases)
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)
    fake = _exec(monkeypatch)

    first = command_run.run_command_request(cfg, "task-1", "myproj", 1, "run")
    assert first == dispatcher_mod.CycleOutcome.HELD
    assert _task(cfg).blocked_reason == handoff.COMMAND_REQUEST

    _refused(cfg, 1, "deny", "already has a verdict: request 1 run by operator")
    assert len(fake.calls) == 1
    assert len(_task(cfg).command_verdicts) == 1


def test_a_request_filed_again_after_the_resume_is_a_new_block(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    phases = _Phases({"implementador": [_implementador_return([_request()])]})
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", phases)
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)
    _exec(monkeypatch)

    again = command_run.run_command_request(cfg, "task-1", "myproj", 1, "deny")
    assert again == dispatcher_mod.CycleOutcome.BLOCKED
    assert _task(cfg).blocked_reason == handoff.COMMAND_REQUEST

    command_run.run_command_request(cfg, "task-1", "myproj", 1, "deny", comment="still no")
    assert [v["comment"] for v in _task(cfg).command_verdicts] == [None, "still no"]


# --- the mapping ------------------------------------------------------------


def test_a_mapping_resume_carries_on_to_the_arquitecto(tmp_path, monkeypatch) -> None:
    cfg = _make_config(
        tmp_path, mapping_enabled=True, mapping_model="haiku", mapping_max_turns=12,
    )
    _fake_index(monkeypatch, exists=False)
    phases = _Phases({project_docs.MAPPER_ROLE: [
        _mapper_return([_request(argv=("npm", "ci"))]), _mapper_return([]),
    ]})
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", phases)
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)
    phases.calls.clear()
    _exec(monkeypatch, stdout="added 3 packages\n")

    outcome = command_run.run_command_request(cfg, "task-1", "myproj", 1, "run")

    resume = phases.calls[0]
    assert resume["role"] == project_docs.MAPPER_ROLE
    assert resume["resume_session_id"] == "sess-cartografo-1"
    assert resume["model"] == "haiku" and resume["max_turns"] == 12
    assert phases.roles()[1:] == ["arquitecto", "implementador", "revisor", "auditor"]
    assert outcome == dispatcher_mod.CycleOutcome.FINISHED


def _mapping_blocked_on_changes(tmp_path, monkeypatch, no_account_on_resume=False):
    """A request-changes cycle (ADR 272) whose cartografo blocked on a request."""
    cfg = _make_config(tmp_path, mapping_enabled=True)
    _fake_index(monkeypatch, exists=False)
    phases = _Phases({project_docs.MAPPER_ROLE: [
        _mapper_return([_request(argv=("npm", "ci"))]), _mapper_return([]),
    ]}, no_account_on_resume=no_account_on_resume)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", phases)
    note = dispatcher_mod.changes_requested_note(
        "a" * 40, [{"author": "operator", "text": "Rename it."}],
    )
    dispatcher_mod.run_task_cycle(
        cfg, "task-1", "myproj", description=_DESCRIPTION, changes_requested=note,
    )
    assert phases.roles() == [project_docs.MAPPER_ROLE]
    envelope = context_transfer.read_handoff_envelope(
        cfg.hive_tasks_dir, "task-1", project_docs.MAPPER_ROLE,
    )
    assert envelope["changes_requested"] == note
    phases.calls.clear()
    return cfg, phases, note


def test_a_mapping_resume_keeps_the_requested_changes_for_the_arquitecto(tmp_path, monkeypatch) -> None:
    """The person's comments survive the block: the arquitecto after the
    resume reads them as the one the cycle started for would have (ADR 273)."""
    cfg, phases, note = _mapping_blocked_on_changes(tmp_path, monkeypatch)
    _exec(monkeypatch, stdout="added 3 packages\n")

    outcome = command_run.run_command_request(cfg, "task-1", "myproj", 1, "run")

    assert outcome == dispatcher_mod.CycleOutcome.FINISHED
    prompts = {call["role"]: call["prompt"] for call in reversed(phases.calls)}
    assert note in prompts["arquitecto"]
    assert all("Rename it." not in prompts[role] for role in ("implementador", "revisor", "auditor"))


def test_a_held_mapping_resume_keeps_the_requested_changes_too(tmp_path, monkeypatch) -> None:
    cfg, phases, note = _mapping_blocked_on_changes(tmp_path, monkeypatch, no_account_on_resume=True)
    _exec(monkeypatch, stdout="added 3 packages\n")
    assert command_run.run_command_request(cfg, "task-1", "myproj", 1, "run") == (
        dispatcher_mod.CycleOutcome.HELD
    )
    phases.calls.clear()
    phases.no_account_on_resume = False

    outcome = command_run.resume_command_verdict(cfg, "task-1", "myproj")

    assert outcome == dispatcher_mod.CycleOutcome.FINISHED
    arquitecto = [call["prompt"] for call in phases.calls if call["role"] == "arquitecto"]
    assert len(arquitecto) == 1 and note in arquitecto[0]


def test_a_cycle_with_no_request_for_changes_resumes_with_no_note(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path, mapping_enabled=True)
    _fake_index(monkeypatch, exists=False)
    phases = _Phases({project_docs.MAPPER_ROLE: [
        _mapper_return([_request(argv=("npm", "ci"))]), _mapper_return([]),
    ]})
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", phases)
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)
    envelope = context_transfer.read_handoff_envelope(
        cfg.hive_tasks_dir, "task-1", project_docs.MAPPER_ROLE,
    )
    assert "changes_requested" not in envelope
    phases.calls.clear()
    _exec(monkeypatch, stdout="added 3 packages\n")

    command_run.run_command_request(cfg, "task-1", "myproj", 1, "run")

    arquitecto = [call["prompt"] for call in phases.calls if call["role"] == "arquitecto"]
    assert "requested changes" not in arquitecto[0]


# --- the CLI ----------------------------------------------------------------


def _cli(monkeypatch, cfg, *argv):
    monkeypatch.setattr(cli, "load_config", lambda path: cfg)
    monkeypatch.setattr(sys, "argv", ["dispatch", "--config", "c.yaml", "command-verdict", *argv])
    cli.main()


def test_the_cli_refuses_with_the_blocked_exit_and_the_reason(tmp_path, monkeypatch, capsys) -> None:
    cfg, _ = _blocked(tmp_path, monkeypatch)
    _exec(monkeypatch)

    with pytest.raises(SystemExit) as exc:
        _cli(monkeypatch, cfg, "task-1", "4", "run", "--project", "myproj")

    assert exc.value.code == cli.EXIT_BLOCKED
    assert "verdict refused" in capsys.readouterr().err


def test_the_cli_deny_with_a_comment_finishes_the_cycle(tmp_path, monkeypatch, capsys) -> None:
    cfg, phases = _blocked(tmp_path, monkeypatch)
    _exec(monkeypatch)

    _cli(monkeypatch, cfg, "task-1", "1", "deny", "--project", "myproj", "--comment", "not now")

    assert "not now" in phases.calls[0]["prompt"]
    assert "the cycle finished" in capsys.readouterr().out


def test_the_log_header_is_json_argv(tmp_path, monkeypatch) -> None:
    cfg, _ = _blocked(tmp_path, monkeypatch, [_request(argv=("echo", "two words"))])
    _exec(monkeypatch)

    command_run.run_command_request(cfg, "task-1", "myproj", 1, "run")

    first = _log(cfg).splitlines()[0]
    assert json.loads(first.removeprefix("argv: ")) == ["echo", "two words"]


# --- the held resume (T-076, ADR 120) ----------------------------------------


def _held(tmp_path, monkeypatch, verdict="run", comment=None, **exec_kwargs):
    """A verdict whose resume no account took; the next resume finds one.

    Returns the prompt the held resume was given, with the phase and exec
    records cleared, so what follows counts only the resume.
    """
    cfg, phases = _blocked(tmp_path, monkeypatch)
    phases.no_account_on_resume = True
    fake = _exec(monkeypatch, **exec_kwargs)
    outcome = command_run.run_command_request(cfg, "task-1", "myproj", 1, verdict, comment=comment)
    assert outcome == dispatcher_mod.CycleOutcome.HELD
    task = _task(cfg)
    assert task.status == "blocked" and task.blocked_reason == handoff.COMMAND_REQUEST
    assert "resumed_at" not in task.command_verdicts[0]
    prompt = phases.calls[0]["prompt"]
    phases.calls.clear()
    fake.calls.clear()
    phases.no_account_on_resume = False
    return cfg, phases, fake, prompt


def _resume_refused(cfg, match):
    with pytest.raises(command_run.VerdictRefused, match=match):
        command_run.resume_command_verdict(cfg, "task-1", "myproj")


def test_a_verdict_that_resumed_records_resumed_at(tmp_path, monkeypatch) -> None:
    cfg, _ = _blocked(tmp_path, monkeypatch)
    _exec(monkeypatch, stdout="ok\n")

    command_run.run_command_request(cfg, "task-1", "myproj", 1, "run")

    verdict = _task(cfg).command_verdicts[0]
    assert verdict["resumed_at"]
    assert verdict["ran"]["exit_code"] == 0 and verdict["ran"]["timed_out"] is False
    assert verdict["ran"]["log"] == context_transfer.command_log_path(cfg.hive_tasks_dir, "task-1", 1)


def test_a_held_run_resumes_with_the_same_prompt_and_never_runs_again(tmp_path, monkeypatch) -> None:
    cfg, phases, fake, held_prompt = _held(tmp_path, monkeypatch, stdout="Resolved 12 packages\n")

    outcome = command_run.resume_command_verdict(cfg, "task-1", "myproj")

    assert fake.calls == []
    resume = phases.calls[0]
    assert resume["prompt"] == held_prompt
    assert "Resolved 12 packages" in resume["prompt"]
    assert resume["resume_session_id"] == "sess-implementador-2"
    assert resume["only_account"] == "cuenta1" and resume["round_num"] == 1
    assert phases.roles() == ["implementador", "revisor", "auditor"]
    assert outcome == dispatcher_mod.CycleOutcome.FINISHED
    verdicts = _task(cfg).command_verdicts
    assert len(verdicts) == 1 and verdicts[0]["resumed_at"]


@pytest.mark.parametrize("exec_kwargs", [
    dict(returncode=2, stdout="partial\n", stderr="error: no lockfile\n"),
    dict(timeout=True, stdout="still resolving\n"),
    dict(stdout="x" * (command_run.OUTPUT_CAP_BYTES + 1000)),
])
def test_a_held_run_rebuilds_exit_timeout_and_truncation_alike(tmp_path, monkeypatch, exec_kwargs) -> None:
    cfg, phases, fake, held_prompt = _held(tmp_path, monkeypatch, **exec_kwargs)

    command_run.resume_command_verdict(cfg, "task-1", "myproj")

    assert fake.calls == []
    assert phases.calls[0]["prompt"] == held_prompt


def test_a_held_deny_resumes_with_its_comment(tmp_path, monkeypatch) -> None:
    cfg, phases, fake, held_prompt = _held(tmp_path, monkeypatch, verdict="deny", comment="use the lockfile")

    command_run.resume_command_verdict(cfg, "task-1", "myproj")

    assert fake.calls == []
    assert phases.calls[0]["prompt"] == held_prompt
    assert "use the lockfile" in held_prompt


def test_a_resume_held_again_stays_resumable(tmp_path, monkeypatch) -> None:
    cfg, phases, fake, held_prompt = _held(tmp_path, monkeypatch)
    phases.no_account_on_resume = True

    assert command_run.resume_command_verdict(cfg, "task-1", "myproj") == dispatcher_mod.CycleOutcome.HELD
    assert command_run.awaits_resume(cfg, "task-1")
    phases.no_account_on_resume = False
    assert command_run.resume_command_verdict(cfg, "task-1", "myproj") == dispatcher_mod.CycleOutcome.FINISHED

    assert fake.calls == []
    assert [call["prompt"] for call in phases.calls[:2]] == [held_prompt, held_prompt]


def test_a_second_resume_is_refused(tmp_path, monkeypatch) -> None:
    cfg, phases, fake, _ = _held(tmp_path, monkeypatch)
    command_run.resume_command_verdict(cfg, "task-1", "myproj")
    phases.calls.clear()

    # The resumed phase moved the task on, which is the first guard.
    _resume_refused(cfg, "not blocked on a command request")
    assert fake.calls == [] and phases.calls == []


def test_a_verdict_already_resumed_is_refused_even_when_still_blocked(tmp_path, monkeypatch) -> None:
    cfg, phases, fake, _ = _held(tmp_path, monkeypatch)
    entry = _task(cfg).command_verdicts[0]
    dispatcher_mod.tasks.update_command_verdict(
        cfg.hive_tasks_dir, "task-1", entry["role"], entry["filed_at"], {"resumed_at": "2026-10-18T10:00:00+00:00"},
    )

    _resume_refused(cfg, "already resumed at 2026-10-18T10:00:00")
    assert fake.calls == [] and phases.calls == []


def test_a_resume_whose_phase_ran_and_failed_is_not_resumed_again(tmp_path, monkeypatch) -> None:
    """The lock was taken, so the session ran: resumed_at stays, whatever came of it."""
    cfg, phases, fake, _ = _held(tmp_path, monkeypatch)

    def failed(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, **kwargs):
        phases.calls.append(dict(role=role, prompt=prompt, resume_session_id=resume_session_id))
        return dispatcher_mod.DispatchResult(
            success=False, session_id="sess-x", result_text="claude timed out", account="cuenta1",
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", failed)

    assert command_run.resume_command_verdict(cfg, "task-1", "myproj") == dispatcher_mod.CycleOutcome.BLOCKED
    assert _task(cfg).command_verdicts[0]["resumed_at"]
    _resume_refused(cfg, "not blocked on a command request")
    assert len(phases.calls) == 1 and fake.calls == []


def test_a_rate_limit_inside_the_held_resume_puts_the_block_back(tmp_path, monkeypatch) -> None:
    """The pinned account took the lock, was refused, and gave it back before the hold."""
    cfg, phases = _blocked(tmp_path, monkeypatch)
    _exec(monkeypatch)

    def rate_limited(cfg_arg, task_id, slug, role, prompt, resume_session_id=None, **kwargs):
        dispatcher_mod.tasks.acquire_lock(cfg_arg.hive_tasks_dir, task_id, owner="cuenta1")
        dispatcher_mod.tasks.release_stale_lock(cfg_arg.hive_tasks_dir, task_id)
        return dispatcher_mod.DispatchResult(
            success=False, session_id=None, result_text="no account", account="", no_account=True,
        )

    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", rate_limited)

    assert command_run.run_command_request(cfg, "task-1", "myproj", 1, "deny") == dispatcher_mod.CycleOutcome.HELD
    task = _task(cfg)
    assert task.status == "blocked" and task.blocked_reason == handoff.COMMAND_REQUEST
    assert command_run.awaits_resume(cfg, "task-1")


def test_a_block_with_no_verdict_is_refused(tmp_path, monkeypatch) -> None:
    cfg, phases = _blocked(tmp_path, monkeypatch)
    fake = _exec(monkeypatch)

    _resume_refused(cfg, "has no verdict yet; give one with command-verdict")
    assert fake.calls == [] and phases.calls == []


def test_a_resume_on_a_task_not_blocked_is_refused(tmp_path, monkeypatch) -> None:
    cfg = _make_config(tmp_path)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", _Phases({}))
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)

    _resume_refused(cfg, "not blocked on a command request")


def test_a_resume_with_no_session_on_record_is_refused(tmp_path, monkeypatch) -> None:
    cfg, phases, fake, _ = _held(tmp_path, monkeypatch)
    path = context_transfer.handoff_path(cfg.hive_tasks_dir, "task-1", "implementador")
    with open(path) as handle:
        envelope = json.load(handle)
    envelope["session_id"] = envelope["account"] = None
    with open(path, "w") as handle:
        json.dump(envelope, handle)

    _resume_refused(cfg, "session is not on record")
    assert fake.calls == [] and phases.calls == []


def _drop_ran(cfg):
    task = _task(cfg)
    task.command_verdicts = [{k: v for k, v in item.items() if k != "ran"} for item in task.command_verdicts]
    context_transfer.write_task_file(task_file_path(cfg.hive_tasks_dir, "task-1"), task)


@pytest.mark.parametrize("exec_kwargs", [
    dict(stdout="Resolved 12 packages\n"),
    dict(returncode=2, stdout="partial\n", stderr="error: no lockfile\n"),
    dict(timeout=True, stdout="still resolving\n"),
    dict(stdout="x" * (command_run.OUTPUT_CAP_BYTES + 1000)),
])
def test_an_entry_without_ran_is_rebuilt_from_its_log(tmp_path, monkeypatch, exec_kwargs) -> None:
    """A T-074 entry, or one whose run stopped between its log and `ran`."""
    cfg, phases, fake, held_prompt = _held(tmp_path, monkeypatch, **exec_kwargs)
    _drop_ran(cfg)

    command_run.resume_command_verdict(cfg, "task-1", "myproj")

    assert fake.calls == []
    assert phases.calls[0]["prompt"] == held_prompt


def test_a_run_verdict_with_no_log_is_refused_and_not_run(tmp_path, monkeypatch) -> None:
    cfg, phases, fake, _ = _held(tmp_path, monkeypatch)
    _drop_ran(cfg)
    import os
    os.remove(context_transfer.command_log_path(cfg.hive_tasks_dir, "task-1", 1))

    _resume_refused(cfg, "no readable log")
    assert fake.calls == [] and phases.calls == []


def test_a_log_for_another_argv_is_refused(tmp_path, monkeypatch) -> None:
    cfg, phases, fake, _ = _held(tmp_path, monkeypatch)
    _drop_ran(cfg)
    context_transfer.save_command_log(
        cfg.hive_tasks_dir, "task-1", 1, 'argv: ["make"]\ntarget: agent c\ncwd: /w\nexit: 0\n--- output ---\n',
    )

    _resume_refused(cfg, "no readable log")
    assert fake.calls == [] and phases.calls == []


def test_a_held_mapping_resume_stays_blocked_and_resumes_on_to_the_arquitecto(tmp_path, monkeypatch) -> None:
    """Pinned and found taken, the cartografo is held, not an optional phase that failed."""
    cfg = _make_config(tmp_path, mapping_enabled=True)
    _fake_index(monkeypatch, exists=False)
    phases = _Phases({project_docs.MAPPER_ROLE: [
        _mapper_return([_request(argv=("npm", "ci"))]), _mapper_return([]),
    ]}, no_account_on_resume=True)
    monkeypatch.setattr(dispatcher_mod, "dispatch_phase", phases)
    dispatcher_mod.run_task_cycle(cfg, "task-1", "myproj", description=_DESCRIPTION)
    phases.calls.clear()
    fake = _exec(monkeypatch, stdout="added 3 packages\n")

    held = command_run.run_command_request(cfg, "task-1", "myproj", 1, "run")

    assert held == dispatcher_mod.CycleOutcome.HELD
    assert phases.roles() == [project_docs.MAPPER_ROLE]
    task = _task(cfg)
    assert task.status == "blocked" and task.blocked_reason == handoff.COMMAND_REQUEST
    held_prompt = phases.calls[0]["prompt"]
    phases.calls.clear()
    fake.calls.clear()
    phases.no_account_on_resume = False

    outcome = command_run.resume_command_verdict(cfg, "task-1", "myproj")

    # The later phases read the project's docs through the same exec; npm ci is never among them.
    assert [call for call in fake.calls if call["command"][:2] == ["npm", "ci"]] == []
    assert phases.calls[0]["prompt"] == held_prompt
    assert phases.calls[0]["resume_session_id"] == "sess-cartografo-1"
    assert phases.roles()[1:] == ["arquitecto", "implementador", "revisor", "auditor"]
    assert outcome == dispatcher_mod.CycleOutcome.FINISHED
    assert _task(cfg).command_verdicts[0]["resumed_at"]


def _resume_cli(monkeypatch, cfg, *argv):
    monkeypatch.setattr(cli, "load_config", lambda path: cfg)
    monkeypatch.setattr(sys, "argv", ["dispatch", "--config", "c.yaml", "command-resume", *argv])
    cli.main()


def test_the_cli_held_verdict_points_to_command_resume(tmp_path, monkeypatch, capsys) -> None:
    cfg, phases = _blocked(tmp_path, monkeypatch)
    phases.no_account_on_resume = True
    _exec(monkeypatch)

    with pytest.raises(SystemExit) as exc:
        _cli(monkeypatch, cfg, "task-1", "1", "deny", "--project", "myproj")

    assert exc.value.code == cli.EXIT_HELD
    assert "command-resume task-1 --project myproj" in capsys.readouterr().err


def test_the_cli_resume_exits_held_refused_and_finished(tmp_path, monkeypatch, capsys) -> None:
    cfg, phases, fake, _ = _held(tmp_path, monkeypatch)
    capsys.readouterr()

    phases.no_account_on_resume = True
    with pytest.raises(SystemExit) as exc:
        _resume_cli(monkeypatch, cfg, "task-1", "--project", "myproj")
    assert exc.value.code == cli.EXIT_HELD
    assert "command-resume task-1" in capsys.readouterr().err

    phases.no_account_on_resume = False
    _resume_cli(monkeypatch, cfg, "task-1", "--project", "myproj")
    assert "the verdict was resumed; the cycle finished" in capsys.readouterr().out

    with pytest.raises(SystemExit) as exc:
        _resume_cli(monkeypatch, cfg, "task-1", "--project", "myproj")
    assert exc.value.code == cli.EXIT_BLOCKED
    assert "resume refused" in capsys.readouterr().err
    assert fake.calls == []
