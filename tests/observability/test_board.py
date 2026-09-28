"""The board's four screens, against recorded fixtures of each endpoint.

No live api anywhere in here: `requests.get` is monkeypatched in the board
module — the pattern `tests/hooks/test_emit_event.py` already uses — so every
fixture is a plain dict and the four states per region are reachable without a
container (`docs/decisions.md` ADR 7). What is under test is the boundary and
the templates: which endpoint was called with which parameters, and what the
page says when the answer is rows, `[]`, rows-with-a-warning, or not a 200.
"""

from __future__ import annotations

import base64
import datetime as dt
import re
from pathlib import Path

import pytest
import requests
from markupsafe import escape

from observability.board import app as board_app

# sha256("password")
PASSWORD_HASH = "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8"
TOKEN = "s3cret-token"
ROUTES = ["/", "/tasks/T-1", "/debt", "/events"]


class _Response:
    """What `requests.get` gives the board, as much of it as the board reads."""

    def __init__(self, payload=None, status_code=200, content_type="application/json", text=None):
        self.status_code = status_code
        self.headers = {"Content-Type": content_type} if content_type else {}
        self._payload = payload
        self.text = text if text is not None else ""

    def json(self):
        if self._payload is _UNPARSEABLE:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


_UNPARSEABLE = object()


def _envelope(data, warnings=()):
    return _Response({"data": data, "warnings": list(warnings)})


def _task_row(**fields) -> dict:
    row = {
        "task_id": "T-1",
        "status": "in_progress",
        "owner": "cuenta1",
        "heartbeat": _ago(30),
        "description": "Do the thing",
        "kanban_issue_id": None,
        "resolved_debt": [],
        "card": None,
        "lock_expired": False,
    }
    row.update(fields)
    return row


def _account_row(**fields) -> dict:
    row = {
        "name": "cuenta1",
        "container": "agent-cuenta1",
        "state": "idle",
        "current_task": None,
        "rate_limited_at": None,
    }
    row.update(fields)
    return row


def _event_row(**fields) -> dict:
    row = {
        "id": 7,
        "created_at": _ago(90),
        "source_app": "agent-cuenta1",
        "event_type": "PreToolUse",
        "payload": {"tool": "Bash"},
    }
    row.update(fields)
    return row


def _debt_row(**fields) -> dict:
    row = {
        "id": "T-008-D1",
        "what": "No lock around set_status",
        "where": "two dispatch processes",
        "fix": "a lock per card",
        "card": "none",
        "resolved": False,
    }
    row.update(fields)
    return row


def _ago(seconds: int) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds)).isoformat()


#: The five endpoints, by the name a test recording one calls it.
_PATHS = {
    "tasks": "/api/tasks",
    "task": "/api/tasks/T-1",
    "accounts": "/api/accounts",
    "events": "/api/events",
    "debt": "/api/debt",
}


class _Api:
    """Recorded answers by endpoint, and what the board asked for.

    Any endpoint with no recording answers an empty envelope, so a test names
    only the ones it is about — and `calls` is what pins that every events call
    carried an explicit `limit`.
    """

    def __init__(self, **by_name):
        self.answers = {_PATHS[name]: answer for name, answer in by_name.items()}
        self.calls: list[tuple[str, dict, dict]] = []

    def get(self, url, params=None, headers=None, timeout=None):
        path = url.split("8789", 1)[-1]
        self.calls.append((path, params or {}, headers or {}))
        assert timeout == board_app.TIMEOUT_SECONDS, "every call is bounded (ADR 7)"
        answer = self.answers.get(path, _envelope([]))
        if isinstance(answer, Exception):
            raise answer
        return answer

    def params_for(self, path: str) -> dict:
        return next(params for called, params, _ in self.calls if called == path)


def _client(monkeypatch, api: _Api, token: str | None = TOKEN, project: str | None = None):
    monkeypatch.setattr(board_app.requests, "get", api.get)
    app = board_app.create_app(
        "http://api:8789",
        token,
        "admin",
        PASSWORD_HASH,
        project,
    )
    return app.test_client()


def _auth() -> dict:
    return {"Authorization": "Basic " + base64.b64encode(b"admin:password").decode()}


def _page(monkeypatch, api: _Api, route: str = "/", **kwargs) -> str:
    resp = _client(monkeypatch, api, **kwargs).get(route, headers=_auth())
    assert resp.status_code == 200, resp.data
    return resp.get_data(as_text=True)


def _says(html: str, sentence: str) -> bool:
    """Is this sentence on the page, as Jinja would have written it?

    Everything the board renders is autoescaped, so a sentence with an
    apostrophe in it — most of the empty states — reaches the page as `&#39;`.
    Escaping the expectation rather than the page keeps the constant in the test
    readable and keeps the escaping itself under test.
    """
    return str(escape(sentence)) in html


# --- who may look --------------------------------------------------------


@pytest.mark.parametrize("route", ROUTES)
def test_every_screen_answers_401_without_credentials(monkeypatch, route: str) -> None:
    resp = _client(monkeypatch, _Api()).get(route)

    assert resp.status_code == 401


def test_the_401_names_this_services_own_realm(monkeypatch) -> None:
    resp = _client(monkeypatch, _Api()).get("/")

    assert resp.headers["WWW-Authenticate"] == 'Basic realm="ia-harness board"'


def test_the_api_token_is_not_a_second_way_into_the_board(monkeypatch) -> None:
    """The human's credential is the only thing the board accepts: `requires_auth`
    is given no token, so the api's token does not open this service
    (`docs/decisions.md` ADR 7)."""
    resp = _client(monkeypatch, _Api()).get("/", headers={"Authorization": f"Bearer {TOKEN}"})

    assert resp.status_code == 401


def test_the_token_travels_to_the_api_and_never_to_the_page(monkeypatch) -> None:
    api = _Api(tasks=_envelope([_task_row()]), accounts=_envelope([_account_row()]))

    html = _page(monkeypatch, api)

    assert all(call[2]["Authorization"] == f"Bearer {TOKEN}" for call in api.calls)
    assert TOKEN not in html
    # Every fetch is server-side: a script dialling the api from the browser is a
    # review finding and not a style preference, so it is an assertion.
    assert "<script" not in html
    assert "fetch(" not in html


def test_an_api_with_no_token_is_called_with_no_authorization_header(monkeypatch) -> None:
    api = _Api()

    _page(monkeypatch, api, token=None)

    assert all("Authorization" not in call[2] for call in api.calls)


# --- the states, per region ----------------------------------------------


def test_the_pool_and_the_work_render_their_rows(monkeypatch) -> None:
    api = _Api(
        accounts=_envelope([_account_row(current_task="T-1", state="working")]),
        tasks=_envelope([_task_row()]),
    )

    html = _page(monkeypatch, api)

    assert "agent-cuenta1" in html
    assert "working" in html
    assert 'href="/tasks/T-1"' in html
    assert "as of" in html


def test_an_empty_region_says_what_would_put_a_row_there(monkeypatch) -> None:
    api = _Api(accounts=_envelope([]), tasks=_envelope([]))

    html = _page(monkeypatch, api)

    assert _says(html, board_app.EMPTY_ACCOUNTS)
    assert _says(html, board_app.EMPTY_TASKS)
    # Empty is never styled as a failure: the sentence is in the region's normal
    # type and carries no error class.
    assert 'class="error"' not in html


def test_a_partial_region_renders_its_warnings_above_its_rows(monkeypatch) -> None:
    warning = "/data/.hive/tasks/T-2.md: unreadable task file: nope"
    api = _Api(
        tasks=_envelope([_task_row()], [warning]),
        accounts=_envelope([_account_row()]),
    )

    html = _page(monkeypatch, api)

    # Verbatim, and above the rows it qualifies: a Phase 1 warning names the file
    # that is the missing row, which is the one artifact an operator pastes into a
    # shell.
    assert warning in html
    assert html.index(warning) < html.index("T-1")


def test_no_warnings_renders_no_container_at_all(monkeypatch) -> None:
    api = _Api(tasks=_envelope([_task_row()]), accounts=_envelope([_account_row()]))

    html = _page(monkeypatch, api)

    # No container and no reserved space: the absence of a warning is not worth a
    # pixel, and its arrival should be a change in the page.
    assert '<ul class="warnings">' not in html


def test_one_endpoint_failing_leaves_the_other_region_standing(monkeypatch) -> None:
    """The region and not the page is the unit. A page that fails because one of
    its calls did is a page that hides three working answers."""
    api = _Api(
        accounts=_Response({"error": "config.yaml is unreadable"}, status_code=503),
        tasks=_envelope([_task_row()]),
    )

    html = _page(monkeypatch, api)

    assert "GET /api/accounts → 503" in html
    assert "config.yaml is unreadable" in html
    assert "T-1" in html
    assert board_app.EMPTY_TASKS not in html


def test_an_html_body_under_a_404_is_the_error_state_and_not_a_traceback(monkeypatch) -> None:
    """The case the content-type guard exists for: Flask's own 404 and 405 are
    HTML and bypass the envelope (`docs/decisions.md` ADR 5)."""
    api = _Api(
        tasks=_Response(None, status_code=404, content_type="text/html", text="<!doctype html>")
    )

    html = _page(monkeypatch, api)

    assert "GET /api/tasks → 404" in html


def test_a_200_whose_body_is_html_is_the_error_state_too(monkeypatch) -> None:
    api = _Api(tasks=_Response(None, content_type="text/html", text="<h1>hello</h1>"))

    html = _page(monkeypatch, api)

    assert "GET /api/tasks → 200" in html
    assert "not application/json" in html


def test_a_200_whose_json_will_not_parse_is_the_error_state(monkeypatch) -> None:
    api = _Api(tasks=_Response(_UNPARSEABLE))

    html = _page(monkeypatch, api)

    assert "GET /api/tasks → 200" in html
    assert "unreadable JSON" in html


@pytest.mark.parametrize(
    "raised",
    [
        requests.ConnectionError("Connection refused"),
        requests.Timeout("Read timed out"),
        requests.RequestException("something else"),
    ],
)
def test_an_api_that_does_not_answer_is_the_error_state_naming_the_endpoint(
    monkeypatch, raised
) -> None:
    api = _Api(tasks=raised)

    html = _page(monkeypatch, api)

    assert "GET /api/tasks → no answer" in html
    assert type(raised).__name__ in html


@pytest.mark.parametrize(
    "body",
    [
        {"warnings": []},
        {"data": []},
        {"data": [], "warnings": "nope"},
        {"data": [], "warnings": [{"file": "x"}]},
        {"data": ["not a row"], "warnings": []},
        # A list endpoint answering one object: Jinja would iterate its keys and
        # render a table of blank cells, which is the failure this shape check
        # exists to turn into an Error state.
        {"data": {"task_id": "T-1"}, "warnings": []},
        [1, 2, 3],
        "a string",
    ],
)
def test_an_envelope_of_the_wrong_shape_is_the_error_state_and_not_a_template_crash(
    monkeypatch, body
) -> None:
    api = _Api(tasks=_Response(body))

    html = _page(monkeypatch, api)

    assert "GET /api/tasks → 200" in html
    assert 'class="error"' in html


def test_a_row_missing_a_key_the_screen_reads_is_the_error_state(monkeypatch) -> None:
    api = _Api(tasks=_envelope([{"task_id": "T-1", "status": "pending"}]))

    html = _page(monkeypatch, api)

    assert "is missing" in html
    assert "lock_expired" in html


def test_only_the_task_page_needs_the_two_fields_only_it_renders(monkeypatch) -> None:
    """`description` and `resolved_debt` are read by `/tasks/<id>` and by no
    other screen, so `TASK_DETAIL_KEYS` checks them and `TASK_KEYS` does not.
    Folded into one tuple, an api that stopped serialising `description` on the
    list route would take the index down over a field the index never renders.
    """
    row = _task_row()
    del row["description"], row["resolved_debt"]
    api = _Api(task=_envelope(row), tasks=_envelope([row]), accounts=_envelope([]))

    detail = _page(monkeypatch, api, "/tasks/T-1")
    index = _page(monkeypatch, api)

    assert "is missing description, resolved_debt" in detail
    assert "is missing" not in index


def test_an_event_row_missing_its_payload_is_the_error_state(monkeypatch) -> None:
    """Both events tables render `payload` through a filter, and a filter is not
    a place a missing key announces itself: `payload` is in `EVENT_KEYS` so the
    shape is caught at the boundary instead."""
    row = _event_row()
    del row["payload"]
    api = _Api(events=_envelope([row]))

    html = _page(monkeypatch, api, "/events")

    assert "is missing payload" in html


# --- the one derived fact the board renders and never derives -------------


def test_an_expired_lock_is_the_loudest_thing_on_the_row(monkeypatch) -> None:
    api = _Api(tasks=_envelope([_task_row(lock_expired=True, heartbeat=_ago(7200))]))

    html = _page(monkeypatch, api)

    assert "lock expired" in html
    assert "2h ago" in html


def test_a_live_lock_shows_the_heartbeats_age_and_nothing_else(monkeypatch) -> None:
    api = _Api(tasks=_envelope([_task_row(lock_expired=False, heartbeat=_ago(240))]))

    html = _page(monkeypatch, api)

    assert "4m ago" in html
    assert "lock expired" not in html
    assert "no heartbeat" not in html


def test_a_null_lock_is_a_task_nobody_holds_and_not_a_healthy_one(monkeypatch) -> None:
    api = _Api(tasks=_envelope([_task_row(lock_expired=None, heartbeat=None, owner=None)]))

    html = _page(monkeypatch, api)

    assert "no heartbeat" in html
    assert "lock expired" not in html


def test_a_heartbeat_the_api_could_not_judge_renders_verbatim(monkeypatch) -> None:
    """`docs/decisions.md` ADR 8 and ADR 10: the board renders an age, and a
    timestamp that will not parse renders as it stands rather than being hidden."""
    api = _Api(tasks=_envelope([_task_row(lock_expired=None, heartbeat="yesterday")]))

    html = _page(monkeypatch, api)

    assert "yesterday" in html


def test_the_board_never_compares_a_timestamp_against_an_expiry_window() -> None:
    """The mechanical form of `docs/decisions.md` ADR 8, as a test rather than a
    review note: an age is allowed in here, a judgement is not — and a judgement
    needs the dispatcher's expiry window, which is a config value only the api
    holds. The ADR names the grep; this is the grep."""
    package = Path(board_app.__file__).parent
    sources = [path for path in sorted(package.rglob("*")) if path.suffix in (".py", ".html")]

    assert sources
    for path in sources:
        assert "ttl" not in path.read_text().lower(), path


def test_nothing_in_the_board_imports_the_dispatcher() -> None:
    """The other half of one parser per fact: everything on every screen arrives
    as JSON from the api, so this package has no reason to reach the readers, the
    config or the task files — and the image it builds into does not carry them."""
    package = Path(board_app.__file__).parent

    for path in sorted(package.rglob("*.py")):
        assert not re.search(r"^\s*(import|from)\s+dispatcher\b", path.read_text(), re.M), path
    dockerfile = (package / "Dockerfile").read_text()
    assert not re.search(r"^COPY\s+dispatcher/", dockerfile, re.M)


def test_the_wheel_the_image_installs_ships_the_templates() -> None:
    """`observability/board/Dockerfile` runs `pip install .`, so the container
    renders from a wheel and not from this checkout. Setuptools puts no
    non-Python file in a wheel unless `package-data` names it, and the failure is
    invisible from here: running `python -m observability.board.app` from a
    checkout finds the templates on the filesystem either way, so only the image
    breaks — with a `TemplateNotFound` on every route. Pinned as a text
    assertion because a phase cannot build the image to find out."""
    import tomllib

    pyproject = Path(board_app.__file__).parents[2] / "pyproject.toml"
    package_data = tomllib.loads(pyproject.read_text())["tool"]["setuptools"]["package-data"]

    assert "templates/*.html" in package_data["observability.board"]
    templates = {path.name for path in (Path(board_app.__file__).parent / "templates").iterdir()}
    assert templates and all(name.endswith(".html") for name in templates), templates


def test_nothing_in_the_board_is_a_node_project() -> None:
    """`docs/charter.md` C-7: a package.json, a lockfile or a build step anywhere
    under here means the phase was built against the wrong ruling — and the
    saving the ruling buys is that this phase adds no dependency to pin."""
    package = Path(board_app.__file__).parent
    names = {path.name for path in package.rglob("*")}

    assert not names & {"package.json", "package-lock.json", "tsconfig.json", "node_modules"}
    assert "npm" not in (package / "Dockerfile").read_text()


# --- a null card is three facts ------------------------------------------


def test_a_task_with_no_issue_id_says_it_was_dispatched_without_a_board(monkeypatch) -> None:
    api = _Api(tasks=_envelope([_task_row(kanban_issue_id=None, card=None)]))

    html = _page(monkeypatch, api)

    assert "dispatched without a board" in html
    assert "board does not hold this card" not in html


def test_an_issue_id_whose_card_is_missing_is_marked(monkeypatch) -> None:
    """The symptom `docs/debt/T-008-D2.md` describes: an id the board does not
    hold is an inconsistency and not a task without a card."""
    api = _Api(tasks=_envelope([_task_row(kanban_issue_id="1111-2222", card=None)]))

    html = _page(monkeypatch, api)

    assert "board does not hold this card" in html
    assert "dispatched without a board" not in html


def test_a_card_the_board_holds_renders_its_title_and_status(monkeypatch) -> None:
    api = _Api(
        tasks=_envelope(
            [
                _task_row(
                    kanban_issue_id="1111-2222",
                    card={"issue_id": "1111-2222", "title": "Phase 2", "status": "pending"},
                )
            ]
        )
    )

    html = _page(monkeypatch, api)

    assert "Phase 2" in html
    assert "pending" in html


# --- /tasks/<id> ---------------------------------------------------------


def test_the_task_screen_shows_the_account_window_and_says_it_is_not_the_tasks(
    monkeypatch,
) -> None:
    api = _Api(
        task=_envelope(_task_row()),
        accounts=_envelope([_account_row()]),
        events=_envelope([_event_row()]),
    )

    html = _page(monkeypatch, api, "/tasks/T-1")

    assert "events from agent-cuenta1, the account that owns this task" in html
    assert "not this task's events" in html
    assert "PreToolUse" in html
    assert api.params_for("/api/events") == {
        "source_app": "agent-cuenta1",
        "limit": board_app.TASK_EVENTS_LIMIT,
    }


def test_a_task_with_no_owner_has_no_account_whose_events_to_show(monkeypatch) -> None:
    """Not an empty state: a sentence saying why there is nothing to ask for. The
    call is never made, so there is no region to be empty."""
    api = _Api(task=_envelope(_task_row(owner=None)), accounts=_envelope([]))

    html = _page(monkeypatch, api, "/tasks/T-1")

    assert "this task has no owner, so there is no account whose events to show" in html
    assert "/api/events" not in [call[0] for call in api.calls]


def test_an_owner_no_account_is_configured_for_says_so(monkeypatch) -> None:
    api = _Api(
        task=_envelope(_task_row(owner="cuenta9")),
        accounts=_envelope([_account_row()]),
    )

    html = _page(monkeypatch, api, "/tasks/T-1")

    assert "no account named cuenta9 is configured" in html


def test_the_events_half_carries_the_accounts_error_when_there_is_no_map(monkeypatch) -> None:
    """`/api/accounts` is the only place a task's `owner` and an event's
    `source_app` meet, so when that call fails the events half says so — and the
    task's own fields above it still render."""
    api = _Api(
        task=_envelope(_task_row()),
        accounts=_Response({"error": "boom"}, status_code=500),
    )

    html = _page(monkeypatch, api, "/tasks/T-1")

    assert "GET /api/accounts → 500" in html
    assert "in_progress" in html


def test_an_empty_events_window_names_the_container_it_asked_about(monkeypatch) -> None:
    api = _Api(
        task=_envelope(_task_row()),
        accounts=_envelope([_account_row()]),
        events=_envelope([]),
    )

    html = _page(monkeypatch, api, "/tasks/T-1")

    assert "no events from agent-cuenta1 yet" in html


def test_a_task_whose_file_will_not_parse_is_a_warning_and_a_sentence(monkeypatch) -> None:
    """`/api/tasks/<id>` answers `data: null` plus a warning for a file it could
    not read, which is a 200 and not an error: the warning names the file."""
    warning = "/data/.hive/tasks/T-1.md: unreadable task file: nope"
    api = _Api(task=_envelope(None, [warning]), accounts=_envelope([]))

    html = _page(monkeypatch, api, "/tasks/T-1")

    assert warning in html
    assert "no task T-1" in html


def test_the_task_endpoint_answering_a_list_is_the_error_state(monkeypatch) -> None:
    """The mirror of the list-endpoint check: `/api/tasks/<id>` answers one object
    or `null`, and a list there would render as a one-row table of blanks."""
    api = _Api(task=_envelope([_task_row()]), accounts=_envelope([]))

    html = _page(monkeypatch, api, "/tasks/T-1")

    assert "not one object" in html


def test_a_task_id_with_a_query_string_in_it_cannot_reach_the_api_as_one(
    monkeypatch,
) -> None:
    """The id arrives from a URL and is then part of one. Unquoted, this would
    reach the api as `/api/tasks/T-1?limit=1` and come back as a 400 about a
    parameter nobody sent."""
    api = _Api(accounts=_envelope([]))

    _page(monkeypatch, api, "/tasks/T-1%3Flimit%3D1")

    assert api.calls[0][0] == "/api/tasks/T-1%3Flimit%3D1"


def test_a_404_from_the_api_is_the_error_state_naming_the_endpoint(monkeypatch) -> None:
    api = _Api(
        task=_Response({"error": "no task 'T-1' in /data/.hive/tasks"}, status_code=404),
        accounts=_envelope([]),
    )

    html = _page(monkeypatch, api, "/tasks/T-1")

    assert "GET /api/tasks/T-1 → 404" in html
    assert _says(html, "no task 'T-1' in /data/.hive/tasks")


# --- /debt ---------------------------------------------------------------


def test_the_debt_screen_renders_the_index_rows(monkeypatch) -> None:
    api = _Api(debt=_envelope([_debt_row(), _debt_row(id="T-008-D2", resolved=True)]))

    html = _page(monkeypatch, api, "/debt")

    assert "T-008-D1" in html
    assert "No lock around set_status" in html
    # An entry outlives its fix: a resolved row is marked, never hidden.
    assert "T-008-D2" in html
    assert "[resolved]" in html


def test_the_configured_project_is_sent_as_a_parameter(monkeypatch) -> None:
    """`docs/decisions.md` ADR 9: `/api/debt` needs a slug wherever the host holds
    more than one checkout, and the board has no config.yaml to read one from."""
    api = _Api(debt=_envelope([]))

    _page(monkeypatch, api, "/debt", project="ia-harness")

    assert api.params_for("/api/debt") == {"project": "ia-harness"}


def test_no_configured_project_sends_no_parameter_and_lets_the_api_pick(monkeypatch) -> None:
    api = _Api(debt=_envelope([]))

    _page(monkeypatch, api, "/debt")

    assert api.params_for("/api/debt") == {}


def test_a_query_parameter_overrides_the_configured_project_for_one_navigation(
    monkeypatch,
) -> None:
    api = _Api(debt=_envelope([]))

    _page(monkeypatch, api, "/debt?project=scratch", project="ia-harness")

    assert api.params_for("/api/debt") == {"project": "scratch"}


def test_the_apis_400_enumerating_the_projects_reaches_the_screen(monkeypatch) -> None:
    api = _Api(
        debt=_Response(
            {"error": "project is required: /data/projects holds ia-harness, scratch"},
            status_code=400,
        )
    )

    html = _page(monkeypatch, api, "/debt")

    assert "GET /api/debt → 400" in html
    assert "ia-harness, scratch" in html


def test_a_project_with_no_debt_says_so(monkeypatch) -> None:
    api = _Api(debt=_envelope([]))

    html = _page(monkeypatch, api, "/debt")

    assert board_app.EMPTY_DEBT in html


# --- /events -------------------------------------------------------------


def test_the_event_log_renders_newest_first_as_the_api_ordered_them(monkeypatch) -> None:
    api = _Api(
        events=_envelope(
            [_event_row(id=9, event_type="Stop"), _event_row(id=8, event_type="PreToolUse")]
        )
    )

    html = _page(monkeypatch, api, "/events")

    assert html.index("Stop") < html.index("PreToolUse")


def test_every_events_call_sets_limit_explicitly(monkeypatch) -> None:
    """The board asks for what it renders rather than relying on the api's
    `DEFAULT_EVENT_LIMIT` staying 100, and asks for less than the api's
    `MAX_EVENT_LIMIT` will answer — `docs/decisions.md` ADR 11."""
    api = _Api(events=_envelope([_event_row()]))

    _page(monkeypatch, api, "/events")

    assert api.params_for("/api/events") == {"limit": board_app.EVENTS_LIMIT}


def test_the_source_app_filter_is_a_get_form_that_re_navigates(monkeypatch) -> None:
    api = _Api(events=_envelope([_event_row()]))

    html = _page(monkeypatch, api, "/events?source_app=agent-cuenta2")

    assert api.params_for("/api/events") == {
        "source_app": "agent-cuenta2",
        "limit": board_app.EVENTS_LIMIT,
    }
    assert 'method="get"' in html
    # Nothing on the page writes: no other form, and no disabled control
    # previewing Phase 4.
    assert "disabled" not in html
    assert 'method="post"' not in html.lower()


def test_an_empty_filter_is_no_filter(monkeypatch) -> None:
    api = _Api(events=_envelope([_event_row()]))

    _page(monkeypatch, api, "/events?source_app=")

    assert api.params_for("/api/events") == {"limit": board_app.EVENTS_LIMIT}


def test_a_filter_that_matched_nothing_says_which_filter(monkeypatch) -> None:
    api = _Api(events=_envelope([]))

    html = _page(monkeypatch, api, "/events?source_app=agent-cuenta2")

    assert "no events from agent-cuenta2 yet" in html
    assert not _says(html, board_app.EMPTY_EVENTS)


def test_an_events_database_that_is_not_there_is_a_warning_over_an_empty_table(
    monkeypatch,
) -> None:
    """What Phase 1 answers for a harness that has never run: a 200, `[]`, and a
    warning naming the file. Not an error state — no events is not an error."""
    warning = "/events/events.db: no events database yet: unable to open database file"
    api = _Api(events=_envelope([], [warning]))

    html = _page(monkeypatch, api, "/events")

    assert warning in html
    assert _says(html, board_app.EMPTY_EVENTS)
    assert 'class="error"' not in html


# --- every page -----------------------------------------------------------


@pytest.mark.parametrize("route", ROUTES)
def test_every_page_says_when_it_was_true(monkeypatch, route: str) -> None:
    """A snapshot with no stamp is indistinguishable from a crashed harness: the
    page is stale one second after it rendered, and only the stamp says which
    second that was."""
    api = _Api(
        tasks=_envelope([_task_row()]),
        task=_envelope(_task_row()),
        accounts=_envelope([_account_row()]),
        events=_envelope([_event_row()]),
        debt=_envelope([_debt_row()]),
    )

    html = _page(monkeypatch, api, route)

    assert "as of" in html
    assert "reload" in html


@pytest.mark.parametrize("route", ROUTES)
def test_every_page_uses_real_tables_with_real_headers(monkeypatch, route: str) -> None:
    api = _Api(
        tasks=_envelope([_task_row()]),
        task=_envelope(_task_row()),
        accounts=_envelope([_account_row()]),
        events=_envelope([_event_row()]),
        debt=_envelope([_debt_row()]),
    )

    html = _page(monkeypatch, api, route)

    assert "<table>" in html
    assert "<th>" in html


def test_a_value_from_the_api_is_escaped_and_not_rendered_as_markup(monkeypatch) -> None:
    """Every cell is a string some agent wrote into a file. Jinja autoescapes, and
    this is the assertion that says so out loud."""
    api = _Api(tasks=_envelope([_task_row(status="<script>alert(1)</script>")]))

    html = _page(monkeypatch, api)

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# --- the boundary's own helpers ------------------------------------------


@pytest.mark.parametrize(
    "seconds, expected",
    [(0, "just now"), (59, "just now"), (60, "1m ago"), (3600, "1h ago"), (90000, "1d ago")],
)
def test_an_age_is_relative_in_the_cell(seconds: int, expected: str) -> None:
    assert board_app._age(_ago(seconds)) == expected


def test_a_timestamp_that_will_not_parse_is_its_own_age(monkeypatch) -> None:
    assert board_app._age("yesterday afternoon") == "yesterday afternoon"
    assert board_app._age(None) == ""
    assert board_app._age(7) == ""


def test_a_timestamp_with_no_offset_is_read_as_utc() -> None:
    """Every writer in `dispatcher/` writes UTC with an offset, so this is a
    hand-edited value; reading it as UTC beats raising in a template."""
    naive = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=2)).replace(tzinfo=None)

    assert board_app._age(naive.isoformat()) == "2m ago"


def test_a_payload_is_one_line_and_bounded() -> None:
    assert board_app._payload({"tool": "Bash"}) == '{"tool": "Bash"}'
    assert board_app._payload(None) == ""
    assert board_app._payload({}) == ""
    assert len(board_app._payload({"x": "y" * 500})) == 120
