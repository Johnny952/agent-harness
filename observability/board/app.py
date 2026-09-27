# observability/board/app.py
"""The screens a human opens instead of a shell prompt.

Phase 2 of `docs/plans/board.md`. Four routes over the five endpoints Phase 1
answers, rendered on the server with Jinja — `docs/charter.md` C-7 rules the
toolchain and no role re-decides it: this repo is Python and Docker, and a
lockfile plus a build step is bought when a screen needs one (Phase 5's live
timeline), not before.

What makes this service cheap to reason about is what it cannot reach. It is an
HTTP client of the api and nothing else: no volumes, no `.hive/`, no events
database, no docker socket, and no import of `dispatcher` anywhere. Every fact
on every page arrives as JSON from one endpoint, which is what keeps the parse
count at one — a board with its own frontmatter parser is a second answer to
the same question, and the first time the two disagree the operator has to work
out which is lying.

Three rules shape the module:

- **The region is the unit, not the page.** `/` calls two endpoints and they
  fail independently; one 503 costs its own table and nothing else. Every call
  goes through `_fetch`, which answers a `Region` and never raises at a
  template (`docs/decisions.md` ADR 7).
- **Render, do not re-derive.** Whether a lock is stale arrives as
  `lock_expired` on the task row. The one clock this service is allowed is its
  own render time — an *age* for a timestamp, never a judgement about whether a
  lock has expired (`docs/decisions.md` ADR 8). The expiry window is config the
  api holds and this service must not learn: a grep for it across this package
  comes back empty, and
  `tests/observability/test_board.py:test_the_board_never_compares_a_timestamp_against_an_expiry_window`
  is that grep.
- **Nothing here writes.** No form but the `source_app` filter, whose method is
  `get` and which is therefore a navigation; no control that does not navigate;
  nothing disabled and waiting for Phase 4.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
from urllib.parse import quote, urlencode

import requests
from flask import Flask, render_template, request

from observability import auth

PORT = 8790

#: Where Phase 1 answers, over `ia_harness_net`. The browser never talks to it:
#: every fetch in here is server-side, which is what keeps the api's credential
#: inside a container instead of in a page an operator can view-source.
DEFAULT_API_BASE_URL = "http://api:8789"

#: This service's realm. Every service keeps its own; see
#: `observability/auth.py:requires_auth` on why none of them is a default.
REALM = "ia-harness board"

#: One bounded read per call (`docs/decisions.md` ADR 7), the timeout
#: `hooks/emit_event.py` already uses for the collector. A board with no push
#: and no timeout is a page that never arrives.
TIMEOUT_SECONDS = 5

#: Every events call sets `limit` explicitly rather than relying on the api's
#: `DEFAULT_EVENT_LIMIT` staying 100. `docs/debt/T-009-D2.md` stays open: the
#: api still does not clamp a hand-typed `?limit=`, and this is not that fix.
EVENTS_LIMIT = 200
TASK_EVENTS_LIMIT = 50

#: What would put a row where there is none, one sentence each, in the region's
#: normal type. Empty is never styled as a failure: Phase 1 deliberately answers
#: a harness that has never run with `[]`, and a board that paints that red
#: teaches the operator to distrust the colour for the case that matters.
EMPTY_ACCOUNTS = "no accounts configured; config.yaml's accounts: block is what puts one here"
EMPTY_TASKS = "no tasks yet; dispatch run-task creates one"
EMPTY_EVENTS = "no events yet; an agent's hook posts the first one"
EMPTY_DEBT = "no debt filed for this project"

#: The events half of `/tasks/<id>` is labelled for what it is. The collector
#: records per agent and not per task — an event carries `source_app`, the
#: account's *container*, while a task's `owner` is the account *name* — so
#: there is no join to make and the screen says so rather than inventing one out
#: of a field that does not exist. A per-task timeline is Phase 5's.
TASK_EVENTS_NOTE = (
    "The collector records events per agent, not per task: an event carries "
    "source_app, which is the account's container, and a task file carries owner, "
    "which is the account's name. There is no field joining an event to a task, so "
    "this is the account's window and not this task's timeline. A per-task timeline "
    "is Phase 5's."
)

#: The keys each screen reads off a row, checked once at the boundary so a shape
#: that changed is a region's Error state and not a `jinja2.UndefinedError`
#: halfway down a half-rendered page.
ACCOUNT_KEYS = ("name", "container", "state", "current_task", "rate_limited_at")
TASK_KEYS = (
    "task_id",
    "status",
    "owner",
    "heartbeat",
    "lock_expired",
    "kanban_issue_id",
    "card",
)
EVENT_KEYS = ("id", "created_at", "source_app", "event_type")
DEBT_KEYS = ("id", "what", "where", "fix", "card", "resolved")


@dataclasses.dataclass(frozen=True)
class Region:
    """One api call's worth of a screen: what it answered, or why it did not.

    `rows` is whatever `data` held — a list for the four list endpoints, a
    mapping for `/api/tasks/<id>`, and `None` for a task whose file would not
    parse. `error` set is the Error state and means `rows` is not to be read.
    """

    endpoint: str
    rows: list | dict | None = None
    warnings: tuple[str, ...] = ()
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def empty(self) -> bool:
        """A 200 with nothing in it, which is a state and not a failure."""
        return self.ok and not self.rows


def create_app(
    api_base_url: str,
    token: str | None,
    username: str,
    password_hash: str,
    project: str | None = None,
) -> Flask:
    """The board, shaped like `observability/api/app.py:create_app`.

    Only `project` has a default: a board whose credentials defaulted to empty
    strings would be a service a typo can open, and `api_base_url` has one right
    answer per deployment rather than one right answer.

    The human's credential and the api's are different things and travel in
    opposite directions: `username`/`password_hash` is what a browser must
    present to this service, `token` is what this service presents to the api.
    `requires_auth` is given no token, so the api's token is not a second door
    into the board (`docs/decisions.md` ADR 7).

    `project` is `BOARD_PROJECT`: `/api/debt` needs a slug wherever the host
    holds more than one checkout, and this service has no `config.yaml` to read
    a default from — by design, since it mounts nothing. `docs/decisions.md`
    ADR 9.
    """
    app = Flask(__name__)
    requires_auth = auth.requires_auth(username, password_hash, realm=REALM)
    # The two things a cell needs that Jinja has no expression for. Both are
    # presentation of a value the api gave: an age, and a payload on one line.
    app.jinja_env.filters["age"] = _age
    app.jinja_env.filters["payload"] = _payload
    app.jinja_env.globals.update(
        EMPTY_ACCOUNTS=EMPTY_ACCOUNTS,
        EMPTY_TASKS=EMPTY_TASKS,
        EMPTY_EVENTS=EMPTY_EVENTS,
        EMPTY_DEBT=EMPTY_DEBT,
        TASK_EVENTS_NOTE=TASK_EVENTS_NOTE,
    )

    def fetch(
        path: str,
        params: dict | None = None,
        keys: tuple[str, ...] = (),
        many: bool = True,
    ) -> Region:
        return _fetch(api_base_url, token, path, params=params, keys=keys, many=many)

    @app.get("/")
    @requires_auth
    def index():
        return _render(
            "index.html",
            accounts=fetch("/api/accounts", keys=ACCOUNT_KEYS),
            tasks=fetch("/api/tasks", keys=TASK_KEYS),
        )

    @app.get("/tasks/<task_id>")
    @requires_auth
    def task(task_id: str):
        # Quoted, because the id arrives from a URL and is then part of one: an
        # id with a `?` or a `#` in it would otherwise reach the api as a query
        # string and come back as a 400 about a parameter nobody sent. `safe=""`
        # so a `/` cannot escape the route either — Werkzeug's converter already
        # refuses one, and this is the second lock, the same pair
        # `observability/api/app.py:_is_bare_task_id` is the other half of.
        found = fetch(f"/api/tasks/{quote(task_id, safe='')}", keys=TASK_KEYS, many=False)
        accounts = fetch("/api/accounts", keys=ACCOUNT_KEYS)
        owner = found.rows.get("owner") if isinstance(found.rows, dict) else None
        container, no_events_because = _owner_container(owner, accounts)
        events = (
            fetch(
                "/api/events",
                params={"source_app": container, "limit": TASK_EVENTS_LIMIT},
                keys=EVENT_KEYS,
            )
            if container
            else None
        )
        return _render(
            "task.html",
            task_id=task_id,
            task=found,
            events=events,
            container=container,
            no_events_because=no_events_because,
        )

    @app.get("/debt")
    @requires_auth
    def debt():
        # `?project=` overrides `BOARD_PROJECT` for one navigation, and omitting
        # both lets the api pick on a host with a single checkout. A query
        # parameter and not a route: the spec's "no other route exists" holds.
        slug = request.args.get("project") or project
        return _render(
            "debt.html",
            debt=fetch("/api/debt", params={"project": slug} if slug else None, keys=DEBT_KEYS),
            project=slug,
        )

    @app.get("/events")
    @requires_auth
    def events():
        source_app = request.args.get("source_app") or None
        return _render(
            "events.html",
            events=fetch(
                "/api/events",
                params={"source_app": source_app, "limit": EVENTS_LIMIT},
                keys=EVENT_KEYS,
            ),
            source_app=source_app,
        )

    return app


def _render(template: str, **context):
    """Every page, stamped with the one clock this service has.

    `as of <HH:MM:SS>` is a requirement and not a nicety: Phase 2 has no push
    and no poll, so every screen is a snapshot, and without the stamp a
    five-minute-old page and a dead harness are the same picture.
    """
    return render_template(template, as_of=_now().strftime("%H:%M:%S"), **context)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# --- the one boundary -----------------------------------------------------


def _fetch(
    base_url: str,
    token: str | None,
    path: str,
    params: dict | None = None,
    keys: tuple[str, ...] = (),
    many: bool = True,
) -> Region:
    """One api call, as a region: rows and warnings, or one error string.

    Everything that can go wrong between here and the api lands in `error`,
    named by the endpoint and the status the way the spec writes it
    (`GET /api/accounts → 503`), plus the api's own `{"error": …}` sentence when
    the body carried one. Nothing raises out of here, because the alternative is
    a traceback where a table should be.

    The content-type guard is not defensiveness: `docs/decisions.md` ADR 5 pins
    that Flask's own 404 and 405 are HTML and bypass the envelope, so a client
    that assumes JSON on every response crashes on a mistyped path instead of
    showing its Error state.
    """
    sent = {k: v for k, v in (params or {}).items() if v is not None}
    endpoint = f"{path}?{urlencode(sent)}" if sent else path
    try:
        resp = requests.get(
            f"{base_url.rstrip('/')}{path}",
            params=sent,
            headers={"Authorization": f"Bearer {token}"} if token else {},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        # No status to report: the api did not answer at all. The class name is
        # in there because `ConnectTimeout` and `ConnectionError` are different
        # news — one is a slow api, the other is no api.
        return Region(endpoint, error=f"GET {endpoint} → no answer: {type(exc).__name__}: {exc}")

    if resp.status_code != 200:
        # The api's own sentence, when it sent one, and only here: parsing the
        # body of a 200 twice buys nothing, and `_api_error` is written for the
        # case where the body may be Flask's HTML.
        return Region(endpoint, error=f"GET {endpoint} → {resp.status_code}{_api_error(resp)}")
    content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
    if content_type != "application/json":
        return Region(
            endpoint,
            error=(
                f"GET {endpoint} → {resp.status_code}, but the body is "
                f"{content_type or 'of no declared type'} and not application/json"
            ),
        )
    try:
        body = resp.json()
    except ValueError as exc:
        return Region(endpoint, error=f"GET {endpoint} → {resp.status_code}, unreadable JSON: {exc}")

    rejected = _envelope_problem(body, keys, many)
    if rejected is not None:
        return Region(endpoint, error=f"GET {endpoint} → {resp.status_code}, but {rejected}")
    return Region(endpoint, rows=body["data"], warnings=tuple(body["warnings"]))


def _api_error(resp) -> str:
    """The api's own `{"error": …}` sentence, when the body has one.

    Best-effort by construction: this runs on a non-200, which is exactly where
    the body may be Flask's HTML instead of the api's JSON.
    """
    try:
        body = resp.json()
    except ValueError:
        return ""
    message = body.get("error") if isinstance(body, dict) else None
    return f": {message}" if isinstance(message, str) and message else ""


def _envelope_problem(body, keys: tuple[str, ...], many: bool = True) -> str | None:
    """What is wrong with this envelope, or None — checked shallowly, once.

    Shallow on purpose: `data` and `warnings` present, `data` the shape the
    screen loops over (a list for the four list endpoints, an object or null for
    `/api/tasks/<id>`), `warnings` a list of strings, each row a mapping carrying
    the keys the screen reads. A deeper check would be a second schema for the
    api's rows, which is the kind of second answer this service exists without.

    `many` is checked rather than assumed because Jinja is forgiving in the wrong
    direction: iterating a mapping gives its keys, so a list endpoint answering
    an object would render a table of blank cells instead of saying the shape was
    wrong.
    """
    if not isinstance(body, dict):
        return f"the body is a {type(body).__name__} and not the envelope"
    if "data" not in body or "warnings" not in body:
        return "the body is missing data or warnings"
    if not isinstance(body["warnings"], list) or not all(
        isinstance(warning, str) for warning in body["warnings"]
    ):
        return "warnings is not a list of strings"
    rows = body["data"]
    if many and not isinstance(rows, list):
        return f"data is a {type(rows).__name__} and not a list of rows"
    if not many and rows is not None and not isinstance(rows, dict):
        return f"data is a {type(rows).__name__} and not one object"
    for row in rows if isinstance(rows, list) else [rows] if rows is not None else []:
        if not isinstance(row, dict):
            return f"a row is a {type(row).__name__} and not an object"
        missing = [key for key in keys if key not in row]
        if missing:
            return f"a row is missing {', '.join(missing)}"
    return None


# --- what the templates ask for -------------------------------------------


def _owner_container(owner: str | None, accounts: Region) -> tuple[str | None, str | None]:
    """The container whose events to show, or the sentence saying why there is none.

    The map from a task's `owner` to an event's `source_app` is `/api/accounts`,
    which is the only place the two vocabularies meet. When that call failed
    there is no map, so its own error is what the events half says — the task
    fields above it still render, which is the whole point of the region being
    the unit.
    """
    if owner is None:
        return None, "this task has no owner, so there is no account whose events to show"
    if not accounts.ok:
        return None, accounts.error
    for account in accounts.rows or []:
        if account.get("name") == owner:
            container = account.get("container")
            if container:
                return container, None
            return None, (
                f"the account {owner} is configured with no container, "
                "so there is no source_app whose events to show"
            )
    return None, (
        f"no account named {owner} is configured, so there is no container whose events to show"
    )


def _age(iso: str | None, now: dt.datetime | None = None) -> str:
    """How long ago, in the cell; the ISO string goes in the `title`.

    An age and never a judgement (`docs/decisions.md` ADR 8): `4m ago` answers
    the question an operator has, and nothing here compares anything against the
    expiry window the api keeps. A timestamp that will not parse renders verbatim
    with no relative part, because an unparseable heartbeat is the api's news to
    report and not this service's to hide — and the judgement about it arrives
    beside it, as the api's own `lock_expired`.
    """
    if not isinstance(iso, str) or not iso:
        return ""
    try:
        when = dt.datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    seconds = ((now or _now()) - when).total_seconds()
    if seconds < 0:
        return "in the future"
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def _payload(value) -> str:
    """An event's payload as one line, for a table cell.

    The collector stores whatever a hook posted, so this is the one field with no
    shape to rely on. `json.dumps` of it and nothing cleverer: a truncated line
    an operator can recognise, with `/api/events` one click away for the whole
    document.
    """
    if value in (None, {}, []):
        return ""
    try:
        text = json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= 120 else text[:119] + "…"


if __name__ == "__main__":
    app = create_app(
        os.environ.get("API_BASE_URL", DEFAULT_API_BASE_URL),
        # `.get` throughout: this service starts on a host whose
        # `docker/compose/.env` predates `API_TOKEN`, and an api with no token
        # configured rejects a bearer rather than accepting one.
        os.environ.get("API_TOKEN"),
        # The two credential variables keep their `DASHBOARD_` names in `.env`
        # and arrive here as `BOARD_*`, which the compose service maps. See
        # `docs/plans/board.md` "Configuration": renaming them means editing a
        # `.env` that exists on a running host to buy a spelling.
        os.environ["BOARD_USERNAME"],
        os.environ["BOARD_PASSWORD_HASH"],
        os.environ.get("BOARD_PROJECT") or None,
    )
    app.run(host="0.0.0.0", port=PORT)
