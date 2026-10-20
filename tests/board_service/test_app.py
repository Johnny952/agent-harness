import json
import os
from pathlib import Path

import pytest

from board_service import app as service
from board_service.app import create_app, main
from dispatcher.config import PHASE_ROLES

CONFIG_YAML = """
accounts:
  - name: cuenta1
    container: agent-cuenta1
quota_threshold_pct: 90
heartbeat_ttl_seconds: 120
heartbeat_interval_seconds: 30
projects_root: /data/projects
hive_tasks_dir: {hive}
state_dir: /data/dispatcher_state
collector_url: http://127.0.0.1:8787
"""

DESCRIPTION = """## Goal

Serve the board over MCP, reads only.

More words about it.

## Done when

`board_list` answers.
"""


@pytest.fixture
def hive(tmp_path: Path) -> Path:
    path = tmp_path / ".hive" / "tasks"
    path.mkdir(parents=True)
    return path


@pytest.fixture
def cfg(tmp_path: Path, hive: Path) -> Path:
    path = tmp_path / "config.yaml"
    # The state dir is under tmp_path: in a container `/data/dispatcher_state`
    # is the dispatcher's real one.
    text = CONFIG_YAML.format(hive=hive).replace("/data/dispatcher_state", str(tmp_path / "state"))
    path.write_text(text)
    (tmp_path / "config.d").mkdir()
    return path


@pytest.fixture
def client(cfg: Path):
    return create_app(str(cfg)).test_client()


def task(hive: Path, task_id: str, status: str = "pending", description: str | None = DESCRIPTION, **extra) -> None:
    lines = [f"task_id: {task_id}", f"status: {status}"]
    lines += [f"{k}: {json.dumps(v)}" for k, v in extra.items()]
    (hive / f"{task_id}.md").write_text("---\n" + "\n".join(lines) + "\n---\n\nbody\n")
    if description is not None:
        d = hive.parent / "board" / task_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "description.md").write_text(description)


def rpc(client, method, params=None, role="operator", msg_id=1, **kw):
    body = {"jsonrpc": "2.0", "method": method}
    if msg_id is not None:
        body["id"] = msg_id
    if params is not None:
        body["params"] = params
    url = "/mcp" if role is None else f"/mcp?role={role}"
    return client.post(url, json=body, **kw)


def call(client, name, arguments=None, role="operator"):
    res = rpc(client, "tools/call", {"name": name, "arguments": arguments or {}}, role=role)
    assert res.status_code == 200
    return res.get_json()


def ok(client, name, arguments=None, role="operator") -> dict:
    body = call(client, name, arguments, role)
    result = body["result"]
    assert result["isError"] is False, result
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    return result["structuredContent"]


def tool_error(client, name, arguments=None) -> str:
    result = call(client, name, arguments)["result"]
    assert result["isError"] is True
    return result["content"][0]["text"]


# --- the handshake and the transport ----------------------------------------


@pytest.mark.parametrize("version", service.PROTOCOL_VERSIONS)
def test_initialize_echoes_a_known_protocol_version(client, version):
    res = rpc(client, "initialize", {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "t"}})
    result = res.get_json()["result"]
    assert result["protocolVersion"] == version
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"]
    assert "Mcp-Session-Id" not in res.headers


def test_initialize_offers_the_newest_for_an_unknown_version(client):
    res = rpc(client, "initialize", {"protocolVersion": "1999-01-01"})
    assert res.get_json()["result"]["protocolVersion"] == service.PROTOCOL_VERSIONS[-1]


def test_a_notification_is_accepted_with_no_body(client):
    res = rpc(client, "notifications/initialized", msg_id=None)
    assert res.status_code == 202
    assert res.data == b""


def test_ping(client):
    assert rpc(client, "ping").get_json() == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_unknown_method(client):
    assert rpc(client, "resources/list").get_json()["error"]["code"] == service.METHOD_NOT_FOUND


@pytest.mark.parametrize("verb", ["get", "delete"])
def test_no_stream_and_no_session(client, verb):
    res = getattr(client, verb)("/mcp?role=operator")
    assert res.status_code == 405
    assert res.headers["Allow"] == "POST"


def test_a_browser_origin_is_refused(client):
    res = rpc(client, "ping", headers={"Origin": "http://evil.example"})
    assert res.status_code == 403


def test_a_body_that_is_not_json(client):
    res = client.post("/mcp?role=operator", data="{", content_type="application/json")
    assert res.status_code == 400
    assert res.get_json()["error"]["code"] == service.PARSE_ERROR


def test_a_batch_is_refused(client):
    res = client.post("/mcp?role=operator", json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
    assert res.status_code == 400
    assert res.get_json()["error"]["code"] == service.INVALID_REQUEST


def test_health(client):
    assert client.get("/health").get_json() == {"ok": True}


# --- the role check ---------------------------------------------------------


@pytest.mark.parametrize("role", [None, "", "operador", "OPERATOR"])
def test_a_missing_or_unknown_role_is_refused(client, role):
    res = rpc(client, "tools/list", role=role)
    assert res.status_code == 400
    assert "role" in res.get_json()["error"]["message"]


def test_an_unknown_query_parameter_is_refused(client):
    res = client.post("/mcp?role=operator&task=T-1", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert res.status_code == 400


@pytest.mark.parametrize("role", ["operator", "arquitecto"])
def test_the_board_roles_see_their_tools(client, role):
    tools = rpc(client, "tools/list", role=role).get_json()["result"]["tools"]
    assert [t["name"] for t in tools] == list(service.ROLE_TOOLS[role])
    assert [t["name"] for t in tools][:2] == ["board_list", "board_get"]
    for t in tools:
        assert t["inputSchema"]["type"] == "object"
        reads = t["name"] in ("board_list", "board_get")
        assert t["annotations"]["readOnlyHint"] is reads


@pytest.mark.parametrize("role", sorted(PHASE_ROLES - {"arquitecto"}))
def test_every_other_phase_role_sees_no_tools_and_may_call_none(client, hive, role):
    task(hive, "T-1")
    assert rpc(client, "tools/list", role=role).get_json()["result"]["tools"] == []
    for name in ("board_list", "board_get"):
        res = rpc(client, "tools/call", {"name": name, "arguments": {"task_id": "T-1"}}, role=role)
        assert res.get_json()["error"]["code"] == service.INVALID_PARAMS


def test_every_role_with_tools_is_a_known_role():
    assert set(service.ROLE_TOOLS) <= service.KNOWN_ROLES
    for names in service.ROLE_TOOLS.values():
        assert set(names) <= set(service.TOOLS)


def test_an_unknown_tool(client):
    res = rpc(client, "tools/call", {"name": "board_write", "arguments": {}})
    assert res.get_json()["error"]["code"] == service.INVALID_PARAMS


# --- board_list ---------------------------------------------------------------


def test_list_rows(client, hive):
    task(hive, "T-2", status="done", merged={"sha": "abc"})
    task(hive, "T-1", epic="T-9")
    task(hive, "T-3", description=None)
    out = ok(client, "board_list")
    assert out["warnings"] == []
    assert out["items"] == [
        {"id": "T-1", "title": "Serve the board over MCP, reads only.", "status": "pending",
         "place": "board", "epic": "T-9", "merged": False, "priority": "normal", "source": None,
         "tags": [], "rank": None, "debt_id": None, "project": None},
        {"id": "T-2", "title": "Serve the board over MCP, reads only.", "status": "done",
         "place": "board", "epic": None, "merged": True, "priority": "normal", "source": None,
         "tags": [], "rank": None, "debt_id": None, "project": None},
        {"id": "T-3", "title": None, "status": "pending", "place": "board", "epic": None,
         "merged": False, "priority": "normal", "source": None,
         "tags": [], "rank": None, "debt_id": None, "project": None},
    ]


def test_list_rows_carry_the_project(client, hive):
    # A card names its project (ADR NNN); one that names none is the
    # harness's one pre-existing project, and is served as None.
    task(hive, "T-1", project="shop")
    task(hive, "T-2")
    out = ok(client, "board_list")
    assert {r["id"]: r["project"] for r in out["items"]} == {"T-1": "shop", "T-2": None}
    assert out["warnings"] == []


def test_list_rows_carry_the_backlog_fields(client, hive):
    task(hive, "T-1", status="proposed", priority="high", source="arquitecto")
    task(hive, "T-2", status="deferred", priority="low", source="debt")
    rows = {r["id"]: r for r in ok(client, "board_list", {"place": "backlog"})["items"]}
    assert (rows["T-1"]["priority"], rows["T-1"]["source"]) == ("high", "arquitecto")
    assert (rows["T-2"]["priority"], rows["T-2"]["source"]) == ("low", "debt")


def test_a_bad_backlog_field_is_served_as_written_and_warned(client, hive):
    task(hive, "T-1", priority="urgent", source="someone")
    out = ok(client, "board_list")
    assert (out["items"][0]["priority"], out["items"][0]["source"]) == ("urgent", "someone")
    assert len(out["warnings"]) == 2
    assert "'urgent'" in out["warnings"][0] and "T-1.md" in out["warnings"][0]
    assert "'someone'" in out["warnings"][1]


def test_a_debt_item_carries_its_debt_id_in_list_and_get(client, hive):
    task(hive, "T-1", status="proposed", source="debt", tags=["debt"], debt_id="T-065-D1")
    task(hive, "T-2")
    out = ok(client, "board_list")
    assert out["warnings"] == []
    assert {r["id"]: r["debt_id"] for r in out["items"]} == {"T-1": "T-065-D1", "T-2": None}
    got = ok(client, "board_get", {"task_id": "T-1"})
    assert got["warnings"] == []
    assert got["item"]["frontmatter"]["debt_id"] == "T-065-D1"


def test_a_bad_debt_id_is_served_as_written_and_warned(client, hive):
    task(hive, "T-1", status="proposed", source="debt", debt_id="not a debt id")
    out = ok(client, "board_list")
    assert out["items"][0]["debt_id"] == "not a debt id"
    assert len(out["warnings"]) == 1
    assert "debt_id" in out["warnings"][0] and "T-1.md" in out["warnings"][0]
    got = ok(client, "board_get", {"task_id": "T-1"})
    assert got["item"]["frontmatter"]["debt_id"] == "not a debt id"
    assert len(got["warnings"]) == 1 and "debt_id" in got["warnings"][0]


def test_get_warns_on_a_bad_backlog_field_as_list_does(client, hive):
    task(hive, "T-1", priority="urgent")
    warnings = ok(client, "board_get", {"task_id": "T-1"})["warnings"]
    assert len(warnings) == 1 and "'urgent'" in warnings[0]


def test_the_backlog_statuses_are_the_dispatchers(client):
    from dispatcher import tasks
    assert service.BACKLOG_STATUSES == set(tasks.BACKLOG_STATUSES)


def test_a_frontmatter_title_wins_over_the_goal(client, hive):
    task(hive, "T-1", title="The board service")
    assert ok(client, "board_list")["items"][0]["title"] == "The board service"


def test_a_long_goal_line_is_cut(client, hive):
    task(hive, "T-1", description="## Goal\n\n" + "x" * 300 + "\n\n## Done when\n\ny\n")
    title = ok(client, "board_list")["items"][0]["title"]
    assert len(title) == service.TITLE_CHARS
    assert title.endswith("…")


def test_a_description_not_in_sections_has_no_title(client, hive):
    task(hive, "T-1", description="just prose\n")
    assert ok(client, "board_list")["items"][0]["title"] is None


def test_list_filters(client, hive):
    task(hive, "T-1", status="pending", epic="T-9")
    task(hive, "T-2", status="done", epic="T-9", description="## Goal\n\nWire the kiosk.\n\n## Done when\n\nok\n")
    task(hive, "T-3", status="proposed")
    task(hive, "T-9", status="pending")

    def ids(**args):
        return [i["id"] for i in ok(client, "board_list", args)["items"]]

    assert ids(place="backlog") == ["T-3"]
    assert ids(place="board") == ["T-1", "T-2", "T-9"]
    assert ids(status="done") == ["T-2"]
    assert ids(epic="T-9") == ["T-1", "T-2"]
    assert ids(text="KIOSK") == ["T-2"]
    assert ids(text="t-3") == ["T-3"]
    assert ids(epic="T-9", status="pending") == ["T-1"]
    assert ids(status="pending", place="backlog") == []


def test_list_refuses_a_bad_place_and_unknown_arguments(client):
    assert "place" in tool_error(client, "board_list", {"place": "attic"})
    assert "owner" in tool_error(client, "board_list", {"owner": "x"})
    assert "string" in tool_error(client, "board_list", {"status": 3})
    assert "at most" in tool_error(client, "board_list", {"text": "x" * 500})


def test_an_unreadable_or_unmigrated_task_is_a_warning_not_a_failure(client, hive):
    task(hive, "T-1")
    (hive / "T-2.md").write_text("no frontmatter here\n")
    (hive / "T-3.md").write_text("---\ntask_id: T-3\nstatus: pending\ndescription: old\n---\n")
    (hive / "T-4.md").write_text("---\nstatus: pending\n---\n")
    out = ok(client, "board_list")
    assert [i["id"] for i in out["items"]] == ["T-1"]
    assert len(out["warnings"]) == 3
    assert any("T-3" in w and "migrate" in w for w in out["warnings"])


def test_the_list_is_clamped(client, hive, monkeypatch):
    monkeypatch.setattr(service, "MAX_LIST_TASKS", 2)
    for n in (1, 2, 3):
        task(hive, f"T-{n}")
    out = ok(client, "board_list")
    assert [i["id"] for i in out["items"]] == ["T-1", "T-2"]
    assert "3 task files" in out["warnings"][0]


def test_an_empty_or_missing_hive(tmp_path):
    assert service.board_list(str(tmp_path / "nowhere")) == {"items": [], "ready": [], "warnings": []}


# --- board_get ----------------------------------------------------------------


def test_get(client, hive):
    task(hive, "T-1", epic="T-9", depends_on=["T-0"], heartbeat="2026-10-18T10:00:00Z")
    comment = '{"at": "2026-10-18T10:00:00Z", "author": "operator", "text": "x"}'
    (hive.parent / "board" / "T-1" / "comments.jsonl").write_text(f"{comment}\n\n{comment}\n")
    out = ok(client, "board_get", {"task_id": "T-1"})
    assert out["warnings"] == []
    item = out["item"]
    assert item["id"] == "T-1"
    assert item["place"] == "board"
    assert item["description"] == DESCRIPTION
    assert item["comment_count"] == 2
    assert item["frontmatter"]["epic"] == "T-9"
    assert item["frontmatter"]["depends_on"] == ["T-0"]


def test_get_counts_only_the_comments_the_thread_shows(client, hive):
    task(hive, "T-1")
    path = hive.parent / "board" / "T-1" / "comments.jsonl"
    path.write_text(
        '{"at": "2026-10-18T10:00:00Z", "author": "operator", "text": "kept"}\n'
        '{"at": "2026-10-18T10:01:00Z", "auth\n'
        '{"a": 1}\n'
    )
    out = ok(client, "board_get", {"task_id": "T-1"})
    assert out["item"]["comment_count"] == 1
    assert len(out["warnings"]) == 2
    assert out["warnings"][0].startswith(f"{path}: line 2: not a JSON line (")
    assert out["warnings"][0].endswith("), skipped")
    assert out["warnings"][1] == f"{path}: line 3: no string at, author, text, skipped"


def test_get_makes_yaml_dates_into_text(client, hive):
    (hive / "T-1.md").write_text("---\ntask_id: T-1\nstatus: proposed\nopened: 2026-10-18\n---\n")
    item = ok(client, "board_get", {"task_id": "T-1"})["item"]
    assert item["frontmatter"]["opened"] == "2026-10-18"
    assert item["place"] == "backlog"
    assert item["description"] is None
    assert item["comment_count"] == 0


@pytest.mark.parametrize("task_id", ["T-404", "../T-1", "a/b", "", "drafts", "d-20261018T100000Z-ab12"])
def test_get_refuses_what_names_no_task(client, hive, task_id):
    task(hive, "T-1")
    assert tool_error(client, "board_get", {"task_id": task_id})


def test_get_needs_a_task_id(client):
    assert "task_id" in tool_error(client, "board_get", {})


def test_get_of_an_unmigrated_task_is_an_error_naming_migrate(client, hive):
    (hive / "T-3.md").write_text("---\ntask_id: T-3\nstatus: pending\ndescription: old\n---\n")
    assert "migrate" in tool_error(client, "board_get", {"task_id": "T-3"})


# --- writes nothing ---------------------------------------------------------


def _snapshot(root: Path) -> dict:
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = os.path.join(dirpath, name)
            st = os.stat(path)
            out[path] = (st.st_mode, st.st_size, st.st_mtime_ns)
    return out


def test_no_call_writes_anything(client, hive, tmp_path):
    task(hive, "T-1")
    task(hive, "T-2", description=None)
    before = _snapshot(tmp_path)
    rpc(client, "initialize", {"protocolVersion": "2025-06-18"})
    rpc(client, "tools/list")
    ok(client, "board_list", {"text": "board"})
    ok(client, "board_get", {"task_id": "T-1"})
    ok(client, "board_get", {"task_id": "T-2"})
    tool_error(client, "board_get", {"task_id": "T-404"})
    assert _snapshot(tmp_path) == before


def test_it_reads_a_read_only_hive(client, hive, tmp_path):
    task(hive, "T-1")
    dirs = [hive, hive.parent / "board", hive.parent / "board" / "T-1", hive.parent]
    for d in dirs:
        d.chmod(0o555)
    try:
        assert [i["id"] for i in ok(client, "board_list")["items"]] == ["T-1"]
        assert ok(client, "board_get", {"task_id": "T-1"})["item"]["comment_count"] == 0
    finally:
        for d in dirs:
            d.chmod(0o755)


# --- main -------------------------------------------------------------------


def test_main_refuses_to_start_with_no_config(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main({"CONFIG_PATH": str(tmp_path / "nope.yaml")})
    assert exc.value.code == 2


def test_main_reads_the_config_from_the_environment(cfg):
    assert main({"CONFIG_PATH": str(cfg)}).test_client().get("/health").status_code == 200


def test_the_dockerfile_copies_what_the_service_imports():
    root = Path(__file__).resolve().parents[2]
    text = (root / "board_service" / "Dockerfile").read_text()
    assert "COPY dispatcher/ dispatcher/" in text
    assert "COPY board_service/ board_service/" in text
    assert f"EXPOSE {service.PORT}" in text
    assert '"board_service.app"' in text
    assert "board_service*" in (root / "pyproject.toml").read_text()
