"""The board service's write tools (T-084, ADR 129), over `/mcp` and over the
console's HTTP endpoints."""

import json
import os
from pathlib import Path

import pytest

from board_service import app as service
from dispatcher import tasks
from dispatcher.config import PHASE_ROLES
from dispatcher.context_transfer import read_task_file, task_file_path
from tests.board_service.test_app import DESCRIPTION, call, cfg, client, hive, ok, rpc, task  # noqa: F401

WRITE_TOOLS = ["backlog_propose", "task_edit", "task_comment", "backlog_accept"]
OTHER = "## Goal\n\nSomething else.\n\n## Done when\n\nIt is.\n"
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def read(hive: Path, task_id: str):
    return read_task_file(task_file_path(str(hive), task_id))


def error(client, name, arguments=None, role="operator") -> str:
    result = call(client, name, arguments, role)["result"]
    assert result["isError"] is True, result
    return result["content"][0]["text"]


def propose(client, role="operator", **arguments) -> dict:
    return ok(client, "backlog_propose", {"title": "A", "description": DESCRIPTION, **arguments}, role)


# --- who may call what ------------------------------------------------------


def test_the_operator_has_every_write_and_the_arquitecto_two(client):
    def names(role):
        return [t["name"] for t in rpc(client, "tools/list", role=role).get_json()["result"]["tools"]]

    assert set(WRITE_TOOLS) <= set(names("operator"))
    assert [n for n in names("arquitecto") if n in WRITE_TOOLS] == ["backlog_propose", "task_comment"]


@pytest.mark.parametrize("name", ["task_edit", "backlog_accept"])
def test_the_arquitecto_may_not_edit_or_accept(client, hive, name):
    task(hive, "T-001", status="proposed")
    before = (hive / "T-001.md").read_bytes()

    body = call(client, name, {"task_id": "T-001"}, role="arquitecto")

    assert body["error"]["code"] == -32602
    assert name in body["error"]["message"]
    assert (hive / "T-001.md").read_bytes() == before


@pytest.mark.parametrize("role", sorted(set(PHASE_ROLES) - {"arquitecto"}))
@pytest.mark.parametrize("name", WRITE_TOOLS)
def test_no_other_phase_may_write(client, hive, role, name):
    task(hive, "T-001", status="proposed")
    body = call(client, name, {"task_id": "T-001", "title": "A", "description": DESCRIPTION, "text": "x"}, role)
    assert body["error"]["code"] == -32602
    assert sorted(p.name for p in hive.iterdir()) == ["T-001.md"]


def test_a_write_with_an_unknown_role_or_an_origin_is_refused(client, hive):
    args = {"name": "backlog_propose", "arguments": {"title": "A", "description": DESCRIPTION}}
    assert rpc(client, "tools/call", args, role="admin").status_code == 400
    assert rpc(client, "tools/call", args, role=None).status_code == 400
    assert rpc(client, "tools/call", args, headers={"Origin": "http://evil.example"}).status_code == 403
    assert list(hive.iterdir()) == []


# --- backlog_propose --------------------------------------------------------


def test_propose_takes_its_source_from_the_role(client, hive):
    result = propose(client, role="arquitecto", priority="high")

    assert result == {"id": "T-001", "created": True, "tasks": []}
    stored = read(hive, "T-001")
    assert (stored.status, stored.source, stored.priority, stored.title) == (
        "proposed", "arquitecto", "high", "A",
    )


def test_propose_will_not_take_a_source_argument(client, hive):
    assert "takes no argument 'source'" in error(
        client, "backlog_propose", {"title": "A", "description": DESCRIPTION, "source": "operator"},
        role="arquitecto",
    )
    assert list(hive.iterdir()) == []


def test_propose_again_answers_the_same_task(client, hive):
    first = propose(client)
    assert propose(client) == {**first, "created": False}


def test_propose_an_epic_over_mcp(client, hive):
    result = propose(
        client, title="E",
        tasks=[{"title": "One", "description": DESCRIPTION},
               {"title": "Two", "description": DESCRIPTION, "after": [1]}],
    )
    assert result == {"id": "T-001", "created": True, "tasks": ["T-001-S1", "T-001-S2"]}
    assert read(hive, "T-001-S2").depends_on == ["T-001-S1"]


@pytest.mark.parametrize(
    "arguments, needle",
    [
        ({"description": DESCRIPTION}, "needs 'title'"),
        ({"title": "A"}, "needs 'description'"),
        ({"title": 7, "description": DESCRIPTION}, "title must be a string"),
        ({"title": "A", "description": DESCRIPTION, "priority": "urgent"}, "priority must be one of"),
        ({"title": "A", "description": DESCRIPTION, "depends_on": "T-1"}, "depends_on must be an array"),
        ({"title": "A", "description": DESCRIPTION, "depends_on": [1]}, "depends_on[0] must be a string"),
        ({"title": "A", "description": DESCRIPTION,
          "tasks": [{"title": "x", "description": DESCRIPTION, "after": [True]},
                    {"title": "y", "description": DESCRIPTION}]}, "must be an integer"),
        ({"title": "A", "description": DESCRIPTION,
          "tasks": [{"title": "x"}, {"title": "y", "description": DESCRIPTION}]}, "needs 'description'"),
        ({"title": "A", "description": DESCRIPTION,
          "tasks": [{"title": "x", "description": DESCRIPTION, "rank": 1},
                    {"title": "y", "description": DESCRIPTION}]}, "takes no argument 'rank'"),
        ({"title": "A", "description": "not in sections"}, "its description"),
        ({"title": "A", "description": DESCRIPTION, "depends_on": ["T-404"]}, "T-404"),
    ],
)
def test_propose_refuses_a_bad_argument_and_writes_nothing(client, hive, arguments, needle):
    assert needle in error(client, "backlog_propose", arguments)
    assert list(hive.iterdir()) == []


def test_a_null_argument_is_one_left_out(client, hive):
    assert propose(client, priority=None, depends_on=None)["created"] is True
    assert read(hive, "T-001").priority is None


# --- task_edit --------------------------------------------------------------


def test_edit_over_mcp(client, hive):
    propose(client)
    result = ok(client, "task_edit", {"task_id": "T-001", "description": OTHER, "priority": "low"})
    assert result == {"id": "T-001", "changed": ["description", "priority"]}
    assert ok(client, "board_get", {"task_id": "T-001"})["item"]["description"] == OTHER


def test_edit_refuses_a_started_task(client, hive):
    task(hive, "T-001", status="in_progress")
    assert "has started" in error(client, "task_edit", {"task_id": "T-001", "priority": "low"})


def test_edit_of_no_task_is_a_tool_error(client):
    assert "no such task" in error(client, "task_edit", {"task_id": "T-404", "priority": "low"})


# --- task_comment -----------------------------------------------------------


def test_comment_takes_its_author_from_the_role(client, hive):
    task(hive, "T-001")

    result = ok(client, "task_comment", {"task_id": "T-001", "text": "Hm."}, role="arquitecto")

    assert result["comment"]["author"] == "arquitecto"
    assert result["comment_count"] == 1
    assert ok(client, "board_get", {"task_id": "T-001"})["item"]["comment_count"] == 1
    line = json.loads((hive.parent / "board" / "T-001" / "comments.jsonl").read_text())
    assert line == result["comment"]


def test_comment_will_not_take_an_author(client, hive):
    task(hive, "T-001")
    assert "takes no argument 'author'" in error(
        client, "task_comment", {"task_id": "T-001", "text": "x", "author": "operator"}, role="arquitecto"
    )


def test_a_comment_on_a_diff_line(client, hive):
    task(hive, "T-001")
    result = ok(client, "task_comment",
                {"task_id": "T-001", "text": "x", "file": "a.py", "line": 3, "commit": COMMIT})
    assert (result["comment"]["file"], result["comment"]["line"]) == ("a.py", 3)
    assert "must be an integer" in error(
        client, "task_comment", {"task_id": "T-001", "text": "x", "file": "a.py", "line": "3", "commit": COMMIT}
    )
    assert "all three" in error(client, "task_comment", {"task_id": "T-001", "text": "x", "file": "a.py"})


# --- review_verdict (review.md step 3) --------------------------------------


@pytest.mark.parametrize("role", ["operator", "arquitecto"])
def test_no_role_is_listed_the_verdict_or_may_call_it(client, hive, role):
    task(hive, "T-001", status="done")
    tools = rpc(client, "tools/list", role=role).get_json()["result"]["tools"]
    assert "review_verdict" not in [t["name"] for t in tools]

    body = call(client, "review_verdict",
                {"task_id": "T-001", "kind": "comment", "text": "x", "commit": COMMIT}, role)

    assert body["error"]["code"] == -32602
    assert not (hive.parent / "board" / "T-001" / "comments.jsonl").exists()


def test_http_a_comment_verdict_is_201_and_a_comment(client, hive):
    task(hive, "T-001", status="done")

    res = client.post("/api/tasks/T-001/verdict", json={"kind": "comment", "text": "Fine.", "commit": COMMIT})

    assert res.status_code == 201
    body = res.get_json()
    assert body["comment"]["verdict"] == {"kind": "comment", "commit": COMMIT}
    assert body["comment"]["author"] == "operator"
    assert ok(client, "board_get", {"task_id": "T-001"})["item"]["comment_count"] == 1


@pytest.mark.parametrize(
    "status, body, needle",
    [
        ("done", {"kind": "approve", "text": "x", "commit": COMMIT}, "not built yet"),
        ("done", {"kind": "praise", "text": "x", "commit": COMMIT}, "kind must be one of"),
        ("done", {"kind": "comment", "text": "x"}, "needs 'commit'"),
        ("done", {"kind": "comment", "text": "x", "commit": "main"}, "not a commit hash"),
        ("done", {"kind": "comment", "text": "x", "commit": COMMIT, "author": "x"}, "no argument 'author'"),
        ("pending", {"kind": "comment", "text": "x", "commit": COMMIT}, "not under review"),
    ],
)
def test_http_a_verdict_refused_is_400_and_writes_nothing(client, hive, status, body, needle):
    task(hive, "T-001", status=status)
    res = client.post("/api/tasks/T-001/verdict", json=body)
    assert res.status_code == 400
    assert needle in res.get_json()["error"]
    assert not (hive.parent / "board" / "T-001" / "comments.jsonl").exists()


def test_http_a_verdict_on_no_task_is_404(client):
    res = client.post("/api/tasks/T-404/verdict", json={"kind": "comment", "text": "x", "commit": COMMIT})
    assert res.status_code == 404


# --- backlog_accept ---------------------------------------------------------


def test_accept_over_mcp(client, hive):
    propose(client, role="arquitecto")
    assert ok(client, "backlog_accept", {"task_id": "T-001", "priority": "high"}) == {
        "id": "T-001", "status": "pending", "changed": True, "children": {},
    }
    stored = read(hive, "T-001")
    assert (stored.status, stored.priority, stored.source) == ("pending", "high", "arquitecto")


def test_accept_refuses_a_bad_to_and_a_task_on_the_board(client, hive):
    task(hive, "T-001", status="review")
    assert "to must be one of" in error(client, "backlog_accept", {"task_id": "T-001", "to": "merged"})
    assert "on the board" in error(client, "backlog_accept", {"task_id": "T-001"})


# Root ignores the mode bits, so as uid 0 (a phase's container) the write
# succeeds and there is nothing to test.
@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the read-only mode")
def test_a_write_to_a_read_only_hive_is_a_tool_error(client, hive):
    task(hive, "T-001", status="proposed")
    dirs = [hive, hive.parent / "board" / "T-001"]
    for d in dirs:
        d.chmod(0o555)
    try:
        assert "could not be written" in error(client, "task_comment", {"task_id": "T-001", "text": "x"})
    finally:
        for d in dirs:
            d.chmod(0o755)


# --- the console's HTTP endpoints -------------------------------------------


def test_http_propose_answers_201_then_200(client, hive):
    res = client.post("/api/backlog", json={"title": "A", "description": DESCRIPTION})
    assert res.status_code == 201
    assert res.get_json() == {"id": "T-001", "created": True, "tasks": []}
    assert read(hive, "T-001").source == "operator"

    again = client.post("/api/backlog", json={"title": "A", "description": DESCRIPTION})
    assert again.status_code == 200
    assert again.get_json()["created"] is False


def test_http_edit_comment_and_accept(client, hive):
    client.post("/api/backlog", json={"title": "A", "description": DESCRIPTION})

    res = client.patch("/api/tasks/T-001", json={"priority": "low"})
    assert (res.status_code, res.get_json()) == (200, {"id": "T-001", "changed": ["priority"]})

    res = client.post("/api/tasks/T-001/comments", json={"text": "ok"})
    assert res.status_code == 201
    assert res.get_json()["comment"]["author"] == "operator"

    res = client.post("/api/tasks/T-001/accept", json={})
    assert (res.status_code, res.get_json()["status"]) == (200, "pending")
    assert read(hive, "T-001").status == "pending"


@pytest.mark.parametrize(
    "method, url, body",
    [
        ("patch", "/api/tasks/T-404", {"priority": "low"}),
        ("post", "/api/tasks/T-404/comments", {"text": "x"}),
        ("post", "/api/tasks/T-404/accept", {}),
    ],
)
def test_http_no_task_is_404(client, method, url, body):
    res = getattr(client, method)(url, json=body)
    assert res.status_code == 404
    assert "no such task" in res.get_json()["error"]


@pytest.mark.parametrize(
    "kwargs, status, needle",
    [
        ({"json": {"title": "A", "description": "flat"}}, 400, "its description"),
        ({"json": {"title": "A", "description": DESCRIPTION, "source": "debt"}}, 400, "no argument 'source'"),
        ({"json": ["not", "an", "object"]}, 400, "not a JSON object"),
        ({"data": "not json", "content_type": "application/json"}, 400, "not a JSON object"),
        ({"json": {"title": "A", "description": DESCRIPTION}, "headers": {"Origin": "http://x"}}, 403, "origin"),
        ({"json": {"title": "A", "description": DESCRIPTION}, "query_string": {"role": "arquitecto"}}, 400,
         "query parameter"),
    ],
)
def test_http_propose_refusals(client, hive, kwargs, status, needle):
    res = client.post("/api/backlog", **kwargs)
    assert res.status_code == status
    assert needle in res.get_json()["error"]
    assert list(hive.iterdir()) == []


def test_http_the_task_id_is_the_paths(client, hive):
    task(hive, "T-001", status="proposed")
    task(hive, "T-002", status="proposed")
    res = client.post("/api/tasks/T-001/accept", json={"task_id": "T-002"})
    assert res.status_code == 400
    assert read(hive, "T-002").status == "proposed"
    assert read(hive, "T-001").status == "proposed"


def test_http_refuses_a_path_that_is_not_a_task_id(client):
    res = client.post("/api/tasks/d-20261018T120000Z-abcd/accept", json={})
    assert res.status_code == 400
    assert "drafts" in res.get_json()["error"]


def test_http_a_tool_refusal_is_400(client, hive):
    task(hive, "T-001", status="merged")
    res = client.post("/api/tasks/T-001/accept", json={})
    assert res.status_code == 400
    assert "on the board" in res.get_json()["error"]


def test_http_reads_are_not_offered(client):
    assert client.get("/api/backlog").status_code == 405
    assert client.get("/api/tasks/T-001").status_code == 405


def test_the_writers_are_tasks_writers():
    """One module writes (plan section 5): the service holds no write of its own."""
    source = Path(service.__file__).read_text()
    assert "write_task_file" not in source
    assert "write_text(" not in source
    assert all(hasattr(tasks, name) for name in ("propose", "edit_task", "add_comment", "accept"))
