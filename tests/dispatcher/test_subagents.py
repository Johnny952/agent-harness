import json
import logging
import subprocess

from dispatcher import subagents

#: The shape the CLI writes: a uuid, and a 17-hex-character agent id. Real
#: ones, from the D1 run recorded in docs/ROADMAP.md.
_SESSION = "85e10326-e62c-48de-be2f-9a7c92741789"
_AGENT = "a0af9044f9cb2c8db"

#: The whole meta file, as the CLI writes it next to the sidechain transcript.
_META = {
    "agentType": "general-purpose",
    "description": "slow count",
    "toolUseId": "toolu_01SPQTLybPpSCcQsW7xmS8C4",
    "spawnDepth": 1,
    "requestShape": "background",
    "requestNonInteractive": True,
}


def _line(agent_id: str, meta: dict | str) -> str:
    """One line of the harvest script's output: filename, TAB, the file."""
    blob = meta if isinstance(meta, str) else json.dumps(meta)
    return f"agent-{agent_id}.meta.json\t{blob}"


def _recorder(monkeypatch, stdout: str = "", returncode: int = 0):
    calls = []

    def fake_run_docker_exec(container, workdir, command, env=None, timeout=None):
        calls.append(dict(container=container, workdir=workdir, command=command, timeout=timeout))
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(subagents.docker_exec, "run_docker_exec", fake_run_docker_exec)
    return calls


def _raiser(monkeypatch, exc: Exception):
    def fake_run_docker_exec(container, workdir, command, env=None, timeout=None):
        raise exc

    monkeypatch.setattr(subagents.docker_exec, "run_docker_exec", fake_run_docker_exec)


def test_a_phase_that_never_got_a_session_is_not_looked_up(monkeypatch) -> None:
    """The first call of a phase has no session id, and most phases never get
    a second. Reaching into the container for it would be one `docker exec`
    per dispatch, answering nothing."""
    calls = _recorder(monkeypatch)

    assert subagents.of_session("agent-cuenta1", None) == []
    assert subagents.of_session("agent-cuenta1", "") == []
    assert calls == []


def test_something_that_is_not_a_session_id_is_refused_and_said_out_loud(monkeypatch, caplog) -> None:
    """The id is interpolated into an `sh -c` string, so it is checked against
    the shape the CLI writes before it gets there. It is never operator input,
    so one that does not look like a uuid is a bug upstream worth a line in
    the log — not a glob to be creative with."""
    calls = _recorder(monkeypatch)

    with caplog.at_level(logging.WARNING):
        assert subagents.of_session("agent-cuenta1", "sess-1; rm -rf /") == []

    assert calls == []
    assert "not a session id" in caplog.text


def test_the_harvest_globs_the_cwd_and_names_the_session(monkeypatch) -> None:
    """The component between the projects root and the session id is the CLI's
    encoding of the cwd, which differs per worktree; globbing it beats
    reproducing the encoding, because the session id under it is unique."""
    calls = _recorder(monkeypatch)

    subagents.of_session("agent-cuenta1", _SESSION)

    assert len(calls) == 1
    call = calls[0]
    assert call["container"] == "agent-cuenta1"
    assert call["command"][:2] == ["sh", "-c"]
    assert call["timeout"] == subagents._HARVEST_TIMEOUT_SECONDS
    script = call["command"][2]
    assert f"{subagents.PROJECTS_ROOT}/*/{_SESSION}/subagents/agent-*.meta.json" in script


def test_the_id_comes_from_the_filename_and_the_rest_from_the_file(monkeypatch) -> None:
    _recorder(monkeypatch, stdout=_line(_AGENT, _META) + "\n")

    assert subagents.of_session("agent-cuenta1", _SESSION) == [
        subagents.Subagent(id=_AGENT, description="slow count", agent_type="general-purpose")
    ]


def test_an_unreadable_meta_file_costs_the_description_not_the_agent(monkeypatch) -> None:
    """The id is the one thing a revive actually needs, and it is in the
    filename. A truncated file is still an agent worth naming."""
    _recorder(monkeypatch, stdout="\n".join([_line(_AGENT, '{"description": "slow c'), ""]))

    assert subagents.of_session("agent-cuenta1", _SESSION) == [
        subagents.Subagent(id=_AGENT, description="", agent_type="")
    ]


def test_output_that_is_not_a_harvest_line_is_skipped(monkeypatch) -> None:
    """A shell that printed something of its own (a warning, an unmatched
    glob) must not turn into an agent id the note then tells a role to
    message."""
    _recorder(
        monkeypatch,
        stdout="\n".join(
            [
                "sh: something to say",
                f"{subagents.PROJECTS_ROOT}/*/{_SESSION}/subagents/agent-*.meta.json",
                _line("", _META),
                _line(_AGENT, _META),
            ]
        ),
    )

    assert [agent.id for agent in subagents.of_session("agent-cuenta1", _SESSION)] == [_AGENT]


def test_the_agents_come_back_in_a_stable_order(monkeypatch) -> None:
    """Sorted, not in glob order: the note goes into a prompt, and a list that
    reshuffles between two calls of the same phase is a diff nobody made."""
    _recorder(
        monkeypatch,
        stdout="\n".join([_line("ccc", _META), _line("aaa", _META), _line("bbb", _META)]),
    )

    assert [a.id for a in subagents.of_session("agent-cuenta1", _SESSION)] == ["aaa", "bbb", "ccc"]


def test_a_failed_harvest_is_swallowed(monkeypatch, caplog) -> None:
    """Like the gates: an id the dispatcher could not read costs a respawn,
    which is what happened before this existed. It must never cost a phase."""
    _recorder(monkeypatch, stdout="", returncode=1)

    with caplog.at_level(logging.WARNING):
        assert subagents.of_session("agent-cuenta1", _SESSION) == []

    assert "exited 1" in caplog.text


def test_a_container_that_does_not_answer_is_swallowed(monkeypatch, caplog) -> None:
    _raiser(monkeypatch, subprocess.TimeoutExpired(cmd="docker exec", timeout=30))

    with caplog.at_level(logging.ERROR):
        assert subagents.of_session("agent-cuenta1", _SESSION) == []

    assert "could not read the subagents" in caplog.text


def test_no_subagents_no_note() -> None:
    """A phase that delegated nothing gets nothing appended to its prompt:
    every byte of it is paid for on the resume."""
    assert subagents.revive_note([]) == ""


def test_the_note_names_the_tool_and_the_raw_id() -> None:
    note = subagents.revive_note(
        [
            subagents.Subagent(id=_AGENT, description="slow count", agent_type="general-purpose"),
            subagents.Subagent(id="b1", description="", agent_type="general-purpose"),
        ]
    )

    assert "SendMessage" in note
    assert "`to`" in note and "`recipient`" in note
    assert f"- `{_AGENT}` — slow count" in note
    # No description on disk is a bare id, not a dangling dash.
    assert "- `b1`\n" in note or note.endswith("- `b1`")
    # The reason the role should prefer this to a fresh one has to travel with
    # the instruction; the role has no other way to know a revive keeps context.
    assert "instructions" in note


def test_the_ids_in_the_handoff_come_from_disk() -> None:
    """Not from the phase: the Agent tool tells it not to repeat an agent id,
    so whatever it put here is either empty or invented."""
    payload = {"status": "partial", "subagents": [{"id": "ag_42", "doing": "something"}]}

    filled = subagents.backfill(
        payload, [subagents.Subagent(id=_AGENT, description="slow count", agent_type="general-purpose")]
    )

    assert filled["subagents"] == [{"id": _AGENT, "doing": "slow count"}]
    assert filled["status"] == "partial"
    assert payload["subagents"] == [{"id": "ag_42", "doing": "something"}], "the parsed payload is not edited in place"


def test_nothing_on_disk_leaves_the_handoff_alone() -> None:
    payload = {"status": "complete", "subagents": []}

    assert subagents.backfill(payload, []) is payload


def test_a_phase_that_answered_in_prose_has_no_handoff_to_fill() -> None:
    agents = [subagents.Subagent(id=_AGENT, description="slow count", agent_type="general-purpose")]

    assert subagents.backfill(None, agents) is None
