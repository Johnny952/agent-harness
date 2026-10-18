"""`GET /api/command-requests`, against a temporary hive (ADR 122).

The mounts and the client are `test_api.py`'s `_harness`. Envelopes are
written through `save_handoff`, the dispatcher's own writer, and verdicts as
the `command_verdicts` records `dispatcher/command_run.py` keeps, so the rows
here are read the way the CLI's own verdict and resume read them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dispatcher import context_transfer
from observability.api import app as api_app
from tests.observability.test_api import _get, _harness, _task
from tests.observability.test_roadmap import _data, _raw_task

PATH = "/api/command-requests"


def _request(argv: list[str], kind: str = "agent", service: str | None = None) -> dict:
    resolved = {"kind": kind, "container": f"{kind}-box", "cwd": "/workspace"}
    if service is not None:
        resolved["service"] = service
    target = {"kind": kind} if service is None else {"kind": kind, "service": service}
    return {
        "argv": argv,
        "reason": f"to check {argv[0]}",
        "target": target,
        "cwd": None,
        "resolved": resolved,
    }


def _block(
    harness,
    task_id: str,
    role: str = "implementador",
    accepted: list[dict] | None = None,
    refused: list[dict] | None = None,
    **extra,
) -> str:
    """One block of requests in `role`'s envelope; its `saved_at`, the block's `filed_at`."""
    context_transfer.save_handoff(
        harness.tasks_dir,
        task_id,
        role,
        {"summary": "blocked"},
        command_requests={"accepted": accepted or [], "refused": refused or []},
        **extra,
    )
    return context_transfer.read_handoff_envelope(harness.tasks_dir, task_id, role)["saved_at"]


def _blocked(harness, task_id: str, verdicts: list[dict] | None = None) -> None:
    _task(
        harness.tasks_dir,
        task_id,
        status="blocked",
        blocked_reason="command_request",
        command_verdicts=verdicts or [],
    )


def _verdict(
    filed_at: str, n: int = 1, verdict: str = "run", role: str = "implementador", **extra
) -> dict:
    return {
        "n": n,
        "verdict": verdict,
        "by": "operator",
        "at": "2026-10-18T10:00:00+00:00",
        "comment": None,
        "role": role,
        "filed_at": filed_at,
        **extra,
    }


def _ran(harness, task_id: str, n: int = 1, exit_code: int = 0) -> dict:
    return {
        "exit_code": exit_code,
        "timed_out": False,
        "truncated": True,
        "log": context_transfer.command_log_path(harness.tasks_dir, task_id, n),
        "resolved": {"kind": "agent", "container": "agent-box", "cwd": "/workspace"},
    }


def test_a_block_with_no_verdict_is_pending_a_row_per_request(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    filed_at = _block(
        harness,
        "T-1",
        accepted=[_request(["pytest", "-q"]), _request(["make", "up"], kind="compose", service="db")],
    )
    _blocked(harness, "T-1")

    data, warnings = _data(harness, PATH)

    assert warnings == []
    assert data["refused"] == []
    first, second = data["requests"]
    assert first == {
        "task_id": "T-1",
        "project": "ia-harness",
        "title": None,
        "role": "implementador",
        "filed_at": filed_at,
        "n": 1,
        "argv": ["pytest", "-q"],
        "target": {"kind": "agent", "container": "agent-box", "cwd": "/workspace", "service": None},
        "agent_reason": "to check pytest",
        "state": "pending",
        "verdict": None,
        "commands": {
            "run": "dispatch command-verdict T-1 1 run --project ia-harness",
            "allow": "dispatch command-verdict T-1 1 allow --project ia-harness",
            "deny": "dispatch command-verdict T-1 1 deny --project ia-harness",
            "resume": None,
        },
    }
    assert second["n"] == 2
    assert second["target"] == {
        "kind": "compose", "container": "compose-box", "service": "db", "cwd": "/workspace"
    }
    # *allow* is the agent's own container's verdict only (ADR 121).
    assert second["commands"]["allow"] is None
    assert second["commands"]["run"] == "dispatch command-verdict T-1 2 run --project ia-harness"


def test_a_verdict_not_resumed_yet_awaits_its_resume(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    filed_at = _block(harness, "T-1", accepted=[_request(["pytest"]), _request(["ls"])])
    _blocked(harness, "T-1", [_verdict(filed_at, n=2, ran=_ran(harness, "T-1", 2))])

    (row,) = _data(harness, PATH)[0]["requests"]

    assert row["n"] == 2
    assert row["argv"] == ["ls"]
    assert row["state"] == "awaiting_resume"
    assert row["verdict"] == {
        "verdict": "run",
        "by": "operator",
        "at": "2026-10-18T10:00:00+00:00",
        "comment": None,
        "allowed": None,
        "resumed_at": None,
        "ran": {"exit_code": 0, "timed_out": False, "truncated": True, "log": "commands/2.log"},
    }
    assert row["commands"] == {
        "run": None,
        "allow": None,
        "deny": None,
        "resume": "dispatch command-resume T-1 --project ia-harness",
    }


def test_a_deny_not_resumed_yet_awaits_its_resume_too(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    filed_at = _block(harness, "T-1", accepted=[_request(["rm", "-rf", "x"])])
    _blocked(harness, "T-1", [_verdict(filed_at, verdict="deny", comment="no")])

    (row,) = _data(harness, PATH)[0]["requests"]

    assert row["state"] == "awaiting_resume"
    assert row["verdict"]["verdict"] == "deny"
    assert row["verdict"]["ran"] is None


def test_a_resumed_block_is_resumed_or_denied(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    run_at = _block(harness, "T-1", accepted=[_request(["pytest"])])
    _task(
        harness.tasks_dir,
        "T-1",
        status="in_progress",
        command_verdicts=[
            _verdict(run_at, verdict="allow", allowed="pytest", resumed_at="2026-10-18T11:00:00+00:00")
        ],
    )
    deny_at = _block(harness, "T-2", accepted=[_request(["curl", "x"])])
    _task(
        harness.tasks_dir,
        "T-2",
        status="in_progress",
        command_verdicts=[_verdict(deny_at, verdict="deny", resumed_at="2026-10-18T11:00:00+00:00")],
    )

    rows = _data(harness, PATH)[0]["requests"]

    assert [(row["task_id"], row["state"]) for row in rows] == [("T-1", "resumed"), ("T-2", "denied")]
    assert rows[0]["verdict"]["allowed"] == "pytest"
    assert rows[0]["verdict"]["resumed_at"] == "2026-10-18T11:00:00+00:00"
    assert all(row["commands"] == dict.fromkeys(("run", "allow", "deny", "resume")) for row in rows)


def test_a_verdict_whose_envelope_is_gone_is_still_a_row(tmp_path: Path) -> None:
    """A later phase re-saves its envelope; the verdict outlives the request it named."""
    harness = _harness(tmp_path)
    _task(
        harness.tasks_dir,
        "T-1",
        status="done",
        command_verdicts=[
            _verdict("2026-10-01T00:00:00+00:00", ran=_ran(harness, "T-1"), resumed_at="x"),
            _verdict("2026-10-02T00:00:00+00:00", verdict="deny", role="cartografo"),
        ],
    )

    first, second = _data(harness, PATH)[0]["requests"]

    assert (first["state"], first["argv"], first["agent_reason"]) == ("resumed", None, None)
    assert first["target"] == {"kind": "agent", "container": "agent-box", "service": None, "cwd": "/workspace"}
    assert first["verdict"]["ran"]["log"] == "commands/1.log"
    assert (second["state"], second["role"], second["target"]) == ("denied", "cartografo", None)


def test_an_undecided_block_the_task_moved_on_from_is_left_out(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _block(harness, "T-1", accepted=[_request(["pytest"])])
    _task(harness.tasks_dir, "T-1", status="in_progress")

    assert _data(harness, PATH)[0] == {"requests": [], "refused": []}


def test_pending_come_first_then_by_filed_at(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _task(
        harness.tasks_dir,
        "T-0",
        status="done",
        command_verdicts=[_verdict("2026-10-01T00:00:00+00:00", resumed_at="x")],
    )
    old_at = _block(harness, "T-9", accepted=[_request(["a"])])
    _blocked(harness, "T-9")
    new_at = _block(harness, "T-3", accepted=[_request(["b"]), _request(["c"])])
    _blocked(harness, "T-3")
    held_at = _block(harness, "T-5", accepted=[_request(["d"])])
    _blocked(harness, "T-5", [_verdict(held_at)])
    assert old_at < new_at < held_at

    rows = _data(harness, PATH)[0]["requests"]

    assert [(row["task_id"], row["n"], row["state"]) for row in rows] == [
        ("T-9", 1, "pending"),
        ("T-3", 1, "pending"),
        ("T-3", 2, "pending"),
        ("T-0", 1, "resumed"),
        ("T-5", 1, "awaiting_resume"),
    ]


def test_refused_requests_of_the_live_block_are_listed_apart(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    filed_at = _block(
        harness,
        "T-1",
        refused=[
            {"request": {"argv": ["sudo", "x"], "reason": "need root", "target": {"kind": "host"}},
             "reason": "target kind 'host' is not one of agent, compose"},
            {"request": "not even a mapping", "reason": "a request is a mapping"},
        ],
    )
    _blocked(harness, "T-1")
    # A block the task is no longer on is history: its refusals are not.
    _block(harness, "T-2", refused=[{"request": {"argv": ["x"]}, "reason": "no"}])
    _task(harness.tasks_dir, "T-2", status="in_progress")

    data, warnings = _data(harness, PATH)

    assert warnings == []
    assert data["requests"] == []
    first, second = data["refused"]
    assert first == {
        "task_id": "T-1",
        "project": "ia-harness",
        "title": None,
        "role": "implementador",
        "filed_at": filed_at,
        "argv": ["sudo", "x"],
        "target": {"kind": "host", "service": None},
        "agent_reason": "need root",
        "refused_reason": "target kind 'host' is not one of agent, compose",
    }
    assert (second["argv"], second["target"], second["agent_reason"]) == (
        None, {"kind": None, "service": None}, None
    )
    # Nothing to run in a refused row: no number and no commands.
    assert "n" not in first and "commands" not in first


def test_the_state_filter(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _block(harness, "T-1", accepted=[_request(["a"])])
    _blocked(harness, "T-1")
    held_at = _block(harness, "T-2", accepted=[_request(["b"])])
    _blocked(harness, "T-2", [_verdict(held_at)])

    for state, ids in [
        ("pending", ["T-1"]),
        ("awaiting_resume", ["T-2"]),
        ("resumed", []),
        ("denied", []),
    ]:
        rows = _data(harness, f"{PATH}?state={state}")[0]["requests"]
        assert [row["task_id"] for row in rows] == ids, state


def test_an_unknown_state_is_a_400(tmp_path: Path) -> None:
    harness = _harness(tmp_path)

    resp = _get(harness, f"{PATH}?state=done")

    assert resp.status_code == 400
    assert "pending" in resp.get_json()["error"]


def test_the_project_filter(tmp_path: Path) -> None:
    harness = _harness(tmp_path, projects=("ia-harness", "other"))
    _block(harness, "T-1", accepted=[_request(["a"])])
    _raw_task(harness.tasks_dir, "T-1", status="blocked", blocked_reason="command_request", project="other")
    _block(harness, "T-2", accepted=[_request(["b"])])
    _raw_task(harness.tasks_dir, "T-2", status="blocked", blocked_reason="command_request", project="ia-harness")
    _block(harness, "T-3", accepted=[_request(["c"])])
    _blocked(harness, "T-3")

    rows = _data(harness, f"{PATH}?project=ia-harness")[0]["requests"]
    # A task naming no project is not left out by the filter, as on the roadmap.
    assert [(row["task_id"], row["project"]) for row in rows] == [
        ("T-2", "ia-harness"), ("T-3", "ia-harness")
    ]
    assert rows[1]["commands"]["run"].endswith("--project ia-harness")

    rows = _data(harness, PATH)[0]["requests"]
    assert [(row["task_id"], row["project"]) for row in rows] == [
        ("T-1", "other"), ("T-2", "ia-harness"), ("T-3", None)
    ]
    # With two checkouts and none named, the slug is for the operator to fill in.
    assert rows[2]["commands"]["run"] == "dispatch command-verdict T-3 1 run --project <project>"

    assert _get(harness, f"{PATH}?project=nope").status_code == 404


@pytest.mark.parametrize(
    "content",
    ["---\ntask_id: [unclosed\n---\n", "no frontmatter at all\n", "---\nTODO write this up\n---\n"],
)
def test_an_unreadable_task_file_is_a_warning(tmp_path: Path, content: str) -> None:
    harness = _harness(tmp_path)
    _block(harness, "T-1", accepted=[_request(["a"])])
    _blocked(harness, "T-1")
    bad = Path(context_transfer.task_file_path(harness.tasks_dir, "T-2"))
    bad.write_text(content)

    data, warnings = _data(harness, PATH)

    assert [row["task_id"] for row in data["requests"]] == ["T-1"]
    assert len(warnings) == 1 and str(bad) in warnings[0]
    assert "unreadable task file" in warnings[0]


def test_an_unreadable_or_malformed_envelope_is_a_warning(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _block(harness, "T-1", accepted=[_request(["a"])])
    _blocked(harness, "T-1")
    broken = context_transfer.handoff_path(harness.tasks_dir, "T-1", "cartografo")
    Path(broken).write_text("{not json")
    _block(harness, "T-2", accepted=[{"argv": "pytest -q", "resolved": {"kind": "agent"}}])
    _blocked(harness, "T-2")
    _block(harness, "T-3", accepted=[_request(["c"])])
    _blocked(harness, "T-3", ["not a mapping"])

    data, warnings = _data(harness, PATH)

    assert [row["task_id"] for row in data["requests"]] == ["T-1"]
    assert any(broken in w and "unreadable handoff" in w for w in warnings)
    t2 = context_transfer.handoff_path(harness.tasks_dir, "T-2", "implementador")
    assert any(t2 in w and "unreadable command requests" in w for w in warnings)
    # A verdict that is not a mapping is a bad task file, not a 500.
    t3 = context_transfer.task_file_path(harness.tasks_dir, "T-3")
    assert any(t3 in w and "unreadable task file" in w for w in warnings)
    assert len(warnings) == 3


def test_the_task_count_is_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_app, "MAX_COMMAND_REQUEST_TASKS", 2)
    harness = _harness(tmp_path)
    for task_id in ("T-1", "T-2", "T-3"):
        _block(harness, task_id, accepted=[_request(["a"])])
        _blocked(harness, task_id)

    data, warnings = _data(harness, PATH)

    assert [row["task_id"] for row in data["requests"]] == ["T-1", "T-2"]
    assert warnings == [
        "3 task files are in the hive and this service answers at most 2: the first 2 by "
        "task id are read, and 1 are left out."
    ]


def test_no_output_session_account_or_email_is_served(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    filed_at = _block(
        harness,
        "T-1",
        accepted=[_request(["env"])],
        session_id="sess-0123456789",
        account="cuenta1",
    )
    log = Path(context_transfer.command_log_path(harness.tasks_dir, "T-1", 1))
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("GITHUB_TOKEN=ghp_SECRETSECRET\n")
    _blocked(
        harness,
        "T-1",
        [_verdict(filed_at, by="someone@example.com", ran=_ran(harness, "T-1"))],
    )

    resp = _get(harness, PATH)
    text = resp.get_data(as_text=True)

    assert resp.status_code == 200
    (row,) = resp.get_json()["data"]["requests"]
    assert row["verdict"]["by"] is None
    assert row["verdict"]["ran"]["log"] == "commands/1.log"
    for leaked in ("ghp_SECRETSECRET", "GITHUB_TOKEN", "sess-0123456789", "cuenta1",
                   "someone@example.com", str(log), harness.tasks_dir):
        assert leaked not in text, leaked
    assert "session_id" not in json.dumps(row) and "account" not in json.dumps(row)


def test_it_writes_nothing(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _block(harness, "T-1", accepted=[_request(["a"])])
    _blocked(harness, "T-1")
    before = {p: p.stat().st_mtime_ns for p in Path(harness.tasks_dir).rglob("*")}

    _data(harness, PATH)

    assert {p: p.stat().st_mtime_ns for p in Path(harness.tasks_dir).rglob("*")} == before
