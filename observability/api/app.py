# observability/api/app.py
"""A read API over the state this harness already keeps on disk.

Phase 1 of `docs/plans/board.md`. Four readers existed and none was reachable
from outside the container holding it: `dispatcher/context_transfer.py` turns a
task file into a `TaskFile`, `dispatcher/state_machine.py` answers what an
account is doing, `observability/collector/db.py` lists events, and
`dispatcher/debt.py` parses the debt index. This is an HTTP surface over those,
and nothing more: every route is a `GET`, every mount is `:ro`, and there is no
second copy of any file format here — a parser this service needs and does not
have is one to make reachable in `dispatcher/`, not one to rewrite.

Two rules shape the whole module:

- **Every 200 is `{"data": …, "warnings": […]}`.** One file that will not parse
  costs a warning naming it and nothing else: the rest of the list still comes
  back, and no read in here answers 500. Nothing is dropped silently, which is
  the entire reason the envelope is not a bare array. Never-500 is bounded and
  the bound is written down — `docs/decisions.md` ADR 5: it is what the guards
  in this module enforce over the files they read, not a property of Flask, and
  the two raises Flask keeps for itself answer HTML.
- **An unknown query parameter is a 400.** Answering a misspelled filter with
  everything is the failure mode that costs the caller without telling them —
  the same reasoning `LocalBoardClient.list_issues` follows for an unknown
  filter key.

It writes nothing, anywhere: `db.init_db` is never called (it runs
`CREATE TABLE` and the events volume is read-only), the board client is only
ever read from, and there is no docker socket on this service —
`dispatcher/docker_exec.py` arrives as an import and is never called.
"""
from __future__ import annotations

import dataclasses
import os
import sqlite3
from pathlib import Path

import yaml
from flask import Flask, jsonify, request

from dispatcher import context_transfer, debt, project_docs, state_machine
from dispatcher.config import Config, load_config
from dispatcher.vibe_kanban_client import LocalBoardClient
from observability import auth
from observability.collector import db

#: Where the dispatcher's own config is mounted, matching the path the
#: dispatcher service is given (`--config /app/config.yaml`). A constant and
#: not an environment variable: the compose service names only the credentials
#: and the events database, and a path this service and the dispatcher must
#: agree on is not a knob worth a fourth.
CONFIG_PATH = "/app/config.yaml"

#: The events database, under `/events` rather than `/data`: `/data` is where
#: the task files and the project checkouts land in the dispatcher's and the
#: agents' layout, and nesting the volume under them to keep this default
#: byte-identical with the collector's would trade a legible mount list for an
#: environment variable.
DEFAULT_DB_PATH = "/events/events.db"

PORT = 8789

#: This service's realm, on both the Basic challenge and the bearer path's 401.
#: Every service keeps its own; see `observability/auth.py:requires_auth` on why
#: none of them is a default.
REALM = "ia-harness api"

#: `db.list_events`' own default, restated so a caller that sends no `limit`
#: gets the same answer whichever way it asked. The board never relies on it
#: (`docs/plans/board.md`: every events call sets `limit` explicitly).
DEFAULT_EVENT_LIMIT = 100

#: The most rows one `/api/events` answer carries, whatever `limit` asked for.
#: Five times the largest window the board opens (`EVENTS_LIMIT = 200` in
#: `observability/board/app.py`), so nothing in this harness is clamped today,
#: and Phase 3's tail is not constrained by it either: that tail is bounded by
#: `since` (`id > last`), which makes its `limit` a burst ceiling rather than a
#: page size. The clamp reports itself in `warnings`. `docs/decisions.md` ADR 11.
MAX_EVENT_LIMIT = 1000

#: The most strings one envelope's `warnings` may carry. The last slot is spent
#: tallying what was left out, so a caller is never told there were fewer than
#: there were. `docs/decisions.md` ADR 11.
MAX_WARNINGS = 100

#: The most bytes of a task's running record one `/api/tasks/<task_id>` answer
#: carries. `handoff()` only ever appends to `body`, so the field grows for the
#: life of a task id — 55 KB for the largest card in this repository on
#: 2026-10-01 — and it was the one thing this service answered with no bound at
#: all (`docs/debt/T-011-D1.md`). Over the cap the *newest* end survives, which
#: is the end `handoff()` writes to and the end a reader of the detail screen
#: came for, and the truncation names itself in `warnings` on `MAX_EVENT_LIMIT`'s
#: model. 128 KB is over twice today's largest card, so nothing in this harness
#: is truncated yet: the first card that is will be one of the long-lived task
#: ids, and the warning is how an operator finds out. `docs/decisions.md`
#: ADR 23, narrowing ADR 21.
MAX_BODY_BYTES = 128 * 1024

#: The most phase records one `/api/phases` answer carries. A hundred because
#: the cap cannot bite the call a screen makes — the console always sends
#: `?task_id=`, and one task has at most one file per role, so five — and
#: because the unfiltered call grows with every task this harness has ever run:
#: it would otherwise be the second thing this service answered with no bound
#: at all (`docs/debt/T-011-D1.md` was the first, closed by ADR 23). The clamp
#: reports itself in `warnings` on `MAX_EVENT_LIMIT`'s model. It bounds a count
#: and not bytes, which is enough while nothing polls the unfiltered form.
#: `docs/decisions.md` ADR 27.
MAX_PHASES = 100

#: What SQLite can hold in an INTEGER column, and therefore what `limit` and
#: `since` may be: they are bound into `LIMIT ?` and `WHERE id > ?`. Not the cap
#: on how much a caller may ask for — `MAX_EVENT_LIMIT` is that, and it clamps
#: rather than rejects — only the range outside which an integer is malformed,
#: because binding it raises rather than answering. See `_int_parameter`.
_SQLITE_INT_MAX = 2**63 - 1
_SQLITE_INT_MIN = -(2**63)

#: What a task file can fail with. `yaml.YAMLError` is in here and is not a
#: `ValueError`: a task file whose frontmatter does not scan raises it out of
#: `read_task_file`, and without it one hand-edited card would 500 the endpoint
#: that exists to report that kind of damage. `TypeError` is here for the third
#: way the same file fails: parsing succeeding does not mean the document has a
#: shape. `---\nTODO write this up\n---` scans into a `str` and a list of bullets
#: into a `list`, and `read_task_file`'s `fm["task_id"]` then subscripts it.
_UNREADABLE = (OSError, TypeError, ValueError, KeyError, yaml.YAMLError)

#: Said once per request that would have read cards, when the harness is
#: pointed at a remote board instead of a local one. See `docs/decisions.md`
#: ADR 3: this service does not dial Vibe Kanban.
_REMOTE_BOARD_WARNING = (
    "config.yaml configures vibe_kanban, and this API reads cards only from a local_board: "
    "every task's `card` is null. See docs/decisions.md ADR 3."
)


def create_app(
    config_path: str,
    db_path: str,
    username: str,
    password_hash: str,
    token: str | None = None,
) -> Flask:
    """The read API: config loaded once, credentials passed in.

    The config is loaded once, here, for the reason the dispatcher loads it at
    startup: a `config.yaml` that does not parse should stop the process, not
    turn every request into a different error. `db_path` is separate from it
    because the events database is not a config key — it is the collector's
    `COLLECTOR_DB_PATH`, and this service mounts that volume somewhere else.

    `token` is last and optional because a host whose `docker/compose/.env`
    predates it has none: the api must still start there with Basic auth working
    and the bearer path shut (`docs/decisions.md` ADR 7). It is what lets the
    board call this service without holding the human's plaintext password.
    """
    cfg = load_config(config_path)
    app = Flask(__name__)
    requires_auth = auth.requires_auth(username, password_hash, realm=REALM, token=token)

    @app.get("/api/tasks")
    @requires_auth
    def tasks():
        rejected = _reject_unknown_parameters(frozenset())
        if rejected is not None:
            return rejected
        cards, warnings = _read_cards(cfg)
        data = []
        for task_id in sorted(context_transfer.list_task_ids(cfg.hive_tasks_dir)):
            path = context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)
            try:
                task = context_transfer.read_task_file(path)
                # The guard wraps the *use* of the parsed values and not only
                # the parse. A frontmatter value of the wrong shape raises
                # where it is used: `_task` looks `kanban_issue_id` up as a
                # dict key, so a mapping or a sequence there is an unhashable
                # type. `app.json.dumps` is the serialisation `_envelope` does
                # anyway, run here so a value YAML builds and JSON cannot —
                # `owner: !!set {a: null}` — costs this one task a warning
                # instead of 500-ing the whole list from outside every
                # `except` in the module. It is the provider `jsonify` uses,
                # not `json.dumps`, so a YAML date still serialises. Narrowing
                # this `try` back to the parse reopens both.
                row = _task(task, cards, cfg.heartbeat_ttl_seconds)
                app.json.dumps(row)
            except _UNREADABLE as exc:
                warnings.append(f"{path}: unreadable task file: {exc}")
                continue
            data.append(row)
        return _envelope(data, warnings)

    @app.get("/api/tasks/<task_id>")
    @requires_auth
    def task(task_id: str):
        rejected = _reject_unknown_parameters(frozenset())
        if rejected is not None:
            return rejected
        if not _is_bare_task_id(task_id):
            return _error(f"no task {task_id!r}", 404)
        path = context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)
        if not os.path.exists(path):
            return _error(f"no task {task_id!r} in {cfg.hive_tasks_dir}", 404)
        cards, warnings = _read_cards(cfg)
        try:
            found = context_transfer.read_task_file(path)
            # Shaping and serialisation inside the guard, as on `/api/tasks`
            # and for the same reason; the comment there says why.
            row = _task(found, cards, cfg.heartbeat_ttl_seconds)
            # The running record, on this route and not on the list: `handoff()`
            # appends every phase's summary to `body`, so it only grows for the
            # life of a task id, and `/api/tasks` is the list a screen polls.
            # `docs/decisions.md` ADR 21. Inside the guard with the shaping and
            # the serialisation round because that is the rule here — the guard
            # wraps the *use* of a parsed value — and not because this field can
            # break it: `read_task_file` splits it out of the file's text, so it
            # is always a `str`. The next field added here may not be.
            #
            # Bounded here rather than served whole: ADR 23, closing
            # `docs/debt/T-011-D1.md`. The bound is also what bounds the one cost
            # that entry named and did not fix — the body is serialised twice,
            # once by the `app.json.dumps` round below and once by `jsonify` —
            # since what is serialised twice is now at most `MAX_BODY_BYTES`.
            row["body"], body_warnings = _bounded_body(path, found.body)
            app.json.dumps(row)
        except _UNREADABLE as exc:
            # A 404 here would say the task does not exist, which is a lie
            # about a file that does: the caller gets `null` and the reason.
            warnings.append(f"{path}: unreadable task file: {exc}")
            return _envelope(None, warnings)
        # `body_warnings` only on the path that serves the row: a truncation
        # note beside `data: null` would describe a record the caller did not
        # get.
        return _envelope(row, warnings + body_warnings)

    @app.get("/api/accounts")
    @requires_auth
    def accounts():
        rejected = _reject_unknown_parameters(frozenset())
        if rejected is not None:
            return rejected
        data, warnings = [], []
        for account in cfg.accounts:
            # One JSON document per account, named after it — the shape
            # `state_machine._state_path` writes. Named here so a warning can
            # point at the file and not at the account.
            path = os.path.join(cfg.state_dir, f"{account.name}.json")
            # Everything the config knows, before the `try`: an unreadable state
            # file nulls what the state file says and nothing the config says.
            # `is_primary` is `load_config`'s, read from the top-level
            # `primary_account` — `docs/decisions.md` ADR 17. The three
            # thresholds are the pool's and are repeated on every row because
            # the envelope has no slot beside `data` for a pool-wide fact:
            # ADR 20, narrowing ADR 18. They come off the `cfg` `create_app`
            # loaded, for the reason `_task` gives about the expiry window.
            row = {
                "name": account.name,
                "container": account.container,
                "is_primary": account.is_primary,
                "quota_threshold_pct": cfg.quota_threshold_pct,
                "reserve_pct": cfg.reserve_pct,
                "quota_cooldown_seconds": cfg.quota_cooldown_seconds,
            }
            try:
                row["state"] = state_machine.get_state(cfg.state_dir, account.name).value
                row["current_task"] = state_machine.get_current_task(cfg.state_dir, account.name)
                row["rate_limited_at"] = state_machine.get_rate_limited_at(
                    cfg.state_dir, account.name
                )
            except (OSError, TypeError, AttributeError, ValueError, KeyError) as exc:
                # The account is configured whatever its state file says, so it
                # stays in the list with the unreadable half nulled: dropping it
                # would hide an account from the one page that watches the pool.
                # `TypeError` and `AttributeError` for the same reason
                # `_UNREADABLE` carries `TypeError`: a state file holding a JSON
                # array parses, and `get_state`'s `data["state"]` and
                # `get_current_task`'s `data.get(...)` then fail on its shape.
                warnings.append(f"{path}: unreadable account state: {exc}")
                row.update(state=None, current_task=None, rate_limited_at=None)
            data.append(row)
        return _envelope(data, warnings)

    @app.get("/api/events")
    @requires_auth
    def events():
        rejected = _reject_unknown_parameters(frozenset({"limit", "source_app", "since"}))
        if rejected is not None:
            return rejected
        limit, rejected = _int_parameter("limit", DEFAULT_EVENT_LIMIT, positive=True)
        if rejected is not None:
            return rejected
        since, rejected = _int_parameter("since", None)
        if rejected is not None:
            return rejected
        warnings: list[str] = []
        if limit > MAX_EVENT_LIMIT:
            # Not a 400: the request is legal, it just asked for more than one
            # answer carries. Telling the caller it got fewer rows than it asked
            # for is the envelope's job — `docs/decisions.md` ADR 5 and ADR 11.
            warnings.append(
                f"limit={limit} is above this service's maximum of {MAX_EVENT_LIMIT}: "
                f"answering {MAX_EVENT_LIMIT} rows. Walk the rest with "
                f"?since=<last id> rather than one large limit."
            )
            limit = MAX_EVENT_LIMIT
        try:
            rows = db.list_events(
                db_path,
                limit=limit,
                source_app=request.args.get("source_app"),
                since=since,
                read_only=True,
            )
        except (sqlite3.Error, OSError, ValueError) as exc:
            # A harness that has never run has no events database, and this
            # service may not create one: the volume is `:ro` and `init_db`
            # would run `CREATE TABLE`. No events is not an error. The clamp
            # above stays in the envelope: a missing database does not make it
            # untrue that the caller asked for more than it could have had.
            return _envelope([], warnings + [_no_events_warning(db_path, exc)])
        return _envelope(rows, warnings)

    @app.get("/api/debt")
    @requires_auth
    def debt_index():
        rejected = _reject_unknown_parameters(frozenset({"project"}))
        if rejected is not None:
            return rejected
        slug, rejected = _project(cfg, request.args.get("project") or None)
        if rejected is not None:
            return rejected
        path = os.path.join(cfg.projects_root, slug, project_docs.DEBT_INDEX)
        try:
            text = Path(path).read_text()
        except (OSError, ValueError) as exc:
            # A project that has never filed debt has no index, which is a
            # project with no debt rather than a broken request. `ValueError`
            # for the other way a file can refuse to be text: a
            # `UnicodeDecodeError` is one, and an index that is not text is
            # still not a 500.
            return _envelope([], [f"{path}: no readable debt index: {exc}"])
        return _envelope(debt.index_rows(text), [])

    @app.get("/api/phases")
    @requires_auth
    def phases():
        rejected = _reject_unknown_parameters(frozenset({"task_id"}))
        if rejected is not None:
            return rejected
        task_id = request.args.get("task_id") or None
        if task_id is not None and not _is_bare_task_id(task_id):
            # A 400 and not a 404: the resource of this route is a phase record
            # and what is wrong here is the caller's parameter. The id never
            # reaches `handoff_path` — `_is_bare_task_id` is the lock the detail
            # route already uses, for the reason written there.
            return _error(f"task_id must name one task file, not {task_id!r}", 400)
        warnings: list[str] = []
        if task_id is None:
            task_ids = sorted(context_transfer.list_task_ids(cfg.hive_tasks_dir))
        elif os.path.exists(context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)):
            task_ids = [task_id]
        else:
            # Not a 404. The console reaches this route with an id it read off
            # `/api/tasks`, and a 404 would take a region to *Broken* on a
            # screen whose own read succeeded. `docs/decisions.md` ADR 27.
            task_ids = []
            warnings.append(
                f"no task {task_id!r} in {cfg.hive_tasks_dir}: no phase records for it"
            )
        rows: list[tuple[str, dict]] = []
        for tid in task_ids:
            # Guarded like the rows below it, and for the same rule: a directory
            # `os.path.isdir` admits can still refuse to be listed, and this
            # route lists one per task — so without this, one directory the api
            # cannot read costs every *other* task its rows. `.hive/` is written
            # by the dispatcher and read here over a `:ro` mount, which is the
            # mixed-ownership case `_write_atomic`'s chmod already exists for.
            try:
                roles = context_transfer.list_handoff_roles(cfg.hive_tasks_dir, tid)
            except OSError as exc:
                warnings.append(
                    f"{context_transfer.scratch_dir(cfg.hive_tasks_dir, tid)}: "
                    f"unreadable handoff directory: {exc}"
                )
                continue
            for role in roles:
                path = context_transfer.handoff_path(cfg.hive_tasks_dir, tid, role)
                try:
                    envelope = context_transfer.read_handoff_envelope(
                        cfg.hive_tasks_dir, tid, role
                    )
                    # The guard wraps the *use* of the parsed values and not
                    # only the parse, as on `/api/tasks`. The sort key is built
                    # here, inside it, and as a `str`: a `saved_at` holding a
                    # mapping is a use, and the sort below runs over the whole
                    # list where one bad file would otherwise cost every row.
                    # `app.json.dumps` is the serialisation `_envelope` does
                    # anyway, run here so a payload JSON cannot take costs this
                    # one file a warning instead of 500-ing the list. Narrowing
                    # this `try` to the reader reopens all three.
                    row = _phase(tid, role, envelope)
                    order = str(row["saved_at"] or "")
                    app.json.dumps(row)
                except _UNREADABLE as exc:
                    warnings.append(f"{path}: unreadable handoff: {exc}")
                    continue
                rows.append((order, row))
        # Newest first: the end stamp is the only time a record carries, and the
        # row a reader came for is the last phase that finished.
        rows.sort(key=lambda pair: pair[0], reverse=True)
        if len(rows) > MAX_PHASES:
            warnings.append(
                f"{len(rows)} phase records matched and this service answers at most "
                f"{MAX_PHASES}: the newest {MAX_PHASES} are served. Narrow it with "
                "?task_id=<id>."
            )
            rows = rows[:MAX_PHASES]
        return _envelope([row for _, row in rows], warnings)

    return app


# --- the envelope ---------------------------------------------------------


def _envelope(data, warnings: list[str]):
    """Every 200 this service answers.

    `warnings` names files and the caller's own parameters, never states —
    `docs/decisions.md` ADR 5, widened by ADR 11 to admit the `limit` clamp.
    Every 200 funnels through here, which is why the cap lives here and not in
    the five views that build the lists.
    """
    return jsonify({"data": data, "warnings": _capped(warnings)})


def _capped(warnings: list[str]) -> list[str]:
    """At most `MAX_WARNINGS` strings, the last of them a tally of the rest.

    A `local_board.dir` holding many documents that will not parse produces one
    warning each, and the envelope is materialised whole before `jsonify`
    serialises it. The cap is on what the caller receives, not on what was
    found, so nothing is silently dropped: the last slot says how many were.
    """
    if len(warnings) <= MAX_WARNINGS:
        return warnings
    kept = warnings[: MAX_WARNINGS - 1]
    return kept + [f"and {len(warnings) - len(kept)} more warnings, not listed"]


def _error(message: str, status: int):
    """Every non-200 this module returns but the 401, which `observability/auth.py` owns.

    Not every non-200 the caller can see: Flask answers a path no route matches
    and a write verb on a route itself, in HTML, and neither reaches this.
    `docs/decisions.md` ADR 5 says so and
    `tests/observability/test_api.py:test_flasks_own_404_and_405_are_html_not_this_envelope`
    pins it, so a Phase 2 client knows to guard the content type.
    """
    return jsonify({"error": message}), status


# --- query parameters -----------------------------------------------------


def _reject_unknown_parameters(allowed: frozenset[str]):
    """A 400 naming the parameter this endpoint does not have, or None."""
    unknown = sorted(set(request.args) - allowed)
    if not unknown:
        return None
    takes = ", ".join(sorted(allowed)) if allowed else "no query parameters"
    return _error(f"unknown query parameter {unknown[0]!r}; this endpoint takes {takes}", 400)


def _is_bare_task_id(task_id: str) -> bool:
    """Can this id only ever name a file inside the task directory?

    The id arrives from a URL, so it is text a stranger wrote, and one with a
    separator in it would name a file somewhere else — the same reach
    `LocalBoardClient._card_path` refuses for a `kanban_issue_id`. Werkzeug's
    default converter already declines to match a `/`, so this is the second
    lock and not the first: it is what keeps a later `<path:task_id>` from
    turning the route into a file reader.
    """
    name = f"{task_id}.md"
    return bool(task_id) and os.path.basename(name) == name and os.sep not in name


def _int_parameter(name: str, default: int | None, positive: bool = False):
    """One integer parameter, or a 400 that says what was wrong with it.

    An empty value reads as absent, so the `?limit=&source_app=&since=` the
    plan writes out is a legal request. Anything else that is not an integer is
    a 400 rather than a silent fallback to the default: a caller who sent
    `limit=lots` is owed the news.

    Validating one of these is two checks and not one: `int()` parsing the text
    does not mean SQLite can bind the result. `limit` and `since` go into
    `LIMIT ?` and `WHERE id > ?`, and a Python int outside the signed 64-bit
    range raises `OverflowError` there — not a `sqlite3.Error`, not an `OSError`
    and not a `ValueError`, so nothing in the events view catches it. Range is a
    fact about the request, so it answers 400 like every other bad parameter;
    widening the `except` instead would report a database that could not be read
    when the database was never the problem.
    """
    raw = request.args.get(name)
    if not raw:
        return default, None
    try:
        value = int(raw)
    except ValueError:
        return None, _error(f"{name} must be an integer, not {raw!r}", 400)
    if positive and value <= 0:
        return None, _error(f"{name} must be a positive integer, not {value}", 400)
    if not _SQLITE_INT_MIN <= value <= _SQLITE_INT_MAX:
        return None, _error(f"{name} must fit a 64-bit integer, not {value}", 400)
    return value, None


# --- what the endpoints read ----------------------------------------------


def _lock_expired(task: context_transfer.TaskFile, ttl_seconds: int) -> bool | None:
    """Is this task's lock stale — and `null` when there is nothing to judge.

    The one derived fact this service adds to a task row, and it is derived here
    because the TTL is config this service holds and no client does
    (`docs/plans/board.md` "Two kinds of stale", `docs/decisions.md` ADR 8: the
    board renders this field and never recomputes it). The computation itself is
    `dispatcher/context_transfer.py:is_lock_expired`, unchanged — the
    dispatcher's callers want the boolean it already returns.

    Which is why the three-valued answer lives on this side. `is_lock_expired`
    answers `False` for a task with no heartbeat, because for the dispatcher "no
    lock to release" and "the lock is fine" lead to the same branch; for a reader
    they are opposite news — nobody is holding this task, versus somebody is and
    is alive. A heartbeat that will not parse is the same kind of nothing: it
    cannot be judged, so it is `null` here, and the unparseable string still
    travels on `heartbeat` for a human to see. `docs/decisions.md` ADR 10 says
    why that is a null rather than a warning that drops the row.
    """
    if task.heartbeat is None:
        return None
    try:
        return context_transfer.is_lock_expired(task, ttl_seconds)
    except (TypeError, ValueError):
        return None


def _task(task: context_transfer.TaskFile, cards: dict[str, dict], ttl_seconds: int) -> dict:
    """One task as `TaskFile` has it, plus the card it points at.

    The fields are `TaskFile`'s and this invents none but `lock_expired`, which
    is argued at `_lock_expired`. `card` is `null` for a task with no
    `kanban_issue_id`, for a harness with no board, and for an id the board does
    not hold — none of the three is an error.

    `ttl_seconds` is an argument and not a module global: it is
    `cfg.heartbeat_ttl_seconds`, which `create_app` has already loaded, and a
    second `load_config` here is a second answer to the same question.

    One `TaskFile` field is deliberately missing here and is added by
    `/api/tasks/<task_id>` after it calls this: `body`. It is the running record
    every phase appends to, so the two task routes answer different key sets on
    purpose — `docs/decisions.md` ADR 21, which says why, and why moving it in
    here would be a regression rather than a tidy-up.
    """
    return {
        "task_id": task.task_id,
        "status": task.status,
        "owner": task.owner,
        "depends_on": task.depends_on,
        "heartbeat": task.heartbeat,
        "description": task.description,
        "kanban_issue_id": task.kanban_issue_id,
        "resolved_debt": task.resolved_debt,
        "card": cards.get(task.kanban_issue_id) if task.kanban_issue_id else None,
        "lock_expired": _lock_expired(task, ttl_seconds),
    }


def _phase(task_id: str, role: str, envelope: dict) -> dict:
    """One phase as the handoff file has it: six keys, and the payload whole.

    The fields are the envelope's and this invents only `id`, which *is* the
    file — one per role per task, so `<task_id>:<role>` is the record's own
    identity and not a surrogate. The route owns that shape, so a later task
    that ever serves more than the approved round adds to the id rather than
    reshaping every client.

    `handoff` travels whole and unpromoted. Nothing is lifted onto the row and
    nothing is projected: the payload's key set is the role's own schema in
    `dispatcher/handoff.py:schema_for`, and a second copy of it maintained by
    hand on this side is what this module's docstring refuses. A key the
    console does not know is not an error here.

    `role` is the envelope's, with the filename as the fallback: one file per
    role is the record, so the name is the stronger claim about which phase
    this is. The full argument for which of `Phase`'s twenty-two fields are
    served, and why the other sixteen are not, is `docs/decisions.md` ADR 27 —
    this docstring cites it rather than re-making it, and a later task that
    finds a field missing reaches that table before reaching for the api.
    """
    return {
        "id": f"{task_id}:{role}",
        "task_id": task_id,
        "role": envelope.get("role") or role,
        "round": envelope.get("round"),
        # When the phase *ended*. No start is recorded anywhere in `.hive/`, so
        # there is no `started_at` to serve and no duration to derive: serving
        # this under another name would be the only lie on this route. ADR 27.
        "saved_at": envelope.get("saved_at"),
        "handoff": envelope.get("handoff"),
    }


def _bounded_body(path: str, body: str) -> tuple[str, list[str]]:
    """The newest `MAX_BODY_BYTES` of a task's running record, and what was cut.

    Bytes and not characters, because what is being bounded is the size of a
    response. A byte slice can land inside a multibyte character, so the tail is
    decoded with `errors="ignore"`: a dropped partial sequence costs one
    character, where `errors="replace"` would put a U+FFFD at the top of the
    record for a human to recognise as an artefact. The first surviving line goes
    with it for the same reason — half a line of markdown at the top of the
    screen reads as damage to the file rather than as a cap — unless dropping it
    would leave nothing, which is what a record with one very long line is.

    Which end survives is the decision, not the arithmetic: `handoff()` appends,
    so the newest phase is at the end and that is what the detail screen is open
    for. The start of the record is reachable in the task file, and the task's
    own `description` — the operator's ask, a separate field — is served whole
    either way. `docs/decisions.md` ADR 23.
    """
    raw = body.encode("utf-8")
    if len(raw) <= MAX_BODY_BYTES:
        return body, []
    kept = raw[-MAX_BODY_BYTES:].decode("utf-8", errors="ignore")
    _partial, newline, rest = kept.partition("\n")
    if newline and rest:
        kept = rest
    return kept, [
        f"{path}: the task body is {len(raw)} bytes and this service answers at "
        f"most {MAX_BODY_BYTES}: the newest {len(kept.encode('utf-8'))} bytes "
        "are served and the start of the running record is not. The whole "
        "record is in the task file."
    ]


def _read_cards(cfg: Config) -> tuple[dict[str, dict], list[str]]:
    """Every card the board holds, by issue id, and what it could not read.

    The board is built here from the config rather than taken from
    `dispatcher/cli.py`, because this service is the one caller that has to
    know which implementation it got: `unreadable()` is a `LocalBoardClient`
    method by design (`docs/decisions.md` ADR 3 and ADR 4), and it is what
    turns a board that is quietly short — `docs/debt/T-008-D2.md` — into a
    warning a reader can see.

    The `except (OSError, TypeError)` is on this side of the seam on purpose.
    `LocalBoardClient._scan` catches `FileNotFoundError` only, because for the
    dispatcher a missing directory is a board with no issues while a
    `local_board.dir` that is a regular file must raise — `create_issue` would
    otherwise mint cards nobody can list. Here the contract is the other one:
    no read answers 500, and every path it could not read is named.

    `TypeError` is in there because a card's fields are text some phase wrote
    and `_read_path` validates one of them: it checks the document is a mapping
    with a string `issue_id` and leaves the rest whatever was on disk. `_scan`
    then sorts on `created_at`, so two cards whose `created_at` are of
    different types compare a `str` against an `int`. Fixing that in `_scan` or
    `_read_path` would be a `dispatcher/` contract change this service has no
    reason to make; never-500 belongs on this side.
    """
    if cfg.local_board is None:
        return {}, [_REMOTE_BOARD_WARNING] if cfg.vibe_kanban is not None else []
    board = LocalBoardClient(cfg.local_board)
    try:
        warnings = [
            f"{path}: unreadable board card; it is on no task's `card`"
            for path in board.unreadable()
        ]
        cards = {issue.issue_id: dataclasses.asdict(issue) for issue in board.list_issues()}
    except (OSError, TypeError) as exc:
        return {}, [f"{cfg.local_board.dir}: unreadable board: {exc}"]
    return cards, warnings


def _no_events_warning(db_path: str, exc: Exception) -> str:
    """Why `/api/events` came back empty, in terms of the file and not of sqlite.

    Two situations answer the same way and deserve different sentences, because
    sqlite's own message (`unable to open database file`) says nothing about
    either. A harness that has never run has no database there, and that is not
    an error. A database that *is* there and will not open read-only is almost
    always the second one, measured rather than guessed
    (`docs/implementations/T-009.md`, "The events volume is `:ro` and the
    collector writes WAL"): a read-only open of a WAL database creates the
    `-shm` sidecar beside it when no writer is holding one, and a `:ro` mount
    cannot host that write. An operator reading `[]` is owed the difference,
    since one of the two is a cold start and the other is a mount to argue with.
    """
    if not os.path.exists(db_path):
        return f"{db_path}: no events database yet: {exc}"
    return (
        f"{db_path}: events database could not be opened read-only: {exc}. "
        "Usually the `-shm` sidecar a WAL database needs, which a read-only "
        "mount cannot create; see docs/implementations/T-009.md."
    )


def _project_slugs(projects_root: str) -> list[str]:
    """The project checkouts under `projects_root`, as `bootstrap-project` lays them out."""
    try:
        names = sorted(os.listdir(projects_root))
    except OSError:
        return []
    return [
        name
        for name in names
        if not name.startswith(".") and os.path.isdir(os.path.join(projects_root, name))
    ]


def _project(cfg: Config, slug: str | None):
    """The checkout to read, or the response saying why there is not one.

    `project` is optional where `projects_root` holds exactly one checkout and
    required where it holds several — a harness with one project should not
    have to name it, and a harness with four must, because picking for them
    would answer a different project's debt to the one they meant.
    """
    slugs = _project_slugs(cfg.projects_root)
    if slug is not None:
        if slug not in slugs:
            return None, _error(
                f"no project {slug!r} under {cfg.projects_root}; it holds: "
                + (", ".join(slugs) or "no checkouts"),
                404,
            )
        return slug, None
    if len(slugs) == 1:
        return slugs[0], None
    if not slugs:
        return None, _error(f"no project checkout under {cfg.projects_root}", 404)
    return None, _error(
        "project is required: " + f"{cfg.projects_root} holds " + ", ".join(slugs), 400
    )


if __name__ == "__main__":
    app = create_app(
        CONFIG_PATH,
        os.environ.get("COLLECTOR_DB_PATH", DEFAULT_DB_PATH),
        os.environ["DASHBOARD_USERNAME"],
        os.environ["DASHBOARD_PASSWORD_HASH"],
        # `.get` and not `[...]`: an operator upgrading a running host has a
        # `docker/compose/.env` written before `API_TOKEN` existed, and this
        # service must still start there with the bearer path shut. The two
        # `DASHBOARD_` names are deliberate and stay — renaming them means
        # editing a `.env` that exists on a running host to buy a spelling.
        os.environ.get("API_TOKEN"),
    )
    app.run(host="0.0.0.0", port=PORT)
