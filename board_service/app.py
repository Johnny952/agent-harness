"""The board service: section 6's tools over MCP, and the console's writes over HTTP.

Steps 4 and 5 of `docs/plans/board-service.md`. The plan makes this process the
board's write side and the agents' MCP server (section 5, `docs/decisions.md`
ADR 124). It serves the two read tools, `board_list` and `board_get`, and the
four write tools, `backlog_propose`, `task_edit`, `task_comment` and
`backlog_accept` (ADR 129); the same four are under `/api/` for the console,
as the operator. None moves a task once it is on the board, and none
dispatches. Three more routes, behind the `BOARD_ACTIONS_TOKEN` bearer, create a
`start`, a `cancel` or a `command_verdict` in the dispatcher's action queue
(ADR 142, ADR 151).

Everything here reads and writes through `dispatcher/tasks.py` and
`dispatcher/context_transfer.py`, so there is no second copy of the task file
or the description format, and no write that skips `task_lock`: a parser or a
writer this service needs and does not have is one to make reachable in
`dispatcher/`. It holds no docker socket and no git
credential; the push of `review.md`'s *approve* stays on the dispatcher's side
(ADR 124).

The transport is MCP's streamable HTTP, answered in its stateless JSON form on
one `POST /mcp` (ADR 125): no session, no event stream, every answer one JSON
body. The caller's role is the `?role=` query parameter, not a credential
(section 5): it decides which tools are listed and which may be called. It
keeps a confused phase off a tool outside its role; it does not stop someone on
the network from naming another role, and is not meant to.
"""
from __future__ import annotations

import datetime as dt
import hmac
import json
import os
import re
import sys

import yaml
from flask import Flask, jsonify, request

from dispatcher import actions, context_transfer
from dispatcher import tasks as task_store
from dispatcher import threads as thread_store
from dispatcher.board_mcp import OPERATOR, ROLE_TOOLS
from dispatcher.config import PHASE_ROLES, load_config

CONFIG_PATH = "/app/config.yaml"
PORT = 8792
ACTIONS_TOKEN_ENV = "BOARD_ACTIONS_TOKEN"

#: Every role the service knows. A role outside it is a misspelt URL, answered
#: 400, rather than a role that silently gets no tools. `OPERATOR` is the
#: operator's session; every other caller is a phase, named by its role.
KNOWN_ROLES = frozenset(PHASE_ROLES) | {OPERATOR}

# `ROLE_TOOLS`, section 6's table of read rows, is imported from
# `dispatcher/board_mcp.py`: the dispatcher reads the same table to decide
# which phases get this server (step 7). A role not named there gets no
# tools: the implementador, revisor, auditor and integrador "get no tools at
# first".

#: The backlog's statuses (section 4), as `dispatcher/tasks.py` defines them
#: (ADR 127). `backlog_propose` files into the first and `backlog_accept` moves
#: between them and out; a task with any other status is on the board.
BACKLOG_STATUSES = frozenset(task_store.BACKLOG_STATUSES)
PLACES = ("backlog", "board")

#: The most task files one `board_list` reads. The api's `MAX_ROADMAP_TASKS`
#: number and reason: the hive holds about seventy on 2026-10-18, and the clamp
#: is taken on the sorted ids before any file is opened.
MAX_LIST_TASKS = 2000
#: The longest title `board_list` serves when it falls back to the Goal's first
#: line: `tasks.DISPLAY_TITLE_CHARS`, the rule the read api shares (ADR 137).
TITLE_CHARS = task_store.DISPLAY_TITLE_CHARS
#: The longest text filter. A filter is a word or a phrase.
MAX_TEXT_CHARS = 200
#: The longest turn message (ADR 181, 3): it is one argv element of `docker
#: exec`, and Linux caps a single argument at 128 KiB.
MAX_TURN_TEXT_CHARS = 20_000

#: What a task's files can fail with; the api's `_UNREADABLE`, for its reasons.
_UNREADABLE = (OSError, TypeError, ValueError, KeyError, yaml.YAMLError)

COMMENTS_FILE = task_store.COMMENTS_FILE

#: The MCP revisions this endpoint answers the handshake of: the ones whose
#: lifecycle starts with `initialize`. The client's own is echoed when it is one
#: of these, and the newest is offered otherwise, as the spec asks.
PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
SERVER_INFO = {"name": "ia-harness-board", "version": "1"}

# JSON-RPC 2.0 error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602


class ToolError(ValueError):
    """A tool call that ran and failed: answered as an `isError` result, for the model to read."""


class NotFoundError(ToolError):
    """A tool call naming a task there is none of: a 404 on the HTTP side."""


# --- the tools --------------------------------------------------------------


def place_of(status: object) -> str:
    """`backlog` or `board`, from a task's status alone (section 4)."""
    return "backlog" if status in BACKLOG_STATUSES else "board"


def _title(frontmatter: dict, description: str | None) -> str | None:
    """A frontmatter `title` when there is one, else the Goal's first line, else None.

    `tasks.display_title`, the one rule the read api's task rows use too, so
    the two services answer one task the same way (ADR 133, ADR 137).
    """
    return task_store.display_title(frontmatter.get("title"), description)


def board_list(
    hive_dir: str,
    place: str | None = None,
    status: str | None = None,
    epic: str | None = None,
    text: str | None = None,
) -> dict:
    """Section 6's `board_list`: one short row per task, filtered, and the warnings.

    Every filter is optional and they combine. `text` matches, case-folded, the
    id, the title and the description. A task file that will not read, or that
    `dispatch migrate` has not moved, costs a warning naming it and not the
    list. Never writes.

    The board's rows come first, by id; then the backlog's, by bucket, then
    rank, then id (ADR 130). `ready` names the listed tasks the next cycle may
    take, in the Ready order: pending, every dependency merged, by effective
    bucket (a dependency takes the highest bucket of what waits on it), rank
    and id. It is worked out over every task read, not only the listed ones,
    so a filter narrows it and does not reorder it.
    """
    if place is not None and place not in PLACES:
        raise ToolError(f"place must be one of {', '.join(PLACES)}, not {place!r}")
    if text is not None and len(text) > MAX_TEXT_CHARS:
        raise ToolError(f"text is at most {MAX_TEXT_CHARS} characters")
    needle = text.casefold() if text else None
    warnings: list[str] = []
    ids = sorted(context_transfer.list_task_ids(hive_dir))
    if len(ids) > MAX_LIST_TASKS:
        warnings.append(
            f"the hive holds {len(ids)} task files; only the first {MAX_LIST_TASKS} "
            "by id were read"
        )
        ids = ids[:MAX_LIST_TASKS]
    items = []
    read: dict[str, context_transfer.TaskFile] = {}
    for task_id in ids:
        path = context_transfer.task_file_path(hive_dir, task_id)
        try:
            task = context_transfer.read_task_file(path)
            frontmatter = _frontmatter(path)
            description = task_store.read_description(hive_dir, task_id)
            row = {
                "id": task_id,
                "title": _title(frontmatter, description),
                "status": task.status,
                "place": place_of(task.status),
                "epic": task.epic,
                "merged": bool(task.merged),
                # The bucket, `normal` for a task that names none, and who
                # proposed it, None for one filed before the field (ADR 127).
                "priority": task_store.effective_priority(task),
                "source": task.source,
                # Normalized as a write would store them; the position in the
                # bucket, None for a task filed before the field (ADR 130).
                "tags": task_store.normal_tags(task.tags),
                "rank": task.rank,
                # The debt index entry a `source: debt` item was filed from,
                # None for any other task (ADR 131).
                "debt_id": task.debt_id,
            }
            problems = task_store.field_problems(task)
        except _UNREADABLE as exc:
            warnings.append(f"{path}: unreadable task file: {exc}")
            continue
        # Served as the file has them, and named: a hand edit got them wrong,
        # and `open_cycle` refuses the task until it is put right.
        warnings.extend(f"{path}: {problem}" for problem in problems)
        read[task_id] = task
        if place is not None and row["place"] != place:
            continue
        if status is not None and row["status"] != status:
            continue
        if epic is not None and row["epic"] != epic:
            continue
        if needle is not None:
            haystack = "\n".join([task_id, row["title"] or "", description or ""])
            if needle not in haystack.casefold():
                continue
        items.append(_json_safe(row))
    listed = {row["id"] for row in items}
    items.sort(key=lambda row: _list_key(row, read[row["id"]]))
    ready = [task_id for task_id in task_store.ready_order(read) if task_id in listed]
    return {"items": items, "ready": ready, "warnings": warnings}


def _list_key(row: dict, task: context_transfer.TaskFile) -> tuple:
    """The board by id, then the backlog by bucket, rank and id (ADR 130)."""
    if row["place"] == "board":
        return (0, row["id"])
    return (1, task_store.order_key(row["id"], task_store.bucket_of(task), task_store.rank_of(task)))


def board_get(hive_dir: str, task_id: str) -> dict:
    """Section 6's `board_get`: one task or epic's frontmatter, description and comment count.

    The description is served verbatim, as `read_description` returns it, and
    `null` when the task has none. The comment count is the comments in
    `comments.jsonl`, 0 when the file is absent; `task_comment` appends to it.
    Raises `ToolError` for an id that names no task. Never writes.
    """
    if not isinstance(task_id, str) or not context_transfer._is_single_path_component(task_id):
        raise ToolError(f"{task_id!r} is not a task id")
    if task_store.is_draft_id(task_id):
        raise ToolError(f"{task_id!r} names drafts, not a task")
    path = context_transfer.task_file_path(hive_dir, task_id)
    if not os.path.isfile(path):
        raise NotFoundError(f"no task {task_id}")
    try:
        task = context_transfer.read_task_file(path)
        frontmatter = _frontmatter(path)
        description = task_store.read_description(hive_dir, task_id)
    except _UNREADABLE as exc:
        raise ToolError(f"{path}: unreadable task file: {exc}") from exc
    # The frontmatter is served as the file has it, `debt_id` among it; what
    # a hand edit got wrong in the backlog fields is named, as `board_list`
    # names it (ADR 131).
    warnings: list[str] = [f"{path}: {problem}" for problem in task_store.field_problems(task)]
    comments = _comment_count(hive_dir, task_id, warnings)
    return {
        "item": _json_safe(
            {
                "id": task_id,
                "place": place_of(frontmatter.get("status")),
                "frontmatter": frontmatter,
                "description": description,
                "comment_count": comments,
            }
        ),
        "warnings": warnings,
    }


def _frontmatter(path: str) -> dict:
    """The whole frontmatter, keys `TaskFile` does not carry included.

    `tasks._read_frontmatter` is the split `read_task_file` makes; the caller
    has already called that, so a file reaching here has a mapping.
    """
    data = task_store._read_frontmatter(path)
    if not isinstance(data, dict):
        raise TypeError(f"frontmatter is a {type(data).__name__}, not a mapping")
    return data


def _comment_count(hive_dir: str, task_id: str, warnings: list[str]) -> int | None:
    """The comments in the task's `comments.jsonl`: 0 when absent, None when unreadable.

    A count is what section 6 serves, and the comments themselves are the file
    a phase is pointed to (section 2). A line that is not a comment is not
    counted and is a warning naming the file and its line, as the read api
    skips and names it, so the count is the thread the console shows (ADR 172).
    """
    path = task_store.comments_path(hive_dir, task_id)
    try:
        return task_store.count_comments(path, warnings)
    except (FileNotFoundError, NotADirectoryError):
        return 0
    except OSError as exc:
        warnings.append(f"{path}: unreadable comments file: {exc}")
        return None


def _json_safe(value):
    """`value` with what YAML builds and JSON cannot carry made into text.

    An unquoted timestamp in a hand-edited task file reads back as a datetime,
    and `!!set` as a set; neither should cost a read.
    """
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_json_safe(v) for v in value]
        return sorted(items, key=str) if isinstance(value, (set, frozenset)) else items
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


# The write tools. Each is one call into `dispatcher/tasks.py`, which checks
# everything, takes the locks and writes; what is left here is who is asking.


def _writing(write, *args, **kwargs) -> dict:
    """Run one `tasks` writer, its refusals made `ToolError`s for the caller to read."""
    try:
        return write(*args, **kwargs)
    except task_store.NoTaskError as exc:
        raise NotFoundError(str(exc)) from exc
    except (task_store.BoardWriteError, task_store.UnmigratedTaskError) as exc:
        raise ToolError(str(exc)) from exc
    except OSError as exc:
        # A hive mounted read-only, a full disk: the write did not happen.
        raise ToolError(f"the hive could not be written: {exc}") from exc
    except _UNREADABLE as exc:
        raise ToolError(f"unreadable task file: {exc}") from exc


def backlog_propose(hive_dir: str, role: str, **arguments) -> dict:
    """Section 6's `backlog_propose`: a task or an epic into the backlog, as `proposed`.

    `source` is the caller's role, not an argument: who proposed an item is
    what the role says, and a phase cannot pass for the operator by naming it.
    """
    return _writing(task_store.propose, hive_dir, source=role, **arguments)


def task_edit(hive_dir: str, role: str, task_id: str, **fields) -> dict:
    """Section 6's `task_edit`: title, description, `depends_on`, `priority`, `tags`, `rank`, while not started."""
    return _writing(task_store.edit_task, hive_dir, task_id, **fields)


def task_comment(hive_dir: str, role: str, task_id: str, text: str, **anchor) -> dict:
    """Section 6's `task_comment`: one comment on a task, or on a line of its diff.

    The author is the caller's role, as the source of a proposal is.
    """
    return _writing(task_store.add_comment, hive_dir, task_id, role, text, **anchor)


def backlog_accept(hive_dir: str, role: str, task_id: str, **arguments) -> dict:
    """Section 6's `backlog_accept`: the person's word, taking an item out of the backlog."""
    return _writing(task_store.accept, hive_dir, task_id, **arguments)


# --- what MCP is told about the tools ---------------------------------------

_STRING = {"type": "string"}
_PRIORITY = {
    "type": "string",
    "enum": list(task_store.PRIORITIES),
    "description": "the bucket; the person confirms or changes it on accepting",
}
_IDS = {"type": "array", "items": _STRING}
_TAGS = {
    "type": "array",
    "items": _STRING,
    "description": (
        f"free labels, at most {task_store.MAX_TAGS} of at most {task_store.MAX_TAG_CHARS} "
        "characters; trimmed and kept once, in order"
    ),
}
_WRITES = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
_DESCRIPTION = {
    "type": "string",
    "description": (
        "Markdown in sections: ## Goal and ## Done when, and optionally ## Surface, "
        "## Steps and ## Out of scope"
    ),
}

TOOLS: dict[str, dict] = {
    "board_list": {
        "name": "board_list",
        "description": (
            "List the harness's tasks and epics: id, title, status, place, epic, merged, "
            "priority, source, tags, rank, debt_id (the debt index entry a source-debt "
            "item was filed from). The board's rows come by id, the backlog's by "
            "priority, then rank. `ready` lists the ids the next cycle may take, in order: "
            "pending with every dependency merged, by priority (a dependency takes the "
            "highest priority of what waits on it), then rank, then id. "
            "Filters combine; all are optional."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "place": {"type": "string", "enum": list(PLACES)},
                "status": _STRING,
                "epic": {**_STRING, "description": "the epic's task id"},
                "text": {**_STRING, "description": "matched in id, title and description"},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    "board_get": {
        "name": "board_get",
        "description": "One task or epic: its frontmatter, its description and its comment count.",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": _STRING},
            "required": ["task_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    "backlog_propose": {
        "name": "backlog_propose",
        "description": (
            "Propose a task, or an epic with 2 to 4 tasks, into the backlog as `proposed`. "
            "Nothing runs until the operator accepts it. Idempotent on the title: the same "
            "proposal again answers the task it made, with created false."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": _STRING,
                "description": _DESCRIPTION,
                "priority": _PRIORITY,
                "tags": _TAGS,
                "depends_on": {**_IDS, "description": "task ids; not for an epic"},
                "tasks": {
                    "type": "array",
                    "description": "an epic's tasks; given, the proposal is an epic",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": _STRING,
                            "description": _DESCRIPTION,
                            "after": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "description": "1-based positions of earlier tasks it depends on",
                            },
                        },
                        "required": ["title", "description"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["title", "description"],
            "additionalProperties": False,
        },
        "annotations": {**_WRITES, "idempotentHint": True},
    },
    "task_edit": {
        "name": "task_edit",
        "description": (
            "Change a task's title, description, depends_on, priority, tags or rank, while "
            "it has not started. Fields left out are kept; tags [] clears them. A new "
            "priority puts the task at the end of that bucket unless rank is given."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": _STRING,
                "title": _STRING,
                "description": _DESCRIPTION,
                "depends_on": _IDS,
                "priority": _PRIORITY,
                "tags": _TAGS,
                "rank": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": task_store.MAX_RANK,
                    "description": (
                        "the position in its bucket, lower first; new items are spaced "
                        f"{task_store.RANK_STEP} apart, so one fits between two"
                    ),
                },
            },
            "required": ["task_id"],
            "additionalProperties": False,
        },
        "annotations": {**_WRITES, "idempotentHint": True},
    },
    "task_comment": {
        "name": "task_comment",
        "description": (
            "Comment on a task, or on one line of its diff with file, line and commit "
            "together. Comments are kept for good."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": _STRING,
                "text": _STRING,
                "file": {**_STRING, "description": "a path inside the repository"},
                "line": {"type": "integer", "description": "1-based"},
                "commit": {**_STRING, "description": "the commit hash the line is in"},
            },
            "required": ["task_id", "text"],
            "additionalProperties": False,
        },
        "annotations": {**_WRITES, "idempotentHint": False},
    },
    "backlog_accept": {
        "name": "backlog_accept",
        "description": (
            "On the person's word only: move a proposed or deferred item to pending (an epic "
            "with its proposed tasks), or to deferred or dropped. Never moves a task already "
            "on the board."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": _STRING,
                "to": {
                    "type": "string",
                    "enum": list(task_store.ACCEPT_TO),
                    "description": "where it goes; pending when left out",
                },
                "priority": _PRIORITY,
            },
            "required": ["task_id"],
            "additionalProperties": False,
        },
        "annotations": {**_WRITES, "idempotentHint": True},
    },
}

_WRITERS = {
    "backlog_propose": backlog_propose,
    "task_edit": task_edit,
    "task_comment": task_comment,
    "backlog_accept": backlog_accept,
}


def tools_for(role: str) -> list[dict]:
    """The tools this role is shown, in section 6's order."""
    return [TOOLS[name] for name in ROLE_TOOLS.get(role, ())]


_JSON_TYPES = {
    "string": (str,),
    "integer": (int,),
    "array": (list,),
    "object": (dict,),
}


def _check(schema: dict, value, where: str, top: bool = False) -> None:
    """`ToolError` unless `value` fits `schema`: the shapes `TOOLS` uses, and no more.

    Types, `enum`, an integer's `minimum` and `maximum`, array items and
    object properties, `required` and
    `additionalProperties: false`. A bool is not an integer, though Python
    says it is. What a value means — a known id, a valid description — is the
    writer's to check.
    """
    kind = schema.get("type")
    if kind is not None and (
        not isinstance(value, _JSON_TYPES[kind]) or isinstance(value, bool)
    ):
        raise ToolError(f"{where} must be {'an' if kind[0] in 'aeiou' else 'a'} {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ToolError(f"{where} must be one of {', '.join(map(str, schema['enum']))}, not {value!r}")
    if "minimum" in schema and value < schema["minimum"]:
        raise ToolError(f"{where} must be at least {schema['minimum']}")
    if "maximum" in schema and value > schema["maximum"]:
        raise ToolError(f"{where} must be at most {schema['maximum']}")
    if kind == "array" and "items" in schema:
        for index, item in enumerate(value):
            _check(schema["items"], item, f"{where}[{index}]")
    if kind == "object":
        properties = schema.get("properties", {})
        unknown = sorted(set(value) - set(properties), key=str)
        if unknown and schema.get("additionalProperties") is False:
            raise ToolError(f"{where} takes no argument {unknown[0]!r}")
        for key in schema.get("required", ()):
            if value.get(key) is None:
                raise ToolError(f"{where} needs {key!r}")
        for key, item in value.items():
            if item is not None and key in properties:
                _check(properties[key], item, key if top else f"{where}.{key}")


def _call(hive_dir: str, role: str, name: str, arguments: dict) -> dict:
    """Run one tool on checked arguments. `ToolError` for arguments the tool does not take.

    An argument given as null is one left out.
    """
    _check(TOOLS[name]["inputSchema"], arguments, name, top=True)
    arguments = {key: value for key, value in arguments.items() if value is not None}
    if name == "board_list":
        return board_list(hive_dir, **arguments)
    if name == "board_get":
        return board_get(hive_dir, arguments["task_id"])
    return _WRITERS[name](hive_dir, role, **arguments)


# --- the app ----------------------------------------------------------------


def create_app(config_path: str) -> Flask:
    """The service over the hive `config_path` names, loaded once as the api loads it."""
    cfg = load_config(config_path)
    hive_dir = cfg.hive_tasks_dir
    state_dir = cfg.state_dir
    # Read once, as the config is: rotating it is a restart (ADR 151, 5).
    actions_token = os.environ.get(ACTIONS_TOKEN_ENV, "")
    app = Flask(__name__)

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    @app.route("/mcp", methods=["GET", "DELETE"])
    def mcp_no_stream():
        # No server-initiated stream and no session to end: the spec's answer
        # for a server that offers neither is 405.
        return jsonify({"error": "this endpoint takes POST only"}), 405, {"Allow": "POST"}

    @app.post("/mcp")
    def mcp():
        # The spec's guard against DNS rebinding: a browser page always sends
        # an Origin, and no browser is a client of this service, so any Origin
        # is refused. Claude Code and the dispatcher's phases send none.
        if request.headers.get("Origin"):
            return _rpc_error(None, INVALID_REQUEST, "no browser origin may call this service", 403)
        role = request.args.get("role", "")
        if role not in KNOWN_ROLES:
            known = ", ".join(sorted(KNOWN_ROLES))
            return _rpc_error(
                None, INVALID_REQUEST, f"?role= must be one of {known}, not {role!r}", 400
            )
        unknown = sorted(set(request.args) - {"role"})
        if unknown:
            return _rpc_error(None, INVALID_REQUEST, f"unknown query parameter {unknown[0]!r}", 400)
        message = request.get_json(silent=True)
        if message is None:
            return _rpc_error(None, PARSE_ERROR, "the body is not JSON", 400)
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _rpc_error(None, INVALID_REQUEST, "one JSON-RPC 2.0 message per POST", 400)
        if "method" not in message:
            # A response to a request this server never sends.
            return "", 202
        if "id" not in message:
            # A notification (`notifications/initialized` and the rest):
            # accepted, nothing to answer.
            return "", 202
        return _dispatch(hive_dir, role, message)

    # The console's writes (section 6: "the console calls the same functions
    # over HTTP"), as the operator, whose role table they follow. The same
    # guards as `/mcp`: no browser Origin, no query; and a JSON object body.

    @app.post("/api/backlog")
    def http_propose():
        return _http(hive_dir, "backlog_propose", None, created_status=201)

    # The thread records (ADR 170): no quota is spent, so no bearer.

    @app.post("/api/threads")
    def http_thread_create():
        def make(body: dict):
            return thread_store.create(hive_dir, cfg, **body)

        return _thread_http(make, _THREAD_CREATE_FIELDS, 201)

    @app.patch("/api/threads/<thread_id>")
    def http_thread_update(thread_id: str):
        def make(body: dict):
            if not body:
                raise thread_store.ThreadRefused("nothing to change")
            return thread_store.update(hive_dir, cfg, thread_id, **body)

        return _thread_http(make, _THREAD_UPDATE_FIELDS, 200)

    @app.patch("/api/tasks/<task_id>")
    def http_edit(task_id: str):
        return _http(hive_dir, "task_edit", task_id)

    @app.post("/api/tasks/<task_id>/comments")
    def http_comment(task_id: str):
        return _http(hive_dir, "task_comment", task_id, created_status=201)

    @app.post("/api/tasks/<task_id>/accept")
    def http_accept(task_id: str):
        return _http(hive_dir, "backlog_accept", task_id)

    # The action queue's four creators (ADR 142, ADR 151, ADR 181): behind a bearer, and
    # the queue is made on the first create, never here.

    @app.post("/api/tasks/<task_id>/start")
    def http_start(task_id: str):
        def make(body: dict):
            unknown = sorted(set(body) - {"project", "note"})
            if unknown:
                raise actions.ActionRefused(400, f"unknown field {unknown[0]!r}")
            return actions.create(
                state_dir, "start", project=body.get("project"), task_id=task_id,
                note=body.get("note"), actor="operator",
                ready=lambda _project, tid: tid in task_store.ready_order(
                    task_store.read_all_tasks(hive_dir)
                ),
            )

        return _action(actions_token, make)

    @app.post("/api/actions/<action_id>/cancel")
    def http_cancel(action_id: str):
        def make(body: dict):
            if body:
                raise actions.ActionRefused(400, "a cancel takes an empty body")
            return actions.create(
                state_dir, "cancel", params={"target": action_id}, actor="operator"
            )

        return _action(actions_token, make)

    @app.post("/api/tasks/<task_id>/command-requests/<n>/verdict")
    def http_verdict(task_id: str, n: str):
        def make(body: dict):
            unknown = sorted(set(body) - {"project", "verdict", "comment", "prefix"})
            if unknown:
                raise actions.ActionRefused(400, f"unknown field {unknown[0]!r}")
            if not _POSITIVE_INT.fullmatch(n):
                raise actions.ActionRefused(
                    400, f"request number {n!r} is not an integer of at least 1"
                )
            # Whether request `n` exists and is still open is not read here:
            # `run_command_request` refuses it on claim and the action ends
            # failed with the reason (T-106).
            return actions.create(
                state_dir, "command_verdict", project=body.get("project"), task_id=task_id,
                params={
                    "n": int(n), "verdict": body.get("verdict"),
                    "comment": body.get("comment"), "prefix": body.get("prefix"),
                },
                actor="operator",
            )

        return _action(actions_token, make)

    @app.post("/api/threads/<thread_id>/turns")
    def http_turn(thread_id: str):
        def make(body: dict):
            unknown = sorted(set(body) - {"text"})
            if unknown:
                raise actions.ActionRefused(400, f"unknown field {unknown[0]!r}")
            text = body.get("text")
            if not isinstance(text, str) or not text.strip():
                raise actions.ActionRefused(400, "text must be a non-empty string")
            if len(text) > MAX_TURN_TEXT_CHARS:
                raise actions.ActionRefused(
                    400, f"text is longer than {MAX_TURN_TEXT_CHARS} characters"
                )
            try:
                thread = thread_store.read_thread(hive_dir, thread_id)
            except thread_store.NoThreadError as exc:
                raise actions.ActionRefused(404, str(exc)) from None
            except thread_store.UnreadableThreadError as exc:
                raise actions.ActionRefused(409, str(exc)) from None
            if thread.get("status") == "archived":
                raise actions.ActionRefused(409, f"thread {thread_id} is archived")
            return actions.create(
                state_dir, "turn", params={"thread": thread_id, "text": text},
                actor="operator",
            )

        return _action(actions_token, make)

    return app


#: A command request's number in the verdict route's path: 1-based, no sign.
_POSITIVE_INT = re.compile(r"[1-9][0-9]*")


def _bearer_ok(token: str) -> bool:
    scheme, _, presented = request.headers.get("Authorization", "").partition(" ")
    presented = presented.strip()
    if scheme.lower() != "bearer" or not presented:
        return False
    return hmac.compare_digest(presented.encode(), token.encode())


def _action(token: str, make):
    """One action route: ADR 151's guards in order, then `make(body)`'s record as 201.

    The token is never put in an answer or a log.
    """
    if request.headers.get("Origin"):
        return jsonify({"error": "no browser origin may call this service"}), 403
    if not token:
        return jsonify({"error": "the action routes are off: no BOARD_ACTIONS_TOKEN is set"}), 503
    if not _bearer_ok(token):
        return (
            jsonify({"error": "a bearer token is required"}), 401,
            {"WWW-Authenticate": "Bearer"},
        )
    if request.args:
        return jsonify({"error": f"unknown query parameter {sorted(request.args)[0]!r}"}), 400
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "the body is not a JSON object"}), 400
    try:
        record = make(body)
    except actions.ActionRefused as exc:
        return jsonify({"error": exc.reason}), exc.status
    except OSError as exc:
        return jsonify({"error": f"the action queue could not be written: {exc}"}), 503
    return jsonify(record), 201


#: The body fields of the thread routes and their JSON type (ADR 170, 7).
_THREAD_CREATE_FIELDS = {
    "title": str, "project": str, "account": str, "model": str, "effort": str,
    "task": str, "main": bool,
}
_THREAD_UPDATE_FIELDS = {
    "title": str, "account": str, "model": str, "effort": str, "status": str,
}


def _thread_http(make, fields: dict, ok_status: int):
    """One thread route: `_http`'s guards in order, then the body's fields, then `make(body)`."""
    if request.headers.get("Origin"):
        return jsonify({"error": "no browser origin may call this service"}), 403
    if request.args:
        return jsonify({"error": f"unknown query parameter {sorted(request.args)[0]!r}"}), 400
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "the body is not a JSON object"}), 400
    unknown = sorted(set(body) - set(fields))
    if unknown:
        return jsonify({"error": f"unknown field {unknown[0]!r}"}), 400
    body = {k: v for k, v in body.items() if v is not None}
    for key, value in body.items():
        if not isinstance(value, fields[key]):
            return jsonify({"error": f"{key} must be a JSON {fields[key].__name__}"}), 400
    try:
        record = make(body)
    except thread_store.NoThreadError as exc:
        return jsonify({"error": str(exc)}), 404
    except (thread_store.ThreadExistsError, thread_store.UnreadableThreadError) as exc:
        return jsonify({"error": str(exc)}), 409
    except thread_store.ThreadRefused as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"error": f"the thread could not be written: {exc}"}), 503
    return jsonify(record), ok_status


def _http(hive_dir: str, name: str, task_id: str | None, created_status: int = 200):
    """One console write as the operator: the tool's result, or `{"error"}` and 400 or 404.

    `created_status` answers a write that made something: a proposal with
    `created: true`, every comment. The task id is the path's, never the body's.
    """
    if request.headers.get("Origin"):
        return jsonify({"error": "no browser origin may call this service"}), 403
    if request.args:
        return jsonify({"error": f"unknown query parameter {sorted(request.args)[0]!r}"}), 400
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "the body is not a JSON object"}), 400
    if task_id is not None:
        if "task_id" in body:
            return jsonify({"error": "the task id is the path's; the body takes none"}), 400
        body = {"task_id": task_id, **body}
    try:
        result = _call(hive_dir, OPERATOR, name, body)
    except NotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ToolError as exc:
        return jsonify({"error": str(exc)}), 400
    made = name == "task_comment" or result.get("created") is True
    return jsonify(result), created_status if made else 200


def _dispatch(hive_dir: str, role: str, message: dict):
    msg_id = message["id"]
    method = message["method"]
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _rpc_error(msg_id, INVALID_PARAMS, "params must be an object")
    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[-1]
        return _rpc_result(
            msg_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method == "ping":
        return _rpc_result(msg_id, {})
    if method == "tools/list":
        return _rpc_result(msg_id, {"tools": tools_for(role)})
    if method == "tools/call":
        name = params.get("name")
        if name not in ROLE_TOOLS.get(role, ()):
            # Unlisted for this role is the same answer as unknown: the role
            # check is here, on every call, and not only in `tools/list`.
            return _rpc_error(msg_id, INVALID_PARAMS, f"no tool {name!r} for role {role}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _rpc_error(msg_id, INVALID_PARAMS, "arguments must be an object")
        try:
            result = _call(hive_dir, role, name, arguments)
        except ToolError as exc:
            return _rpc_result(
                msg_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True}
            )
        return _rpc_result(
            msg_id,
            {
                "content": [{"type": "text", "text": _dumps(result)}],
                "structuredContent": result,
                "isError": False,
            },
        )
    return _rpc_error(msg_id, METHOD_NOT_FOUND, f"no method {method!r}")


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _rpc_result(msg_id, result: dict):
    return jsonify({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _rpc_error(msg_id, code: int, message: str, status: int = 200):
    body = {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
    return jsonify(body), status


def main(environ=None) -> Flask:
    """The app built from the environment, or exit 2 when there is no config to read."""
    environ = os.environ if environ is None else environ
    config_path = environ.get("CONFIG_PATH", CONFIG_PATH)
    if not os.path.isfile(config_path):
        print(f"board-service: refusing to start: no config at {config_path}", file=sys.stderr)
        raise SystemExit(2)
    return create_app(config_path)


if __name__ == "__main__":
    main().run(host="0.0.0.0", port=PORT)
