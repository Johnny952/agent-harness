# tests/dispatcher/test_vibe_kanban_client.py
import dataclasses

import pytest

from dispatcher.config import VibeKanbanConfig
from dispatcher.vibe_kanban_client import (
    NullKanbanClient,
    VibeKanbanClient,
    _payload,
    _unwrap,
)

ISSUE_ID = "0e1d2c3b-4a59-6878-9706-5a4b3c2d1e0f"
PROJECT_ID = "11112222-3333-4444-5555-666677778888"


def make_client(**overrides) -> VibeKanbanClient:
    kwargs = {"command": ["vibe-kanban", "mcp"], "project_id": PROJECT_ID}
    kwargs.update(overrides)
    return VibeKanbanClient(VibeKanbanConfig(**kwargs))


class Calls(list):
    """What would have gone over stdio, and the replies to answer with."""

    def __init__(self):
        super().__init__()
        self.returns: list = []


@pytest.fixture
def calls(monkeypatch) -> Calls:
    recorded = Calls()

    def fake_call(self, tool, arguments):
        recorded.append((tool, arguments))
        return recorded.returns.pop(0) if recorded.returns else {}

    monkeypatch.setattr(VibeKanbanClient, "_call", fake_call)
    return recorded


# --- create_issue ---------------------------------------------------------


def test_create_issue_returns_the_server_assigned_uuid(calls) -> None:
    calls.returns.append({"id": ISSUE_ID, "simple_id": "VK-7"})

    assert make_client().create_issue("Do Y", "why") == ISSUE_ID
    assert calls == [
        ("create_issue", {"title": "Do Y", "description": "why", "project_id": PROJECT_ID})
    ]


def test_create_issue_omits_an_absent_description(calls) -> None:
    calls.returns.append({"issue_id": ISSUE_ID})

    make_client().create_issue("Do Y")

    assert "description" not in calls[0][1]


def test_create_issue_lets_the_server_infer_the_project(calls) -> None:
    calls.returns.append({"issue_id": ISSUE_ID})

    make_client(project_id=None).create_issue("Do Y")

    # project_id is optional in the schema: a server running inside a linked
    # workspace knows its own project, and sending nothing is how it gets to.
    assert calls[0][1] == {"title": "Do Y"}


def test_create_issue_reads_a_nested_issue_object(calls) -> None:
    calls.returns.append({"issue": {"id": ISSUE_ID, "title": "Do Y"}})

    assert make_client().create_issue("Do Y") == ISSUE_ID


def test_create_issue_complains_when_no_id_comes_back(calls) -> None:
    calls.returns.append({"ok": True})

    with pytest.raises(RuntimeError, match="no issue id"):
        make_client().create_issue("Do Y")


# --- set_status -----------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "name"),
    [
        ("in_progress:implementer", "In Progress"),
        ("in_progress:reviewer", "In Progress"),
        ("blocked", "In Review"),
        ("done", "Done"),
    ],
)
def test_set_status_translates_dispatcher_vocabulary(calls, status: str, name: str) -> None:
    make_client().set_status(ISSUE_ID, status)

    # The role after the colon has no home on the board; the phase does.
    assert calls == [("update_issue", {"issue_id": ISSUE_ID, "status": name})]


def test_set_status_uses_a_renamed_column(calls) -> None:
    make_client(status_map={"done": "Shipped"}).set_status(ISSUE_ID, "done")

    assert calls[0][1]["status"] == "Shipped"


def test_set_status_skips_an_unmapped_status(calls, caplog) -> None:
    make_client(status_map={"done": "Done"}).set_status(ISSUE_ID, "blocked")

    # update_issue rejects any name the project doesn't have, so an unmapped
    # status is a no-op with a warning rather than a guaranteed error.
    assert calls == []
    assert "no kanban status mapped" in caplog.text


# --- reading issues -------------------------------------------------------


def test_list_issues_parses_a_plain_list(calls) -> None:
    calls.returns.append(
        [{"id": ISSUE_ID, "title": "Do X", "status": "In Progress", "simple_id": "VK-7"}]
    )

    issues = make_client().list_issues()

    assert len(issues) == 1
    assert issues[0].issue_id == ISSUE_ID
    assert issues[0].title == "Do X"
    assert issues[0].status == "In Progress"
    assert issues[0].simple_id == "VK-7"


def test_list_issues_unwraps_a_structured_result(calls) -> None:
    calls.returns.append({"issues": [{"issue_id": ISSUE_ID, "title": "Do X"}], "total": 1})

    assert [i.issue_id for i in make_client().list_issues()] == [ISSUE_ID]


def test_list_issues_reads_a_status_object(calls) -> None:
    calls.returns.append([{"id": ISSUE_ID, "title": "Do X", "status": {"name": "Done", "id": 3}}])

    assert make_client().list_issues()[0].status == "Done"


def test_list_issues_passes_filters_and_drops_the_unset_ones(calls) -> None:
    make_client().list_issues(simple_id="VK-7", status=None, limit=5)

    assert calls == [
        ("list_issues", {"simple_id": "VK-7", "limit": 5, "project_id": PROJECT_ID})
    ]


def test_list_issues_survives_a_shape_it_cannot_read(calls) -> None:
    calls.returns.append("Not found")

    assert make_client().list_issues() == []


def test_get_issue_reads_one_issue(calls) -> None:
    calls.returns.append({"id": ISSUE_ID, "title": "Do X", "status": "Done"})

    issue = make_client().get_issue(ISSUE_ID)

    assert calls == [("get_issue", {"issue_id": ISSUE_ID})]
    assert issue is not None
    assert issue.title == "Do X"


def test_get_issue_returns_none_for_an_unreadable_reply(calls) -> None:
    calls.returns.append(None)

    assert make_client().get_issue(ISSUE_ID) is None


# --- MCP response shapes --------------------------------------------------
#
# The server's replies are described nowhere it hands out, so the parser takes
# each shape an MCP server may legally answer with.


@dataclasses.dataclass
class FakeBlock:
    text: str


@dataclasses.dataclass
class FakeResult:
    content: list = dataclasses.field(default_factory=list)
    structured_content: dict | None = None
    is_error: bool = False


def test_payload_prefers_structured_content() -> None:
    result = FakeResult(
        content=[FakeBlock('{"id": "text-wins-not"}')],
        structured_content={"id": ISSUE_ID},
    )

    assert _payload(result) == {"id": ISSUE_ID}


def test_payload_falls_back_to_json_in_a_text_block() -> None:
    assert _payload(FakeResult(content=[FakeBlock('{"id": "%s"}' % ISSUE_ID)])) == {"id": ISSUE_ID}


def test_payload_returns_prose_as_it_came() -> None:
    assert _payload(FakeResult(content=[FakeBlock("Issue not found")])) == "Issue not found"


def test_payload_of_an_empty_reply_is_none() -> None:
    assert _payload(FakeResult()) is None


# --- the board a harness was never given ----------------------------------


def test_null_client_answers_every_call_without_a_board() -> None:
    client = NullKanbanClient()

    assert client.enabled is False
    assert client.list_issues(status="Done") == []
    assert client.get_issue(ISSUE_ID) is None
    assert client.create_issue("Do X", "why") is None
    assert client.set_status(ISSUE_ID, "done") is None


def test_null_client_offers_what_the_real_one_does() -> None:
    # The dispatcher calls these unconditionally; a missing name here would be
    # an AttributeError on a harness that simply has no board.
    surface = {name for name in vars(VibeKanbanClient) if not name.startswith("_")}
    assert surface <= set(dir(NullKanbanClient))


def test_a_failed_tool_call_raises_with_the_reason() -> None:
    result = FakeResult(content=[FakeBlock("status 'Shipped' not found")], is_error=True)

    with pytest.raises(RuntimeError, match="update_issue failed: status 'Shipped' not found"):
        _unwrap("update_issue", result)


def test_a_failed_tool_call_raises_even_without_a_reason() -> None:
    with pytest.raises(RuntimeError, match="no reason given"):
        _unwrap("update_issue", FakeResult(is_error=True))
