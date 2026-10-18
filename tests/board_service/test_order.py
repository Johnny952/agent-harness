"""Tags, rank and the Ready order through the board service (T-085, ADR 130),
over `/mcp` and over the console's HTTP endpoints."""

import pytest

from board_service import app as service
from dispatcher import tasks
from tests.board_service.test_app import DESCRIPTION, call, cfg, client, hive, ok, task  # noqa: F401
from tests.board_service.test_writes import OTHER, error, propose, read

STEP = tasks.RANK_STEP


def ids(out: dict) -> list[str]:
    return [row["id"] for row in out["items"]]


# --- the schemas --------------------------------------------------------------


def test_the_schemas_name_the_new_arguments_and_stay_closed():
    propose_schema = service.TOOLS["backlog_propose"]["inputSchema"]
    edit_schema = service.TOOLS["task_edit"]["inputSchema"]
    assert propose_schema["properties"]["tags"]["type"] == "array"
    assert "rank" not in propose_schema["properties"]
    assert edit_schema["properties"]["rank"]["type"] == "integer"
    assert edit_schema["properties"]["tags"]["type"] == "array"
    assert propose_schema["additionalProperties"] is False
    assert edit_schema["additionalProperties"] is False


@pytest.mark.parametrize(
    "rank, needle",
    [
        (True, "rank must be an integer"),
        ("3", "rank must be an integer"),
        (1.5, "rank must be an integer"),
        (-1, "rank must be at least 0"),
        (tasks.MAX_RANK + 1, "rank must be at most"),
    ],
)
def test_a_bad_rank_is_refused_before_the_writer(client, hive, rank, needle):
    propose(client)
    assert needle in error(client, "task_edit", {"task_id": "T-001", "rank": rank})
    assert read(hive, "T-001").rank == STEP


def test_a_tag_that_is_not_a_string_is_refused(client, hive):
    assert "tags[1] must be a string" in error(
        client, "backlog_propose", {"title": "A", "description": DESCRIPTION, "tags": ["a", 1]}
    )
    assert list(hive.iterdir()) == []


def test_proposing_a_rank_is_refused(client, hive):
    assert "takes no argument 'rank'" in error(
        client, "backlog_propose", {"title": "A", "description": DESCRIPTION, "rank": 1}
    )


def test_a_blank_tag_is_the_writer_s_refusal(client, hive):
    assert "blank" in error(
        client, "backlog_propose", {"title": "A", "description": DESCRIPTION, "tags": [" "]}
    )


# --- over /mcp ----------------------------------------------------------------


def test_tags_and_rank_are_listed_and_got(client, hive):
    propose(client, tags=["debt", " debt", "api"])
    row = ok(client, "board_list")["items"][0]
    assert (row["tags"], row["rank"]) == (["debt", "api"], STEP)
    frontmatter = ok(client, "board_get", {"task_id": "T-001"})["item"]["frontmatter"]
    assert (frontmatter["tags"], frontmatter["rank"]) == (["debt", "api"], STEP)


def test_task_edit_reorders_and_retags(client, hive):
    propose(client)
    propose(client, title="B", description=OTHER)
    assert ok(client, "task_edit", {"task_id": "T-002", "rank": 1, "tags": ["x"]})["changed"] == [
        "tags", "rank",
    ]
    assert ids(ok(client, "board_list", {"place": "backlog"})) == ["T-002", "T-001"]
    assert ok(client, "task_edit", {"task_id": "T-002", "tags": []})["changed"] == ["tags"]
    assert "tags" not in ok(client, "board_get", {"task_id": "T-002"})["item"]["frontmatter"]


def test_the_backlog_is_listed_by_bucket_then_rank_and_the_board_by_id(client, hive):
    task(hive, "T-100", priority="low")
    task(hive, "T-050", priority="high", rank=9)
    propose(client, title="Low", priority="low")  # T-101
    propose(client, title="High", description=OTHER, priority="high")  # T-102
    propose(client, title="Normal", description=DESCRIPTION + "\nMore.\n")  # T-103
    assert ids(ok(client, "board_list")) == ["T-050", "T-100", "T-102", "T-103", "T-101"]
    # The high end counts the board's T-050.
    assert read(hive, "T-102").rank == 9 + STEP


def test_ready_is_the_pickup_order(client, hive):
    task(hive, "T-1", status="done", merged={"target": "main"})
    task(hive, "T-2", priority="low", depends_on=["T-1"])
    task(hive, "T-3", priority="low")
    task(hive, "T-4", priority="high", depends_on=["T-3"], rank=1)
    task(hive, "T-5", priority="normal", rank=STEP)
    task(hive, "T-6", status="proposed", priority="high", depends_on=["T-2"])
    out = ok(client, "board_list")
    # T-3 is lifted by T-4, which waits on it; T-2 is not by the proposal T-6.
    assert out["ready"] == ["T-3", "T-5", "T-2"]
    # A filter narrows it and does not reorder it.
    assert ok(client, "board_list", {"text": "T-5"})["ready"] == ["T-5"]
    assert ok(client, "board_list", {"place": "backlog"})["ready"] == []


def test_ready_lifts_an_epic_s_tasks(client, hive):
    task(hive, "T-1", priority="normal")
    task(hive, "T-9", status="epic", priority="high", split_into=["T-9-S1"])
    task(hive, "T-9-S1", priority="low", epic="T-9", rank=STEP * 5)
    assert ok(client, "board_list")["ready"] == ["T-9-S1", "T-1"]


def test_a_bad_tag_or_rank_on_disk_is_a_warning_not_a_failure(client, hive):
    task(hive, "T-1", tags=["ok", ""], rank="first")
    out = ok(client, "board_list")
    assert (out["items"][0]["tags"], out["items"][0]["rank"]) == (["ok"], "first")
    assert any("tag" in w for w in out["warnings"])
    assert any("rank" in w for w in out["warnings"])
    assert out["ready"] == ["T-1"]


# --- over HTTP ----------------------------------------------------------------


def test_http_carries_tags_and_rank(client, hive):
    res = client.post("/api/backlog", json={"title": "A", "description": DESCRIPTION, "tags": ["api"]})
    assert res.status_code == 201
    assert read(hive, "T-001").tags == ["api"]

    res = client.patch("/api/tasks/T-001", json={"rank": 7, "tags": ["debt"]})
    assert (res.status_code, res.get_json()) == (200, {"id": "T-001", "changed": ["tags", "rank"]})
    assert (read(hive, "T-001").rank, read(hive, "T-001").tags) == (7, ["debt"])


@pytest.mark.parametrize("body", [{"rank": True}, {"rank": -3}, {"tags": "debt"}, {"rank": 1, "position": 2}])
def test_http_refuses_a_bad_rank_or_tags(client, hive, body):
    client.post("/api/backlog", json={"title": "A", "description": DESCRIPTION})
    res = client.patch("/api/tasks/T-001", json=body)
    assert res.status_code == 400
    assert read(hive, "T-001").rank == STEP
    assert read(hive, "T-001").tags is None
