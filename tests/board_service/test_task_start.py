"""The thread's `task_start` tool (ADR 187).

Own config: hive and state dirs are under tmp_path, never the real ones.
"""
import json
from pathlib import Path

import pytest

from board_service.app import create_app
from dispatcher import actions
from tests.board_service.test_app import CONFIG_YAML, task

TOKEN = "s3cret"


@pytest.fixture
def hive(tmp_path: Path) -> Path:
    path = tmp_path / ".hive" / "tasks"
    path.mkdir(parents=True)
    return path


@pytest.fixture
def state(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture
def cfg(tmp_path: Path, hive: Path, state: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        CONFIG_YAML.format(hive=hive).replace("/data/dispatcher_state", str(state))
        + "thread_models: [m1]\nprojects_root: /data/projects\n"
    )
    (tmp_path / "config.d").mkdir()
    return path


@pytest.fixture
def client(cfg: Path, monkeypatch):
    monkeypatch.setenv("BOARD_ACTIONS_TOKEN", TOKEN)
    return create_app(str(cfg)).test_client()


def new_thread(client, **body) -> str:
    body = {"title": "t", "project": "demo", **body}
    return client.post("/api/threads", json=body).get_json()["id"]


def running_turn(client, state, text="please start T-1", thread=None, actor="operator", claim=True):
    """A turn a person sent, claimed by the worker (running) unless `claim` is off."""
    thread = thread or new_thread(client)
    record = actions.create(
        state, "turn", params={"thread": thread, "text": text}, actor=actor
    )
    if claim:
        actions.claim(state, record["id"])
    return record["id"]


def rpc(client, turn, name, arguments, role="thread"):
    url = f"/mcp?role={role}" + (f"&turn={turn}" if turn else "")
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}
    return client.post(url, json=body)


def start(client, turn, **arguments):
    res = rpc(client, turn, "task_start", arguments)
    assert res.status_code == 200
    return res.get_json()


def refused(client, turn, **arguments) -> str:
    result = start(client, turn, **arguments)["result"]
    assert result["isError"] is True
    return result["content"][0]["text"]


def pending(state: Path) -> list[Path]:
    root = state / "actions" / "pending"
    return sorted(root.glob("*.json")) if root.exists() else []


def starts(state: Path):
    return [p for p in pending(state) if json.loads(p.read_text())["kind"] == "start"]


def test_it_enqueues_a_start_the_worker_would_claim(client, hive, state):
    task(hive, "T-1")
    turn = running_turn(client, state)
    result = start(client, turn, task_id="T-1")["result"]
    assert result["isError"] is False
    record = result["structuredContent"]
    assert [p.stem for p in starts(state)] == [record["id"]]
    claimed = actions.claim(state, record["id"])
    assert claimed is not None and claimed["status"] == "running"


def test_the_record_carries_the_thread_the_turn_and_a_non_operator_actor(client, hive, state):
    task(hive, "T-1")
    thread = new_thread(client, project="demo")
    turn = running_turn(client, state, thread=thread)
    record = start(client, turn, task_id="T-1")["result"]["structuredContent"]
    assert record["kind"] == "start" and record["actor"] == "thread"
    assert record["note"] is None
    assert record["params"] == {"limit": None, "thread": thread, "turn": turn}
    assert (record["project"], record["task_id"]) == ("demo", "T-1")
    on_disk = json.loads(starts(state)[0].read_text())
    assert on_disk == record


def test_a_named_project_wins_over_the_threads(client, hive, state):
    task(hive, "T-1")
    turn = running_turn(client, state)
    record = start(client, turn, task_id="T-1", project="other")["result"]["structuredContent"]
    assert record["project"] == "other"


def test_the_thread_is_listed_exactly_three_tools(client, state):
    turn = running_turn(client, state)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    res = client.post(f"/mcp?role=thread&turn={turn}", json=body)
    names = [t["name"] for t in res.get_json()["result"]["tools"]]
    assert names == ["board_list", "board_get", "task_start"]


@pytest.mark.parametrize("role", ["arquitecto", "operator", "implementador"])
def test_a_role_without_the_tool_is_refused(client, hive, state, role):
    task(hive, "T-1")
    res = rpc(client, None, "task_start", {"task_id": "T-1"}, role=role)
    assert res.get_json()["error"]["code"] == -32602
    assert pending(state) == []


def test_the_other_roles_do_not_list_it(client):
    for role in ("arquitecto", "operator"):
        res = client.post(f"/mcp?role={role}", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert "task_start" not in [t["name"] for t in res.get_json()["result"]["tools"]]


def test_the_thread_role_needs_a_turn_and_no_other_role_takes_one(client, state):
    turn = running_turn(client, state)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    for url in ("/mcp?role=thread", "/mcp?role=thread&turn=nope", f"/mcp?role=operator&turn={turn}"):
        assert client.post(url, json=body).status_code == 400, url


def test_a_task_not_in_ready_is_refused(client, hive, state):
    task(hive, "T-1", status="in_progress")
    task(hive, "T-2", depends_on=["T-9"])
    turn = running_turn(client, state, text="start T-1, T-2 and T-404")
    for task_id in ("T-1", "T-2", "T-404"):
        assert "not in Ready" in refused(client, turn, task_id=task_id)
    assert starts(state) == []


def test_a_pending_start_for_the_task_is_refused(client, hive, state):
    task(hive, "T-1")
    turn = running_turn(client, state)
    assert start(client, turn, task_id="T-1")["result"]["isError"] is False
    assert "already queued" in refused(client, turn, task_id="T-1")
    assert len(starts(state)) == 1


def test_a_console_start_blocks_the_threads(client, hive, state):
    task(hive, "T-1")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    assert client.post("/api/tasks/T-1/start", json={"project": "demo"}, headers=headers).status_code == 201
    turn = running_turn(client, state)
    assert "already queued" in refused(client, turn, task_id="T-1")


@pytest.mark.parametrize(
    "arguments",
    [{}, {"task_id": 1}, {"task_id": ""}, {"task_id": "T-1", "extra": 1},
     {"task_id": "T-1", "project": 5}, {"task_id": "T-1", "note": "x"}],
)
def test_bad_arguments_are_refused(client, hive, state, arguments):
    task(hive, "T-1")
    turn = running_turn(client, state)
    assert refused(client, turn, **arguments)
    assert starts(state) == []


def test_no_token_turns_the_tool_off(cfg, hive, state, monkeypatch):
    monkeypatch.delenv("BOARD_ACTIONS_TOKEN", raising=False)
    client = create_app(str(cfg)).test_client()
    task(hive, "T-1")
    turn = running_turn(client, state)
    assert "no BOARD_ACTIONS_TOKEN" in refused(client, turn, task_id="T-1")
    assert starts(state) == []


@pytest.mark.parametrize("why", ["unknown", "pending", "finished", "not_operator", "not_a_turn"])
def test_it_starts_nothing_without_a_running_turn_a_person_sent(client, hive, state, why):
    task(hive, "T-1")
    thread = new_thread(client)
    if why == "unknown":
        turn = "a-20260101T000000000000Z-abcd"
    elif why == "pending":
        turn = running_turn(client, state, thread=thread, claim=False)
    elif why == "finished":
        turn = running_turn(client, state, thread=thread)
        actions.finish(state, turn, "done")
    elif why == "not_operator":
        turn = running_turn(client, state, thread=thread, actor="thread")
    else:
        other = actions.create(state, "start", project="demo", task_id="T-1 start T-1")
        turn = other["id"]
    assert "no running turn" in refused(client, turn, task_id="T-1")
    assert starts(state) == [] or why == "not_a_turn"


@pytest.mark.parametrize(
    "text, task_id, named",
    [
        ("start T-1", "T-1", True),
        ("Start t-1, please", "T-1", True),
        ("(T-1)", "T-1", True),
        ("start T-10", "T-1", False),
        ("start T-1-S1", "T-1", False),
        ("start XT-1", "T-1", False),
        ("start T-1_b", "T-1", False),
        ("what is the state of the board", "T-1", False),
    ],
)
def test_the_task_must_be_named_as_a_whole_token(client, hive, state, text, task_id, named):
    task(hive, "T-1")
    turn = running_turn(client, state, text=text)
    result = start(client, turn, task_id=task_id)["result"]
    assert result["isError"] is not named
    if not named:
        assert "is not named" in result["content"][0]["text"]
        assert starts(state) == []


def test_an_archived_thread_starts_nothing(client, hive, state):
    task(hive, "T-1")
    thread = new_thread(client)
    turn = running_turn(client, state, thread=thread)
    client.patch(f"/api/threads/{thread}", json={"status": "archived"})
    assert "archived" in refused(client, turn, task_id="T-1")
    assert starts(state) == []
