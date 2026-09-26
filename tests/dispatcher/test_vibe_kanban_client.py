# tests/dispatcher/test_vibe_kanban_client.py
import dataclasses
import json
import uuid
from pathlib import Path

import pytest

from dispatcher.config import LocalBoardConfig, VibeKanbanConfig
from dispatcher.vibe_kanban_client import (
    INITIAL_CARD_STATUS,
    LocalBoardClient,
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


@pytest.mark.parametrize("client", [NullKanbanClient, LocalBoardClient])
def test_every_board_offers_what_the_real_one_does(client) -> None:
    # The dispatcher calls these unconditionally; a missing name here would be
    # an AttributeError on a harness that has the other board, or none.
    surface = {name for name in vars(VibeKanbanClient) if not name.startswith("_")}
    assert surface <= set(dir(client))


def test_the_local_board_adds_nothing_to_the_shared_surface() -> None:
    # Held to the *same* surface, not a superset: a public method only one
    # board has is one `dispatcher.py` cannot call, because it does not know
    # which implementation it got.
    public = {
        name
        for client in (VibeKanbanClient, LocalBoardClient)
        for name in vars(client)
        if not name.startswith("_")
    }
    assert {name for name in vars(LocalBoardClient) if not name.startswith("_")} == public


def test_a_failed_tool_call_raises_with_the_reason() -> None:
    result = FakeResult(content=[FakeBlock("status 'Shipped' not found")], is_error=True)

    with pytest.raises(RuntimeError, match="update_issue failed: status 'Shipped' not found"):
        _unwrap("update_issue", result)


def test_a_failed_tool_call_raises_even_without_a_reason() -> None:
    with pytest.raises(RuntimeError, match="no reason given"):
        _unwrap("update_issue", FakeResult(is_error=True))


# --- the board that is a directory ----------------------------------------


def local_board(tmp_path: Path, name: str = "board") -> LocalBoardClient:
    # Deliberately a path that does not exist yet: the directory is the first
    # write's job, and a board nobody has written to is a board with no issues.
    return LocalBoardClient(LocalBoardConfig(dir=str(tmp_path / name)))


def stored_card(board: LocalBoardClient, issue_id: str) -> dict:
    return json.loads((Path(board.config.dir) / f"{issue_id}.json").read_text())


def test_local_board_round_trips_a_created_issue(tmp_path: Path) -> None:
    board = local_board(tmp_path)

    issue_id = board.create_issue("Add a /healthz", "so the stack can be probed")

    issue = board.get_issue(issue_id)
    assert issue is not None
    assert issue.issue_id == issue_id
    assert issue.title == "Add a /healthz"
    assert issue.status == INITIAL_CARD_STATUS
    # A display handle is the UI's problem; nothing here renders one.
    assert issue.simple_id is None
    # The description has no home on KanbanIssue, so the stored document is
    # where the round-trip V2.5 could never verify is verifiable.
    card = stored_card(board, issue_id)
    assert card["description"] == "so the stack can be probed"
    assert card["created_at"]


def test_local_board_mints_a_uuid4(tmp_path: Path) -> None:
    # cli.py rejects a --kanban-issue-id that is not a uuid, so an id this
    # board mints has to be one the operator can pass back in.
    issue_id = local_board(tmp_path).create_issue("Add a /healthz")

    assert uuid.UUID(issue_id).version == 4


def test_local_board_keeps_a_description_it_was_not_given(tmp_path: Path) -> None:
    board = local_board(tmp_path)

    issue_id = board.create_issue("Add a /healthz")

    assert stored_card(board, issue_id)["description"] is None


def test_local_board_creates_its_directory_on_first_write(tmp_path: Path) -> None:
    board = local_board(tmp_path, "not-there-yet")

    board.create_issue("Add a /healthz")

    assert Path(board.config.dir).is_dir()


def test_local_board_with_no_directory_is_a_board_with_no_issues(tmp_path: Path) -> None:
    board = local_board(tmp_path, "never-written")

    assert board.list_issues() == []
    assert board.get_issue(ISSUE_ID) is None


def test_local_board_get_issue_is_a_lookup_not_a_failure(tmp_path: Path) -> None:
    board = local_board(tmp_path)
    board.create_issue("Add a /healthz")

    assert board.get_issue(ISSUE_ID) is None


def test_local_board_set_status_moves_a_stored_status(tmp_path: Path) -> None:
    board = local_board(tmp_path)
    issue_id = board.create_issue("Add a /healthz", "why")

    board.set_status(issue_id, "in_progress:implementador")

    issue = board.get_issue(issue_id)
    assert issue is not None
    # Stored verbatim, role included: there is no column here to map onto, and
    # the role is the dimension a generic board flattens away.
    assert issue.status == "in_progress:implementador"
    # A status move is not an edit to the rest of the card.
    assert issue.title == "Add a /healthz"
    assert stored_card(board, issue_id)["description"] == "why"


def test_local_board_set_status_raises_on_an_unknown_id(tmp_path: Path) -> None:
    board = local_board(tmp_path)
    board.create_issue("Add a /healthz")

    # A remote board could legitimately be out of sync; local storage that has
    # forgotten a card the task file still points at is a bug. Every call site
    # wraps this in try/except, so raising surfaces it without stopping a run.
    with pytest.raises(LookupError, match=ISSUE_ID):
        board.set_status(ISSUE_ID, "done")


def test_local_board_filters_by_a_stored_field(tmp_path: Path) -> None:
    board = local_board(tmp_path)
    done = board.create_issue("Add a /healthz", "probe me")
    board.create_issue("Add a /readyz", "probe me too")
    board.set_status(done, "done")

    assert [i.issue_id for i in board.list_issues(status="done")] == [done]
    assert [i.issue_id for i in board.list_issues(description="probe me")] == [done]
    assert len(board.list_issues()) == 2


def test_local_board_reads_an_unset_filter_as_any(tmp_path: Path) -> None:
    board = local_board(tmp_path)
    board.create_issue("Add a /healthz")

    # The Vibe Kanban client drops a None filter, and the dispatcher's own
    # callers pass optional filters through the same way.
    assert len(board.list_issues(status=None)) == 1


def test_local_board_rejects_an_unknown_filter_key(tmp_path: Path) -> None:
    board = local_board(tmp_path)
    board.create_issue("Add a /healthz")

    # Answering with every issue is the failure that costs the caller silently.
    with pytest.raises(ValueError, match="no issue field 'assignee'"):
        board.list_issues(assignee="cuenta1")


def test_local_board_rejects_an_unknown_key_even_when_it_is_unset(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no issue field 'assignee'"):
        local_board(tmp_path).list_issues(assignee=None)


def test_local_board_lists_oldest_first(tmp_path: Path) -> None:
    board = local_board(tmp_path)
    first = board.create_issue("Add a /healthz")
    second = board.create_issue("Add a /readyz")
    # Same directory listing order either way: the ids are random, so creation
    # time is the only order a reader can rely on.
    Path(board.config.dir, f"{second}.json").write_text(
        json.dumps({**stored_card(board, second), "created_at": "2099-01-01T00:00:00+00:00"})
    )

    assert [i.issue_id for i in board.list_issues()] == [first, second]


def test_two_local_boards_share_one_directory(tmp_path: Path) -> None:
    writer = local_board(tmp_path)
    reader = local_board(tmp_path)

    issue_id = writer.create_issue("Add a /healthz", "why")

    # Nothing is cached in the client: the directory is the board, which is
    # what lets a `dispatch` run and a later `dispatch learnings` agree.
    assert reader.get_issue(issue_id) is not None
    reader.set_status(issue_id, "done")
    issue = writer.get_issue(issue_id)
    assert issue is not None
    assert issue.status == "done"


def test_local_board_ignores_what_it_did_not_write(tmp_path: Path, caplog) -> None:
    board = local_board(tmp_path)
    issue_id = board.create_issue("Add a /healthz")
    directory = Path(board.config.dir)
    (directory / "notes.txt").write_text("not a card")
    (directory / ".card-half.tmp").write_text("{")
    (directory / "broken.json").write_text("{ not json")

    assert [i.issue_id for i in board.list_issues()] == [issue_id]
    assert "unreadable card" in caplog.text


def test_local_board_keeps_an_id_from_naming_a_file_elsewhere(tmp_path: Path, caplog) -> None:
    board = local_board(tmp_path)
    board.create_issue("Add a /healthz")
    (tmp_path / "escaped.json").write_text(json.dumps({"issue_id": "escaped", "title": "no"}))

    # The id comes off a task file's frontmatter, which is text a phase wrote.
    assert board.get_issue("../escaped") is None
    with pytest.raises(LookupError):
        board.set_status("../escaped", "done")

    # Refused out loud: an id with reach is not the same event as a card the
    # board never had, and only the log tells them apart.
    assert "cannot name a card" in caplog.text


def test_local_board_says_out_loud_that_a_document_is_not_a_card(tmp_path: Path, caplog) -> None:
    board = local_board(tmp_path)
    issue_id = board.create_issue("Add a /healthz")
    # Parses as JSON, but carries no card: the shape `_read_path` requires is
    # a dict with a string `issue_id`.
    (Path(board.config.dir) / "listy.json").write_text(json.dumps(["not", "a", "card"]))

    assert [i.issue_id for i in board.list_issues()] == [issue_id]
    assert "no string issue_id" in caplog.text


def test_local_board_get_issue_does_not_raise_on_an_id_no_path_can_hold(tmp_path: Path) -> None:
    board = local_board(tmp_path)
    board.create_issue("Add a /healthz")

    # `get_issue` is a lookup and does not raise, for any id: a task file's
    # `kanban_issue_id` is a YAML scalar, and a double-quoted one can carry an
    # escape no filesystem call accepts ("embedded null byte" is a ValueError,
    # not an OSError). A lookup answers "no such issue" rather than exploding.
    assert board.get_issue("nul\x00byte") is None
    with pytest.raises(LookupError):
        board.set_status("nul\x00byte", "done")
