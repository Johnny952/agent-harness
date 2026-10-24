# observability/api/app.py
"""A read API over the state this harness already keeps on disk.

Phase 1 of `docs/plans/board.md`. Five readers existed and none was reachable
from outside the container holding it: `dispatcher/context_transfer.py` turns a
task file into a `TaskFile`, `dispatcher/state_machine.py` answers what an
account is doing, `observability/collector/db.py` lists events,
`dispatcher/debt.py` parses the debt index, and `dispatcher/learnings.py` parses
the hive's trap entries; `context_transfer.read_usage` reads a task's per-call
usage log (ADR 54), and `observability/api/plans.py` reads a plan's frontmatter
(T-072; it lives here and not in `dispatcher/` because no dispatcher code reads
a plan, and its test imports it), and `dispatcher/command_run.py` answers which
block of command requests a task is held on and what verdict it has (T-078),
and `dispatcher/projects.py` reads the project records (ADR 209). This is an HTTP surface over those, and nothing more:
every route is a `GET`, every mount is `:ro`, and there is no second copy of any
file format here — a parser this service needs and does not have is one to make
reachable in `dispatcher/`, not one to rewrite.

Two rules shape the whole module:

- **Every 200 is `{"data": …, "warnings": […]}`.** One file that will not parse
  costs a warning naming it and nothing else: the rest of the list still comes
  back, and no read in here answers 500. Nothing is dropped silently, which is
  the entire reason the envelope is not a bare array. Never-500 is bounded and
  the bound is written down — `docs/decisions.md` ADR 5: it is what the guards
  in this module enforce over the files they read, not a property of Flask, and
  the two raises Flask keeps for itself answer HTML.
- **An unknown query parameter is a 400.** Answering a misspelled filter with
  everything is the failure mode that costs the caller without telling them.

It writes nothing, anywhere: `db.init_db` is never called (it runs
`CREATE TABLE` and the events volume is read-only), the learnings read calls neither `ensure_dirs` nor `reconcile`,
the actions read calls neither `actions.ensure_dirs` nor `actions.read_all` (which makes the queue's directories),
and there is no docker socket on this service — `dispatcher/docker_exec.py`
arrives as an import, for one frozenset and the name of a task's branch, and
none of its commands is called. The one process it starts is `git`, read-only,
for `/api/tasks/<task_id>/diff` over the `:ro` checkouts (ADR 219).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import shlex
import sqlite3
import subprocess
from pathlib import Path
from typing import NamedTuple

import yaml
from flask import Flask, jsonify, request

from dispatcher import (
    actions,
    command_run,
    context_transfer,
    debt,
    docker_exec,
    epic_runs,
    learnings,
    notifications,
    project_docs,
    state_machine,
)
# Aliased like `tasks`: `create_app` names its `/api/projects` view `projects`.
from dispatcher import projects as project_store
# Aliased like `tasks`: `create_app` names its `/api/threads` view `threads`.
from dispatcher import threads as thread_store
# Aliased: `create_app` names its `/api/tasks` view `tasks`.
from dispatcher import tasks as task_store
# Aliased: `/api/actions` reads the worker's heartbeat; for two constants,
# never called, as `docker_exec` is.
from dispatcher import worker as action_worker
from dispatcher.config import Config, load_config, refresh_primary
from observability import auth
from observability.api import plans as plan_frontmatter
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

HOST = "0.0.0.0"
PORT = 8789

#: Request threads (`docs/decisions.md` ADR 417). The service holds no
#: long-lived stream, so eight leaves seven free for polls while one diff
#: request waits out its `git` timeouts.
THREADS = 8

#: This service's realm, on both the Basic challenge and the bearer path's 401.
#: Every service keeps its own; see `observability/auth.py:requires_auth` on why
#: none of them is a default.
REALM = "ia-harness api"

#: `db.list_events`' own default, restated so a caller that sends no `limit`
#: gets the same answer whichever way it asked. The console leans on it: its tail
#: sends `since` and no `limit` and takes this number as the burst ceiling a
#: cursor-bounded poll wants (`front/src/lib/api/client.ts:listEvents`), so a
#: change here changes what that tail can skip.
DEFAULT_EVENT_LIMIT = 100

#: The most rows one `/api/events` answer carries, whatever `limit` asked for.
#: Five times the largest window any client here has opened — the retired Flask
#: board asked for 200 and the console's tail asks for the default above — so
#: nothing in this harness is clamped today,
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

#: The most learning entries one `/api/learnings` answer carries. Five hundred
#: because the hive inbox is drained when a task's branch merges and holds
#: seventeen entries today, so nothing in this harness is clamped: the cap
#: exists so this is not the next thing this service answers with no bound at
#: all, on `MAX_PHASES`' model and `MAX_EVENT_LIMIT`'s before it. The clamp
#: reports itself in `warnings`. It bounds a count and not bytes, which is
#: enough while `rule` and `when` are one line each — a row that ever carries
#: the entry body is a different bound. `docs/decisions.md` ADR 41.
MAX_LEARNINGS = 500

#: The most usage lines one `/api/tasks/<task_id>/usage` answer carries. A
#: thousand because a phase writes one line per `claude` call — one, plus a
#: retry per gate or review failure and a shrink retry per oversized handoff —
#: so a task that reaches this has been re-dispatched hundreds of times and the
#: clamp is the news. The newest lines are the ones kept, because the attempt a
#: reader came for is the last one; the first group served may then lack its
#: `phase` line, and the warning says so. It bounds a count and not bytes, on
#: `MAX_PHASES`' model, which is enough while a line is seventeen scalars plus
#: `raw_keys`, `models_used` and `permission_denials`. The last is the one list
#: whose length the CLI decides, and the dispatcher caps it before it is
#: written: at most 50 entries, the rest only counted, and every string in a
#: `tool_input` cut to 500 characters. A tool's input is a handful of named
#: parameters, so a line stays tens of KB at the very worst and a full answer
#: stays a bounded read rather than one that grows with what a phase tried.
#: `docs/decisions.md` ADR 54, ADR 68, ADR 116.
MAX_USAGE_CALLS = 1000

#: The most comments one `/api/tasks/<task_id>/comments` answer carries. Five
#: hundred because a comment is a note a person or a phase writes by hand, and a
#: thread past that is the news rather than a thread to read whole. The newest
#: are kept, on `MAX_USAGE_CALLS`' model: the end of a thread is what a reader
#: opens the task page for, and the clamp names itself in `warnings`.
#: `docs/decisions.md` ADR 135.
MAX_COMMENTS = 500

#: The most bytes of a task's `comments.jsonl` one answer reads, from its end.
#: The count above bounds what is served and not what is read, and one comment
#: may be `tasks.MAX_COMMENT_CHARS` (20 000) characters, so the file is bounded
#: too: 1 MiB is fifty of the longest comments `add_comment` writes and
#: thousands of ordinary ones. Over it the newest end is read, starting at a
#: whole line, and the cut names itself in `warnings`. ADR 135.
MAX_COMMENTS_BYTES = 1024 * 1024

#: The most notifications one `/api/notifications` answer carries, the newest,
#: on `MAX_COMMENTS`' model: an inbox is read from its top, and the clamp
#: names itself in `warnings`. ADR 221.
MAX_NOTIFICATIONS = 500

#: The most bytes of `notifications.jsonl` one answer reads, from its end. A
#: line is a few hundred bytes (`notifications.MAX_TITLE_CHARS` bounds the
#: only free text), so 1 MiB holds thousands, past `MAX_NOTIFICATIONS`. ADR 221.
MAX_NOTIFICATIONS_BYTES = 1024 * 1024

#: The most bytes of a task's unified diff one `/api/tasks/<task_id>/diff`
#: answer carries. A diff is read on the review screen line by line, so a
#: branch past 1 MiB is not reviewed there whole anyway; the cut is taken at a
#: whole line, says so in `truncated` and in `warnings`, and the rest stays on
#: the branch for `git diff` on the host. ADR 219.
MAX_DIFF_BYTES = 1024 * 1024

#: How long one `git` call of the diff route may run, in seconds. Every call
#: it makes is a read over one repository's refs and objects; one that takes
#: longer is a wedged mount, and the route answers why instead of hanging.
GIT_TIMEOUT_SECONDS = 20

#: The keys a comment line may carry across this api: `tasks.add_comment`'s
#: three, and the diff anchor's three; and `tasks.record_verdict`'s `verdict`,
#: checked apart in `_comment`. Anything else on a line is dropped.
_COMMENT_KEYS = task_store.COMMENT_KEYS
_ANCHOR_KEYS = ("file", "line", "commit")

#: The most plans one `/api/roadmap` answer carries. Five hundred because a plan
#: is one file a person writes under `docs/plans/` and this harness has 21 of
#: them on 2026-10-17, so nothing is clamped: the cap exists so the route is not
#: the next thing this service answers with no bound at all, on `MAX_PHASES`'
#: model. Every plan past it would also be read from disk, so the clamp is
#: taken on the sorted file names before any is opened, and reports itself in
#: `warnings`. It bounds a count and not bytes, which is enough while a plan row
#: is its title, four frontmatter values and the names of other plans.
MAX_ROADMAP_PLANS = 500

#: The most tasks one `/api/roadmap` answer carries, under plans and under
#: `no_plan` together. Two thousand because the route reads every task file in
#: the hive — tasks are per harness, not per project, today — and the hive holds
#: about seventy on 2026-10-17, so nothing is clamped, while a harness that runs
#: a few tasks a day for years still fits. The clamp is taken on the sorted task
#: ids before any file is opened, so a hive past it costs a bounded read rather
#: than one that grows with every task this harness has run, and it reports
#: itself in `warnings`. A task row is four scalars, and an epic row only nests rows already
#: counted, so a count is bound enough.
MAX_ROADMAP_TASKS = 2000

#: The most task files one `/api/command-requests` answer reads, for the reason
#: `MAX_ROADMAP_TASKS` gives and with its number: the route reads every task in
#: the hive, and the clamp is taken on the sorted ids before any file is
#: opened. A task carries at most three requests a block
#: (`dispatcher/handoff.py`) and two filing roles, so a count of tasks bounds
#: the rows.
MAX_COMMAND_REQUEST_TASKS = 2000

#: How many finished actions one `/api/actions` answer carries when the
#: caller sends no `limit`. `done/` grows by one file per action and nothing
#: prunes it yet (ADR 142, *Consequences*), so the route serves the newest end:
#: fifty is a screen of history, and every file past it is never opened.
DEFAULT_ACTIONS_DONE = 50

#: The most finished actions one `/api/actions` answer carries, whatever
#: `limit` asked for; the clamp reports itself in `warnings` on
#: `MAX_EVENT_LIMIT`'s model. It bounds the files read as well as the rows
#: served, since only the newest names are opened.
MAX_ACTIONS_DONE = 500

#: How many log lines one `/api/threads/<id>/events` answer carries when the
#: caller sends no `limit`. A turn streams a line per message and per tool
#: result, so a turn is tens of lines and this is a screen of turns (ADR 180).
DEFAULT_THREAD_EVENT_LIMIT = 500

#: The most log lines one `/api/threads/<id>/events` answer carries; the clamp
#: reports itself in `warnings` on `MAX_EVENT_LIMIT`'s model.
MAX_THREAD_EVENT_LIMIT = 2000

#: How many hook events one `/api/tasks/<id>/events` answer carries when the
#: caller sends no `limit`. A phase makes a pair of events per tool call, so a
#: phase is tens to hundreds and this is a cycle's first page (ADR 197).
DEFAULT_TASK_EVENT_LIMIT = 500

#: The most hook events one `/api/tasks/<id>/events` answer carries, on
#: `MAX_EVENT_LIMIT`'s model: the clamp reports itself in `warnings`.
MAX_TASK_EVENT_LIMIT = 1000

#: Which events belong to a task: those whose hook payload's `cwd` sits under
#: the task's worktrees, `{projects_root}/{slug}/worktrees/{task_id}/<name>`
#: (`docker_exec.task_worktrees_dir`). The writer's `work` and each reviewer's
#: own directory alike, at any depth below them. The id is bounded by slashes
#: on both sides so `T-1` never matches `T-10`, and `json_valid` keeps a row
#: whose payload is not JSON from failing the whole query.
_TASK_EVENTS_SQL = (
    "SELECT id, source_app, event_type, payload, created_at FROM events "
    "WHERE id > ? AND instr("
    "COALESCE(CASE WHEN json_valid(payload) THEN json_extract(payload, '$.cwd') END, '')"
    " || '/', '/worktrees/' || ? || '/') > 0 "
    "ORDER BY id ASC LIMIT ?"
)

#: How old the worker's last beat may be before `/api/actions` calls it stale.
#: The worker beats once a pass, every `worker.POLL_SECONDS` (2s) plus the
#: pass's own work, so fifteen missed beats is a worker that is gone or wedged,
#: not one that is slow. A pass that waits on an account's `/usage` probe can
#: run past this and read stale until it ends; `beat_at` is served beside the
#: flag, so a reader can tell a long pass from a dead worker.
WORKER_STALE_SECONDS = 15 * action_worker.POLL_SECONDS

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
    console's server half (`front/src/lib/api/forward.ts`) call this service
    without holding the human's plaintext password.
    """
    cfg = load_config(config_path)
    app = Flask(__name__)
    requires_auth = auth.requires_auth(username, password_hash, realm=REALM, token=token)
    # What a phase dispatched right now would be allowed to do, as one string.
    # The same three inputs `dispatcher/dispatcher.py` passes, so this service's
    # answer to *stale* is the answer that dispatch would get — and computed
    # here, off the one `Config` this function loaded, never by a second
    # `load_config` inside a view
    # (`docs/learnings/a-new-field-on-an-api-row-has-two-questions.md`).
    # `docs/decisions.md` ADR 41 part 8.
    harness = learnings.harness_fingerprint(
        cfg.permission_mode, cfg.allowed_tools, docker_exec.WRITER_ROLES
    )

    @app.get("/api/tasks")
    @requires_auth
    def tasks():
        rejected = _reject_unknown_parameters(frozenset())
        if rejected is not None:
            return rejected
        warnings: list[str] = []
        data = []
        for task_id in sorted(context_transfer.list_task_ids(cfg.hive_tasks_dir)):
            path = context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)
            try:
                task = context_transfer.read_task_file(path)
                # The guard wraps the *use* of the parsed values and not only
                # the parse. A frontmatter value of the wrong shape raises
                # where it is used, not where it is read.
                # `app.json.dumps` is the serialisation `_envelope` does
                # anyway, run here so a value YAML builds and JSON cannot —
                # `owner: !!set {a: null}` — costs this one task a warning
                # instead of 500-ing the whole list from outside every
                # `except` in the module. It is the provider `jsonify` uses,
                # not `json.dumps`, so a YAML date still serialises. Narrowing
                # this `try` back to the parse reopens that.
                # The description is read inside the guard too: a task file
                # `dispatch migrate` has not moved yet raises here, a
                # `ValueError` whose text names the command, and costs this
                # task its row rather than serving it with no ask.
                description = task_store.read_description(cfg.hive_tasks_dir, task_id)
                row = _task(task, description, cfg.heartbeat_ttl_seconds)
                app.json.dumps(row)
                # A bad backlog field is named, and the row is still served:
                # the file reads, and one hand edit's `urgent` is not a reason
                # to hide the task (ADR 133).
                problems = task_store.field_problems(task)
            except _UNREADABLE as exc:
                warnings.append(f"{path}: unreadable task file: {exc}")
                continue
            warnings.extend(f"{path}: {problem}" for problem in problems)
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
        warnings: list[str] = []
        try:
            found = context_transfer.read_task_file(path)
            # Shaping and serialisation inside the guard, as on `/api/tasks`
            # and for the same reason; the comment there says why.
            description = task_store.read_description(cfg.hive_tasks_dir, task_id)
            row = _task(found, description, cfg.heartbeat_ttl_seconds)
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
            # As on `/api/tasks`: named, and the row still served (ADR 133).
            problems = task_store.field_problems(found)
        except _UNREADABLE as exc:
            # A 404 here would say the task does not exist, which is a lie
            # about a file that does: the caller gets `null` and the reason.
            warnings.append(f"{path}: unreadable task file: {exc}")
            return _envelope(None, warnings)
        # `body_warnings` only on the path that serves the row: a truncation
        # note beside `data: null` would describe a record the caller did not
        # get.
        field_warnings = [f"{path}: {problem}" for problem in problems]
        return _envelope(row, warnings + field_warnings + body_warnings)

    @app.get("/api/ready")
    @requires_auth
    def ready():
        """The Ready column, in the order work should be taken: task ids.

        `dispatcher/tasks.py:ready_order` over every task file that reads, the
        one function the board service's `ready` list and the queue use, so
        the order is never computed twice (ADR 133). Its own loop and not
        `read_all_tasks`, which drops a file that will not read in silence:
        here that file is a warning, as on `/api/tasks`, and so is a bad
        backlog field, which `ready_order` already sorts as the default.
        """
        rejected = _reject_unknown_parameters(frozenset())
        if rejected is not None:
            return rejected
        warnings: list[str] = []
        read: dict[str, context_transfer.TaskFile] = {}
        for task_id in sorted(context_transfer.list_task_ids(cfg.hive_tasks_dir)):
            path = context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)
            try:
                task = context_transfer.read_task_file(path)
                problems = task_store.field_problems(task)
            except _UNREADABLE as exc:
                warnings.append(f"{path}: unreadable task file: {exc}")
                continue
            warnings.extend(f"{path}: {problem}" for problem in problems)
            read[task_id] = task
        try:
            order = task_store.ready_order(read)
        except _UNREADABLE as exc:
            # `ready_order` is written to take any field shape; this is the
            # guard's rule kept, so a shape it missed is a warning, not a 500.
            return _envelope([], warnings + [f"{cfg.hive_tasks_dir}: no ready order: {exc}"])
        return _envelope(order, warnings)

    @app.get("/api/accounts")
    @requires_auth
    def accounts():
        rejected = _reject_unknown_parameters(frozenset())
        if rejected is not None:
            return rejected
        data, warnings = [], []
        # The primary is the one key read live (ADR 176, ADR 178): the console
        # moves it while this process runs on the config it loaded at boot.
        refresh_primary(cfg)
        for account in cfg.accounts:
            # One JSON document per account, named after it — the shape
            # `state_machine._state_path` writes. Named here so a warning can
            # point at the file and not at the account.
            path = os.path.join(cfg.state_dir, f"{account.name}.json")
            # Everything the config knows, before the `try`: an unreadable state
            # file nulls what the state file says and nothing the config says.
            # `is_primary` is `load_config`'s, read from the top-level
            # `primary_account` — `docs/decisions.md` ADR 17 — and re-marked
            # by `refresh_primary` above. The three
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
                row.update(
                    state=None, current_task=None, rate_limited_at=None, last_probe=None,
                )
            else:
                # The last `/usage` probe and the ceilings it was held to, as
                # the dispatcher's gate recorded them — ADR 49, narrowing ADR
                # 18's "probe numbers stay unserved". Its own `try`, after the
                # state half, so a record in the wrong shape nulls only itself:
                # the account's state is still true when its probe is not. A
                # file the first `try` could not read never gets here, so one
                # corrupt file is one warning and not two.
                try:
                    row["last_probe"] = state_machine.get_last_probe(
                        cfg.state_dir, account.name
                    )
                except (OSError, TypeError, AttributeError, ValueError, KeyError) as exc:
                    warnings.append(f"{path}: unreadable last probe: {exc}")
                    row["last_probe"] = None
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

    @app.get("/api/tasks/<task_id>/usage")
    @requires_auth
    def task_usage(task_id: str):
        # A path segment and not `/api/usage?task_id=`: the id is required and
        # names one task, which is what `/api/tasks/<task_id>` already spells
        # as a segment, while a query parameter on this api is a filter on a
        # list — and there is no unfiltered form of this read to bound.
        # `docs/decisions.md` ADR 54.
        rejected = _reject_unknown_parameters(frozenset())
        if rejected is not None:
            return rejected
        # A 404 for the same two cases and in the same words as the detail
        # route, and not ADR 27's 200: this read belongs to the screen that
        # opens `/api/tasks/<task_id>` first, which already answers 404 for an
        # id with no task file, so a 200 here would contradict its own parent.
        if not _is_bare_task_id(task_id):
            return _error(f"no task {task_id!r}", 404)
        if not os.path.exists(context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)):
            return _error(f"no task {task_id!r} in {cfg.hive_tasks_dir}", 404)
        path = context_transfer.usage_log_path(cfg.hive_tasks_dir, task_id)
        try:
            log = context_transfer.read_usage(cfg.hive_tasks_dir, task_id)
        except ValueError:
            # `.` and `..` pass `_is_bare_task_id` — `..md` is a bare filename —
            # and `read_usage` refuses them because as a *directory* name they
            # leave the hive. Neither can have a task file, so the check above
            # answers first today; this keeps the refusal a 404 if it ever does
            # not.
            return _error(f"no task {task_id!r}", 404)
        except PermissionError as exc:
            # `data: null` and not `[]`: an empty list says the task made no
            # call, which is a different claim from "the api may not look".
            # Named apart from the other `OSError`s because it has one known
            # cause here: a 0700 scratch or usage directory, closed to this
            # service's uid. ADR 54 records it; ADR 55 makes the dispatcher
            # create both 0755, so what is left is a directory made before
            # that, or by something other than the dispatcher.
            return _envelope(
                None,
                [
                    f"{path}: permission denied reading the usage log: {exc} "
                    "(the task's scratch or usage directory may be 0700 — made at "
                    "the dispatcher's umask before docs/decisions.md ADR 55, or "
                    "not by the dispatcher; ADR 54)"
                ],
            )
        except OSError as exc:
            return _envelope(None, [f"{path}: unreadable usage log: {exc}"])
        warnings: list[str] = []
        if log.skipped:
            # The lines the reader could not parse, counted and named: ADR 5's
            # "nothing is dropped silently" for a log whose damage is per line,
            # so the reader's count is the warning and no `unreadable()`
            # sibling re-reads the file.
            warnings.append(
                f"{path}: {log.skipped} unreadable usage line(s) skipped; they are in no group"
            )
        lines = log.lines
        if len(lines) > MAX_USAGE_CALLS:
            warnings.append(
                f"{len(lines)} usage lines and this service answers at most "
                f"{MAX_USAGE_CALLS}: the newest {MAX_USAGE_CALLS} are served, and "
                "the first group may be missing its phase line"
            )
            lines = lines[-MAX_USAGE_CALLS:]
        try:
            # The guard wraps the use of the parsed values, as on `/api/phases`:
            # grouping compares fields a hand-edited line may hold as anything
            # JSON can, and the serialisation round is what `_envelope` does
            # anyway, run here so it costs a warning and not a 500.
            groups = _usage_groups(task_id, lines)
            app.json.dumps(groups)
        except _UNREADABLE as exc:
            warnings.append(f"{path}: unreadable usage log: {exc}")
            return _envelope(None, warnings)
        return _envelope(groups, warnings)

    @app.get("/api/tasks/<task_id>/events")
    @requires_auth
    def task_events(task_id: str):
        """The hook events of a task's phases, oldest first (ADR 197).

        `docs/plans/main-thread.md` step 7: the dock's execution view. A phase
        runs `claude -p --output-format json` and leaves no stream log, so the
        hook events the collector stores are the only record of what it did
        between its start and its handoff. `/api/events` cannot serve them: it
        answers the newest rows and filters by nothing a task owns. This one
        matches each payload's `cwd` against the task's worktrees and walks up
        from `since=<last id>`, the cursor `/api/events` already keeps. A row
        is that route's row. No database yet is an empty list and a warning.
        """
        rejected = _reject_unknown_parameters(frozenset({"limit", "since"}))
        if rejected is not None:
            return rejected
        limit, rejected = _int_parameter("limit", DEFAULT_TASK_EVENT_LIMIT, positive=True)
        if rejected is not None:
            return rejected
        since, rejected = _int_parameter("since", 0)
        if rejected is not None:
            return rejected
        # A 404 in the usage route's words: this read belongs to a task the
        # caller already holds, and an id that is not a bare filename never
        # reaches the query.
        if not _is_bare_task_id(task_id):
            return _error(f"no task {task_id!r}", 404)
        if not os.path.exists(context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)):
            return _error(f"no task {task_id!r} in {cfg.hive_tasks_dir}", 404)
        warnings: list[str] = []
        if limit > MAX_TASK_EVENT_LIMIT:
            warnings.append(
                f"limit={limit} is above this service's maximum of "
                f"{MAX_TASK_EVENT_LIMIT}: answering {MAX_TASK_EVENT_LIMIT} "
                f"rows. Walk the rest with ?since=<last id>."
            )
            limit = MAX_TASK_EVENT_LIMIT
        try:
            conn = db.connect_read_only(db_path)
            try:
                found = conn.execute(_TASK_EVENTS_SQL, (since, task_id, limit)).fetchall()
            finally:
                conn.close()
        except (sqlite3.Error, OSError, ValueError) as exc:
            return _envelope([], warnings + [_no_events_warning(db_path, exc)])
        # Only a payload that parsed can have matched, so `json.loads` holds.
        rows = [
            {
                "id": event_id,
                "source_app": source_app,
                "event_type": event_type,
                "payload": json.loads(payload),
                "created_at": created_at,
            }
            for event_id, source_app, event_type, payload, created_at in found
        ]
        return _envelope(rows, warnings)

    @app.get("/api/tasks/<task_id>/comments")
    @requires_auth
    def task_comments(task_id: str):
        """A task's comment thread, oldest first: `{"task_id", "comments"}`.

        The lines `dispatcher/tasks.py:add_comment` appends to the task's
        `comments.jsonl`, each line judged by `tasks.parse_comment_line`, the
        rule the board service's count applies too. A line that is not
        a comment is a warning naming the file and its line, and the rest of
        the thread is still served. `docs/decisions.md` ADR 135, ADR 172.
        """
        rejected = _reject_unknown_parameters(frozenset())
        if rejected is not None:
            return rejected
        # A 404 in the same two cases and words as the detail route, for the
        # reason `/api/tasks/<task_id>/usage` gives: this read belongs to the
        # screen that opens the task first.
        if not _is_bare_task_id(task_id):
            return _error(f"no task {task_id!r}", 404)
        if not os.path.exists(context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)):
            return _error(f"no task {task_id!r} in {cfg.hive_tasks_dir}", 404)
        try:
            path = task_store.comments_path(cfg.hive_tasks_dir, task_id)
        except ValueError:
            # `.` and `..` pass `_is_bare_task_id`; as a directory name they
            # leave the board root, and `comments_path` refuses them.
            return _error(f"no task {task_id!r}", 404)
        try:
            first_line, raw, cut_bytes = _comments_tail(path)
        except (FileNotFoundError, NotADirectoryError):
            # No file is no comment yet: `add_comment` creates it on the first.
            return _envelope({"task_id": task_id, "comments": []}, [])
        except OSError as exc:
            # `null` and not `[]`: an empty thread is a different claim from
            # "the api may not read it".
            return _envelope(None, [f"{path}: unreadable comments file: {exc}"])
        warnings: list[str] = []
        if cut_bytes:
            warnings.append(
                f"{path}: the comments file is over {MAX_COMMENTS_BYTES} bytes: its "
                f"first {cut_bytes} bytes are not read, and the oldest comments "
                "in them are not served"
            )
        comments = []
        for number, line in enumerate(raw.split(b"\n"), start=first_line):
            if not line.strip():
                continue
            comment, problem = _comment(line)
            if problem is not None:
                warnings.append(f"{path}: line {number}: {problem}")
            if comment is not None:
                comments.append(comment)
        if len(comments) > MAX_COMMENTS:
            warnings.append(
                f"{len(comments)} comments and this service answers at most "
                f"{MAX_COMMENTS}: the newest {MAX_COMMENTS} are served"
            )
            comments = comments[-MAX_COMMENTS:]
        return _envelope({"task_id": task_id, "comments": comments}, warnings)

    @app.get("/api/notifications")
    @requires_auth
    def notifications_inbox():
        """The notification record, newest first (ADR 221).

        The lines `dispatcher/notifications.py:append` writes to
        `<hive root>/notifications.jsonl`, each judged by
        `notifications.parse_line`. A line that is not a notification is a
        warning naming the file and its line, and the rest is still served. No
        file is no notification yet: `[]`. Read state is not here: the inbox
        keeps it (`docs/plans/notifications.md` step 2).
        """
        rejected = _reject_unknown_parameters(frozenset())
        if rejected is not None:
            return rejected
        path = notifications.notifications_path(cfg.hive_tasks_dir)
        try:
            first_line, raw, cut_bytes = _comments_tail(path, MAX_NOTIFICATIONS_BYTES)
        except (FileNotFoundError, NotADirectoryError):
            return _envelope([], [])
        except OSError as exc:
            return _envelope(None, [f"{path}: unreadable notifications file: {exc}"])
        warnings: list[str] = []
        if cut_bytes:
            warnings.append(
                f"{path}: the notifications file is over {MAX_NOTIFICATIONS_BYTES} bytes: "
                f"its first {cut_bytes} bytes are not read, and the oldest notifications "
                "in them are not served"
            )
        found = []
        for number, line in enumerate(raw.split(b"\n"), start=first_line):
            if not line.strip():
                continue
            record, problem = notifications.parse_line(line)
            if problem is not None:
                warnings.append(f"{path}: line {number}: {problem}")
            if record is not None:
                found.append(record)
        if len(found) > MAX_NOTIFICATIONS:
            warnings.append(
                f"{len(found)} notifications and this service answers at most "
                f"{MAX_NOTIFICATIONS}: the newest {MAX_NOTIFICATIONS} are served"
            )
            found = found[-MAX_NOTIFICATIONS:]
        found.reverse()
        return _envelope(found, warnings)

    @app.get("/api/tasks/<task_id>/diff")
    @requires_auth
    def task_diff(task_id: str):
        """A task branch's changes, as the raw unified diff `git diff` prints.

        Step 2 of `docs/plans/review.md` (ADR 219): the diff of
        `agent/task/<task_id>` against its merge base with the branch the
        project's checkout is on — the base `dispatch merge-task` merges into,
        and the three-dot diff `docker_exec.branch_changed_paths` reads, so
        the person reviews what a merge would bring. Served with the tip
        `commit` it was taken at, which a line comment's anchor names.
        `?project=` picks the checkout; without it the task file's `project`
        does, and a task naming none takes the one checkout `_project` allows.
        Nothing is parsed here: the browser splits the text.

        No branch, a detached checkout, or a `git` this image cannot run is
        `data: null` and the reason, never a 500. An empty `diff` is a branch
        with no change of its own, which is a different claim.
        """
        rejected = _reject_unknown_parameters(frozenset({"project"}))
        if rejected is not None:
            return rejected
        # A 404 in the same two cases and words as the detail route, as on
        # `/api/tasks/<task_id>/comments`.
        if not _is_bare_task_id(task_id):
            return _error(f"no task {task_id!r}", 404)
        path = context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)
        if not os.path.exists(path):
            return _error(f"no task {task_id!r} in {cfg.hive_tasks_dir}", 404)
        slug = request.args.get("project") or None
        if slug is None:
            try:
                slug = context_transfer.read_task_file(path).project
            except _UNREADABLE as exc:
                return _envelope(None, [f"{path}: unreadable task file: {exc}"])
            if slug is not None and not isinstance(slug, str):
                return _envelope(
                    None, [f"{path}: project is {slug!r}, not a slug: no checkout to read"]
                )
        slug, error = _project(cfg, slug)
        if error is not None:
            return error
        data, warnings = _task_diff(os.path.join(cfg.projects_root, slug), slug, task_id)
        return _envelope(data, warnings)

    @app.get("/api/learnings")
    @requires_auth
    def learnings_index():
        rejected = _reject_unknown_parameters(frozenset({"project", "task"}))
        if rejected is not None:
            return rejected
        slug, rejected = _project(cfg, request.args.get("project") or None)
        if rejected is not None:
            return rejected
        task_id = request.args.get("task") or None
        if task_id is not None and not _is_bare_task_id(task_id):
            return _error(f"task must name one task file, not {task_id!r}", 400)
        root = learnings.root_dir(cfg.hive_tasks_dir)
        if not os.path.isdir(root):
            # `ensure_dirs` makes this tree on every `run-task`, so its absence
            # means no run has happened against this hive — which is different
            # news from an empty inbox, and an operator reading an empty screen
            # is owed the difference. `_no_events_warning`'s model.
            return _envelope(
                [],
                [
                    f"{root}: no learnings tree yet: the dispatcher makes it on every "
                    "run-task, so no task has run against this hive"
                ],
            )
        entries: list[learnings.Entry] = []
        warnings: list[str] = []
        for directory in (
            learnings.inbox_dir(cfg.hive_tasks_dir),
            learnings.harness_dir(cfg.hive_tasks_dir),
        ):
            # Per directory, each in its own guard, because this route lists two
            # and one that will not list must not cost the other its rows.
            # `read_dir` guards with `os.path.isdir` and then calls `os.listdir`,
            # which still raises for a directory it cannot read; the guard is at
            # this caller and `read_dir` keeps raising, because its four
            # dispatcher callers need the raise.
            # `docs/learnings/a-per-item-listing-in-a-never-500-read-needs-its-own-guard.md`
            # is the same shape one level up.
            try:
                entries += learnings.read_dir(root, directory)
                # The damage the parser drops silently: named, in no row,
                # and the rest of the list still comes back. ADR 41 part 6.
                warnings += [
                    f"{path}: unreadable learning entry; it is in no row"
                    for path in learnings.unreadable(directory)
                ]
            except OSError as exc:
                warnings.append(f"{directory}: unreadable learnings directory: {exc}")
        # What a phase of this project is shown, which is this screen's whole
        # subject: everything a human promoted into `harness/`, plus everything
        # this project discovered itself. ADR 41 part 2.
        served = learnings.applicable(entries, slug)
        context = learnings.NO_CONTEXT
        if task_id is not None:
            context = _learnings_task_context(cfg, task_id, slug, warnings)
        # Not an index into the order below: whether a row reaches a running
        # phase is `handed(select(eligible(…), context))`, the dispatcher's own
        # selectors, so a refuted row, a row the task's paths and tags filter
        # out and a row past the cap are each `false` for their own reason and
        # none is a fact a client could derive. With no `task` the context is
        # `NO_CONTEXT` and `select` returns `eligible` unchanged: the unfiltered
        # table. ADR 321.
        reaching = {
            entry.ref
            for entry in learnings.handed(
                learnings.select(learnings.eligible(served, slug), context), harness
            )
        }
        # No per-row `try` and no `app.json.dumps` round, unlike `/api/tasks` and
        # `/api/phases`. That is a property of the row and not a relaxation of
        # the rule: every value here comes through `Entry`'s own accessors, which
        # coerce with `str()`, or is a `bool` or `MAX_ROWS`, so no file content
        # reaches the envelope un-coerced and there is nothing a YAML document
        # could put in a row that JSON cannot serialise. A later field read
        # straight off `meta` reopens this and takes the guard and the
        # serialisation round with it. ADR 41 part 6.
        rows = [
            {
                "ref": entry.ref,
                "task": entry.task,
                "carried_by": entry.carried_by,
                "scope": entry.scope,
                "status": entry.status,
                "when": entry.when,
                "rule": entry.rule,
                "stale": entry.stale(harness),
                "in_phase_table": entry.ref in reaching,
                # Repeated per row because the envelope has no slot beside
                # `data` for a collection-wide fact — ADR 20's shape, the way
                # an account row carries the pool's three thresholds.
                "phase_table_cap": learnings.MAX_ROWS,
            }
            # The order a phase sees, so the first row is the first row handed
            # and the last is the first to fall off the end of the cap.
            for entry in learnings.ordered(served, harness)
        ]
        if len(rows) > MAX_LEARNINGS:
            warnings.append(
                f"{len(rows)} learning entries matched and this service answers at most "
                f"{MAX_LEARNINGS}: the first {MAX_LEARNINGS} in the phase table's own "
                "order are served. The rest are files under the hive's learnings tree."
            )
            rows = rows[:MAX_LEARNINGS]
        return _envelope(rows, warnings)

    @app.get("/api/roadmap")
    @requires_auth
    def roadmap():
        """The project's plans in three bands, with the tasks under them.

        The api piece of `docs/plans/roadmap-view.md`'s cheap form: plans from
        their frontmatter and tasks from the hive, with epics (ADR 447) and no
        ADR relations yet. An epic, a task file with `split_into`, is a row in
        the same lists a task is (a plan's `tasks`, `no_plan.now`, a month's
        `tasks`), with `kind: "epic"`, `place` (`board` or `backlog`),
        `merged_tasks` and its sub-tasks nested in `tasks` in `split_into`
        order; they are no longer flat rows. A sub-task with no `plan` takes
        its epic's. A backlog epic (`proposed` or `deferred`) is in the future
        band under its plan, as a second row for that plan file when the
        plan is `building` or past, or in `no_plan.now` with none. A `plan` is
        a plan file name or `docs/plans/` plus it. Odd epics (no sub-tasks,
        an `epic` naming no file, an unreadable epic file) are warnings. The plans are the checkout's own `docs/plans/`, chosen by
        `?project=` the way `/api/debt` chooses it, because the image carries no
        copy of this repository's docs. The tasks are per harness, not per
        project: no task file carries a project key today, so every task in the
        hive is read, and one is left out only when its file names a `project`
        that is not this one.
        """
        rejected = _reject_unknown_parameters(frozenset({"project"}))
        if rejected is not None:
            return rejected
        slug, rejected = _project(cfg, request.args.get("project") or None)
        if rejected is not None:
            return rejected
        warnings: list[str] = []
        plans_dir = os.path.join(cfg.projects_root, slug, project_docs.PLANS_DIR)
        plan_rows = _roadmap_plans(plans_dir, warnings)
        entries: list[_RoadmapEntry] = []
        # Ids that have a file but no row, and why: an epic's `split_into`
        # or a sub-task's `epic` naming one is told apart from a missing file.
        absent: dict[str, str] = {}
        task_ids = sorted(context_transfer.list_task_ids(cfg.hive_tasks_dir))
        if len(task_ids) > MAX_ROADMAP_TASKS:
            for left_out in task_ids[MAX_ROADMAP_TASKS:]:
                absent[left_out] = "past the task cap"
            warnings.append(
                f"{len(task_ids)} task files are in the hive and this service answers at "
                f"most {MAX_ROADMAP_TASKS}: the first {MAX_ROADMAP_TASKS} by task id are "
                f"read, and {len(task_ids) - MAX_ROADMAP_TASKS} are left out."
            )
            task_ids = task_ids[:MAX_ROADMAP_TASKS]
        for task_id in task_ids:
            path = context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)
            try:
                # The guard wraps the use of the values and the serialisation
                # round, for the reasons `/api/tasks` gives.
                task = context_transfer.read_task_file(path)
                # `plan` and `project` are not `TaskFile` fields, so the
                # frontmatter is read again raw for them.
                front = _task_frontmatter(path)
                project = front.get("project")
                if project is not None and project != slug:
                    absent[task_id] = "it belongs to another project"
                    continue
                row, merged, month = _roadmap_task(task)
                title = front.get("title")
                entry = _RoadmapEntry(
                    row=row,
                    plan=front.get("plan"),
                    merged=merged,
                    month=month,
                    epic=task.epic,
                    is_epic="split_into" in front or task.status == "epic",
                    split_into=front.get("split_into"),
                    title=title if isinstance(title, str) else None,
                    key=task_store.order_key(
                        task_id, task_store.bucket_of(task), task_store.rank_of(task)
                    ),
                )
                app.json.dumps(row)
            except _UNREADABLE as exc:
                warnings.append(f"{path}: unreadable task file: {exc}")
                absent[task_id] = "its file did not read"
                continue
            entries.append(entry)
        placed = _roadmap_epics(entries, absent, warnings)
        return _envelope(_roadmap(slug, plan_rows, placed, warnings), warnings)

    @app.get("/api/command-requests")
    @requires_auth
    def command_requests():
        """The command requests filed across the hive, each with its state.

        The data half of step 6 of `docs/plans/command-requests.md`
        (`docs/decisions.md` ADR 122). Hive-wide, as `/api/roadmap` reads its
        tasks: `?project=` narrows to one checkout and leaves out only a task
        whose file names another, and `?state=` narrows `requests` to one of
        `COMMAND_REQUEST_STATES`. Which block a task is held on and whether it
        waits on a resume are `dispatcher/command_run.py`'s answers, not a
        second copy here; nothing it calls touches docker, a subprocess or a
        file it would write. No output is served: a run's log is named by its
        path under the task's scratch dir, and its text stays there.
        """
        rejected = _reject_unknown_parameters(frozenset({"project", "state"}))
        if rejected is not None:
            return rejected
        slug = request.args.get("project") or None
        if slug is not None:
            slug, rejected = _project(cfg, slug)
            if rejected is not None:
                return rejected
        state = request.args.get("state") or None
        if state is not None and state not in COMMAND_REQUEST_STATES:
            return _error(
                f"state {state!r} is not one of {', '.join(COMMAND_REQUEST_STATES)}", 400
            )
        slugs = _project_slugs(cfg.projects_root)
        default_project = slug or (slugs[0] if len(slugs) == 1 else None)
        warnings: list[str] = []
        rows: list[dict] = []
        refused: list[dict] = []
        task_ids = sorted(context_transfer.list_task_ids(cfg.hive_tasks_dir))
        if len(task_ids) > MAX_COMMAND_REQUEST_TASKS:
            warnings.append(
                f"{len(task_ids)} task files are in the hive and this service answers at "
                f"most {MAX_COMMAND_REQUEST_TASKS}: the first {MAX_COMMAND_REQUEST_TASKS} by "
                f"task id are read, and {len(task_ids) - MAX_COMMAND_REQUEST_TASKS} are left out."
            )
            task_ids = task_ids[:MAX_COMMAND_REQUEST_TASKS]
        for task_id in task_ids:
            path = context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)
            try:
                task = context_transfer.read_task_file(path)
                project = _task_frontmatter(path).get("project")
                if slug is not None and project is not None and project != slug:
                    continue
                project = project if isinstance(project, str) else default_project
                task_rows, task_refused = _command_requests(
                    cfg, task, project, warnings
                )
                app.json.dumps([task_rows, task_refused])
            except _UNREADABLE as exc:
                warnings.append(f"{path}: unreadable task file: {exc}")
                continue
            rows.extend(task_rows)
            refused.extend(task_refused)
        if state is not None:
            rows = [row for row in rows if row["state"] == state]
        rows.sort(key=_command_request_order)
        refused.sort(key=lambda row: (str(row["filed_at"] or ""), row["task_id"]))
        return _envelope({"requests": rows, "refused": refused}, warnings)

    @app.get("/api/actions")
    @requires_auth
    def action_queue():
        """The action queue under `<state_dir>/actions/`, and whether a worker runs.

        Step 4 of ADR 142's split. `pending` in the order the worker takes
        them (name order), each with the `waiting` the worker set; `claimed`,
        the running ones, in name order; `done`, the newest `limit` by name,
        newest first; and `worker`, the heartbeat `dispatcher/worker.py`
        writes with a `stale` flag; and `epics`, each started epic's run
        with its spend and `limit_reached` (ADR 218). The directory a record sits in is its
        state (ADR 142, 3), so a record is served whole under the directory
        it was found in. Read through `/state:ro`: no directory is made here,
        and a queue that was never created is an empty one with no worker.
        """
        rejected = _reject_unknown_parameters(frozenset({"limit"}))
        if rejected is not None:
            return rejected
        limit, rejected = _int_parameter("limit", DEFAULT_ACTIONS_DONE, positive=True)
        if rejected is not None:
            return rejected
        warnings: list[str] = []
        if limit > MAX_ACTIONS_DONE:
            warnings.append(
                f"limit={limit} is above this service's maximum of {MAX_ACTIONS_DONE}: "
                f"answering the newest {MAX_ACTIONS_DONE} finished actions."
            )
            limit = MAX_ACTIONS_DONE
        root = actions.actions_dir(cfg.state_dir)
        pending, _ = _action_records(root / actions.PENDING, None, warnings)
        claimed, _ = _action_records(root / actions.CLAIMED, None, warnings)
        done, done_total = _action_records(root / actions.DONE, limit, warnings)
        data = {
            "pending": pending,
            "claimed": claimed,
            "done": done,
            "done_total": done_total,
            "worker": _worker_heartbeat(root / action_worker.HEARTBEAT, warnings),
            "epics": _epic_runs(warnings),
        }
        return _envelope(data, warnings)

    @app.get("/api/actions/<action_id>")
    @requires_auth
    def action_record(action_id: str):
        """One action's record, wherever the queue holds it (ADR 216).

        What *New project* reads its setup actions back by: `/api/actions`
        serves only the newest finished ones, so an action that many others
        finished after falls out of that answer while this one still finds
        it. The walk is pending, claimed, done, the order the worker moves a
        record in, so a record moved between two looks is found in the later
        one. An id that is not an action id, or names no record, is a 404,
        and is never made into a path. A file that does not read is `data:
        null` and the reason, as `/api/projects/<slug>` answers a broken
        record. Read through `/state:ro`: no directory is made here.
        """
        rejected = _reject_unknown_parameters(frozenset())
        if rejected is not None:
            return rejected
        if not actions.ID_RE.fullmatch(action_id):
            return _error(f"no action {action_id!r}", 404)
        root = actions.actions_dir(cfg.state_dir)
        for state in (actions.PENDING, actions.CLAIMED, actions.DONE):
            path = root / state / f"{action_id}.json"
            try:
                return _envelope(actions._load(path), [])
            except FileNotFoundError:
                continue
            except (OSError, ValueError) as exc:
                return _envelope(None, [f"{path}: unreadable action: {exc}"])
        return _error(f"no action {action_id!r} in {root}", 404)

    def _epic_runs(warnings: list[str]) -> list[dict]:
        """Each started epic's run, with its measured spend and limit (ADR 218)."""
        runs, skipped = actions.read_epic_runs(cfg.state_dir)
        warnings.extend(skipped)
        out = []
        for run in runs:
            try:
                out.append(epic_runs.view(cfg.hive_tasks_dir, run))
            except _UNREADABLE as exc:
                warnings.append(f"epic {run.get('epic')}: unreadable run: {exc}")
        return out

    @app.get("/api/threads")
    @requires_auth
    def threads():
        """Every thread record under `<hive root>/threads/`, as written (ADR 170, 177).

        `docs/plans/main-thread.md` step 3. The order is
        `thread_store.read_threads`'s: the main thread first, then by
        `created`, oldest first. Archived threads are served with their
        `status` and filtered by the console, so this route takes no query
        parameters. A file that does not parse, or is not a JSON object, is a
        warning and the rest are still served. Read through `/data/.hive:ro`:
        no directory is made, and a hive with no `threads/` is an empty list.
        """
        rejected = _reject_unknown_parameters(frozenset())
        if rejected is not None:
            return rejected
        try:
            records, warnings = thread_store.read_threads(cfg.hive_tasks_dir)
        except OSError as exc:
            # `read_threads` turns an absent directory into an empty list; one
            # it cannot list (permissions, a file in its place) is this warning.
            root = thread_store.threads_root(cfg.hive_tasks_dir)
            return _envelope([], [f"{root}: unreadable threads directory: {exc}"])
        return _envelope(records, warnings)

    @app.get("/api/threads/<thread_id>/events")
    @requires_auth
    def thread_events(thread_id: str):
        """The thread's conversation log, one row per line (ADR 173, 4; ADR 180).

        `docs/plans/main-thread.md` step 3. A row is `{"n", "event"}`: `n` is
        the line's number in `conversations/<thread>.jsonl`, from 1, and
        `event` the line as written, a harness line or the CLI's stream event
        verbatim. `since=<n>` serves the lines after it, the cursor
        `/api/events` keeps with its ids. A final line with no newline yet is
        being written and is left for the next read; a whole line that does
        not parse is a row with a null `event` and a warning, so the cursor
        still moves past it. No log yet is an empty list.
        """
        rejected = _reject_unknown_parameters(frozenset({"limit", "since"}))
        if rejected is not None:
            return rejected
        limit, rejected = _int_parameter("limit", DEFAULT_THREAD_EVENT_LIMIT, positive=True)
        if rejected is not None:
            return rejected
        since, rejected = _int_parameter("since", 0)
        if rejected is not None:
            return rejected
        try:
            path = thread_store.conversation_path(cfg.hive_tasks_dir, thread_id)
        except thread_store.NoThreadError:
            # A 400 and not a 404, as `/api/phases` answers a malformed
            # `task_id`: the id never reaches a path. A well-formed id with no
            # record is not checked: its log is just absent (ADR 27).
            return _error(f"no thread id {thread_id!r}: not one the harness makes", 400)
        warnings: list[str] = []
        if limit > MAX_THREAD_EVENT_LIMIT:
            warnings.append(
                f"limit={limit} is above this service's maximum of "
                f"{MAX_THREAD_EVENT_LIMIT}: answering {MAX_THREAD_EVENT_LIMIT} "
                f"rows. Walk the rest with ?since=<last n>."
            )
            limit = MAX_THREAD_EVENT_LIMIT
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except FileNotFoundError:
            return _envelope([], warnings)
        except OSError as exc:
            return _envelope([], warnings + [f"{path}: unreadable conversation log: {exc}"])
        lines = text.split("\n")
        # The piece after the last newline: empty, or a line still being written.
        lines.pop()
        rows: list[dict] = []
        for n, line in enumerate(lines[max(since, 0):], start=max(since, 0) + 1):
            if len(rows) == limit:
                break
            try:
                event = json.loads(line)
            except ValueError:
                event = None
            if not isinstance(event, dict):
                warnings.append(f"{path}: line {n} is not a JSON object")
                event = None
            rows.append({"n": n, "event": event})
        return _envelope(rows, warnings)

    @app.get("/api/projects")
    @requires_auth
    def projects():
        """Every project record under `<hive root>/projects/`, by slug (ADR 199, ADR 209).

        `docs/plans/projects.md` step 4. A row is the record's own fields,
        `ProjectRecord.to_dict()`, and nothing derived. Archived records are
        served with their `status` and filtered by the console, as `/api/threads`
        serves archived threads, so this route takes no query parameters. A
        record that does not read is a warning naming its file and the rest are
        still served. The remote crosses through `redact_remote` (ADR 205):
        `parse` already refuses a record whose remote carries a credential, so
        this is the second lock, and the warning's text has been through the
        same redaction in `project_store.read`. Read through `/data/.hive:ro`: no
        directory is made, and a hive with no `projects/` is an empty list.
        """
        rejected = _reject_unknown_parameters(frozenset())
        if rejected is not None:
            return rejected
        hive = context_transfer.hive_root(cfg.hive_tasks_dir)
        try:
            names = project_store.slugs(hive)
        except OSError as exc:
            # `slugs` turns an absent directory into an empty list; one it
            # cannot list (permissions, a file in its place) is this warning.
            root = project_store.projects_dir(hive)
            return _envelope([], [f"{root}: unreadable projects directory: {exc}"])
        rows: list[dict] = []
        warnings: list[str] = []
        for slug in names:
            row, problem = _project_record(hive, slug)
            if problem is not None:
                warnings.append(problem)
            elif row is not None:
                # None without a problem: the file went between the listing and
                # the read, which is a record that is no longer there.
                rows.append(row)
        return _envelope(rows, warnings)

    @app.get("/api/projects/<slug>")
    @requires_auth
    def project_record(slug: str):
        """One project record, as a row of `/api/projects` is (ADR 209).

        A slug that is not one, or has no record, is a 404. A record that does
        not read is `data: null` and the reason, as `/api/tasks/<id>` answers a
        task file that does not parse: a 404 would say the record does not
        exist, which is a lie about a file that does.
        """
        rejected = _reject_unknown_parameters(frozenset())
        if rejected is not None:
            return rejected
        if not project_store.is_slug(slug):
            return _error(f"no project {slug!r}", 404)
        hive = context_transfer.hive_root(cfg.hive_tasks_dir)
        row, problem = _project_record(hive, slug)
        if problem is not None:
            return _envelope(None, [problem])
        if row is None:
            return _error(f"no project {slug!r} in {project_store.projects_dir(hive)}", 404)
        return _envelope(row, [])

    return app


# --- the project records ----------------------------------------------------


def _project_record(hive: str, slug: str) -> tuple[dict | None, str | None]:
    """`slug`'s record as a row, and the warning when it does not read (ADR 209).

    `(row, None)` for a record, `(None, None)` for no record file, `(None,
    warning)` for one that is broken. `project_store.read` prefixes its message
    with the file's path and has already redacted a remote in it; the row's own
    remote goes through `redact_remote` here as well, so no credential leaves
    this route whichever check a future record format loosens.
    """
    try:
        record = project_store.read(hive, slug)
    except project_store.RecordError as exc:
        return None, str(exc)
    if record is None:
        return None, None
    row = record.to_dict()
    row["remote"] = project_store.redact_remote(row["remote"])
    return row, None


# --- the envelope ---------------------------------------------------------


def _envelope(data, warnings: list[str]):
    """Every 200 this service answers.

    `warnings` names files and the caller's own parameters, never states —
    `docs/decisions.md` ADR 5, widened by ADR 11 to admit the `limit` clamp.
    Every 200 funnels through here, which is why the cap lives here and not in
    the six views that build the lists.
    """
    return jsonify({"data": data, "warnings": _capped(warnings)})


def _capped(warnings: list[str]) -> list[str]:
    """At most `MAX_WARNINGS` strings, the last of them a tally of the rest.

    A directory holding many files that will not parse produces one
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
    separator in it would name a file somewhere else. Werkzeug's
    default converter already declines to match a `/`, so this is the second
    lock and not the first: it is what keeps a later `<path:task_id>` from
    turning the route into a file reader.
    """
    name = f"{task_id}.md"
    return bool(task_id) and os.path.basename(name) == name and os.sep not in name


def _learnings_task_context(
    cfg: Config, task_id: str, slug: str, warnings: list[str]
) -> learnings.TaskContext:
    """What `_role_prompt` would build for this task, or nothing, with the reason.

    The dispatcher's own `task_context`, fed the same card path and description,
    so the column cannot disagree with the table a phase is handed. A task that
    does not exist, or a description that will not read, is a warning and an
    unfiltered column rather than an error. ADR 321.
    """
    path = context_transfer.task_file_path(cfg.hive_tasks_dir, task_id)
    if not os.path.exists(path):
        warnings.append(
            f"no task {task_id!r} in {cfg.hive_tasks_dir}: in_phase_table is the unfiltered table"
        )
        return learnings.NO_CONTEXT
    description = None
    try:
        description = task_store.read_description(cfg.hive_tasks_dir, task_id)
    except _UNREADABLE as exc:
        warnings.append(
            f"{task_store.description_path(cfg.hive_tasks_dir, task_id)}: "
            f"unreadable task description: {exc}"
        )
    try:
        card_project = context_transfer.read_task_file(path).project
    except _UNREADABLE:
        card_project = None
    if isinstance(card_project, str) and card_project and card_project != slug:
        warnings.append(f"task {task_id!r} belongs to project {card_project!r}, not {slug!r}")
    return learnings.task_context(cfg.hive_tasks_dir, task_id, path, description)


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
    (`docs/plans/board.md` "Two kinds of stale", `docs/decisions.md` ADR 8: a
    client renders this field and never recomputes it). The computation itself is
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


def _task(
    task: context_transfer.TaskFile,
    description: str | None,
    ttl_seconds: int,
) -> dict:
    """One task as `TaskFile` has it, plus its description.

    The fields are `TaskFile`'s, but for `description` — the text of the task's
    `description.md` as `dispatcher/tasks.py:read_description` returned it,
    served verbatim and `null` when there is none — and this invents none but
    `lock_expired`, which is argued at `_lock_expired`, and the two derived
    ones the board service serves too: `merged` as a boolean and `title`
    (ADR 133, ADR 137). No card: the task
    file is the board's data and nothing mirrors it (`docs/decisions.md`
    ADR 59).

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
        "description": description,
        "resolved_debt": task.resolved_debt,
        # The role of the phase running now, or `null` between phases: the
        # dispatcher writes it with the lock and clears it with the lock
        # (ADR 126), so it is the file's, not invented here. A value beside
        # `lock_expired: true` is a dead dispatcher's, not a running phase.
        "phase": task.phase,
        "lock_expired": _lock_expired(task, ttl_seconds),
        # The backlog fields, shaped as `board_service/app.py:board_list` shapes
        # them, so the two services answer one task the same way (ADR 133).
        # `priority` is the bucket, `normal` when the file names none, and a
        # bad value is served as written beside a warning, never mended here.
        # `rank` only orders inside its bucket, which is why the bucket rides
        # with it; `debt_id` pairs with `source: debt`. `tags` is the cleaned
        # list, [] for none; `rank` and `debt_id` are the file's, `null` when
        # absent. What is wrong with any of them is `field_problems`'s answer,
        # which the routes add to `warnings`.
        "priority": task_store.effective_priority(task),
        "source": task.source,
        "tags": task_store.normal_tags(task.tags),
        "rank": task.rank,
        "debt_id": task.debt_id,
        "dropped_reason": task.dropped_reason,
        "dropped_by": task.dropped_by,
        "proposed_by": task.proposed_by,
        # The project slug the card belongs to, `null` for one that names none,
        # which is the harness's one pre-existing project (ADR 199).
        "project": task.project,
        # The epic fields, for the console's epic view (board-service plan
        # step 8, ADR 137). `epic` is the parent epic's id on a sub-task,
        # `null` when absent; `split_into` is an epic's sub-task ids in order,
        # [] otherwise. Both are the file's, served as written: a non-list
        # `split_into` is not mended here, and `dispatcher/tasks.py:
        # epic_problems` is where the dispatcher names it.
        "epic": task.epic,
        "split_into": task.split_into,
        # A boolean, as `board_service/app.py:board_list` serves it (ADR 133):
        # the file's `{"into", "at"}` record is the dispatcher's, and "has it
        # merged" is the reader's question.
        "merged": bool(task.merged),
        # `tasks.display_title`, the board service's rule and not a copy of it:
        # the frontmatter `title`, else the Goal's first line cut short, else
        # `null` (ADR 125, ADR 129, ADR 137).
        "title": task_store.display_title(task.title, description),
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


def _usage_groups(task_id: str, lines: list[dict]) -> list[dict]:
    """The usage log's lines, grouped into the attempts that wrote them.

    One group per attempt at a phase, in file order — which is the order the
    calls were made, since the log is append-only. A line opens a new group
    when it is a `phase` call, the one every attempt writes first, or when its
    `(role, round, account)` differs from the open group's, which is what a
    failover to the next account or a re-dispatch looks like when a `phase`
    line is missing. Values are compared with `==` and never hashed, because a
    hand-edited line may hold a list where a string belongs.

    Lines travel verbatim in `calls`: nothing is projected and no subkey is
    assumed, since `raw_keys` exists because the CLI's own set is not pinned.
    The group adds only what holds whatever `docs/debt/T-024-D2.md` answers —
    how many calls, which kinds, how many were measured — and the `phase` line
    itself under `phase_call`, the one per-attempt figure that is right under
    either answer. Nothing is summed: a resumed call's usage may be cumulative,
    and a total across `calls` would then count the phase twice. ADR 54 is the
    argument and says what changes once D2 is answered.
    """
    groups: list[dict] = []
    for line in lines:
        key = (line.get("role"), line.get("round"), line.get("account"))
        current = groups[-1] if groups else None
        if (
            current is None
            or line.get("call") == "phase"
            or (current["role"], current["round"], current["account"]) != key
        ):
            current = {
                "task_id": task_id,
                "role": key[0],
                "round": key[1],
                "account": key[2],
                "calls": [],
                "call_count": 0,
                "call_kinds": [],
                "measured_calls": 0,
                "unmeasured_calls": 0,
                "phase_call": None,
            }
            groups.append(current)
        current["calls"].append(line)
        current["call_count"] += 1
        kind = line.get("call")
        if isinstance(kind, str) and kind not in current["call_kinds"]:
            current["call_kinds"].append(kind)
        # `is True` / `is False` and not truthiness: a line whose `measured` is
        # neither is in neither count, so the two falling short of
        # `call_count` is the visible sign that the format drifted.
        if line.get("measured") is True:
            current["measured_calls"] += 1
        elif line.get("measured") is False:
            current["unmeasured_calls"] += 1
        if kind == "phase" and current["phase_call"] is None:
            current["phase_call"] = line
    return groups


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


def _comments_tail(path: str, cap: int | None = None) -> tuple[int, bytes, int]:
    """The newest `cap` bytes of a comments file, as whole lines.

    `cap` is `MAX_COMMENTS_BYTES` when not given, read at the call; the
    notifications file is read the same way under its own (ADR 221).

    Returns the file's line number of the first line kept, the bytes kept and
    how many bytes were left unread (0 for a file under the cap). The prefix is
    read in chunks only to count its newlines, so a warning about a kept line
    names the line an editor shows, and so a cut never lands mid-line: what
    follows the last newline of the prefix, a partial line, is dropped.
    """
    if cap is None:
        cap = MAX_COMMENTS_BYTES
    with open(path, "rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        if size <= cap:
            return 1, fh.read(cap), 0
        skip = size - cap
        newlines = 0
        last = b"\n"
        left = skip
        while left:
            chunk = fh.read(min(left, 64 * 1024))
            if not chunk:
                break
            newlines += chunk.count(b"\n")
            last = chunk[-1:]
            left -= len(chunk)
        kept = fh.read(cap)
    if last != b"\n":
        # The prefix ended mid-line: the rest of that line is not a comment.
        partial, newline, rest = kept.partition(b"\n")
        skip += len(partial) + len(newline)
        newlines += 1 if newline else 0
        kept = rest
    return newlines + 1, kept, skip


def _comment(line: bytes) -> tuple[dict | None, str | None]:
    """One `comments.jsonl` line as served, and what was wrong with it.

    `(None, problem)` for a line that is not a comment: not JSON, not an
    object, or without a string `at`, `author` and `text`. A comment whose diff
    anchor is broken — not all three of `file`, `line` and `commit`, or one of
    the wrong type — is served without it, with the problem, because its text
    is still readable; so is one whose `verdict` (`tasks.record_verdict`) is
    not an object of a known `kind` and a string `commit`. Only the seven known
    keys cross, and of a verdict only its two; nothing is mended.
    """
    value, problem = task_store.parse_comment_line(line)
    if value is None:
        return None, problem
    comment = {key: value[key] for key in _COMMENT_KEYS}
    problems = []
    present = [key for key in _ANCHOR_KEYS if key in value]
    if present:
        anchor_ok = (
            len(present) == len(_ANCHOR_KEYS)
            and isinstance(value["file"], str)
            and type(value["line"]) is int
            and isinstance(value["commit"], str)
        )
        if anchor_ok:
            comment.update({key: value[key] for key in _ANCHOR_KEYS})
        else:
            problems.append(
                "a diff anchor that is not a string file, an integer line and a "
                "string commit; the comment is served without it"
            )
    if "verdict" in value:
        verdict = value["verdict"]
        if (
            isinstance(verdict, dict)
            and verdict.get("kind") in task_store.VERDICT_KINDS
            and isinstance(verdict.get("commit"), str)
        ):
            comment["verdict"] = {"kind": verdict["kind"], "commit": verdict["commit"]}
        else:
            problems.append(
                "a verdict that is not an object of a known kind and a string "
                "commit; the comment is served without it"
            )
    return comment, "; ".join(problems) or None


class _GitFailed(Exception):
    """A `git` call of the diff route that gave no usable answer."""


def _git(project_dir: str, *args: str) -> bytes:
    """One read-only `git` call in `project_dir`: its stdout, or `_GitFailed`.

    `safe.directory` is set for this one call because this service runs as
    root over a checkout the host user owns, and git refuses such a repository
    without it. `--no-optional-locks` keeps the call from refreshing the index,
    a write the `:ro` mount would refuse. `GIT_CEILING_DIRECTORIES` stops git
    at the checkout, so a directory that is not a repository is not answered
    with whatever repository holds it. `LC_ALL=C` as `docker_exec._GIT_ENV`
    sets it, so the error a warning quotes is git's English.
    """
    command = [
        "git", "--no-optional-locks", "-c", f"safe.directory={project_dir}",
        "-C", project_dir, *args,
    ]
    env = {
        **os.environ,
        "LC_ALL": "C",
        "GIT_CEILING_DIRECTORIES": os.path.dirname(os.path.abspath(project_dir)),
    }
    try:
        proc = subprocess.run(
            command, capture_output=True, timeout=GIT_TIMEOUT_SECONDS, env=env, check=False
        )
    except FileNotFoundError as exc:
        raise _GitFailed(f"git is not installed in this service's image: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise _GitFailed(
            f"git {args[0]} took over {GIT_TIMEOUT_SECONDS}s in {project_dir}"
        ) from exc
    except OSError as exc:
        raise _GitFailed(f"git could not run in {project_dir}: {exc}") from exc
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise _GitFailed(
            f"git {args[0]} failed in {project_dir} (exit {proc.returncode})"
            + (f": {stderr}" if stderr else "")
        )
    return proc.stdout


def _task_diff(project_dir: str, slug: str, task_id: str) -> tuple[dict | None, list[str]]:
    """`/api/tasks/<task_id>/diff`'s `data` and warnings, read with `git`.

    Four reads, each its own question: the base (the branch the checkout is
    on), the task branch's tip, their merge base, and the diff from one to
    the other. The first that cannot be answered is the warning beside
    `data: null`.
    """
    branch = docker_exec.task_branch(task_id)
    try:
        base_ref = _git(project_dir, "symbolic-ref", "--quiet", "--short", "HEAD")
    except _GitFailed as exc:
        return None, [f"{project_dir}: no branch checked out to diff {branch} against: {exc}"]
    base = base_ref.decode("utf-8", errors="replace").strip()
    try:
        tip = _git(
            project_dir, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}"
        )
    except _GitFailed:
        return None, [f"{project_dir}: no branch {branch}: the task has not committed there"]
    commit = tip.decode("ascii", errors="replace").strip()
    try:
        merge_base = _git(project_dir, "merge-base", base, commit).decode("ascii").strip()
        raw = _git(
            project_dir, "diff", "--no-color", "--no-ext-diff", "--no-textconv",
            f"{merge_base}..{commit}",
        )
    except _GitFailed as exc:
        return None, [str(exc)]
    warnings: list[str] = []
    truncated = len(raw) > MAX_DIFF_BYTES
    if truncated:
        cut = raw.rfind(b"\n", 0, MAX_DIFF_BYTES)
        raw = raw[: cut + 1]
        warnings.append(
            f"the diff of {branch} is over {MAX_DIFF_BYTES} bytes: its first "
            f"{len(raw)} bytes are served, and the rest is on the branch"
        )
    return {
        "task_id": task_id,
        "project": slug,
        "branch": branch,
        "base": base,
        "commit": commit,
        "merge_base": merge_base,
        "diff": raw.decode("utf-8", errors="replace"),
        "truncated": truncated,
    }, warnings


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


# --- the roadmap ----------------------------------------------------------

_FUTURE = {"idea", "design", "approved"}
_NOW = {"building"}


def _roadmap_plans(plans_dir: str, warnings: list[str]) -> list[dict]:
    """One row per plan file, in file-name order, `superseded_by` filled in.

    A plan whose frontmatter is missing or breaks a rule of
    `observability/api/plans.py`, or that will not read at all, is still a row:
    `status`, `approved` and `built` null, `supersedes` empty, and a warning
    naming the file and the problem. Leaving it out would hide a plan because
    its block is wrong, which is the damage the warning exists to report.
    """
    try:
        names = sorted(name for name in os.listdir(plans_dir) if name.endswith(".md"))
    except OSError as exc:
        # A checkout that keeps no plans is a project with no roadmap, not a
        # broken request: `/api/debt`'s model for a project with no index.
        warnings.append(f"{plans_dir}: no readable plans directory: {exc}")
        return []
    # Every name, before the cap: a `supersedes` entry names a plan file, and
    # one past the cap is still a plan file.
    plan_names = set(names)
    if len(names) > MAX_ROADMAP_PLANS:
        warnings.append(
            f"{len(names)} plans are in {plans_dir} and this service answers at most "
            f"{MAX_ROADMAP_PLANS}: the first {MAX_ROADMAP_PLANS} by file name are read, "
            f"and {len(names) - MAX_ROADMAP_PLANS} are left out."
        )
        names = names[:MAX_ROADMAP_PLANS]
    rows = []
    for name in names:
        path = os.path.join(plans_dir, name)
        data, title = None, None
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, ValueError) as exc:
            # `ValueError` for a `UnicodeDecodeError`, as `/api/debt` has it.
            warnings.append(f"{path}: unreadable plan, listed with no status: {exc}")
        else:
            data, problems = plan_frontmatter.read(text, plan_names)
            title = plan_frontmatter.title(text)
            if problems:
                warnings.append(
                    f"{path}: invalid plan frontmatter, listed with no status: "
                    + "; ".join(problems)
                )
        data = data or {}
        rows.append(
            {
                "file": name,
                "title": title,
                "status": data.get("status"),
                "approved": _iso_date(data.get("approved")),
                "built": _iso_date(data.get("built")),
                # Strings once `read` has passed them: each is a plan file name.
                "supersedes": list(data.get("supersedes") or []),
                "superseded_by": [],
                "tasks": [],
            }
        )
    by_file = {row["file"]: row for row in rows}
    for row in rows:
        for name in row["supersedes"]:
            # A plan past the cap has no row to carry the back link.
            if name in by_file:
                by_file[name]["superseded_by"].append(row["file"])
    return rows


def _iso_date(value: object) -> str | None:
    """A frontmatter date as JSON carries it: YAML makes `2026-10-11` a `date`."""
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


def _task_frontmatter(path: str) -> dict:
    """A task file's frontmatter as a mapping, for keys `TaskFile` does not carry.

    Split as `context_transfer.read_task_file` splits it. Raises what that
    raises, and `TypeError` for a block that is not a mapping, so the caller's
    `_UNREADABLE` guard catches all of it.
    """
    text = Path(path).read_text()
    _, front, _ = text.split(context_transfer._FRONTMATTER_DELIM, 2)
    data = yaml.safe_load(front) or {}
    if not isinstance(data, dict):
        raise TypeError(f"frontmatter is a {type(data).__name__}, not a mapping")
    return data


def _roadmap_task(task: context_transfer.TaskFile) -> tuple[dict, bool, str | None]:
    """A task's row, whether it is merged, and the `YYYY-MM` it merged in.

    Merged means the `merged` block `tasks.mark_merged` writes, not a status:
    that block is what a dependency waits for (`docs/plans/task-split.md`,
    section 3). `title` is null: neither a task file nor its description
    carries one, and the description's first line is its `## Goal` heading.
    The month is null when `merged.at` is missing or not an ISO timestamp.
    """
    merged = bool(task.merged)
    at = task.merged.get("at") if isinstance(task.merged, dict) else None
    if isinstance(at, (dt.date, dt.datetime)):
        # An unquoted timestamp in a hand-edited card reads back as a datetime.
        at = at.isoformat()
    elif at is not None:
        at = str(at)
    month = None
    if at is not None:
        try:
            parsed = dt.datetime.fromisoformat(at)
        except ValueError:
            pass
        else:
            month = f"{parsed.year:04d}-{parsed.month:02d}"
    row = {"id": task.task_id, "title": None, "state": task.status, "merged_at": at}
    return row, merged, month


def _band_order(rows: list[dict]) -> list[dict]:
    """Newest first by `built`, else `approved`; undated last; then by file name."""
    rows = sorted(rows, key=lambda row: row["file"])
    # Stable, and stable under `reverse` too, so equal dates keep file order;
    # an undated row sorts as "", below every date.
    return sorted(rows, key=lambda row: row["built"] or row["approved"] or "", reverse=True)


class _RoadmapEntry(NamedTuple):
    """One task file as `/api/roadmap` reads it, before epics are nested."""

    row: dict
    plan: object
    merged: bool
    month: str | None
    epic: object
    is_epic: bool
    split_into: object
    title: str | None
    key: tuple


class _Placed(NamedTuple):
    """A row to place: a flat task or an epic with its sub-tasks nested."""

    row: dict
    plan: object
    merged: bool
    month: str | None
    backlog: bool
    key: tuple


def _plan_name(plan: object) -> object:
    """A `plan` value as a plan file name: `docs/plans/x.md` is `x.md` (ADR 447)."""
    prefix = project_docs.PLANS_DIR + "/"
    if isinstance(plan, str) and plan.startswith(prefix):
        return plan[len(prefix):]
    return plan


def _latest(stamps: list[str | None]) -> str | None:
    """The latest of ISO timestamps, or None when any is missing or will not compare."""
    try:
        parsed = [dt.datetime.fromisoformat(stamp) for stamp in stamps if stamp is not None]
        if len(parsed) != len(stamps) or not parsed:
            return None
        return stamps[parsed.index(max(parsed))]
    except (ValueError, TypeError):
        return None


def _month_of(at: str | None) -> str | None:
    try:
        parsed = dt.datetime.fromisoformat(at) if at is not None else None
    except ValueError:
        return None
    return f"{parsed.year:04d}-{parsed.month:02d}" if parsed else None


def _roadmap_epics(
    entries: list[_RoadmapEntry], absent: dict[str, str], warnings: list[str]
) -> list[_Placed]:
    """Nest each epic's sub-tasks in it; the rows to place, in id order (ADR 447).

    A sub-task is nested when its epic's `split_into` lists it. One whose
    `epic` is not so listed, or names an id with no row, stays flat, with a
    warning. `absent` says why an id with a file has no row. Every odd case
    is a warning here and never an exception.
    """
    by_id = {entry.row["id"]: entry for entry in entries}
    members: dict[str, list[str]] = {}
    nested: dict[str, str] = {}
    for entry in entries:
        if not entry.is_epic:
            continue
        epic_id = entry.row["id"]
        listed = entry.split_into
        if listed is None:
            listed = []
        if not isinstance(listed, list) or not all(isinstance(item, str) for item in listed):
            warnings.append(f"epic {epic_id}: split_into is not a list of task ids")
            listed = []
        members[epic_id] = []
        for sub_id in listed:
            sub = by_id.get(sub_id)
            if sub is None:
                why = absent.get(sub_id, "no task file")
                warnings.append(f"epic {epic_id}: sub-task {sub_id} is skipped: {why}")
            elif sub.is_epic or sub_id == epic_id:
                warnings.append(f"epic {epic_id}: sub-task {sub_id} is an epic, left flat")
            elif sub_id in nested:
                warnings.append(
                    f"task {sub_id}: listed by epics {nested[sub_id]} and {epic_id}; "
                    f"nested under {nested[sub_id]}"
                )
            else:
                nested[sub_id] = epic_id
                members[epic_id].append(sub_id)
        if not members[epic_id]:
            warnings.append(f"epic {epic_id}: no sub-tasks")
    for entry in entries:
        sub_id, epic_id = entry.row["id"], entry.epic
        if not isinstance(epic_id, str) or nested.get(sub_id) == epic_id:
            continue
        if epic_id in by_id:
            warnings.append(
                f"task {sub_id}: names epic {epic_id}, whose split_into does not list it; "
                "it is left flat"
            )
        else:
            why = absent.get(epic_id, "no task file")
            warnings.append(f"task {sub_id}: names epic {epic_id}, which has no row: {why}")
    placed: list[_Placed] = []
    for entry in entries:
        row, task_id = entry.row, entry.row["id"]
        if task_id in nested:
            continue
        if not entry.is_epic:
            placed.append(_Placed(row, entry.plan, entry.merged, entry.month, False, entry.key))
            continue
        subs = [by_id[sub_id] for sub_id in members[task_id]]
        for sub in subs:
            if sub.plan is not None and _plan_name(sub.plan) != _plan_name(entry.plan):
                warnings.append(
                    f"task {sub.row['id']}: plan {sub.plan!r} differs from epic "
                    f"{task_id}'s {entry.plan!r}; it stays under the epic"
                )
        all_merged = bool(subs) and all(sub.merged for sub in subs)
        merged_at = _latest([sub.row["merged_at"] for sub in subs]) if all_merged else None
        epic_row = {
            "id": task_id,
            "title": entry.title,
            "state": row["state"],
            "merged_at": merged_at,
            "kind": "epic",
            "place": "backlog" if row["state"] in _BACKLOG_STATES else "board",
            "merged_tasks": sum(1 for sub in subs if sub.merged),
            "tasks": [sub.row for sub in subs],
        }
        backlog = row["state"] in ("proposed", "deferred")
        placed.append(_Placed(epic_row, entry.plan, all_merged, _month_of(merged_at), backlog, entry.key))
    return placed


_BACKLOG_STATES = ("proposed", "deferred", "dropped")


def _is_future(plan_row: dict) -> bool:
    """A plan row is in the future band: no readable status, or not yet building."""
    return plan_row["status"] is None or plan_row["status"] in _FUTURE


def _roadmap(slug: str, plan_rows: list[dict], task_rows: list[_Placed], warnings: list[str]) -> dict:
    """The answer: plans banded and ordered, rows under their plan or under `no_plan`.

    Rows are tasks and epics (with their sub-tasks nested). A backlog epic
    goes after the other rows of its list, in board order, and in the future
    band: in its plan's row when that is a future one, else in a second row
    for the plan file (ADR 447, part 5).
    """
    by_file = {row["file"]: row for row in plan_rows}
    no_plan_now: list[dict] = []
    months: dict[str | None, list[dict]] = {}
    echoes: dict[str, dict] = {}
    ordered = [item for item in task_rows if not item.backlog]
    ordered += sorted((item for item in task_rows if item.backlog), key=lambda item: item.key)
    for row, plan, merged, month, backlog, _ in ordered:
        name = _plan_name(plan)
        if plan is not None:
            if isinstance(name, str) and name in by_file:
                target = by_file[name]
                if backlog and not _is_future(target):
                    if name not in echoes:
                        echoes[name] = dict(
                            target, tasks=[], superseded_by=list(target["superseded_by"])
                        )
                    target = echoes[name]
                target["tasks"].append(row)
                continue
            warnings.append(
                f"task {row['id']}: plan {plan!r} is not a plan file of {slug}; "
                "it is listed under no_plan"
            )
        if merged and not backlog:
            months.setdefault(month, []).append(row)
        else:
            no_plan_now.append(row)
    bands: dict[str, list[dict]] = {"future": list(echoes.values()), "now": [], "past": []}
    for row in plan_rows:
        status = row["status"]
        # A plan with no readable status is in `future`: it is not known to be
        # built, and the warning beside it says why it has none.
        if _is_future(row):
            bands["future"].append(row)
        elif status in _NOW:
            bands["now"].append(row)
        else:
            bands["past"].append(row)
    dated = sorted((month for month in months if month is not None), reverse=True)
    past = [{"month": month, "tasks": months[month]} for month in dated]
    if None in months:
        past.append({"month": None, "tasks": months[None]})
    return {
        "project": slug,
        "bands": {band: _band_order(rows) for band, rows in bands.items()},
        "no_plan": {"now": no_plan_now, "past": past},
    }


# --- command requests -----------------------------------------------------

#: What `?state=` takes and a request row's `state` is, ADR 122:
#: - `pending`: the task is blocked on this block and it has no verdict yet;
#:   every accepted request of it is one the operator can give a verdict on.
#: - `awaiting_resume`: the block has a verdict, of any kind, whose resume no
#:   account took yet (`command_run.awaits_resume`).
#: - `denied`: a *deny*, no longer waiting on its resume.
#: - `resumed`: a *run* or *allow*, no longer waiting on its resume; its
#:   `verdict.resumed_at` is null when the task was moved on another way.
COMMAND_REQUEST_STATES = ("pending", "awaiting_resume", "resumed", "denied")


def _command_requests(
    cfg: Config, task: context_transfer.TaskFile, project: str | None, warnings: list[str]
) -> tuple[list[dict], list[dict]]:
    """One task's request rows and its current block's refused requests.

    Read from each filing role's envelope on record, which holds that role's
    newest block, and from the task's verdicts, which outlive the envelope a
    later phase overwrote: such a verdict is still a row, with `argv`,
    `agent_reason` and, on a *deny*, `target` null, since the request it was
    given on is no longer on record. A block with a verdict is one row, the
    request the verdict names; one with none is a row per accepted request
    while the task is blocked on it, and no row once it is not (the task was
    moved on with no verdict, and nothing is left to decide). An envelope that
    will not parse is a warning and the rest of the task still comes back.
    """
    hive = cfg.hive_tasks_dir
    task_id = task.task_id
    if not all(isinstance(entry, dict) for entry in task.command_verdicts):
        # `command_run` reads each as a mapping and would fail on the task:
        # the file is a bad one, and the route's guard says so.
        raise TypeError("command_verdicts is a list of mappings")
    current = _current_block(cfg, task_id)
    awaiting = current is not None and command_run.awaits_resume(cfg, task_id)
    base = {"task_id": task_id, "project": project, "title": None}
    rows: list[dict] = []
    refused: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for role in command_run._FILING_ROLES:
        envelope_path = context_transfer.handoff_path(hive, task_id, role)
        try:
            envelope = context_transfer.read_handoff_envelope(hive, task_id, role)
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            warnings.append(f"{envelope_path}: unreadable handoff: {exc}")
            continue
        try:
            block_rows, block_refused, key = _block_rows(
                cfg, task, role, envelope, current, awaiting, base
            )
        except _UNREADABLE as exc:
            warnings.append(f"{envelope_path}: unreadable command requests: {exc}")
            continue
        if key is not None:
            seen.add(key)
        rows.extend(block_rows)
        refused.extend(block_refused)
    for entry in task.command_verdicts:
        key = (entry.get("role"), entry.get("filed_at"))
        if key in seen:
            continue
        ran = entry.get("ran") if isinstance(entry.get("ran"), dict) else {}
        rows.append(_request_row(
            cfg, base, entry.get("role"), entry.get("filed_at"), entry.get("n"), None,
            _resolved_target(ran.get("resolved")), None, _verdict_state(entry, False), entry,
        ))
    return rows, refused


def _current_block(cfg: Config, task_id: str) -> tuple[str, object] | None:
    """The `(role, filed_at)` the task is blocked on now, or None when it is not.

    `command_run`'s own two checks, so a verdict or a resume from the CLI and
    this row agree on which block is the live one.
    """
    try:
        command_run._check_blocked(cfg, task_id)
        role, envelope = command_run._filed_block(cfg, task_id)
    except command_run.VerdictRefused:
        return None
    return role, envelope.get("saved_at")


def _block_rows(
    cfg: Config,
    task: context_transfer.TaskFile,
    role: str,
    envelope: dict,
    current: tuple[str, object] | None,
    awaiting: bool,
    base: dict,
) -> tuple[list[dict], list[dict], tuple[str, object] | None]:
    """The rows one envelope's block makes. Raises `TypeError` or `ValueError` on a bad shape."""
    requests = envelope.get("command_requests")
    if not isinstance(requests, dict):
        return [], [], None
    accepted = requests.get("accepted") or []
    filed = requests.get("refused") or []
    if not isinstance(accepted, list) or not isinstance(filed, list):
        raise TypeError("command_requests' accepted and refused are lists")
    if not (accepted or filed):
        return [], [], None
    filed_at = envelope.get("saved_at")
    is_current = current == (role, filed_at)
    entry = command_run._block_verdict(task, role, filed_at)
    rows: list[dict] = []
    if entry is not None:
        n = entry.get("n")
        request = accepted[n - 1] if isinstance(n, int) and 1 <= n <= len(accepted) else None
        rows.append(_accepted_row(
            cfg, base, role, filed_at, n, request,
            _verdict_state(entry, is_current and awaiting), entry,
        ))
    elif is_current:
        for n, request in enumerate(accepted, start=1):
            rows.append(_accepted_row(cfg, base, role, filed_at, n, request, "pending", None))
    refused = [_refused_row(base, role, filed_at, item) for item in filed] if is_current else []
    return rows, refused, (role, filed_at)


def _verdict_state(entry: dict, awaiting: bool) -> str:
    if awaiting:
        return "awaiting_resume"
    return "denied" if entry.get("verdict") == command_run.DENY else "resumed"


def _accepted_row(
    cfg: Config, base: dict, role: str, filed_at: object, n: object, request: dict | None,
    state: str, entry: dict | None,
) -> dict:
    """A row for an accepted request as its envelope has it; `request` None when gone."""
    if request is None:
        ran = entry.get("ran") if entry is not None and isinstance(entry.get("ran"), dict) else {}
        return _request_row(
            cfg, base, role, filed_at, n, None, _resolved_target(ran.get("resolved")), None,
            state, entry,
        )
    if not isinstance(request, dict):
        raise TypeError(f"an accepted request is a mapping, not a {type(request).__name__}")
    argv = request.get("argv")
    if not isinstance(argv, list) or not all(isinstance(token, str) for token in argv):
        raise TypeError("an accepted request's argv is a list of strings")
    target = _resolved_target(request.get("resolved"))
    if target is None:
        raise TypeError("an accepted request carries its resolved target")
    reason = request.get("reason")
    return _request_row(
        cfg, base, role, filed_at, n, list(argv), target,
        reason if isinstance(reason, str) else None, state, entry,
    )


def _resolved_target(resolved: object) -> dict | None:
    """kind, container, service and cwd of a resolved target; None for no mapping."""
    if not isinstance(resolved, dict):
        return None
    return {key: resolved.get(key) for key in ("kind", "container", "service", "cwd")}


def _request_row(
    cfg: Config, base: dict, role: object, filed_at: object, n: object, argv: list | None,
    target: dict | None, agent_reason: str | None, state: str, entry: dict | None,
) -> dict:
    task_id = base["task_id"]
    return {
        **base,
        "role": role,
        "filed_at": filed_at,
        "n": n,
        "argv": argv,
        "target": target,
        "agent_reason": agent_reason,
        "state": state,
        "verdict": _verdict(cfg, task_id, entry) if entry is not None else None,
        "commands": _commands(task_id, base["project"], n, state, target),
    }


def _verdict(cfg: Config, task_id: str, entry: dict) -> dict:
    """The verdict as the card shows it: no output, and the log by its relative path.

    `by` is served only when it does not look like an email: the CLI writes
    `operator`, and a board that passes a person must not have their address
    land on every screen that lists requests.
    """
    by = entry.get("by")
    ran = entry.get("ran")
    row = {
        "verdict": entry.get("verdict"),
        "by": by if isinstance(by, str) and "@" not in by else None,
        "at": entry.get("at"),
        "comment": entry.get("comment"),
        "allowed": entry.get("allowed"),
        "resumed_at": entry.get("resumed_at"),
        "ran": None,
    }
    if isinstance(ran, dict):
        n = entry.get("n")
        row["ran"] = {
            "exit_code": ran.get("exit_code"),
            "timed_out": bool(ran.get("timed_out")),
            "truncated": bool(ran.get("truncated")),
            "log": _relative_log(cfg, task_id, n) if isinstance(n, int) else None,
        }
    return row


def _relative_log(cfg: Config, task_id: str, n: int) -> str:
    """`commands/<n>.log`: where the run's log is, under the task's scratch dir."""
    hive = cfg.hive_tasks_dir
    return os.path.relpath(
        context_transfer.command_log_path(hive, task_id, n),
        context_transfer.scratch_dir(hive, task_id),
    )


def _commands(
    task_id: str, project: str | None, n: object, state: str, target: dict | None
) -> dict:
    """The CLI lines that move this row on, or null where none applies.

    *allow* only for a request resolved to the agent's own container, the one
    kind `command-verdict` takes it for (ADR 121). A task with no project
    known gets `<project>` in place of a slug, for the operator to fill in.
    """
    tail = f"--project {shlex.quote(project) if project else '<project>'}"
    task = shlex.quote(task_id)
    commands = {"run": None, "allow": None, "deny": None, "resume": None}
    if state == "pending" and isinstance(n, int):
        for verdict in (command_run.RUN, command_run.DENY):
            commands[verdict] = f"dispatch command-verdict {task} {n} {verdict} {tail}"
        if target is not None and target.get("kind") == "agent":
            commands["allow"] = f"dispatch command-verdict {task} {n} allow {tail}"
    elif state == "awaiting_resume":
        commands["resume"] = f"dispatch command-resume {task} {tail}"
    return commands


def _refused_row(base: dict, role: str, filed_at: object, item: object) -> dict:
    """A request the check refused at filing: no number, nothing to run, only why."""
    if not isinstance(item, dict):
        raise TypeError(f"a refused request is a mapping, not a {type(item).__name__}")
    request = item.get("request") if isinstance(item.get("request"), dict) else {}
    argv = request.get("argv")
    target = request.get("target") if isinstance(request.get("target"), dict) else {}
    reason = request.get("reason")
    return {
        **base,
        "role": role,
        "filed_at": filed_at,
        "argv": list(argv) if isinstance(argv, list) and all(isinstance(t, str) for t in argv) else None,
        "target": {
            key: target.get(key) if isinstance(target.get(key), str) else None
            for key in ("kind", "service")
        },
        "agent_reason": reason if isinstance(reason, str) else None,
        "refused_reason": str(item.get("reason")),
    }


def _command_request_order(row: dict) -> tuple:
    """Pending first, then everything by `filed_at`, oldest first, then task and number."""
    n = row["n"] if isinstance(row["n"], int) else 0
    return (row["state"] != "pending", str(row["filed_at"] or ""), row["task_id"], n)


# --- the action queue -----------------------------------------------------


def _action_records(
    directory: Path, newest: int | None, warnings: list[str]
) -> tuple[list[dict], int]:
    """The records in one queue directory, and how many files it holds.

    `newest=None` serves every file in name order, which is the order the
    worker takes them; a number serves only the last that many names, newest
    first, and opens no other file. A directory that does not exist is empty
    and no warning: the queue is made by its first writer, and a host that
    never ran one has none. A file that does not read is a warning naming it,
    parsed by `actions._load`, the queue's own reader, so a record this route
    serves is one the worker would take.
    """
    try:
        names = sorted(n for n in os.listdir(directory) if n.endswith(".json"))
    except FileNotFoundError:
        return [], 0
    except OSError as exc:
        warnings.append(f"{directory}: cannot list the actions: {exc}")
        return [], 0
    total = len(names)
    if newest is not None:
        names = names[::-1][:newest]
    records = []
    for name in names:
        path = directory / name
        try:
            records.append(actions._load(path))
        except (OSError, ValueError) as exc:
            warnings.append(f"{path}: unreadable action: {exc}")
    return records, total


def _worker_heartbeat(path: Path, warnings: list[str]) -> dict:
    """`{heartbeat, stale, stale_after_seconds}` from the worker's `worker.json`.

    No file is no worker: `heartbeat` null and `stale` true, with no warning,
    since a stack runs without the `worker` profile (ADR 142, 1). A file that
    does not read is a warning and the same answer. A `beat_at` that is not a
    UTC ISO time serves the heartbeat as it is, stale, and says why. The pid is
    served and not checked: it is the worker container's, not this one's.
    """
    answer = {"heartbeat": None, "stale": True, "stale_after_seconds": WORKER_STALE_SECONDS}
    try:
        with open(path) as f:
            heartbeat = json.load(f)
    except FileNotFoundError:
        return answer
    except (OSError, ValueError) as exc:
        warnings.append(f"{path}: unreadable worker heartbeat: {exc}")
        return answer
    if not isinstance(heartbeat, dict):
        warnings.append(f"{path}: unreadable worker heartbeat: not a JSON object")
        return answer
    answer["heartbeat"] = heartbeat
    beat_at = heartbeat.get("beat_at")
    try:
        beat = dt.datetime.fromisoformat(beat_at)
        if beat.tzinfo is None:
            raise ValueError("no time zone")
    except (TypeError, ValueError) as exc:
        warnings.append(f"{path}: beat_at {beat_at!r} is not an ISO time: {exc}")
        return answer
    age = (dt.datetime.now(dt.timezone.utc) - beat).total_seconds()
    answer["stale"] = age > WORKER_STALE_SECONDS
    return answer


def _log_waitress() -> None:
    """Send waitress's start line and queue warnings to stderr once (ADR 420, 423)."""
    logger = logging.getLogger("waitress")
    logger.setLevel(logging.INFO)
    logger.propagate = False  # waitress.serve() gives root a handler too (ADR 423)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(logging.BASIC_FORMAT))
        logger.addHandler(handler)


def run(environ=None, config_path: str = CONFIG_PATH) -> None:
    """Serve the read API from waitress (`docs/decisions.md` ADR 417)."""
    environ = os.environ if environ is None else environ
    app = create_app(
        config_path,
        environ.get("COLLECTOR_DB_PATH", DEFAULT_DB_PATH),
        environ["DASHBOARD_USERNAME"],
        environ["DASHBOARD_PASSWORD_HASH"],
        # `.get` and not `[...]`: an operator upgrading a running host has a
        # `docker/compose/.env` written before `API_TOKEN` existed, and this
        # service must still start there with the bearer path shut. The two
        # `DASHBOARD_` names are deliberate and stay — renaming them means
        # editing a `.env` that exists on a running host to buy a spelling.
        environ.get("API_TOKEN"),
    )
    # Imported after the app is built, so a missing variable raises whether or
    # not waitress is installed.
    from waitress import serve

    _log_waitress()
    serve(app, host=HOST, port=PORT, threads=THREADS)


if __name__ == "__main__":
    run()
