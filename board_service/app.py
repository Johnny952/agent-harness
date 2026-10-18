"""The board service, reads only: `board_list` and `board_get` over MCP.

Step 4 of `docs/plans/board-service.md`. The plan makes this process the
board's write side and the agents' MCP server (section 5); this first part
serves the two read tools of section 6 and writes nothing, anywhere. The write
tools come with step 5, on this same process (`docs/decisions.md` ADR 124).

Everything here reads through `dispatcher/tasks.py` and
`dispatcher/context_transfer.py`, so there is no second copy of the task file
or the description format: a parser this service needs and does not have is
one to make reachable in `dispatcher/`. It holds no docker socket and no git
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
import json
import os
import sys

import yaml
from flask import Flask, jsonify, request

from dispatcher import context_transfer
from dispatcher import tasks as task_store
from dispatcher.config import PHASE_ROLES, load_config

CONFIG_PATH = "/app/config.yaml"
PORT = 8792

#: The operator's session; every other caller is a phase, named by its role.
OPERATOR = "operator"
#: Every role the service knows. A role outside it is a misspelt URL, answered
#: 400, rather than a role that silently gets no tools.
KNOWN_ROLES = frozenset(PHASE_ROLES) | {OPERATOR}

#: Section 6's table, the read rows. A role not named here gets no tools: the
#: implementador, revisor, auditor and integrador "get no tools at first".
ROLE_TOOLS: dict[str, tuple[str, ...]] = {
    OPERATOR: ("board_list", "board_get"),
    "arquitecto": ("board_list", "board_get"),
}

#: The backlog's statuses (section 4), as `dispatcher/tasks.py` defines them
#: (ADR 127). Nothing here writes them yet (step 5b); a task with any other
#: status is on the board.
BACKLOG_STATUSES = frozenset(task_store.BACKLOG_STATUSES)
PLACES = ("backlog", "board")

#: The most task files one `board_list` reads. The api's `MAX_ROADMAP_TASKS`
#: number and reason: the hive holds about seventy on 2026-10-18, and the clamp
#: is taken on the sorted ids before any file is opened.
MAX_LIST_TASKS = 2000
#: The longest title `board_list` serves when it falls back to the Goal's first
#: line. A row is meant to be scanned, and the whole ask is one `board_get` away.
TITLE_CHARS = 100
#: The longest text filter. A filter is a word or a phrase.
MAX_TEXT_CHARS = 200

#: What a task's files can fail with; the api's `_UNREADABLE`, for its reasons.
_UNREADABLE = (OSError, TypeError, ValueError, KeyError, yaml.YAMLError)

COMMENTS_FILE = "comments.jsonl"

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


# --- the tools --------------------------------------------------------------


def place_of(status: object) -> str:
    """`backlog` or `board`, from a task's status alone (section 4)."""
    return "backlog" if status in BACKLOG_STATUSES else "board"


def _title(frontmatter: dict, description: str | None) -> str | None:
    """A frontmatter `title` when there is one, else the Goal's first line, else None.

    No task file carries a title yet: section 3 gives an epic one and step 5's
    `task_edit` writes it. Until then the Goal's first non-blank line stands in,
    cut to `TITLE_CHARS`, so a list of ids reads as a list of asks (ADR 125).
    """
    title = frontmatter.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    if description is None:
        return None
    try:
        goal = task_store.parse_description(description).goal
    except task_store.DescriptionError:
        return None
    for line in goal.splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= TITLE_CHARS else line[: TITLE_CHARS - 1] + "…"
    return None


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
            }
            problems = task_store.field_problems(task)
        except _UNREADABLE as exc:
            warnings.append(f"{path}: unreadable task file: {exc}")
            continue
        # Served as the file has them, and named: a hand edit got them wrong,
        # and `open_cycle` refuses the task until it is put right.
        warnings.extend(f"{path}: {problem}" for problem in problems)
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
    return {"items": items, "warnings": warnings}


def board_get(hive_dir: str, task_id: str) -> dict:
    """Section 6's `board_get`: one task or epic's frontmatter, description and comment count.

    The description is served verbatim, as `read_description` returns it, and
    `null` when the task has none. The comment count is the lines of
    `comments.jsonl`, 0 when the file is absent; nothing writes that file yet
    (step 5). Raises `ToolError` for an id that names no task. Never writes.
    """
    if not isinstance(task_id, str) or not context_transfer._is_single_path_component(task_id):
        raise ToolError(f"{task_id!r} is not a task id")
    if task_store.is_draft_id(task_id):
        raise ToolError(f"{task_id!r} names drafts, not a task")
    path = context_transfer.task_file_path(hive_dir, task_id)
    if not os.path.isfile(path):
        raise ToolError(f"no task {task_id}")
    try:
        context_transfer.read_task_file(path)
        frontmatter = _frontmatter(path)
        description = task_store.read_description(hive_dir, task_id)
    except _UNREADABLE as exc:
        raise ToolError(f"{path}: unreadable task file: {exc}") from exc
    warnings: list[str] = []
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
    """Non-blank lines of the task's `comments.jsonl`: 0 when absent, None when unreadable.

    Counted, not parsed: a count is what section 6 serves, and the comments
    themselves are the file a phase is pointed to (section 2).
    """
    path = os.path.join(
        os.path.dirname(task_store.description_path(hive_dir, task_id)), COMMENTS_FILE
    )
    try:
        with open(path, "rb") as fh:
            return sum(1 for line in fh if line.strip())
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


# --- what MCP is told about the tools ---------------------------------------

_STRING = {"type": "string"}

TOOLS: dict[str, dict] = {
    "board_list": {
        "name": "board_list",
        "description": (
            "List the harness's tasks and epics: id, title, status, place, epic, merged, "
            "priority, source. "
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
}


def tools_for(role: str) -> list[dict]:
    """The tools this role is shown, in section 6's order."""
    return [TOOLS[name] for name in ROLE_TOOLS.get(role, ())]


def _call(hive_dir: str, name: str, arguments: dict) -> dict:
    """Run one tool on checked arguments. `ToolError` for arguments the tool does not take."""
    schema = TOOLS[name]["inputSchema"]
    unknown = sorted(set(arguments) - set(schema["properties"]))
    if unknown:
        raise ToolError(f"{name} takes no argument {unknown[0]!r}")
    for key in schema.get("required", ()):
        if key not in arguments:
            raise ToolError(f"{name} needs {key!r}")
    for key, value in arguments.items():
        if value is not None and not isinstance(value, str):
            raise ToolError(f"{key} must be a string")
    if name == "board_list":
        return board_list(hive_dir, **arguments)
    return board_get(hive_dir, arguments["task_id"])


# --- the app ----------------------------------------------------------------


def create_app(config_path: str) -> Flask:
    """The service over the hive `config_path` names, loaded once as the api loads it."""
    cfg = load_config(config_path)
    hive_dir = cfg.hive_tasks_dir
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

    return app


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
            result = _call(hive_dir, name, arguments)
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
