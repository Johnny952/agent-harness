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
import datetime as dt
import json
import types
from pathlib import Path

import pytest
import yaml

from dispatcher import context_transfer, docker_exec, learnings, state_machine
from dispatcher.config import LocalBoardConfig, load_config
from dispatcher.state_machine import AccountState
from dispatcher.vibe_kanban_client import LocalBoardClient
from observability.api import app as api_app
from observability.api.app import _is_bare_task_id, create_app
from observability.collector import db as collector_db

# sha256("password")
PASSWORD_HASH = "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8"

#: Every route, for the checks that are true of all of them.
ROUTES = [
    "/api/tasks",
    "/api/tasks/T-1",
    "/api/accounts",
    "/api/events",
    "/api/debt",
    "/api/phases",
    "/api/learnings",
]


def _auth() -> dict:
    token = base64.b64encode(b"admin:password").decode()
    return {"Authorization": f"Basic {token}"}


def _harness(
    tmp_path: Path,
    projects: tuple[str, ...] = ("ia-harness",),
    board: str | None = "local",
    accounts: tuple[str, ...] = ("cuenta1",),
    with_db: bool = True,
    heartbeat_ttl_seconds: int | None = None,
    token: str | None = None,
    primary_account: str | None = None,
    quota_threshold_pct: int | None = None,
    reserve_pct: int | None = None,
    quota_cooldown_seconds: int | None = None,
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
    if heartbeat_ttl_seconds is not None:
        # Left out by default, so `dispatcher/config.py`'s own default (120s) is
        # what `lock_expired` is derived against — the same value the dispatcher
        # runs with. Passed only where a test pins that the ttl comes from config
        # rather than from a constant in the api.
        raw["heartbeat_ttl_seconds"] = heartbeat_ttl_seconds
    for key, value in (
        # Left out by default for the reason above, and one more: the three
        # thresholds agree with `dispatcher/config.py`'s defaults today, so a
        # route answering literals of its own would pass every case that takes
        # them. `reserve_pct` is the one to watch — its default bends to
        # `quota_threshold_pct` (`_load_reserve_pct`) and an explicit value does
        # not, so the pinned case sets all three rather than one. An absent
        # `primary_account` marks nothing, which is why `is_primary` needs a case
        # that writes it.
        ("primary_account", primary_account),
        ("quota_threshold_pct", quota_threshold_pct),
        ("reserve_pct", reserve_pct),
        ("quota_cooldown_seconds", quota_cooldown_seconds),
    ):
        if value is not None:
            raw[key] = value
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw))

    db_path = str(events_dir / "events.db")
    if with_db:
        collector_db.init_db(db_path)

    app = create_app(str(config_path), db_path, "admin", PASSWORD_HASH, token)
    return types.SimpleNamespace(
        client=app.test_client(),
        config_path=str(config_path),
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
            depends_on=fields.pop("depends_on", []),
            heartbeat=fields.pop("heartbeat", None),
            body=fields.pop("body", ""),
            **fields,
        ),
    )


def _handoff(
    tasks_dir: str,
    task_id: str,
    role: str,
    payload: dict | None,
    round_num: int | None = None,
) -> str:
    """One phase record, through the dispatcher's own writer.

    `save_handoff` and not a hand-built JSON document, for the reason the file
    docstring gives: a change to the envelope's shape has to show up here as a
    failure rather than as two files that disagree.
    """
    return context_transfer.save_handoff(tasks_dir, task_id, role, payload, round_num=round_num)


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



def test_warnings_are_capped_and_the_last_one_tallies_what_was_left_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # T-009-D2: one document that will not parse is one warning, so a tasks
    # directory full of them built a list with no ceiling and serialised it
    # whole. The cap is on what the caller receives, never on what was found,
    # which is what the last line is for.
    monkeypatch.setattr(api_app, "MAX_WARNINGS", 4)
    harness = _harness(tmp_path)
    for index in range(6):
        Path(context_transfer.task_file_path(harness.tasks_dir, f"T-{index}")).write_text(
            "no frontmatter here at all\n"
        )

    warnings = _get(harness, "/api/tasks").get_json()["warnings"]

    assert len(warnings) == 4
    assert warnings[-1] == "and 3 more warnings, not listed"


def test_a_warnings_list_under_the_cap_is_the_list_itself(tmp_path: Path) -> None:
    # The cap must not cost a truthful short list its last entry.
    harness = _harness(tmp_path)
    broken = Path(context_transfer.task_file_path(harness.tasks_dir, "T-2"))
    broken.write_text("no frontmatter here at all\n")

    warnings = _get(harness, "/api/tasks").get_json()["warnings"]

    assert len(warnings) == 1
    assert str(broken) in warnings[0]

# --- tasks ----------------------------------------------------------------


def test_tasks_reports_the_fields_the_task_file_carries(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _task(
        harness.tasks_dir,
        "T-1",
        status="in_progress",
        owner="cuenta1",
        depends_on=["T-0"],
        heartbeat="2026-09-26T18:00:00+00:00",
        description="Do the thing",
        resolved_debt=["T-008-D2"],
        body="arquitecto: plan ready",
    )

    [task] = _get(harness, "/api/tasks").get_json()["data"]

    assert task == {
        "task_id": "T-1",
        "status": "in_progress",
        "owner": "cuenta1",
        "depends_on": ["T-0"],
        "heartbeat": "2026-09-26T18:00:00+00:00",
        "description": "Do the thing",
        "kanban_issue_id": None,
        "resolved_debt": ["T-008-D2"],
        "card": None,
        # A fixed timestamp in the past, so against any TTL this is a lock whose
        # holder stopped writing. The three values of this field have their own
        # tests below, against heartbeats relative to now.
        "lock_expired": True,
        # No `body`, and the fixture above wrote one: it is the detail route's
        # alone (`docs/decisions.md` ADR 21). Asserting the whole dict is what
        # makes moving it into `_task` fail here rather than pass quietly.
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


def test_frontmatter_that_scans_into_a_scalar_is_a_warning_too(tmp_path: Path) -> None:
    # The third way a task file fails, and the one no exception in the yaml or
    # the value families covers: it scans, into something that is not a mapping.
    # read_task_file then subscripts a str and raises TypeError.
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    broken = Path(context_transfer.task_file_path(harness.tasks_dir, "T-2"))
    broken.write_text("---\nTODO write this up\n---\n\nbody\n")

    resp = _get(harness, "/api/tasks")

    assert resp.status_code == 200
    assert [task["task_id"] for task in resp.get_json()["data"]] == ["T-1"]
    assert str(broken) in resp.get_json()["warnings"][0]


def test_a_kanban_issue_id_that_is_not_a_string_is_a_warning_and_not_a_500(
    tmp_path: Path,
) -> None:
    # The fourth way a task file fails, and the first that survives the parse:
    # `read_task_file` hands `kanban_issue_id` back whatever YAML built, and
    # `_task` looks it up as a dict key. A mapping there is unhashable, so the
    # TypeError lands at the use and not at the read.
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    broken = Path(context_transfer.task_file_path(harness.tasks_dir, "T-2"))
    broken.write_text(
        "---\ntask_id: T-2\nstatus: in_progress\nowner: null\ndepends_on: []\n"
        "heartbeat: null\nkanban_issue_id:\n  id: 11111111-2222-3333-4444-555555555555\n"
        "---\n\nbody\n"
    )

    resp = _get(harness, "/api/tasks")

    assert resp.status_code == 200
    assert [task["task_id"] for task in resp.get_json()["data"]] == ["T-1"]
    assert str(broken) in resp.get_json()["warnings"][0]


def test_frontmatter_json_cannot_serialise_is_a_warning_and_not_a_500(tmp_path: Path) -> None:
    # The fifth, and the one that used to land outside every `except` in the
    # module: `!!set` is a tag `yaml.safe_load` builds and `jsonify` refuses,
    # and `jsonify` runs inside `_envelope`, after the guard has been left.
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    broken = Path(context_transfer.task_file_path(harness.tasks_dir, "T-2"))
    broken.write_text(
        "---\ntask_id: T-2\nstatus: in_progress\nowner: !!set {a: null}\n"
        "depends_on: []\nheartbeat: null\n---\n\nbody\n"
    )

    resp = _get(harness, "/api/tasks")

    assert resp.status_code == 200
    assert [task["task_id"] for task in resp.get_json()["data"]] == ["T-1"]
    assert str(broken) in resp.get_json()["warnings"][0]


def test_a_frontmatter_date_serialises_rather_than_costing_the_task_a_warning(
    tmp_path: Path,
) -> None:
    # The check above is `app.json.dumps` and not `json.dumps` for this file:
    # `owner: 2026-09-26` scans into a `datetime.date`, which Flask's provider
    # renders and the stdlib refuses. Checking with the stdlib would answer 200
    # by warning about a task the service has always served.
    harness = _harness(tmp_path)
    dated = Path(context_transfer.task_file_path(harness.tasks_dir, "T-1"))
    dated.write_text(
        "---\ntask_id: T-1\nstatus: in_progress\nowner: 2026-09-26\n"
        "depends_on: []\nheartbeat: null\n---\n\nbody\n"
    )

    resp = _get(harness, "/api/tasks")

    assert resp.status_code == 200
    assert resp.get_json()["warnings"] == []
    [task] = resp.get_json()["data"]
    assert task["owner"] == "Sat, 26 Sep 2026 00:00:00 GMT"


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
                "depends_on": [],
                "heartbeat": None,
                "description": None,
                "kanban_issue_id": "11111111-2222-3333-4444-555555555555",
                "resolved_debt": [],
                "card": None,
                "lock_expired": None,
            }
        ],
        "warnings": [],
    }


# --- lock_expired ---------------------------------------------------------


def _ago(seconds: int) -> str:
    return (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds)
    ).isoformat()


@pytest.mark.parametrize("route", ["/api/tasks", "/api/tasks/T-1"])
def test_a_live_heartbeat_is_not_an_expired_lock(tmp_path: Path, route: str) -> None:
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1", owner="cuenta1", heartbeat=_ago(5))

    body = _get(harness, route).get_json()

    task = body["data"][0] if route == "/api/tasks" else body["data"]
    assert task["lock_expired"] is False


@pytest.mark.parametrize("route", ["/api/tasks", "/api/tasks/T-1"])
def test_a_heartbeat_older_than_the_ttl_is_an_expired_lock(tmp_path: Path, route: str) -> None:
    """The most useful single fact the board shows, and the one it must not
    derive: the TTL is config this service holds and no client does
    (`docs/plans/board.md` "Two kinds of stale")."""
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1", owner="cuenta1", heartbeat=_ago(600))

    body = _get(harness, route).get_json()

    task = body["data"][0] if route == "/api/tasks" else body["data"]
    assert task["lock_expired"] is True


@pytest.mark.parametrize("route", ["/api/tasks", "/api/tasks/T-1"])
def test_a_task_with_no_heartbeat_has_a_null_lock_rather_than_a_live_one(
    tmp_path: Path, route: str
) -> None:
    """`is_lock_expired` answers `False` here, because for the dispatcher "no
    lock to release" and "the lock is fine" take the same branch. For a reader
    they are opposite news, so the null is made here and the dispatcher's
    function is left alone."""
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1", status="pending", owner=None, heartbeat=None)

    body = _get(harness, route).get_json()

    task = body["data"][0] if route == "/api/tasks" else body["data"]
    assert task["lock_expired"] is None


def test_the_ttl_lock_expired_uses_is_the_one_in_config(tmp_path: Path) -> None:
    """Pins that the value is read from `config.yaml` rather than defaulted in
    this module: one heartbeat, two harnesses, two answers."""
    a_minute_old = _ago(60)
    generous = _harness(tmp_path / "generous", heartbeat_ttl_seconds=3600)
    strict = _harness(tmp_path / "strict", heartbeat_ttl_seconds=10)
    _task(generous.tasks_dir, "T-1", owner="cuenta1", heartbeat=a_minute_old)
    _task(strict.tasks_dir, "T-1", owner="cuenta1", heartbeat=a_minute_old)

    assert _get(generous, "/api/tasks").get_json()["data"][0]["lock_expired"] is False
    assert _get(strict, "/api/tasks").get_json()["data"][0]["lock_expired"] is True


def test_a_heartbeat_that_will_not_parse_is_a_null_lock_and_keeps_its_row(
    tmp_path: Path,
) -> None:
    """`docs/decisions.md` ADR 10. `is_lock_expired` raises `ValueError` out of
    `fromisoformat` here, and that exception family is what `/api/tasks` treats
    as an unreadable task file — so left alone this would drop a task whose file
    reads perfectly well, and with it the status and owner an operator needs. It
    is a heartbeat that cannot be judged, which is what `null` already means."""
    harness = _harness(tmp_path)
    Path(context_transfer.task_file_path(harness.tasks_dir, "T-1")).write_text(
        "---\ntask_id: T-1\nstatus: in_progress\nowner: cuenta1\ndepends_on: []\n"
        "heartbeat: yesterday afternoon\n---\n\nbody\n"
    )

    body = _get(harness, "/api/tasks").get_json()

    assert body["warnings"] == []
    [task] = body["data"]
    assert task["lock_expired"] is None
    # The string travels on, for a human to read and to paste into a shell.
    assert task["heartbeat"] == "yesterday afternoon"
    assert task["status"] == "in_progress"


def test_a_naive_heartbeat_is_a_null_lock_too(tmp_path: Path) -> None:
    """The second way the comparison fails: a timestamp with no offset parses
    into a naive datetime, and subtracting it from an aware `now` raises
    `TypeError` rather than `ValueError`. Every writer in `dispatcher/` writes
    UTC with an offset, so this is a hand-edited file — which is exactly the
    kind this endpoint exists to report on rather than choke on."""
    harness = _harness(tmp_path)
    Path(context_transfer.task_file_path(harness.tasks_dir, "T-1")).write_text(
        "---\ntask_id: T-1\nstatus: in_progress\nowner: cuenta1\ndepends_on: []\n"
        "heartbeat: '2026-09-26T18:00:00'\n---\n\nbody\n"
    )

    body = _get(harness, "/api/tasks").get_json()

    assert body["warnings"] == []
    [task] = body["data"]
    assert task["lock_expired"] is None


# --- the second way to authenticate ---------------------------------------


@pytest.mark.parametrize("route", ROUTES)
def test_every_route_accepts_the_configured_bearer_token(tmp_path: Path, route: str) -> None:
    """How the board reaches this service without holding a human's password
    (`docs/decisions.md` ADR 7). The realm on the 401 does not change, and the
    Basic path is unaffected — `tests/observability/test_auth.py` holds the rest."""
    harness = _harness(tmp_path, token="s3cret-token")
    _task(harness.tasks_dir, "T-1")
    _debt_index(harness.projects_root, "ia-harness", _INDEX)

    resp = harness.client.get(route, headers={"Authorization": "Bearer s3cret-token"})

    assert resp.status_code == 200
    assert set(resp.get_json()) == {"data", "warnings"}


@pytest.mark.parametrize("route", ROUTES)
def test_a_wrong_bearer_token_is_401(tmp_path: Path, route: str) -> None:
    harness = _harness(tmp_path, token="s3cret-token")
    _task(harness.tasks_dir, "T-1")

    resp = harness.client.get(route, headers={"Authorization": "Bearer not-it"})

    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == 'Basic realm="ia-harness api"'


def test_an_api_with_no_token_configured_accepts_no_bearer_at_all(tmp_path: Path) -> None:
    """The upgrade case: a host whose `docker/compose/.env` predates `API_TOKEN`
    starts this service with `token=None`, and Basic auth keeps working while the
    bearer path stays shut."""
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")

    assert harness.client.get(
        "/api/tasks", headers={"Authorization": "Bearer anything"}
    ).status_code == 401
    assert _get(harness, "/api/tasks").status_code == 200


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


def test_a_board_directory_that_is_a_file_is_a_warning_and_not_a_500(tmp_path: Path) -> None:
    # LocalBoardClient._scan catches FileNotFoundError only — for the dispatcher
    # a missing board is an empty board, but a `local_board.dir` that is a file
    # must raise rather than mint cards nobody can list. Here the contract is
    # the other one: never 500, say which path could not be read.
    harness = _harness(tmp_path)
    harness.board_dir.write_text("this is a file, not a board directory")
    _task(harness.tasks_dir, "T-1", kanban_issue_id="11111111-2222-3333-4444-555555555555")

    resp = _get(harness, "/api/tasks")

    assert resp.status_code == 200
    assert [task["task_id"] for task in resp.get_json()["data"]] == ["T-1"]
    assert resp.get_json()["data"][0]["card"] is None
    assert str(harness.board_dir) in resp.get_json()["warnings"][0]
    assert "unreadable board" in resp.get_json()["warnings"][0]


def test_a_card_whose_created_at_will_not_sort_is_a_warning_and_not_a_500(
    tmp_path: Path,
) -> None:
    # `_read_path` checks the document is a mapping with a string `issue_id`
    # and nothing else, so `created_at` is whatever was on disk and `_scan`
    # sorts on it. Two cards are the point: a one-element sort compares
    # nothing. The fix is this side's `except`, not `_scan`'s key — see
    # `_read_cards`.
    harness = _harness(tmp_path)
    good = harness.board.create_issue("Phase 1")
    unsortable = harness.board_dir / "99999999-8888-7777-6666-555555555555.json"
    unsortable.write_text(
        json.dumps(
            {
                "issue_id": "99999999-8888-7777-6666-555555555555",
                "title": "hand-written",
                "status": "pending",
                "created_at": 1758900000,
            }
        )
    )
    _task(harness.tasks_dir, "T-1", kanban_issue_id=good)

    resp = _get(harness, "/api/tasks")

    assert resp.status_code == 200
    assert [task["task_id"] for task in resp.get_json()["data"]] == ["T-1"]
    assert resp.get_json()["data"][0]["card"] is None
    assert str(harness.board_dir) in resp.get_json()["warnings"][0]
    assert "unreadable board" in resp.get_json()["warnings"][0]


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


def test_one_task_whose_kanban_issue_id_is_not_a_string_is_null_with_a_warning(
    tmp_path: Path,
) -> None:
    # Same guard as `/api/tasks`, reached by the route that reads one file:
    # a task that will not shape answers `null` and the reason, not a 500.
    harness = _harness(tmp_path)
    broken = Path(context_transfer.task_file_path(harness.tasks_dir, "T-1"))
    broken.write_text(
        "---\ntask_id: T-1\nstatus: in_progress\nowner: null\ndepends_on: []\n"
        "heartbeat: null\nkanban_issue_id:\n  - 11111111-2222-3333-4444-555555555555\n"
        "---\n\nbody\n"
    )

    resp = _get(harness, "/api/tasks/T-1")

    assert resp.status_code == 200
    assert resp.get_json()["data"] is None
    assert str(broken) in resp.get_json()["warnings"][0]


def test_one_task_by_id_carries_the_running_body_and_the_list_route_does_not(
    tmp_path: Path,
) -> None:
    """`docs/decisions.md` ADR 21: `body` is the detail route's and nothing else's.

    The two task routes answer different key sets on purpose, so both halves are
    asserted over one task file: a `body` that reached `_task` would show up on
    the list, which is the request a screen repeats every few seconds.
    """
    harness = _harness(tmp_path)
    record = "arquitecto: plan ready\n\nimplementador: built it\n"
    _task(harness.tasks_dir, "T-1", depends_on=["T-0"], body=record)

    detail = _get(harness, "/api/tasks/T-1").get_json()["data"]
    [listed] = _get(harness, "/api/tasks").get_json()["data"]

    assert detail["body"] == record
    assert detail["depends_on"] == ["T-0"]
    assert set(detail) == set(listed) | {"body"}
    assert "body" not in listed
    # Not only absent under that name: the record must not have travelled on
    # another key either, which is what a rename rather than a move would look
    # like.
    assert record not in listed.values()


def test_a_body_under_the_cap_is_served_whole_and_says_nothing(tmp_path: Path) -> None:
    # The common case, pinned so the cap cannot start announcing itself on every
    # card: `MAX_BODY_BYTES` is over twice the largest card in this repository.
    harness = _harness(tmp_path)
    record = "### arquitecto\nplan ready\n" * 40
    _task(harness.tasks_dir, "T-1", body=record)

    body = _get(harness, "/api/tasks/T-1").get_json()

    assert body["data"]["body"] == record
    assert body["warnings"] == []


def test_a_body_over_the_cap_keeps_its_newest_end_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`docs/decisions.md` ADR 23, closing `docs/debt/T-011-D1.md`.

    Which end survives is the decision this test pins, not the arithmetic:
    `handoff()` appends, so the oldest phase is what a bounded answer drops. The
    constant is monkeypatched rather than met with a 128 KB fixture — the bound
    is what is under test, not the number.
    """
    harness = _harness(tmp_path)
    monkeypatch.setattr(api_app, "MAX_BODY_BYTES", 60)
    oldest = "### cartografo\n" + "o" * 200 + "\n"
    newest = "### auditor\nthe phase a reader came for\n"
    _task(harness.tasks_dir, "T-1", body=oldest + newest)

    body = _get(harness, "/api/tasks/T-1").get_json()

    assert body["data"]["body"] == newest
    assert "cartografo" not in body["data"]["body"]
    # The caller is told, in the terms ADR 5 allows a warning: the file, the
    # size, the cap. Nothing is dropped silently, which is the whole reason the
    # envelope is not a bare object.
    [warning] = body["warnings"]
    assert context_transfer.task_file_path(harness.tasks_dir, "T-1") in warning
    assert "256" in warning and "60" in warning


def test_a_truncated_body_starts_at_a_whole_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path)
    monkeypatch.setattr(api_app, "MAX_BODY_BYTES", 30)
    _task(harness.tasks_dir, "T-1", body="aaaaaaaaaaaaaaaaaaaa\nbbbb\ncccc\n")

    served = _get(harness, "/api/tasks/T-1").get_json()["data"]["body"]

    # The byte slice lands inside the first line, and half a line of markdown at
    # the top of the screen reads as damage to the file rather than as a cap.
    assert served == "bbbb\ncccc\n"


def test_a_truncated_body_does_not_carry_a_replacement_character(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The cap counts bytes, so the slice can land inside a multibyte character.
    # A dropped partial sequence costs one character; a U+FFFD would cost a
    # human a minute working out whether the task file is corrupt.
    harness = _harness(tmp_path)
    monkeypatch.setattr(api_app, "MAX_BODY_BYTES", 9)
    _task(harness.tasks_dir, "T-1", body="ñññññ")

    served = _get(harness, "/api/tasks/T-1").get_json()["data"]["body"]

    assert "�" not in served
    assert served == "ññññ"


def test_the_list_route_never_reports_a_truncated_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The cap belongs to the field and the field belongs to the detail route
    # (ADR 21), so a card over the cap changes nothing about `/api/tasks`.
    harness = _harness(tmp_path)
    monkeypatch.setattr(api_app, "MAX_BODY_BYTES", 10)
    _task(harness.tasks_dir, "T-1", body="x" * 500)

    body = _get(harness, "/api/tasks").get_json()

    assert body["warnings"] == []
    assert "body" not in body["data"][0]


def test_one_task_json_cannot_serialise_is_null_with_a_warning(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    broken = Path(context_transfer.task_file_path(harness.tasks_dir, "T-1"))
    broken.write_text(
        "---\ntask_id: T-1\nstatus: in_progress\nowner: !!set {a: null}\n"
        "depends_on: []\nheartbeat: null\n---\n\nbody\n"
    )

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
    # The four config columns carry `dispatcher/config.py`'s own defaults here —
    # this fixture names none of them — and the cases below pin that they are
    # read from the config rather than written as literals.
    assert body["data"] == [
        {
            "name": "cuenta1",
            "container": "agent-cuenta1",
            "state": "BUSY",
            "current_task": "T-1",
            "rate_limited_at": None,
            "is_primary": False,
            "quota_threshold_pct": 90,
            "reserve_pct": 60,
            "quota_cooldown_seconds": 1800,
        },
        {
            "name": "cuenta2",
            "container": "agent-cuenta2",
            "state": "IDLE",
            "current_task": None,
            "rate_limited_at": 1700000000.0,
            "is_primary": False,
            "quota_threshold_pct": 90,
            "reserve_pct": 60,
            "quota_cooldown_seconds": 1800,
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


def test_a_state_file_that_is_not_an_object_is_a_warning_naming_it(tmp_path: Path) -> None:
    # json.loads succeeds and state_machine then subscripts a list: parsed is
    # not shaped, and TypeError is not a ValueError. The account stays in the
    # list with the unreadable half nulled, as an unparseable file does.
    harness = _harness(tmp_path)
    path = Path(harness.state_dir) / "cuenta1.json"
    path.write_text(json.dumps(["BUSY"]))

    resp = _get(harness, "/api/accounts")

    assert resp.status_code == 200
    [account] = resp.get_json()["data"]
    assert account["name"] == "cuenta1"
    assert account["state"] is None
    assert str(path) in resp.get_json()["warnings"][0]


def test_the_primary_account_is_marked_and_the_rest_are_not(tmp_path: Path) -> None:
    """`is_primary` is `load_config`'s, from the top-level `primary_account`.

    Every other case here leaves the key out, which marks nothing, so without
    this one the field is only ever asserted false — `docs/charter.md` C-3 makes
    which account is primary configuration, and this is the route answering it.
    """
    harness = _harness(tmp_path, accounts=("cuenta1", "cuenta2"), primary_account="cuenta2")

    body = _get(harness, "/api/accounts").get_json()

    assert [(row["name"], row["is_primary"]) for row in body["data"]] == [
        ("cuenta1", False),
        ("cuenta2", True),
    ]


def test_the_thresholds_on_a_row_are_the_configured_ones(tmp_path: Path) -> None:
    """`docs/decisions.md` ADR 18 and ADR 20: configured, not compiled in.

    All three differ from `dispatcher/config.py`'s defaults, and `reserve_pct`
    is set above that default's ceiling of 60 — the value a route holding
    literals, or re-deriving the default, would get wrong while the pool parks
    somewhere else entirely.
    """
    harness = _harness(
        tmp_path,
        accounts=("cuenta1", "cuenta2"),
        quota_threshold_pct=95,
        reserve_pct=70,
        quota_cooldown_seconds=900,
    )

    body = _get(harness, "/api/accounts").get_json()

    assert body["warnings"] == []
    for row in body["data"]:
        assert (row["quota_threshold_pct"], row["reserve_pct"], row["quota_cooldown_seconds"]) == (
            95,
            70,
            900,
        )


def test_an_unreadable_state_file_keeps_the_config_half_of_the_row(tmp_path: Path) -> None:
    # The four config columns are built before the `try`, so a state file that
    # will not parse nulls what the state file says and nothing else: an account
    # the config calls primary does not stop being primary because the pool's
    # record of what it is doing is damaged.
    harness = _harness(tmp_path, primary_account="cuenta1", quota_threshold_pct=95)
    (Path(harness.state_dir) / "cuenta1.json").write_text("{truncated")

    [account] = _get(harness, "/api/accounts").get_json()["data"]

    assert account["state"] is None
    assert account["is_primary"] is True
    assert account["quota_threshold_pct"] == 95


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


@pytest.mark.parametrize("name", ["limit", "since"])
def test_an_integer_sqlite_cannot_bind_answers_400_and_not_a_500(
    tmp_path: Path, name: str
) -> None:
    # SQLite integers are signed 64-bit and `WHERE id > ?` / `LIMIT ?` bind
    # these straight in, so one past the boundary raises OverflowError — which
    # is not sqlite3.Error, not OSError and not ValueError, so no except in the
    # view catches it. Both ends are pinned, so this cannot pass by rejecting
    # every large number: the boundary itself is a legal request.
    harness = _harness(tmp_path)

    assert _get(harness, f"/api/events?{name}=9223372036854775807").status_code == 200
    resp = _get(harness, f"/api/events?{name}=9223372036854775808")

    assert resp.status_code == 400
    assert "64-bit" in resp.get_json()["error"]


def test_a_limit_above_the_maximum_is_clamped_and_the_clamp_is_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # T-009-D2. The clamp reaches the query and not only the envelope: three
    # rows exist, the caller asks for three, and gets the two this service
    # answers with.
    monkeypatch.setattr(api_app, "MAX_EVENT_LIMIT", 2)
    harness = _harness(tmp_path)
    for index in range(3):
        collector_db.insert_event(harness.db_path, "agent-cuenta1", f"E{index}", {})

    body = _get(harness, "/api/events?limit=3").get_json()

    assert len(body["data"]) == 2
    assert len(body["warnings"]) == 1
    assert "limit=3" in body["warnings"][0]
    assert "maximum of 2" in body["warnings"][0]
    assert "?since=" in body["warnings"][0]


def test_the_maximum_itself_is_answered_whole_and_silently(tmp_path: Path) -> None:
    # The cap is not a nudge: asking for exactly what is allowed is not clamped
    # and says nothing.
    harness = _harness(tmp_path)
    collector_db.insert_event(harness.db_path, "agent-cuenta1", "Stop", {})

    body = _get(harness, f"/api/events?limit={api_app.MAX_EVENT_LIMIT}").get_json()

    assert len(body["data"]) == 1
    assert body["warnings"] == []


def test_the_largest_integer_sqlite_can_bind_is_clamped_and_not_rejected(
    tmp_path: Path,
) -> None:
    # The 400 above is for an integer SQLite cannot bind, which is a malformed
    # parameter. This is one it can bind and no caller wants, which is a legal
    # request for more than one answer carries: 200, clamped, and told so.
    resp = _get(_harness(tmp_path), "/api/events?limit=9223372036854775807")

    assert resp.status_code == 200
    assert str(api_app.MAX_EVENT_LIMIT) in resp.get_json()["warnings"][0]


def test_the_clamp_outlives_a_missing_events_database(tmp_path: Path) -> None:
    # A database that is not there does not make it untrue that the caller
    # asked for more rows than it could have had. Both warnings, in order.
    harness = _harness(tmp_path, with_db=False)

    body = _get(harness, "/api/events?limit=9223372036854775807").get_json()

    assert body["data"] == []
    assert len(body["warnings"]) == 2
    assert "maximum" in body["warnings"][0]
    assert str(harness.db_path) in body["warnings"][1]


def test_a_missing_events_database_is_an_empty_list_and_a_warning(tmp_path: Path) -> None:
    # A harness that has never run has no events, and this service may not
    # create the database: the volume is :ro and init_db runs CREATE TABLE.
    harness = _harness(tmp_path, with_db=False)

    resp = _get(harness, "/api/events")

    assert resp.status_code == 200
    assert resp.get_json()["data"] == []
    assert harness.db_path in resp.get_json()["warnings"][0]
    assert "no events database yet" in resp.get_json()["warnings"][0]
    assert not Path(harness.db_path).exists()


def test_a_database_that_is_not_one_is_a_warning_rather_than_a_500(tmp_path: Path) -> None:
    # A file that is there and will not open is a different situation from a
    # harness that has never run, and sqlite's own message says neither: the
    # two warnings are told apart so an operator knows which one they have.
    harness = _harness(tmp_path, with_db=False)
    Path(harness.db_path).write_text("this is not a database")

    resp = _get(harness, "/api/events")

    assert resp.status_code == 200
    assert resp.get_json()["data"] == []
    warning = resp.get_json()["warnings"][0]
    assert harness.db_path in warning
    assert "could not be opened read-only" in warning
    assert "-shm" in warning


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


# --- phases ---------------------------------------------------------------


def _stamp(tasks_dir: str, task_id: str, role: str, saved_at: str) -> None:
    """Rewrite one record's `saved_at`, so an order assertion does not race a clock.

    `save_handoff` stamps `now()`, and two writes in the same microsecond would
    make the newest-first assertion below depend on how fast the machine is.
    What is pinned is the order the api puts the rows in, not the clock.
    """
    path = Path(context_transfer.handoff_path(tasks_dir, task_id, role))
    envelope = json.loads(path.read_text())
    envelope["saved_at"] = saved_at
    path.write_text(json.dumps(envelope))


def test_phases_reports_the_six_keys_a_handoff_file_carries(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    payload = {"status": "complete", "changed": ["docs/decisions.md: ADR 27"], "pending": []}
    _handoff(harness.tasks_dir, "T-1", "arquitecto", payload)

    body = _get(harness, "/api/phases").get_json()

    [phase] = body["data"]
    saved_at = phase.pop("saved_at")
    assert phase == {
        "id": "T-1:arquitecto",
        "task_id": "T-1",
        "role": "arquitecto",
        # The arquitecto is saved with no round, so `null` is a served value.
        "round": None,
        # The payload whole and unpromoted: nothing is lifted onto the row and
        # nothing is projected. `docs/decisions.md` ADR 27. Asserting the whole
        # dict is what makes a seventh key fail here rather than pass quietly.
        "handoff": payload,
    }
    assert dt.datetime.fromisoformat(saved_at).tzinfo is not None
    assert body["warnings"] == []


def test_phases_serves_the_payload_whole_whatever_keys_the_role_returned(
    tmp_path: Path,
) -> None:
    # The per-role key set is `dispatcher/handoff.py:schema_for`'s and this
    # service holds no second copy of it: a revisor's two extra keys arrive
    # because nothing here is filtering on a list of names. ADR 27.
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    payload = {
        "status": "complete",
        "verdict": "CHANGES_REQUESTED",
        "debt_rulings": [{"id": "T-013-D1", "ruling": "accepted"}],
    }
    _handoff(harness.tasks_dir, "T-1", "revisor", payload, round_num=1)

    [phase] = _get(harness, "/api/phases").get_json()["data"]

    assert phase["handoff"] == payload


def test_a_round_is_the_number_the_envelope_carries(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    _handoff(harness.tasks_dir, "T-1", "implementador", {"status": "complete"}, round_num=2)

    [phase] = _get(harness, "/api/phases").get_json()["data"]

    assert phase["round"] == 2


def test_a_handoff_saved_with_no_payload_is_a_null_handoff_and_not_a_warning(
    tmp_path: Path,
) -> None:
    # `save_handoff` writes the envelope even when the return would not parse,
    # so a phase that left nothing is a record and not a missing file: `null` is
    # a real answer here and the timeline shows it. ADR 27.
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    _handoff(harness.tasks_dir, "T-1", "implementador", None, round_num=2)

    body = _get(harness, "/api/phases").get_json()

    [phase] = body["data"]
    assert phase["handoff"] is None
    assert phase["round"] == 2
    assert body["warnings"] == []


def test_every_role_with_a_file_gets_a_row_and_the_newest_is_first(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    for role in ("arquitecto", "implementador", "revisor"):
        _handoff(harness.tasks_dir, "T-1", role, {"status": "complete"})
    _stamp(harness.tasks_dir, "T-1", "arquitecto", "2026-10-01T09:00:00+00:00")
    _stamp(harness.tasks_dir, "T-1", "implementador", "2026-10-01T11:00:00+00:00")
    _stamp(harness.tasks_dir, "T-1", "revisor", "2026-10-01T13:00:00+00:00")

    data = _get(harness, "/api/phases").get_json()["data"]

    assert [phase["role"] for phase in data] == ["revisor", "implementador", "arquitecto"]


def test_task_id_filters_to_one_task(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    _task(harness.tasks_dir, "T-2")
    _handoff(harness.tasks_dir, "T-1", "arquitecto", {"status": "complete"})
    _handoff(harness.tasks_dir, "T-2", "auditor", {"status": "complete"})

    body = _get(harness, "/api/phases?task_id=T-1").get_json()

    assert [phase["id"] for phase in body["data"]] == ["T-1:arquitecto"]
    assert body["warnings"] == []


def test_an_unknown_task_id_is_an_empty_list_and_a_warning_not_a_404(tmp_path: Path) -> None:
    # The console reaches this route with an id it read off `/api/tasks`, so a
    # 404 would take a region to Broken on a screen whose own read succeeded.
    # ADR 27.
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")

    resp = _get(harness, "/api/phases?task_id=T-999")

    assert resp.status_code == 200
    assert resp.get_json()["data"] == []
    assert "T-999" in resp.get_json()["warnings"][0]


def test_a_task_id_that_is_not_a_bare_id_is_400(tmp_path: Path) -> None:
    # A parameter is 400 territory, and the id never reaches `handoff_path`:
    # `_is_bare_task_id` is the lock the detail route already uses.
    harness = _harness(tmp_path)

    for bad in ("../../etc/passwd", "a/b"):
        resp = _get(harness, f"/api/phases?task_id={bad}")
        assert resp.status_code == 400
        assert "task_id" in resp.get_json()["error"]


def test_a_handoff_that_will_not_parse_is_named_in_warnings_and_the_others_still_come_back(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    _handoff(harness.tasks_dir, "T-1", "arquitecto", {"status": "complete"})
    _handoff(harness.tasks_dir, "T-1", "revisor", {"verdict": "APPROVED"})
    broken = Path(context_transfer.handoff_path(harness.tasks_dir, "T-1", "revisor"))
    broken.write_text("{")

    resp = _get(harness, "/api/phases")

    # Not a 404: a 404 says the phase does not exist, which is a lie about a
    # file that does. One warning naming it, and the other row still answers.
    assert resp.status_code == 200
    body = resp.get_json()
    assert [phase["role"] for phase in body["data"]] == ["arquitecto"]
    assert len(body["warnings"]) == 1
    assert str(broken) in body["warnings"][0]


def test_a_handoff_whose_envelope_is_not_an_object_is_a_warning(tmp_path: Path) -> None:
    # It parses, into something that is not an envelope — the shape no exception
    # in the value family covers on its own.
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    _handoff(harness.tasks_dir, "T-1", "auditor", {"status": "complete"})
    broken = Path(context_transfer.handoff_path(harness.tasks_dir, "T-1", "auditor"))
    broken.write_text("[1, 2]")

    resp = _get(harness, "/api/phases")

    assert resp.status_code == 200
    assert resp.get_json()["data"] == []
    assert str(broken) in resp.get_json()["warnings"][0]


def test_a_saved_at_of_the_wrong_type_does_not_raise_in_the_sort(tmp_path: Path) -> None:
    # The sort key is built inside the per-row guard and coerced to `str` there,
    # so the sort that runs after the loop cannot compare a dict against a
    # string — and one hand-edited record cannot cost every other row its
    # answer. `docs/learnings/a-never-500-read-wraps-the-use-not-the-parse.md`.
    #
    # The odd value is still *served*, verbatim and with no warning, which is
    # `docs/decisions.md` ADR 10's rule about an unparseable heartbeat applied to
    # the one timestamp this route has: a value the api cannot judge is the
    # harness's news to report and not this service's to hide. Nothing raises
    # here, so there is nothing to warn about.
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    _handoff(harness.tasks_dir, "T-1", "arquitecto", {"status": "complete"})
    _handoff(harness.tasks_dir, "T-1", "auditor", {"status": "complete"})
    path = Path(context_transfer.handoff_path(harness.tasks_dir, "T-1", "auditor"))
    envelope = json.loads(path.read_text())
    envelope["saved_at"] = {"when": "yesterday"}
    path.write_text(json.dumps(envelope))

    resp = _get(harness, "/api/phases")

    assert resp.status_code == 200
    body = resp.get_json()
    assert sorted(phase["role"] for phase in body["data"]) == ["arquitecto", "auditor"]
    assert [phase["saved_at"] for phase in body["data"] if phase["role"] == "auditor"] == [
        {"when": "yesterday"}
    ]
    assert body["warnings"] == []


def test_a_handoffs_directory_that_will_not_list_is_a_warning_and_not_a_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `list_handoff_roles` guards with `os.path.isdir` and then calls
    # `os.listdir`, which still raises for a directory that exists and cannot be
    # read — and this route calls it once per task, so one such directory would
    # otherwise cost every other task its rows. Not reachable through a chmod in
    # this suite, which may run as root; monkeypatched because what is pinned is
    # the guard and not the filesystem.
    #
    # It is a live shape rather than a hypothetical: `.hive/` is written by the
    # dispatcher and read by this service over a `:ro` mount, which is why
    # `_write_atomic` restores 0644 for "agent containers (possibly non-root)".
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    _task(harness.tasks_dir, "T-2")
    _handoff(harness.tasks_dir, "T-2", "auditor", {"status": "complete"})
    real = context_transfer.list_handoff_roles

    def refuse(hive_dir: str, task_id: str):
        if task_id == "T-1":
            raise PermissionError(13, "Permission denied")
        return real(hive_dir, task_id)

    monkeypatch.setattr(context_transfer, "list_handoff_roles", refuse)

    resp = _get(harness, "/api/phases")

    assert resp.status_code == 200
    body = resp.get_json()
    assert [phase["id"] for phase in body["data"]] == ["T-2:auditor"]
    assert len(body["warnings"]) == 1
    assert "T-1" in body["warnings"][0]


def test_a_task_with_no_handoffs_directory_is_an_empty_list_and_no_warning(
    tmp_path: Path,
) -> None:
    # The task ids on this harness that predate `save_handoff` are this case,
    # and it must not look like damage.
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")

    body = _get(harness, "/api/phases").get_json()

    assert body == {"data": [], "warnings": []}


def test_the_answer_is_clamped_and_the_clamp_names_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The cap cannot bite the call a screen makes — one task has at most one
    # file per role — and it is there so the unfiltered call is not the second
    # thing this service answers without a bound. ADR 27, on ADR 11's model.
    monkeypatch.setattr(api_app, "MAX_PHASES", 2)
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")
    for role in ("arquitecto", "implementador", "revisor"):
        _handoff(harness.tasks_dir, "T-1", role, {"status": "complete"})

    body = _get(harness, "/api/phases").get_json()

    assert len(body["data"]) == 2
    assert "3 phase records" in body["warnings"][0]
    assert "?task_id=" in body["warnings"][0]


# --- learnings ------------------------------------------------------------


def _learning(
    tasks_dir: str,
    name: str,
    *,
    directory: str = learnings.INBOX_NAME,
    scope: str = learnings.SCOPE_PROJECT,
    status: str = learnings.UNCONFIRMED,
    when: str = "the suite talks to a database",
    rule: str | None = "Start postgres before the suite, not with it.",
    project: str = "ia-harness",
    task: str = "T-1",
    **meta,
) -> Path:
    """One entry file, in the shape the phases' prompt tells them to write.

    Written as text and not through the module, for the reason
    `tests/dispatcher/test_learnings.py:_entry` gives: there is no public writer
    for these — the phases write them by hand — so a test that went through one
    would be exercising a path nothing on this harness takes. `rule=None` leaves
    the `## Rule` section out entirely, which is the entry whose served `rule`
    falls back to `when`.

    The tree is a **sibling** of `hive_tasks_dir` and not under it, so this
    makes it rather than assuming `_harness` did: `_harness` writes only what it
    is passed (`docs/learnings/the-api-test-harness-writes-only-what-it-is-passed.md`),
    and a case that wants no tree at all has to be able to get one.
    """
    frontmatter = {
        "project": project,
        "task": task,
        "phase": "implementador",
        "scope": scope,
        "status": status,
        "when": when,
    }
    frontmatter.update(meta)
    path = Path(learnings.ensure_dirs(tasks_dir)) / directory / name
    body = (
        "## Symptom\n\n```\nECONNREFUSED 127.0.0.1:5432\n```\n\n"
        "## Why\n\nThe fixture assumed a server that nothing starts.\n\n"
    )
    if rule is not None:
        body += f"## Rule\n\n{rule}\n\n"
    body += "## Evidence\n\n`pytest -q tests/db` in the writers' worktree.\n"
    path.write_text("---\n" + yaml.safe_dump(frontmatter, sort_keys=False) + "---\n\n" + body)
    return path


def _fingerprint(harness: types.SimpleNamespace) -> str:
    """The surface the api holds, as the api computes it.

    Off the same `config.yaml` the app was created from, so the three inputs
    are `cfg.permission_mode`, `cfg.allowed_tools` and
    `docker_exec.WRITER_ROLES` — the three `dispatcher/dispatcher.py` passes. A
    hex literal here would stop pinning anything the day
    `dispatcher/config.py`'s permission defaults move, and spelling the two
    defaults out as literals has the same fault one level in.
    """
    cfg = load_config(harness.config_path)
    return learnings.harness_fingerprint(
        cfg.permission_mode, cfg.allowed_tools, docker_exec.WRITER_ROLES
    )


def test_learnings_reports_the_ten_keys_an_entry_carries(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _learning(harness.tasks_dir, "T-1-db.md", task="T-1", carried_by="T-2")
    _learning(harness.tasks_dir, "T-2-pool.md", task="T-2", status=learnings.CONFIRMED)
    _learning(
        harness.tasks_dir,
        "shared.md",
        directory=learnings.HARNESS_NAME,
        scope=learnings.SCOPE_HARNESS,
        status=learnings.CONFIRMED,
        project="someone-else",
        task="T-9",
        when="you reach for docker in a phase",
        rule="Plan it as a human's row instead.",
    )

    body = _get(harness, "/api/learnings").get_json()

    assert body["warnings"] == []
    # The phase table's own order: confirmed first, `ref` as the tie-break.
    assert [row["ref"] for row in body["data"]] == [
        "harness/shared.md",
        "inbox/T-2-pool.md",
        "inbox/T-1-db.md",
    ]
    assert body["data"][2] == {
        "ref": "inbox/T-1-db.md",
        "task": "T-1",
        "carried_by": "T-2",
        "scope": "project",
        "status": "unconfirmed",
        "when": "the suite talks to a database",
        "rule": "Start postgres before the suite, not with it.",
        "stale": False,
        "in_phase_table": True,
        "phase_table_cap": learnings.MAX_ROWS,
    }


def test_an_entry_with_no_rule_section_serves_its_trigger_line_instead(tmp_path: Path) -> None:
    # `Entry.rule` is the first non-blank line of `## Rule` and falls back to
    # `when`: the served `rule` is the one line the dispatcher's own table
    # shows, not the body, so it is never empty for an entry that has a trigger.
    harness = _harness(tmp_path)
    _learning(harness.tasks_dir, "ruled.md", rule="  - Start postgres first.\n\nAnd more.")
    _learning(harness.tasks_dir, "unruled.md", rule=None, when="you have no rule section")

    rows = {row["ref"]: row for row in _get(harness, "/api/learnings").get_json()["data"]}

    assert rows["inbox/ruled.md"]["rule"] == "Start postgres first."
    assert rows["inbox/unruled.md"]["rule"] == "you have no rule section"


def test_an_entry_with_no_frontmatter_is_a_warning_and_not_a_missing_list(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    _learning(harness.tasks_dir, "good.md")
    stray = Path(learnings.inbox_dir(harness.tasks_dir)) / "note.md"
    stray.write_text("no frontmatter, just prose\n")

    resp = _get(harness, "/api/learnings")

    assert resp.status_code == 200
    body = resp.get_json()
    assert [row["ref"] for row in body["data"]] == ["inbox/good.md"]
    assert body["warnings"] == [f"{stray}: unreadable learning entry; it is in no row"]


def test_a_learnings_directory_that_will_not_list_is_a_warning_and_not_a_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `read_dir` guards with `os.path.isdir` and then calls `os.listdir`, which
    # still raises for a directory that exists and cannot be read — and this
    # route lists two, so one such directory must not cost the other its rows.
    # Not reachable through a chmod in this suite, which may run as root;
    # monkeypatched because what is pinned is the guard, on
    # `test_a_handoffs_directory_that_will_not_list_is_a_warning_and_not_a_500`'s
    # model.
    harness = _harness(tmp_path)
    _learning(harness.tasks_dir, "inboxed.md")
    _learning(
        harness.tasks_dir,
        "shared.md",
        directory=learnings.HARNESS_NAME,
        project="someone-else",
    )
    inbox = learnings.inbox_dir(harness.tasks_dir)
    real = learnings.read_dir

    def refuse(root: str, path: str):
        if path == inbox:
            raise PermissionError(13, "Permission denied")
        return real(root, path)

    monkeypatch.setattr(learnings, "read_dir", refuse)

    resp = _get(harness, "/api/learnings")

    assert resp.status_code == 200
    body = resp.get_json()
    assert [row["ref"] for row in body["data"]] == ["harness/shared.md"]
    assert len(body["warnings"]) == 1
    assert body["warnings"][0].startswith(f"{inbox}: unreadable learnings directory:")


def test_no_learnings_tree_at_all_is_an_empty_list_and_a_warning_naming_it(tmp_path: Path) -> None:
    # `ensure_dirs` runs on every `run-task`, so an absent tree means no task
    # has run against this hive — different news from an empty inbox, and the
    # difference is what an operator reading an empty screen is owed.
    harness = _harness(tmp_path)

    body = _get(harness, "/api/learnings").get_json()

    assert body["data"] == []
    assert len(body["warnings"]) == 1
    assert learnings.root_dir(harness.tasks_dir) in body["warnings"][0]


def test_a_learnings_tree_with_nothing_in_it_is_empty_and_silent(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    learnings.ensure_dirs(harness.tasks_dir)

    assert _get(harness, "/api/learnings").get_json() == {"data": [], "warnings": []}


def test_learnings_project_is_optional_with_one_checkout_and_required_with_two(
    tmp_path: Path,
) -> None:
    one = _harness(tmp_path / "one", projects=("ia-harness",))
    _learning(one.tasks_dir, "ours.md")
    two = _harness(tmp_path / "two", projects=("ia-harness", "scratch"))
    _learning(two.tasks_dir, "ours.md", project="scratch")

    assert _get(one, "/api/learnings").status_code == 200

    resp = _get(two, "/api/learnings")
    assert resp.status_code == 400
    assert "scratch" in resp.get_json()["error"]
    assert _get(two, "/api/learnings?project=scratch").status_code == 200
    assert _get(two, "/api/learnings?project=nope").status_code == 404


def test_another_projects_unreviewed_entry_is_not_served_and_a_promoted_one_is(
    tmp_path: Path,
) -> None:
    # The filter is `learnings.applicable`, which is what a phase of that
    # project is shown: everything a human promoted into `harness/`, plus
    # everything the project discovered itself. `docs/decisions.md` ADR 41.
    harness = _harness(tmp_path, projects=("ia-harness", "scratch"))
    _learning(harness.tasks_dir, "ours.md", project="ia-harness")
    _learning(harness.tasks_dir, "theirs.md", project="scratch")
    _learning(
        harness.tasks_dir,
        "shared.md",
        directory=learnings.HARNESS_NAME,
        project="scratch",
    )

    ours = _get(harness, "/api/learnings?project=ia-harness").get_json()["data"]
    theirs = _get(harness, "/api/learnings?project=scratch").get_json()["data"]

    assert [row["ref"] for row in ours] == ["harness/shared.md", "inbox/ours.md"]
    assert [row["ref"] for row in theirs] == ["harness/shared.md", "inbox/theirs.md"]


def test_a_refuted_entry_is_served_and_says_it_reaches_no_phase(tmp_path: Path) -> None:
    # Retiring an entry is about it no longer costing turns, not about hiding
    # that it was written: `eligible` drops it before the cap is applied, so it
    # comes back with `in_phase_table: false` and the console says so.
    harness = _harness(tmp_path)
    _learning(harness.tasks_dir, "retired.md", status=learnings.REFUTED)
    _learning(harness.tasks_dir, "live.md")

    rows = {row["ref"]: row for row in _get(harness, "/api/learnings").get_json()["data"]}

    assert rows["inbox/retired.md"]["in_phase_table"] is False
    assert rows["inbox/retired.md"]["status"] == "refuted"
    assert rows["inbox/live.md"]["in_phase_table"] is True


def test_a_row_past_the_phase_table_cap_says_it_reaches_no_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The cap is the dispatcher's, not this service's, and it travels on the row
    # so the console measures against the number the dispatcher is using rather
    # than a literal of its own. ADR 41, ADR 42.
    monkeypatch.setattr(learnings, "MAX_ROWS", 2)
    harness = _harness(tmp_path)
    for name in ("a.md", "b.md", "c.md"):
        _learning(harness.tasks_dir, name)

    rows = _get(harness, "/api/learnings").get_json()["data"]

    assert [(row["ref"], row["in_phase_table"]) for row in rows] == [
        ("inbox/a.md", True),
        ("inbox/b.md", True),
        ("inbox/c.md", False),
    ]
    assert {row["phase_table_cap"] for row in rows} == {2}


def test_stale_is_the_apis_judgement_against_the_surface_it_is_configured_with(
    tmp_path: Path,
) -> None:
    # Both fingerprints have to be there: an entry from before the stamp existed
    # is not stale, it is unknown, and treating unknown as stale would retire
    # the whole inbox the first time this ran.
    harness = _harness(tmp_path)
    _learning(harness.tasks_dir, "foreign.md", harness="oldoldoldold")
    _learning(harness.tasks_dir, "current.md", harness=_fingerprint(harness))
    _learning(harness.tasks_dir, "unstamped.md")

    rows = {row["ref"]: row for row in _get(harness, "/api/learnings").get_json()["data"]}

    assert rows["inbox/foreign.md"]["stale"] is True
    assert rows["inbox/current.md"]["stale"] is False
    assert rows["inbox/unstamped.md"]["stale"] is False


def test_the_learnings_answer_is_clamped_and_the_clamp_names_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Nothing in this harness is clamped — the inbox is drained at merge — and
    # the cap is there so this is not the next thing the service answers with no
    # bound at all. ADR 41 part 7, on `MAX_PHASES`' model.
    monkeypatch.setattr(api_app, "MAX_LEARNINGS", 2)
    harness = _harness(tmp_path)
    for name in ("a.md", "b.md", "c.md"):
        _learning(harness.tasks_dir, name)

    body = _get(harness, "/api/learnings").get_json()

    assert [row["ref"] for row in body["data"]] == ["inbox/a.md", "inbox/b.md"]
    assert "3 learning entries" in body["warnings"][0]
    assert "at most 2" in body["warnings"][0]


def test_reading_the_learnings_route_writes_nothing(tmp_path: Path) -> None:
    # No `ensure_dirs`, no `reconcile`, no `stamp`: the hive is mounted `:ro`,
    # and the three dispatcher paths that would rewrite an entry are all
    # reachable from this module's import of `learnings`.
    harness = _harness(tmp_path)
    _learning(harness.tasks_dir, "db.md")
    root = Path(learnings.root_dir(harness.tasks_dir))
    before = {path: path.read_bytes() for path in root.rglob("*.md")}

    _get(harness, "/api/learnings")

    assert {path: path.read_bytes() for path in before} == before
    assert sorted(path.name for path in root.iterdir()) == ["harness", "inbox"]


# --- what this service must never do --------------------------------------


def test_no_route_accepts_a_write(tmp_path: Path) -> None:
    # Phase 4 puts writes behind an action queue; there are none here, and a
    # POST that started working would be the first sign that changed.
    harness = _harness(tmp_path)
    _task(harness.tasks_dir, "T-1")

    for route in ROUTES:
        for method in (harness.client.post, harness.client.put, harness.client.delete):
            assert method(route, headers=_auth()).status_code == 405


def test_flasks_own_404_and_405_are_html_not_this_envelope(tmp_path: Path) -> None:
    # docs/decisions.md ADR 5 promises a JSON {"error": ...} for every non-200
    # this module *returns*, and these two it does not return: Flask raises them
    # before any view. Pinned rather than assumed, because a Phase 2 client
    # calling .json() on any non-200 hits these on exactly the mistakes it makes
    # most — a typo'd path and a wrong verb. A later task that registers
    # errorhandlers to make the promise unconditional should fail here and
    # update the ADR with it.
    harness = _harness(tmp_path)

    missing = _get(harness, "/api/nope")
    wrong_verb = harness.client.post("/api/tasks", headers=_auth())

    assert (missing.status_code, wrong_verb.status_code) == (404, 405)
    for resp in (missing, wrong_verb):
        assert resp.headers["Content-Type"].startswith("text/html")
        assert resp.get_json(silent=True) is None


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
