"""The *allow* verdict and the per-project, per-role allowlist
(`docs/plans/command-requests.md` step 5; `docs/decisions.md` ADR 121)."""

import json
import logging
import os
import sys

import pytest

import dispatcher.dispatcher as dispatcher_mod
from dispatcher import allowlist, cli, command_run
from dispatcher.config import AccountConfig
from dispatcher.docker_exec import ClaudeResult
from tests.dispatcher.test_command_requests import _request
from tests.dispatcher.test_command_run import (
    _blocked,
    _exec,
    _held,
    _task,
    _worktree,
)
from tests.dispatcher.test_dispatcher import (  # noqa: F401 - fixtures used by name
    _USAGE_TEXT,
    _fake_index,
    _make_config,
    fake_git,
)


def _file(cfg, slug="myproj"):
    return os.path.join(cfg.state_dir, allowlist.STATE_SUBDIR, f"{slug}.json")


def _stored(cfg, slug="myproj"):
    with open(_file(cfg, slug)) as handle:
        return json.load(handle)


def _source(n=1):
    return {"task_id": "task-1", "role": "implementador", "n": n, "filed_at": "2026-10-18T00:00:00+00:00"}


# --- allow ------------------------------------------------------------------


def test_allow_runs_once_writes_the_exact_entry_and_tells_the_phase(tmp_path, monkeypatch) -> None:
    cfg, phases = _blocked(tmp_path, monkeypatch)
    fake = _exec(monkeypatch, stdout="Resolved 12 packages\n")

    outcome = command_run.run_command_request(cfg, "task-1", "myproj", 1, "allow")

    assert outcome == dispatcher_mod.CycleOutcome.FINISHED
    assert fake.calls == [dict(
        container="agent-cuenta1", workdir=_worktree(cfg), command=["uv", "sync"],
        timeout=command_run.COMMAND_TIMEOUT_SECONDS,
    )]
    stored = _stored(cfg)
    assert list(stored) == ["implementador"]
    [entry] = stored["implementador"]
    assert entry["pattern"] == "Bash(uv sync)"
    assert entry["by"] == "operator" and entry["at"].endswith("+00:00")
    assert entry["from"]["task_id"] == "task-1" and entry["from"]["role"] == "implementador"
    assert entry["from"]["n"] == 1 and entry["from"]["filed_at"]
    prompt = phases.calls[0]["prompt"]
    assert "Resolved 12 packages" in prompt
    assert "allowed it for your role in this project as Bash(uv sync)" in prompt
    [verdict] = _task(cfg).command_verdicts
    assert verdict["verdict"] == "allow" and verdict["allowed"] == "Bash(uv sync)"
    assert verdict["resumed_at"]


def test_allow_with_a_prefix_writes_the_prefix_entry(tmp_path, monkeypatch) -> None:
    cfg, phases = _blocked(tmp_path, monkeypatch, [_request(argv=("uv", "run", "pytest", "-q"))])
    fake = _exec(monkeypatch)

    command_run.run_command_request(cfg, "task-1", "myproj", 1, "allow", prefix=3)

    assert len(fake.calls) == 1 and fake.calls[0]["command"] == ["uv", "run", "pytest", "-q"]
    assert [e["pattern"] for e in _stored(cfg)["implementador"]] == ["Bash(uv run pytest:*)"]
    assert "Bash(uv run pytest:*)" in phases.calls[0]["prompt"]


def test_allowing_the_same_command_twice_keeps_one_entry(tmp_path) -> None:
    state = str(tmp_path)
    first = allowlist.add(state, "myproj", "implementador", "Bash(uv sync)", "operator", "t1", _source(1))
    again = allowlist.add(state, "myproj", "implementador", "Bash(uv sync)", "operator", "t2", _source(2))

    assert again == first
    assert len(allowlist.entries(state, "myproj")["implementador"]) == 1


# --- refusals: nothing written, nothing run ---------------------------------


def _refused_allow(cfg, phases, fake, match, prefix=None, verdict="allow"):
    with pytest.raises(command_run.VerdictRefused, match=match):
        command_run.run_command_request(cfg, "task-1", "myproj", 1, verdict, prefix=prefix)
    assert fake.calls == [] and phases.calls == []
    assert _task(cfg).command_verdicts == []
    assert not os.path.exists(_file(cfg))


def test_an_argv_no_pattern_can_hold_is_refused(tmp_path, monkeypatch) -> None:
    cfg, phases = _blocked(tmp_path, monkeypatch, [_request(argv=("echo", "two words"))])
    fake = _exec(monkeypatch)
    _refused_allow(cfg, phases, fake, "cannot stand on the allowlist")


@pytest.mark.parametrize("argv,prefix", [
    (("python", "-m", "pytest"), 1),
    (("git", "status"), 1),
    (("uv", "run", "pytest"), 2),
    (("uv", "run", "pytest"), 1),
])
def test_a_prefix_that_could_cover_a_denied_command_is_refused(tmp_path, monkeypatch, argv, prefix) -> None:
    cfg, phases = _blocked(tmp_path, monkeypatch, [_request(argv=argv)])
    fake = _exec(monkeypatch)
    _refused_allow(cfg, phases, fake, "cannot stand on the allowlist", prefix=prefix)


def test_a_prefix_longer_than_the_argv_is_refused(tmp_path, monkeypatch) -> None:
    cfg, phases = _blocked(tmp_path, monkeypatch)
    fake = _exec(monkeypatch)
    _refused_allow(cfg, phases, fake, "cannot stand on the allowlist", prefix=3)


def test_allow_is_refused_for_a_command_that_is_not_the_phases_own(tmp_path, monkeypatch) -> None:
    account = AccountConfig(name="cuenta1", container="agent-cuenta1", dind="dind-cuenta1")
    cfg, phases = _blocked(
        tmp_path, monkeypatch, [_request(kind="dind", argv=("docker", "ps"))], accounts=[account],
    )
    fake = _exec(monkeypatch)
    _refused_allow(cfg, phases, fake, "Give run instead")


def test_a_prefix_with_run_is_refused(tmp_path, monkeypatch) -> None:
    cfg, phases = _blocked(tmp_path, monkeypatch)
    fake = _exec(monkeypatch)
    _refused_allow(cfg, phases, fake, "prefix", prefix=1, verdict="run")


@pytest.mark.parametrize("argv,prefix", [
    (["git", "push"], None),
    (["git", "push", "origin"], 2),
    (["git"], 1),
    (["git", "status"], 1),
    (["sudo", "ls"], None),
    (["bash", "-c", "ls"], None),
    (["sh", "x.sh"], 1),
    (["docker", "ps"], None),
    (["python", "-c", "print"], 2),
    (["git", "push", "--force"], None),
    (["python3", "-m", "pytest"], 2),
    (["env", "ls"], 1),
    (["env", "ls", "-la"], 2),
    (["xargs", "rm"], 2),
    (["uv", "run"], 2),
    (["uv", "run", "python"], 3),
    (["npx", "jest"], 2),
    (["echo", "'quoted'"], None),
    (["echo", '"quoted"'], None),
    (["echo", "(x)"], None),
    (["echo", "a:*"], None),
    (["echo", "a;b"], None),
    (["echo", "$HOME"], None),
    (["echo", "a|b"], None),
    (["echo", ""], None),
    ([], None),
    (["A=1", "make"], None),
    (["make", "--jobs=4"], 2),
])
def test_pattern_for_refuses(argv, prefix) -> None:
    with pytest.raises(allowlist.AllowRefused):
        allowlist.pattern_for(argv, prefix)


@pytest.mark.parametrize("argv,prefix,pattern", [
    (["uv", "sync"], None, "Bash(uv sync)"),
    (["git", "status"], None, "Bash(git status)"),
    (["git", "status"], 2, "Bash(git status:*)"),
    (["uv", "run", "pytest", "-q"], 3, "Bash(uv run pytest:*)"),
    (["npm", "test"], 2, "Bash(npm test:*)"),
    (["node", "--test"], None, "Bash(node --test)"),
])
def test_pattern_for_writes(argv, prefix, pattern) -> None:
    assert allowlist.pattern_for(argv, prefix) == pattern


def test_add_refuses_a_bad_pattern_and_writes_nothing(tmp_path) -> None:
    with pytest.raises(allowlist.AllowRefused):
        allowlist.add(str(tmp_path), "myproj", "implementador", "Bash(git:*)", "operator", "t", _source())
    assert not os.path.exists(os.path.join(str(tmp_path), allowlist.STATE_SUBDIR, "myproj.json"))


def test_a_slug_that_is_not_one_path_component_is_refused(tmp_path) -> None:
    with pytest.raises(ValueError):
        allowlist.add(str(tmp_path), "../x", "implementador", "Bash(uv sync)", "operator", "t", _source())


# --- the merge --------------------------------------------------------------


def _seed(state):
    allowlist.add(state, "myproj", "implementador", "Bash(uv sync)", "operator", "t", _source())
    allowlist.add(state, "myproj", "implementador", "Bash(npm test:*)", "operator", "t", _source(2))
    allowlist.add(state, "myproj", "revisor", "Bash(make lint)", "operator", "t", _source(3))
    allowlist.add(state, "other", "implementador", "Bash(cargo build)", "operator", "t", _source(4))


def test_the_merge_puts_the_global_list_first_and_repeats_nothing(tmp_path) -> None:
    state = str(tmp_path)
    _seed(state)
    base = ["Bash(npm test:*)", "Read"]

    merged = allowlist.merged_allowed_tools(state, "myproj", "implementador", base)

    assert merged == ["Bash(npm test:*)", "Read", "Bash(uv sync)"]
    assert base == ["Bash(npm test:*)", "Read"]


def test_the_merge_takes_only_the_matching_project_and_role(tmp_path) -> None:
    state = str(tmp_path)
    _seed(state)

    assert allowlist.merged_allowed_tools(state, "myproj", "revisor", []) == ["Bash(make lint)"]
    assert allowlist.merged_allowed_tools(state, "other", "implementador", []) == ["Bash(cargo build)"]
    assert allowlist.merged_allowed_tools(state, "myproj", "auditor", ["X"]) == ["X"]
    assert allowlist.merged_allowed_tools(state, "nope", "implementador", []) == []
    assert allowlist.merged_allowed_tools(state, None, "implementador", ["X"]) == ["X"]


def test_a_bad_stored_entry_is_skipped_and_logged(tmp_path, caplog) -> None:
    state = str(tmp_path)
    allowlist.add(state, "myproj", "implementador", "Bash(uv sync)", "operator", "t", _source())
    path = os.path.join(state, allowlist.STATE_SUBDIR, "myproj.json")
    with open(path) as handle:
        data = json.load(handle)
    data["implementador"][:0] = [
        {"pattern": "Bash(git:*)"}, {"pattern": "Bash(sudo ls)"}, {"pattern": 7}, {"nope": 1},
    ]
    with open(path, "w") as handle:
        json.dump(data, handle)

    with caplog.at_level(logging.WARNING):
        merged = allowlist.merged_allowed_tools(state, "myproj", "implementador", [])

    assert merged == ["Bash(uv sync)"]
    assert "Bash(git:*)" in caplog.text and "Bash(sudo ls)" in caplog.text


def test_a_corrupt_file_adds_nothing(tmp_path, caplog) -> None:
    os.makedirs(os.path.join(str(tmp_path), allowlist.STATE_SUBDIR))
    with open(os.path.join(str(tmp_path), allowlist.STATE_SUBDIR, "myproj.json"), "w") as handle:
        handle.write("{not json")

    with caplog.at_level(logging.WARNING):
        assert allowlist.merged_allowed_tools(str(tmp_path), "myproj", "implementador", ["X"]) == ["X"]
    assert caplog.text


def test_the_phase_flags_merge_with_a_slug_and_leave_the_global_list_alone(tmp_path) -> None:
    cfg = _make_config(tmp_path, allowed_tools=["Bash(node --test*)"])
    allowlist.add(cfg.state_dir, "myproj", "implementador", "Bash(uv sync)", "operator", "t", _source())

    with_slug = dispatcher_mod._phase_permission_flags(cfg, "implementador", "myproj")
    without = dispatcher_mod._phase_permission_flags(cfg, "implementador")
    other_role = dispatcher_mod._phase_permission_flags(cfg, "revisor", "myproj")

    assert with_slug["allowed_tools"] == ["Bash(node --test*)", "Bash(uv sync)"]
    assert without["allowed_tools"] == ["Bash(node --test*)"]
    assert other_role["allowed_tools"] == ["Bash(node --test*)"]
    assert cfg.allowed_tools == ["Bash(node --test*)"]


def _claude_calls(monkeypatch):
    calls = []

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        if "usage" in prompt.lower():
            return ClaudeResult(session_id=None, result_text=_USAGE_TEXT, raw={})
        calls.append(dict(kwargs, resume_session_id=resume_session_id))
        return ClaudeResult(session_id="sess-1", result_text="ok", raw={"is_error": False})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    monkeypatch.setattr(
        dispatcher_mod.docker_exec, "create_worktree",
        lambda container, projects_root, slug, task_id, role: f"{projects_root}/{slug}/worktrees/{task_id}",
    )
    return calls


@pytest.mark.parametrize("resume", [None, "sess-0"])
def test_a_phase_and_its_resume_get_the_merged_list(tmp_path, monkeypatch, resume) -> None:
    cfg = _make_config(tmp_path, allowed_tools=["Bash(node --test*)"])
    allowlist.add(cfg.state_dir, "myproj", "revisor", "Bash(make lint)", "operator", "t", _source())
    calls = _claude_calls(monkeypatch)

    dispatcher_mod.dispatch_phase(
        cfg, "task-1", "myproj", "revisor", "review the thing", resume_session_id=resume,
    )

    assert calls[0]["resume_session_id"] == resume
    assert calls[0]["allowed_tools"] == ["Bash(node --test*)", "Bash(make lint)"]


def test_a_held_allow_resumes_with_the_allowed_line_and_runs_nothing_again(tmp_path, monkeypatch) -> None:
    cfg, phases, fake, held_prompt = _held(tmp_path, monkeypatch, verdict="allow", stdout="ok\n")

    command_run.resume_command_verdict(cfg, "task-1", "myproj")

    assert fake.calls == []
    assert phases.calls[0]["prompt"] == held_prompt
    assert "as Bash(uv sync)" in held_prompt
    assert len(_stored(cfg)["implementador"]) == 1
    assert len(_task(cfg).command_verdicts) == 1


# --- the CLI ----------------------------------------------------------------


def _main(monkeypatch, cfg, *argv):
    monkeypatch.setattr(cli, "load_config", lambda path: cfg)
    monkeypatch.setattr(sys, "argv", ["dispatch", "--config", "c.yaml", *argv])
    cli.main()


def test_the_cli_allow_with_a_prefix(tmp_path, monkeypatch, capsys) -> None:
    cfg, phases = _blocked(tmp_path, monkeypatch, [_request(argv=("npm", "test", "--", "x"))])
    fake = _exec(monkeypatch)

    _main(monkeypatch, cfg, "command-verdict", "task-1", "1", "allow", "--project", "myproj", "--prefix", "2")

    assert len(fake.calls) == 1
    assert [e["pattern"] for e in _stored(cfg)["implementador"]] == ["Bash(npm test:*)"]


@pytest.mark.parametrize("extra", [["run", "--prefix", "1"], ["allow", "--prefix", "0"]])
def test_the_cli_refuses_a_bad_prefix_before_anything_runs(tmp_path, monkeypatch, extra) -> None:
    cfg, phases = _blocked(tmp_path, monkeypatch)
    fake = _exec(monkeypatch)

    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, cfg, "command-verdict", "task-1", "1", *extra, "--project", "myproj")

    assert exc.value.code == 2
    assert fake.calls == [] and phases.calls == [] and not os.path.exists(_file(cfg))


def test_the_cli_lists_and_revokes(tmp_path, monkeypatch, capsys) -> None:
    cfg = _make_config(tmp_path)
    _main(monkeypatch, cfg, "allowlist", "--project", "myproj")
    assert "myproj: no allowlist entries" in capsys.readouterr().out

    allowlist.add(cfg.state_dir, "myproj", "implementador", "Bash(uv sync)", "operator", "T0", _source())
    allowlist.add(cfg.state_dir, "myproj", "revisor", "Bash(make lint)", "operator", "T1", _source(2))

    _main(monkeypatch, cfg, "allowlist", "--project", "myproj")
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "implementador\tBash(uv sync)\tby operator at T0\tfrom task-1 request 1",
        "revisor\tBash(make lint)\tby operator at T1\tfrom task-1 request 2",
    ]

    _main(monkeypatch, cfg, "allowlist", "--project", "myproj", "--role", "revisor")
    assert capsys.readouterr().out.splitlines() == [out[1]]

    _main(monkeypatch, cfg, "allowlist", "--project", "myproj", "--role", "revisor", "--revoke", "Bash(make lint)")
    assert "revoked Bash(make lint) for the revisor" in capsys.readouterr().out
    assert "revisor" not in _stored(cfg)
    assert dispatcher_mod._phase_permission_flags(cfg, "revisor", "myproj")["allowed_tools"] == []

    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, cfg, "allowlist", "--project", "myproj", "--role", "revisor", "--revoke", "Bash(make lint)")
    assert exc.value.code == 1
    assert "has no allowlist entry" in capsys.readouterr().err

    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, cfg, "allowlist", "--project", "myproj", "--revoke", "Bash(uv sync)")
    assert exc.value.code == 2
    assert len(_stored(cfg)["implementador"]) == 1
