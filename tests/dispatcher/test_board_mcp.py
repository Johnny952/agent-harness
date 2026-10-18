"""Each phase gets its board config (`docs/plans/board-service.md` step 7;
`docs/decisions.md` ADR 128)."""

import json
import urllib.parse

import pytest

import dispatcher.dispatcher as dispatcher_mod
from board_service import app as board_app
from dispatcher import allowlist, board_mcp
from dispatcher.config import PHASE_ROLES
from dispatcher.docker_exec import ClaudeResult
from tests.dispatcher.test_dispatcher import (  # noqa: F401 - fixtures used by name
    _USAGE_TEXT,
    _fake_worktree,
    _make_config,
    fake_git,
)

BOARD_TOOLS = [
    "mcp__board__board_list",
    "mcp__board__board_get",
    "mcp__board__backlog_propose",
    "mcp__board__task_comment",
]


def _server(mcp_config: str) -> dict:
    parsed = json.loads(mcp_config)
    assert list(parsed) == ["mcpServers"]
    assert list(parsed["mcpServers"]) == ["board"]
    return parsed["mcpServers"]["board"]


# --- the module -------------------------------------------------------------


def test_the_service_and_the_dispatcher_read_one_table() -> None:
    assert board_app.ROLE_TOOLS is board_mcp.ROLE_TOOLS
    assert board_app.OPERATOR == board_mcp.OPERATOR == "operator"
    assert board_mcp.ROLE_TOOLS == {
        "operator": (
            "board_list", "board_get", "backlog_propose", "task_edit", "task_comment",
            "backlog_accept",
        ),
        "arquitecto": ("board_list", "board_get", "backlog_propose", "task_comment"),
    }


def test_the_default_url_is_the_service_name_and_its_port() -> None:
    assert board_mcp.DEFAULT_URL == f"http://board-service:{board_app.PORT}/mcp"


def test_the_tool_names_are_the_cli_s_mcp_names() -> None:
    assert board_mcp.allowed_tools("arquitecto") == BOARD_TOOLS
    assert board_mcp.allowed_tools("implementador") == []
    assert board_mcp.allowed_tools("nobody") == []


def test_the_mcp_config_is_one_http_server_with_the_role_in_the_query() -> None:
    value = board_mcp.mcp_config("http://board-service:8792/mcp", "arquitecto")

    assert " " not in value
    assert _server(value) == {
        "type": "http",
        "url": "http://board-service:8792/mcp?role=arquitecto",
    }


def test_the_role_is_quoted_into_the_query() -> None:
    url = _server(board_mcp.mcp_config("http://b/mcp", "a b&c"))["url"]

    assert url == "http://b/mcp?role=a+b%26c"
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(url).query) == {"role": ["a b&c"]}


def test_the_url_it_writes_is_one_the_service_accepts(tmp_path) -> None:
    """The service refuses an unknown role and any other parameter with 400,
    so the URL the dispatcher writes is checked against the service itself."""
    config = tmp_path / "config.yaml"
    config.write_text(
        "accounts: []\nprojects_root: /p\nhive_tasks_dir: "
        f"{tmp_path / 'hive'}\nstate_dir: /s\ncollector_url: http://c\n"
    )
    client = board_app.create_app(str(config)).test_client()
    url = urllib.parse.urlsplit(_server(board_mcp.mcp_config("http://b/mcp", "arquitecto"))["url"])

    response = client.post(
        f"{url.path}?{url.query}",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )

    assert response.status_code == 200
    names = [tool["name"] for tool in response.get_json()["result"]["tools"]]
    assert [f"mcp__board__{name}" for name in names] == BOARD_TOOLS


# --- the flags per role -----------------------------------------------------


def test_the_arquitecto_gets_the_server_and_its_tools(tmp_path) -> None:
    cfg = _make_config(tmp_path, allowed_tools=["Bash(git status*)"])

    flags = dispatcher_mod._phase_permission_flags(cfg, "arquitecto")

    assert _server(flags["mcp_config"])["url"] == "http://board-service:8792/mcp?role=arquitecto"
    assert flags["allowed_tools"] == ["Bash(git status*)", *BOARD_TOOLS]
    assert cfg.allowed_tools == ["Bash(git status*)"]


@pytest.mark.parametrize("role", sorted(PHASE_ROLES - {"arquitecto"}))
def test_every_other_phase_gets_neither(tmp_path, role) -> None:
    cfg = _make_config(tmp_path, allowed_tools=["Bash(git status*)"])
    off = _make_config(tmp_path, allowed_tools=["Bash(git status*)"], board_mcp_url=None)

    flags = dispatcher_mod._phase_permission_flags(cfg, role)

    assert "mcp_config" not in flags
    assert not any(tool.startswith("mcp__board__") for tool in flags["allowed_tools"])
    assert flags == dispatcher_mod._phase_permission_flags(off, role)


@pytest.mark.parametrize("url", [None, ""])
def test_turned_off_the_arquitecto_gets_neither(tmp_path, url) -> None:
    cfg = _make_config(tmp_path, allowed_tools=["Bash(git status*)"], board_mcp_url=url)

    flags = dispatcher_mod._phase_permission_flags(cfg, "arquitecto")

    assert "mcp_config" not in flags
    assert flags["allowed_tools"] == ["Bash(git status*)"]


def test_a_configured_url_is_the_one_written(tmp_path) -> None:
    cfg = _make_config(tmp_path, board_mcp_url="https://board.example:9000/mcp")

    flags = dispatcher_mod._phase_permission_flags(cfg, "arquitecto")

    assert _server(flags["mcp_config"])["url"] == "https://board.example:9000/mcp?role=arquitecto"


def test_the_board_names_follow_the_project_allowlist_with_no_repeat(tmp_path) -> None:
    cfg = _make_config(tmp_path, allowed_tools=["mcp__board__board_get"])
    allowlist.add(
        cfg.state_dir, "myproj", "arquitecto", "Bash(uv sync)", "operator", "t",
        {"task_id": "task-1", "role": "arquitecto", "n": 1,
         "filed_at": "2026-10-18T00:00:00+00:00"},
    )

    flags = dispatcher_mod._phase_permission_flags(cfg, "arquitecto", "myproj")

    assert flags["allowed_tools"] == [
        "mcp__board__board_get", "Bash(uv sync)", "mcp__board__board_list",
        "mcp__board__backlog_propose", "mcp__board__task_comment",
    ]


# --- a dispatched phase -----------------------------------------------------


def _calls(monkeypatch):
    calls = []

    def fake_exec_claude(container, workdir, prompt, resume_session_id=None, model=None, effort=None, timeout_seconds=None, **kwargs):
        if "usage" in prompt.lower():
            return ClaudeResult(session_id=None, result_text=_USAGE_TEXT, raw={})
        calls.append(dict(kwargs, resume_session_id=resume_session_id))
        return ClaudeResult(session_id="sess-1", result_text="ok", raw={"is_error": False})

    monkeypatch.setattr(dispatcher_mod.docker_exec, "exec_claude", fake_exec_claude)
    _fake_worktree(monkeypatch)
    return calls


@pytest.mark.parametrize("resume", [None, "sess-0"])
def test_an_arquitecto_phase_and_its_resume_carry_the_config(
    tmp_path, monkeypatch, fake_git, resume,
) -> None:
    cfg = _make_config(tmp_path)
    calls = _calls(monkeypatch)

    dispatcher_mod.dispatch_phase(
        cfg, "task-1", "myproj", "arquitecto", "plan it", resume_session_id=resume,
    )

    assert calls[0]["resume_session_id"] == resume
    assert _server(calls[0]["mcp_config"])["url"].endswith("?role=arquitecto")
    assert calls[0]["allowed_tools"] == BOARD_TOOLS


def test_a_revisor_phase_carries_no_config(tmp_path, monkeypatch, fake_git) -> None:
    cfg = _make_config(tmp_path)
    calls = _calls(monkeypatch)

    dispatcher_mod.dispatch_phase(cfg, "task-1", "myproj", "revisor", "review it")

    assert "mcp_config" not in calls[0]
    assert calls[0]["allowed_tools"] == []
