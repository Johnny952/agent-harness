# dispatcher/vibe_kanban_client.py
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import logging
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from dispatcher.config import VibeKanbanConfig

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class KanbanIssue:
    """One issue as the board reports it.

    `issue_id` is a server-assigned uuid, not a name this harness can mint:
    every id in Vibe Kanban's MCP schema is `format: "uuid"`. `simple_id` is
    the short human handle the board shows ("VK-12") and is the one field an
    operator can search on, so it is worth keeping even though the harness
    keys on the uuid.
    """

    issue_id: str
    title: str
    status: str | None = None
    simple_id: str | None = None


class NullKanbanClient:
    """The board for a harness that was never given one.

    `vibe_kanban` is optional in config.yaml, and the dispatcher treats the
    board as a visibility aid rather than dispatch state. With no block
    configured this stands in for the real client so every call site stays
    unconditional — and, unlike a real client pointed at nothing, it fails at
    nothing, so a run without a board logs no warning per phase.
    """

    enabled = False

    def list_issues(self, **filters: object) -> list[KanbanIssue]:
        return []

    def get_issue(self, issue_id: str) -> KanbanIssue | None:
        return None

    def create_issue(self, title: str, description: str | None = None) -> str | None:
        return None

    def set_status(self, issue_id: str, status: str) -> None:
        return None


class VibeKanbanClient:
    """Thin wrapper around the Vibe Kanban MCP server.

    The server is spawned as a subprocess and spoken to over stdio: it serves
    no SSE endpoint, so there is no URL to point at and nothing here reaches
    the network on its own account. What it talks to *on the far side* is the
    operator's business — Vibe Kanban's own cloud, for a `vibe-kanban mcp`
    that is signed in — which is also why this client is optional.

    One subprocess per call, on purpose: the dispatcher's phases are minutes
    apart, a long-lived session would have to outlive the event loop each call
    runs in, and a board update that fails is already non-fatal upstream.
    """

    enabled = True

    def __init__(self, config: VibeKanbanConfig):
        self.config = config

    # --- MCP plumbing -----------------------------------------------------

    def _call(self, tool_name: str, arguments: dict):
        return asyncio.run(self._call_async(tool_name, arguments))

    async def _call_async(self, tool_name: str, arguments: dict):
        command, *args = self.config.command
        server = StdioServerParameters(command=command, args=args)
        # The server's own stderr goes nowhere: `npx` chatters on every spawn
        # and this is a side channel, not the dispatcher's output. A call that
        # actually fails raises, and the caller logs that.
        with open(os.devnull, "w") as errlog:
            async with stdio_client(server, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return _unwrap(tool_name, await session.call_tool(tool_name, arguments))

    # --- issues -----------------------------------------------------------

    def list_issues(self, **filters: object) -> list[KanbanIssue]:
        """List the project's issues, passing through `list_issues`' filters.

        Useful ones: `status`, `search`, `simple_id`, `limit`. This is how an
        operator finds the uuid that `run-task --kanban-issue-id` wants.
        """
        arguments = {k: v for k, v in filters.items() if v is not None}
        arguments.update(self._project())
        return [_issue(item) for item in _items(self._call("list_issues", arguments))]

    def get_issue(self, issue_id: str) -> KanbanIssue | None:
        payload = self._call("get_issue", {"issue_id": issue_id})
        return _issue(payload) if isinstance(payload, dict) else None

    def create_issue(self, title: str, description: str | None = None) -> str:
        arguments: dict[str, object] = {"title": title}
        if description is not None:
            arguments["description"] = description
        arguments.update(self._project())
        return _issue_id(self._call("create_issue", arguments))

    def set_status(self, issue_id: str, status: str) -> None:
        """Move an issue to whatever the board calls this dispatcher status.

        `status` is the dispatcher's own vocabulary — "blocked", "done", or
        "in_progress:<role>", of which only the prefix carries over; the board
        has no room for the role. An unmapped status is skipped rather than
        sent, because `update_issue` rejects any name the project doesn't have.
        """
        name = self.config.status_map.get(status.split(":", 1)[0])
        if name is None:
            logger.warning("no kanban status mapped for %r; leaving issue %s alone", status, issue_id)
            return
        self._call("update_issue", {"issue_id": issue_id, "status": name})

    def _project(self) -> dict[str, str]:
        # Optional in the schema: the server infers it when it runs inside a
        # workspace already linked to a remote project.
        return {"project_id": self.config.project_id} if self.config.project_id else {}


#: Either board a run can be given. The dispatcher takes one of these and
#: calls it unconditionally; which one it got is `enabled`.
KanbanClient = VibeKanbanClient | NullKanbanClient


# --- response parsing -----------------------------------------------------
#
# Vibe Kanban's *request* schemas came from its own advertised tool list, so
# the arguments above are exact. Its responses are not described anywhere the
# server hands out, and reading one back needs an account on Vibe Kanban's
# cloud, so the readers below accept the shapes an MCP server can legally
# return rather than pinning one that has never been seen.


def _unwrap(tool_name: str, result):
    """The result of a tool call, or an exception carrying what went wrong.

    A tool that fails answers with `is_error` and its complaint as text rather
    than raising, so without this a rejected status name would look like a
    successful update.
    """
    if getattr(result, "is_error", False):
        raise RuntimeError(f"{tool_name} failed: {_text_of(result) or 'no reason given'}")
    return _payload(result)


def _text_of(result) -> str:
    return " ".join(
        text for block in (result.content or []) if (text := getattr(block, "text", None))
    )


def _payload(result):
    structured = getattr(result, "structured_content", None) or getattr(
        result, "structuredContent", None
    )
    if structured is not None:
        return structured
    text = _text_of(result)
    if not text:
        return None
    with contextlib.suppress(json.JSONDecodeError):
        return json.loads(text)
    return text


def _items(payload) -> list[dict]:
    if isinstance(payload, dict):
        # A structured result is an object even when it holds a list.
        for key in ("issues", "items", "result", "data"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _issue(item: dict) -> KanbanIssue:
    status = item.get("status")
    if isinstance(status, dict):  # a status object rather than its name
        status = status.get("name")
    return KanbanIssue(
        issue_id=_issue_id(item),
        title=item.get("title", ""),
        status=status if isinstance(status, str) else None,
        simple_id=item.get("simple_id"),
    )


def _issue_id(payload) -> str:
    if isinstance(payload, dict):
        for key in ("issue_id", "id"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
        for key in ("issue", "result", "data"):
            if isinstance(payload.get(key), dict):
                return _issue_id(payload[key])
    raise RuntimeError(f"no issue id in Vibe Kanban's reply: {payload!r}")
