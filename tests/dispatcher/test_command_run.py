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
    _refused(cfg, 1, "allow", "not one of")


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
