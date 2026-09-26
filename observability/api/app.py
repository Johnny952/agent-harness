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
  the entire reason the envelope is not a bare array.
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
#: not an environment variable: this phase adds no configuration, and the
#: compose service names only the three variables the dashboard already uses.
CONFIG_PATH = "/app/config.yaml"

#: The events database, under `/events` rather than `/data`: `/data` is where
#: the task files and the project checkouts land in the dispatcher's and the
#: agents' layout, and nesting the volume under them to keep this default
#: byte-identical with the dashboard's would trade a legible mount list for an
#: environment variable.
DEFAULT_DB_PATH = "/events/events.db"

PORT = 8789

#: This service's Basic-Auth realm. The dashboard keeps its own; see
#: `observability/auth.py:requires_auth` on why neither is a default.
REALM = "ia-harness api"

#: `db.list_events`' own default, restated so a caller that sends no `limit`
#: gets the same answer whichever of the two services it asked.
DEFAULT_EVENT_LIMIT = 100

#: What a task file can fail with. `yaml.YAMLError` is in here and is not a
#: `ValueError`: a task file whose frontmatter does not scan raises it out of
#: `read_task_file`, and without it one hand-edited card would 500 the endpoint
#: that exists to report that kind of damage.
_UNREADABLE = (OSError, ValueError, KeyError, yaml.YAMLError)

#: Said once per request that would have read cards, when the harness is
#: pointed at a remote board instead of a local one. See `docs/decisions.md`
#: ADR 3: this service does not dial Vibe Kanban.
_REMOTE_BOARD_WARNING = (
    "config.yaml configures vibe_kanban, and this API reads cards only from a local_board: "
    "every task's `card` is null. See docs/decisions.md ADR 3."
)


def create_app(config_path: str, db_path: str, username: str, password_hash: str) -> Flask:
    """The read API, shaped like the dashboard's `create_app`.

    The config is loaded once, here, for the reason the dispatcher loads it at
    startup: a `config.yaml` that does not parse should stop the process, not
    turn every request into a different error. `db_path` is separate from it
    because the events database is not a config key — it is the collector's
    `COLLECTOR_DB_PATH`, and this service mounts that volume somewhere else.
    """
    cfg = load_config(config_path)
    app = Flask(__name__)
    requires_auth = auth.requires_auth(username, password_hash, realm=REALM)

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
            except _UNREADABLE as exc:
                warnings.append(f"{path}: unreadable task file: {exc}")
                continue
            data.append(_task(task, cards))
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
        except _UNREADABLE as exc:
            # A 404 here would say the task does not exist, which is a lie
            # about a file that does: the caller gets `null` and the reason.
            warnings.append(f"{path}: unreadable task file: {exc}")
            return _envelope(None, warnings)
        return _envelope(_task(found, cards), warnings)

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
            row = {"name": account.name, "container": account.container}
            try:
                row["state"] = state_machine.get_state(cfg.state_dir, account.name).value
                row["current_task"] = state_machine.get_current_task(cfg.state_dir, account.name)
                row["rate_limited_at"] = state_machine.get_rate_limited_at(
                    cfg.state_dir, account.name
                )
            except (OSError, ValueError, KeyError) as exc:
                # The account is configured whatever its state file says, so it
                # stays in the list with the unreadable half nulled: dropping it
                # would hide an account from the one page that watches the pool.
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
            # would run `CREATE TABLE`. No events is not an error.
            return _envelope([], [f"{db_path}: no readable events database: {exc}"])
        return _envelope(rows, [])

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

    return app


# --- the envelope ---------------------------------------------------------


def _envelope(data, warnings: list[str]):
    """Every 200 this service answers. `warnings` names files, never states."""
    return jsonify({"data": data, "warnings": warnings})


def _error(message: str, status: int):
    """Every non-200 but the 401, which `observability/auth.py` owns."""
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
    return value, None


# --- what the endpoints read ----------------------------------------------


def _task(task: context_transfer.TaskFile, cards: dict[str, dict]) -> dict:
    """One task as `TaskFile` has it, plus the card it points at.

    The fields are `TaskFile`'s and this invents none. `card` is `null` for a
    task with no `kanban_issue_id`, for a harness with no board, and for an id
    the board does not hold — none of the three is an error.
    """
    return {
        "task_id": task.task_id,
        "status": task.status,
        "owner": task.owner,
        "heartbeat": task.heartbeat,
        "description": task.description,
        "kanban_issue_id": task.kanban_issue_id,
        "resolved_debt": task.resolved_debt,
        "card": cards.get(task.kanban_issue_id) if task.kanban_issue_id else None,
    }


def _read_cards(cfg: Config) -> tuple[dict[str, dict], list[str]]:
    """Every card the board holds, by issue id, and what it could not read.

    The board is built here from the config rather than taken from
    `dispatcher/cli.py`, because this service is the one caller that has to
    know which implementation it got: `unreadable()` is a `LocalBoardClient`
    method by design (`docs/decisions.md` ADR 3 and ADR 4), and it is what
    turns a board that is quietly short — `docs/debt/T-008-D2.md` — into a
    warning a reader can see.
    """
    if cfg.local_board is None:
        return {}, [_REMOTE_BOARD_WARNING] if cfg.vibe_kanban is not None else []
    board = LocalBoardClient(cfg.local_board)
    warnings = [
        f"{path}: unreadable board card; it is on no task's `card`"
        for path in board.unreadable()
    ]
    return {
        issue.issue_id: dataclasses.asdict(issue) for issue in board.list_issues()
    }, warnings


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
    )
    app.run(host="0.0.0.0", port=PORT)
