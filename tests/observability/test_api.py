"""The read API, against a temporary directory standing in for the mounts.

No running harness anywhere in here: the fixture builds the four directories
the compose service mounts (`/state`, `/data/.hive/tasks`, `/data/projects`,
the events volume) under `tmp_path` and writes a `config.yaml` pointing at
them, so what is exercised is the same `dispatcher/config.py` load the
dispatcher does. Fixtures are written through the dispatcher's own writers —
`state_machine.set_state`, `context_transfer.write_task_file`,
`LocalBoardClient.create_issue` — so a change to one of those formats shows up
here as a failure rather than as two files that disagree.
"""

from __future__ import annotations

import base64
import json
import types
from pathlib import Path

import pytest
import yaml

from dispatcher import context_transfer, state_machine
from dispatcher.config import LocalBoardConfig
from dispatcher.state_machine import AccountState
from dispatcher.vibe_kanban_client import LocalBoardClient
from observability.api.app import _is_bare_task_id, create_app
from observability.collector import db as collector_db

# sha256("password")
PASSWORD_HASH = "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8"

#: Every route, for the checks that are true of all of them.
ROUTES = ["/api/tasks", "/api/tasks/T-1", "/api/accounts", "/api/events", "/api/debt"]


def _auth() -> dict:
    token = base64.b64encode(b"admin:password").decode()
    return {"Authorization": f"Basic {token}"}


def _harness(
    tmp_path: Path,
    projects: tuple[str, ...] = ("ia-harness",),
    board: str | None = "local",
    accounts: tuple[str, ...] = ("cuenta1",),
    with_db: bool = True,
) -> types.SimpleNamespace:
    """The mounts, the config and a test client over them."""
    state_dir = tmp_path / "state"
    tasks_dir = tmp_path / "hive" / "tasks"
    projects_root = tmp_path / "projects"
    board_dir = tmp_path / "board"
    events_dir = tmp_path / "events"
    for directory in (state_dir, tasks_dir, projects_root, events_dir):
        directory.mkdir(parents=True)
    for slug in projects:
        (projects_root / slug).mkdir()

    raw: dict = {
        "accounts": [{"name": name, "container": f"agent-{name}"} for name in accounts],
        "projects_root": str(projects_root),
        "hive_tasks_dir": str(tasks_dir),
        "state_dir": str(state_dir),
        "collector_url": "http://collector:8787/events",
    }
    if board == "local":
        raw["local_board"] = {"dir": str(board_dir)}
    elif board == "vibe":
        raw["vibe_kanban"] = {"command": ["npx", "vibe-kanban", "mcp"]}
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw))

    db_path = str(events_dir / "events.db")
    if with_db:
        collector_db.init_db(db_path)

    app = create_app(str(config_path), db_path, "admin", PASSWORD_HASH)
    return types.SimpleNamespace(
        client=app.test_client(),
        state_dir=str(state_dir),
        tasks_dir=str(tasks_dir),
        projects_root=projects_root,
        board=LocalBoardClient(LocalBoardConfig(dir=str(board_dir))),
        board_dir=board_dir,
        db_path=db_path,
    )


def _get(harness: types.SimpleNamespace, path: str):
    return harness.client.get(path, headers=_auth())


def _task(tasks_dir: str, task_id: str, **fields) -> None:
    context_transfer.write_task_file(
        context_transfer.task_file_path(tasks_dir, task_id),
        context_transfer.TaskFile(
            task_id=task_id,
            status=fields.pop("status", "in_progress"),
            owner=fields.pop("owner", None),
            depends_on=[],
            heartbeat=fields.pop("heartbeat", None),
            body=fields.pop("body", ""),
            **fields,
        ),
    )


def _debt_index(projects_root: Path, slug: str, text: str) -> Path:
    path = projects_root / slug / "docs" / "debt" / "README.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


_INDEX = """# Debt

| id | what | where | fix | card |
|---|---|---|---|---|
| `T-008-D1` | No lock around set_status | two dispatch processes | a lock per card | none |
| `T-008-D2` | **Resolved 2026-09-26.** A card that will not parse | Phase 1's read API | the envelope | `abc` |
"""


# --- the envelope ---------------------------------------------------------


@pytest.mark.parametrize("route", ROUTES)
def test_every_route_answers_401_without_credentials(tmp_path: Path, route: str) -> None:
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")

    resp = harness.client.get(route)

    assert resp.status_code == 401


@pytest.mark.parametrize("route", ROUTES)
def test_every_route_answers_400_for_a_parameter_it_does_not_have(
    tmp_path: Path, route: str
) -> None:
    # A filter that silently does nothing costs the caller more than no filter:
    # the reasoning LocalBoardClient.list_issues follows for an unknown key.
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")

    resp = _get(harness, f"{route}?statuss=done")

    assert resp.status_code == 400
    assert "statuss" in resp.get_json()["error"]


@pytest.mark.parametrize("route", ROUTES)
def test_every_route_answers_the_same_envelope(tmp_path: Path, route: str) -> None:
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    _debt_index(harness.projects_root, "ia-harness", _INDEX)

    resp = _get(harness, route)

    assert resp.status_code == 200
    assert set(resp.get_json()) == {"data", "warnings"}
    assert isinstance(resp.get_json()["warnings"], list)


# --- tasks ----------------------------------------------------------------


def test_tasks_reports_the_fields_the_task_file_carries(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _task(
        harness.tasks_dir,
        "T-1",
        status="in_progress",
        owner="cuenta1",
        heartbeat="2026-09-26T18:00:00+00:00",
        description="Do the thing",
        resolved_debt=["T-008-D2"],
    )

    [task] = _get(harness, "/api/tasks").get_json()["data"]

    assert task == {
        "task_id": "T-1",
        "status": "in_progress",
        "owner": "cuenta1",
        "heartbeat": "2026-09-26T18:00:00+00:00",
        "description": "Do the thing",
        "kanban_issue_id": None,
        "resolved_debt": ["T-008-D2"],
        "card": None,
    }


def test_a_task_file_that_will_not_parse_is_a_warning_and_not_a_missing_list(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    _task(harness.tasks_dir, "T-3")
    broken = Path(context_transfer.task_file_path(harness.tasks_dir, "T-2"))
    broken.write_text("no frontmatter here at all\n")

    body = _get(harness, "/api/tasks").get_json()

    assert [task["task_id"] for task in body["data"]] == ["T-1", "T-3"]
    assert len(body["warnings"]) == 1
    assert str(broken) in body["warnings"][0]


def test_a_task_file_whose_frontmatter_does_not_scan_is_a_warning_too(tmp_path: Path) -> None:
    # yaml.YAMLError is not a ValueError, so a reader that caught only OSError
    # and ValueError would 500 on exactly the card an operator hand-edited.
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    broken = Path(context_transfer.task_file_path(harness.tasks_dir, "T-2"))
    broken.write_text("---\nstatus: [unclosed\n---\n\nbody\n")

    body = _get(harness, "/api/tasks").get_json()

    assert [task["task_id"] for task in body["data"]] == ["T-1"]
    assert str(broken) in body["warnings"][0]


def test_a_task_carries_the_card_its_issue_id_points_at(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    issue_id = harness.board.create_issue("Phase 1", "a read API")
    _task(harness.tasks_dir, "T-1", kanban_issue_id=issue_id)

    [task] = _get(harness, "/api/tasks").get_json()["data"]

    assert task["card"] == {
        "issue_id": issue_id,
        "title": "Phase 1",
        "status": "pending",
        "simple_id": None,
    }


def test_a_card_id_the_board_does_not_hold_is_a_null_card(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1", kanban_issue_id="11111111-2222-3333-4444-555555555555")

    [task] = _get(harness, "/api/tasks").get_json()["data"]

    assert task["card"] is None


def test_a_harness_with_no_board_is_not_an_error(tmp_path: Path) -> None:
    harness = _harness(tmp_path, board=None)
    _task(harness.tasks_dir, "T-1", kanban_issue_id="11111111-2222-3333-4444-555555555555")

    body = _get(harness, "/api/tasks").get_json()

    assert body == {
        "data": [
            {
                "task_id": "T-1",
                "status": "in_progress",
                "owner": None,
                "heartbeat": None,
                "description": None,
                "kanban_issue_id": "11111111-2222-3333-4444-555555555555",
                "resolved_debt": [],
                "card": None,
            }
        ],
        "warnings": [],
    }


def test_a_card_that_will_not_parse_is_named_in_warnings(tmp_path: Path) -> None:
    # T-008-D2: list_issues skips it with a log line nobody reads, so the board
    # comes back quietly short. unreadable() is what puts it in the envelope.
    harness = _harness(tmp_path)
    good = harness.board.create_issue("Phase 1")
    broken = harness.board_dir / "99999999-8888-7777-6666-555555555555.json"
    broken.write_text("{truncated")
    _task(harness.tasks_dir, "T-1", kanban_issue_id=good)

    body = _get(harness, "/api/tasks").get_json()

    assert body["data"][0]["card"]["issue_id"] == good
    assert len(body["warnings"]) == 1
    assert str(broken) in body["warnings"][0]


def test_a_remote_board_is_a_warning_rather_than_a_dialled_subprocess(tmp_path: Path) -> None:
    # docs/decisions.md ADR 3: this service does not spawn Vibe Kanban's MCP
    # server per request. The caller is told, rather than left reading nulls.
    harness = _harness(tmp_path, board="vibe")
    _task(harness.tasks_dir, "T-1", kanban_issue_id="11111111-2222-3333-4444-555555555555")

    body = _get(harness, "/api/tasks").get_json()

    assert body["data"][0]["card"] is None
    assert "vibe_kanban" in body["warnings"][0]


def test_one_task_by_id(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1", description="Do the thing")
    _task(harness.tasks_dir, "T-2")

    body = _get(harness, "/api/tasks/T-1").get_json()

    assert body["data"]["task_id"] == "T-1"
    assert body["data"]["description"] == "Do the thing"


def test_one_task_by_an_id_no_file_answers_404(tmp_path: Path) -> None:
    harness = _harness(tmp_path)

    resp = _get(harness, "/api/tasks/T-404")

    assert resp.status_code == 404
    assert "T-404" in resp.get_json()["error"]


def test_an_id_that_would_name_a_file_elsewhere_answers_404(tmp_path: Path) -> None:
    # The same reach LocalBoardClient._card_path refuses: an id with a
    # separator in it is not a task, it is a bug with reach.
    harness = _harness(tmp_path)
    (Path(harness.tasks_dir).parent / "secret.md").write_text("---\ntask_id: x\nstatus: y\n---\n")

    resp = _get(harness, "/api/tasks/..%2Fsecret")

    assert resp.status_code == 404


@pytest.mark.parametrize("task_id", ["../secret", "a/b", "sub/T-1", ""])
def test_only_a_bare_id_can_name_a_task_file(task_id: str) -> None:
    # Werkzeug's converter declines a `/` before the view is reached, so this
    # is the second lock: it is what a later `<path:task_id>` would hit.
    assert not _is_bare_task_id(task_id)


def test_a_task_id_that_names_a_file_in_the_directory_passes(tmp_path: Path) -> None:
    assert _is_bare_task_id("T-009")


def test_one_task_that_will_not_parse_is_null_with_a_warning_not_a_404(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    broken = Path(context_transfer.task_file_path(harness.tasks_dir, "T-1"))
    broken.write_text("no frontmatter here at all\n")

    resp = _get(harness, "/api/tasks/T-1")

    assert resp.status_code == 200
    assert resp.get_json()["data"] is None
    assert str(broken) in resp.get_json()["warnings"][0]


# --- accounts -------------------------------------------------------------


def test_accounts_reports_the_pool_from_the_state_directory(tmp_path: Path) -> None:
    harness = _harness(tmp_path, accounts=("cuenta1", "cuenta2"))
    state_machine.set_state(harness.state_dir, "cuenta1", AccountState.BUSY, "T-1")
    state_machine.record_rate_limit(harness.state_dir, "cuenta2", at=1700000000.0)

    body = _get(harness, "/api/accounts").get_json()

    assert body["warnings"] == []
    assert body["data"] == [
        {
            "name": "cuenta1",
            "container": "agent-cuenta1",
            "state": "BUSY",
            "current_task": "T-1",
            "rate_limited_at": None,
        },
        {
            "name": "cuenta2",
            "container": "agent-cuenta2",
            "state": "IDLE",
            "current_task": None,
            "rate_limited_at": 1700000000.0,
        },
    ]


def test_an_account_with_no_state_file_reads_as_idle(tmp_path: Path) -> None:
    harness = _harness(tmp_path)

    [account] = _get(harness, "/api/accounts").get_json()["data"]

    assert (account["state"], account["current_task"]) == ("IDLE", None)


def test_an_unreadable_account_state_file_is_a_warning_naming_it(tmp_path: Path) -> None:
    harness = _harness(tmp_path, accounts=("cuenta1", "cuenta2"))
    state_machine.set_state(harness.state_dir, "cuenta1", AccountState.IDLE)
    state_machine.set_state(harness.state_dir, "cuenta2", AccountState.BUSY, "T-1")
    # Whatever file state_machine wrote for cuenta1, so the warning's path and
    # the writer's path cannot drift apart without this failing.
    [written] = [path for path in Path(harness.state_dir).iterdir() if "cuenta1" in path.name]
    written.write_text("{truncated")

    body = _get(harness, "/api/accounts").get_json()

    assert str(written) in body["warnings"][0]
    assert [account["name"] for account in body["data"]] == ["cuenta1", "cuenta2"]
    assert body["data"][0]["state"] is None
    assert body["data"][1]["state"] == "BUSY"


# --- events ---------------------------------------------------------------


def test_events_come_back_newest_first_with_the_payload_parsed(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    collector_db.insert_event(harness.db_path, "agent-cuenta1", "PreToolUse", {"tool": "Bash"})
    collector_db.insert_event(harness.db_path, "agent-cuenta2", "Stop", {"ok": True})

    body = _get(harness, "/api/events").get_json()

    assert [event["event_type"] for event in body["data"]] == ["Stop", "PreToolUse"]
    assert body["data"][1]["payload"] == {"tool": "Bash"}
    assert body["warnings"] == []


def test_events_filter_by_source_app(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    collector_db.insert_event(harness.db_path, "agent-cuenta1", "PreToolUse", {})
    collector_db.insert_event(harness.db_path, "agent-cuenta2", "Stop", {})

    body = _get(harness, "/api/events?source_app=agent-cuenta2").get_json()

    assert [event["source_app"] for event in body["data"]] == ["agent-cuenta2"]


def test_since_returns_only_ids_above_it(tmp_path: Path) -> None:
    # `since` is an id and not a time: Phase 3 tails by remembering the last id
    # it saw, which is the only stable order these rows have.
    harness = _harness(tmp_path)
    for index in range(5):
        collector_db.insert_event(harness.db_path, "agent-cuenta1", f"E{index}", {})

    body = _get(harness, "/api/events?since=3").get_json()

    assert [event["id"] for event in body["data"]] == [5, 4]


def test_since_composes_with_limit(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    for index in range(6):
        collector_db.insert_event(harness.db_path, "agent-cuenta1", f"E{index}", {})

    body = _get(harness, "/api/events?since=2&limit=2").get_json()

    assert [event["id"] for event in body["data"]] == [6, 5]


def test_the_plans_own_empty_parameters_are_a_legal_request(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    collector_db.insert_event(harness.db_path, "agent-cuenta1", "Stop", {})

    resp = _get(harness, "/api/events?limit=&source_app=&since=")

    assert resp.status_code == 200
    assert len(resp.get_json()["data"]) == 1


@pytest.mark.parametrize("query", ["limit=lots", "limit=0", "limit=-1", "since=soon"])
def test_a_parameter_that_is_not_an_integer_answers_400(tmp_path: Path, query: str) -> None:
    harness = _harness(tmp_path)

    resp = _get(harness, f"/api/events?{query}")

    assert resp.status_code == 400


def test_a_missing_events_database_is_an_empty_list_and_a_warning(tmp_path: Path) -> None:
    # A harness that has never run has no events, and this service may not
    # create the database: the volume is :ro and init_db runs CREATE TABLE.
    harness = _harness(tmp_path, with_db=False)

    resp = _get(harness, "/api/events")

    assert resp.status_code == 200
    assert resp.get_json()["data"] == []
    assert harness.db_path in resp.get_json()["warnings"][0]
    assert not Path(harness.db_path).exists()


def test_a_database_that_is_not_one_is_a_warning_rather_than_a_500(tmp_path: Path) -> None:
    harness = _harness(tmp_path, with_db=False)
    Path(harness.db_path).write_text("this is not a database")

    resp = _get(harness, "/api/events")

    assert resp.status_code == 200
    assert resp.get_json()["data"] == []
    assert harness.db_path in resp.get_json()["warnings"][0]


# --- debt -----------------------------------------------------------------


def test_debt_rows_come_back_as_the_index_writes_them(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _debt_index(harness.projects_root, "ia-harness", _INDEX)

    body = _get(harness, "/api/debt").get_json()

    assert body["warnings"] == []
    assert [row["id"] for row in body["data"]] == ["T-008-D1", "T-008-D2"]
    assert body["data"][0] == {
        "id": "T-008-D1",
        "what": "No lock around set_status",
        "where": "two dispatch processes",
        "fix": "a lock per card",
        "card": "none",
        "resolved": False,
    }
    assert body["data"][1]["resolved"] is True


def test_project_is_optional_with_one_checkout_and_required_with_two(tmp_path: Path) -> None:
    one = _harness(tmp_path / "one", projects=("ia-harness",))
    _debt_index(one.projects_root, "ia-harness", _INDEX)
    two = _harness(tmp_path / "two", projects=("ia-harness", "scratch"))
    _debt_index(two.projects_root, "scratch", _INDEX)

    assert _get(one, "/api/debt").status_code == 200

    resp = _get(two, "/api/debt")
    assert resp.status_code == 400
    assert "scratch" in resp.get_json()["error"]
    assert _get(two, "/api/debt?project=scratch").status_code == 200


def test_a_project_with_no_checkout_answers_404(tmp_path: Path) -> None:
    harness = _harness(tmp_path)

    resp = _get(harness, "/api/debt?project=nope")

    assert resp.status_code == 404
    assert "nope" in resp.get_json()["error"]


def test_a_projects_root_with_no_checkouts_answers_404(tmp_path: Path) -> None:
    harness = _harness(tmp_path, projects=())

    assert _get(harness, "/api/debt").status_code == 404


def test_a_checkout_with_no_debt_index_is_empty_with_a_warning(tmp_path: Path) -> None:
    harness = _harness(tmp_path)

    body = _get(harness, "/api/debt").get_json()

    assert body["data"] == []
    assert "docs/debt/README.md" in body["warnings"][0]


# --- what this service must never do --------------------------------------


def test_no_route_accepts_a_write(tmp_path: Path) -> None:
    # Phase 4 puts writes behind an action queue; there are none here, and a
    # POST that started working would be the first sign that changed.
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")

    for route in ROUTES:
        for method in (harness.client.post, harness.client.put, harness.client.delete):
            assert method(route, headers=_auth()).status_code == 405


def test_reading_the_events_endpoint_creates_no_schema(tmp_path: Path) -> None:
    # db.init_db is never called from here: it runs CREATE TABLE against a
    # volume mounted :ro, which is a 500 on the first request of a cold start.
    harness = _harness(tmp_path, with_db=False)

    _get(harness, "/api/events")

    assert sorted(path.name for path in (tmp_path / "events").iterdir()) == []


def test_the_task_endpoints_write_nothing(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1", kanban_issue_id=harness.board.create_issue("Phase 1"))
    before = {
        path: path.read_bytes()
        for path in (*Path(harness.tasks_dir).iterdir(), *harness.board_dir.iterdir())
    }

    _get(harness, "/api/tasks")
    _get(harness, "/api/tasks/T-1")

    assert {path: path.read_bytes() for path in before} == before
    assert json.loads(next(iter(harness.board_dir.iterdir())).read_text())["status"] == "pending"
