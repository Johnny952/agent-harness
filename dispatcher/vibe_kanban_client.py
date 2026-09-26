# dispatcher/vibe_kanban_client.py
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import datetime
import json
import logging
import os
import tempfile
import uuid
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from dispatcher.config import LocalBoardConfig, VibeKanbanConfig

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class KanbanIssue:
    """One issue as the board reports it.

    `issue_id` is a uuid either way, because every id in Vibe Kanban's MCP
    schema is `format: "uuid"` and `dispatcher/cli.py` rejects a
    `--kanban-issue-id` that does not parse as one. `VibeKanbanClient` is
    handed it by the server; `LocalBoardClient` mints it.

    `simple_id` is the remote board's short human handle ("VK-12"), worth
    keeping even though the harness keys on the uuid. A local card has none:
    `LocalBoardClient` leaves it `None` and `CARD_FIELDS` does not store it,
    so `list_issues(simple_id=...)` is an unknown filter there rather than a
    search that quietly matches nothing.
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


#: What one stored card holds, and therefore the only keys `list_issues` will
#: filter on. `simple_id` is not among them: a display handle is the UI's
#: problem and it can number by `created_at`.
CARD_FIELDS = frozenset({"issue_id", "title", "description", "status", "created_at"})

#: A card's status before any phase has moved it. The dispatcher's own
#: vocabulary, like every status this board stores — `_update_task_status`
#: sends "in_progress:<role>", "blocked" and "done", and they are written down
#: whole, role included. There is no board here to rename a column, so the one
#: dimension a generic kanban flattens is the one this keeps.
INITIAL_CARD_STATUS = "pending"

_CARD_SUFFIX = ".json"


class LocalBoardClient:
    """A board that is a directory, for a harness with nowhere to put cards.

    The same four methods as `VibeKanbanClient` with no service behind them:
    one JSON document per issue under `local_board.dir`, written atomically
    the way `state_machine.py` writes account state. It is the source of truth
    for nothing — `.hive/tasks/*.md` still is — so what it holds is an index a
    later phase can read rather than dispatch state a run depends on.

    Two differences from the remote client, both deliberate. Ids are minted
    here rather than server-assigned, as `uuid4`, because `cli.py` rejects a
    `--kanban-issue-id` that is not a uuid and a short id would mean relaxing
    that. And `set_status` on an id this board has never heard of raises
    instead of warning: a remote board could legitimately be out of sync, but
    local storage that has forgotten a card the task file still points at is a
    bug, and every call site already wraps this in try/except and logs.
    """

    enabled = True

    def __init__(self, config: LocalBoardConfig):
        self.config = config

    # --- issues -----------------------------------------------------------

    def list_issues(self, **filters: object) -> list[KanbanIssue]:
        """Every stored issue whose fields equal the filters given.

        An unknown filter key raises rather than being ignored: answering
        "filter by `assignee`" with every issue on the board is the failure
        mode that costs the caller silently. A filter set to None is dropped,
        as the Vibe Kanban client drops it, so `status=None` reads as "any".
        """
        unknown = sorted(set(filters) - CARD_FIELDS)
        if unknown:
            raise ValueError(
                f"local board has no issue field {unknown[0]!r} to filter on; "
                "stored fields are " + ", ".join(sorted(CARD_FIELDS))
            )
        wanted = {key: value for key, value in filters.items() if value is not None}
        return [
            _card_issue(card)
            for card in self._cards()
            if all(card.get(key) == value for key, value in wanted.items())
        ]

    def get_issue(self, issue_id: str) -> KanbanIssue | None:
        """One issue, or None. A lookup: it does not raise."""
        card = self._read_card(issue_id)
        return _card_issue(card) if card is not None else None

    def create_issue(self, title: str, description: str | None = None) -> str:
        card = {
            "issue_id": str(uuid.uuid4()),
            "title": title,
            "description": description,
            "status": INITIAL_CARD_STATUS,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        self._write_card(card)
        return card["issue_id"]

    def set_status(self, issue_id: str, status: str) -> None:
        """Move a stored card to a dispatcher status, verbatim."""
        card = self._read_card(issue_id)
        if card is None:
            raise LookupError(
                f"no issue {issue_id} on the local board at {self.config.dir}"
            )
        self._write_card({**card, "status": status})

    # --- storage ----------------------------------------------------------

    def _card_path(self, issue_id: str) -> str | None:
        """Where one issue's document lives, or None for an unusable id.

        The id arrives from a task file's frontmatter, so it is text some
        phase wrote: one with a separator in it would name a file outside the
        board directory, and that is not a card, it is a bug with reach.
        """
        name = f"{issue_id}{_CARD_SUFFIX}"
        if not issue_id or os.path.basename(name) != name:
            # Rejecting it silently would leave the reach — a `kanban_issue_id`
            # that tried to name a file elsewhere — looking exactly like a card
            # the board never had.
            logger.warning("local board: %r cannot name a card in this board", issue_id)
            return None
        return os.path.join(self.config.dir, name)

    def _read_card(self, issue_id: str) -> dict | None:
        path = self._card_path(issue_id)
        return self._read_path(path) if path is not None else None

    def _read_path(self, path: str) -> dict | None:
        try:
            data = json.loads(Path(path).read_text())
        except FileNotFoundError:
            return None  # an id with no card; the caller decides what that means
        except (OSError, ValueError) as exc:
            # Writes here are atomic, so a document that will not parse came
            # from outside this client. Worth saying out loud, not worth
            # turning a lookup into an exception. `ValueError` and not
            # `json.JSONDecodeError` (which is one) so that an id no path can
            # hold — a YAML escape leaves "embedded null byte" reachable from a
            # task file — is answered with "no such issue" rather than raised.
            logger.warning("local board: ignoring unreadable card %s: %s", path, exc)
            return None
        if isinstance(data, dict) and isinstance(data.get("issue_id"), str):
            return data
        # Parsed, but not a card. Same story as an unreadable one, and dropping
        # it without a line leaves a reader counting cards no way to find out
        # why the count is short.
        logger.warning("local board: ignoring card %s with no string issue_id", path)
        return None

    def _cards(self) -> list[dict]:
        """Every readable card, oldest first.

        A missing directory is a board with no issues, not an error: the
        directory is created by the first write.
        """
        try:
            names = sorted(os.listdir(self.config.dir))
        except FileNotFoundError:
            return []
        cards = [
            card
            for name in names
            if name.endswith(_CARD_SUFFIX) and not name.startswith(".")
            if (card := self._read_path(os.path.join(self.config.dir, name))) is not None
        ]
        cards.sort(key=lambda card: (card.get("created_at") or "", card["issue_id"]))
        return cards

    def _write_card(self, card: dict) -> None:
        """Replace one card's document, atomically.

        A temp file in the target directory and then `os.replace`, the way
        `state_machine.py` writes account state: a half-written card is not a
        state this harness has to reason about, and a second client reading the
        directory at the same moment sees either the old document or the new
        one.
        """
        directory = self.config.dir
        Path(directory).mkdir(parents=True, exist_ok=True)
        path = os.path.join(directory, f"{card['issue_id']}{_CARD_SUFFIX}")
        # Dotted prefix and a .tmp suffix so a write interrupted between the
        # temp file and the replace leaves something `_cards` skips rather
        # than an issue nobody created.
        tmp = tempfile.NamedTemporaryFile(
            mode="w", dir=directory, prefix=".card-", suffix=".tmp", delete=False
        )
        try:
            tmp.write(json.dumps(card))
        finally:
            tmp.close()
        os.replace(tmp.name, path)


def _card_issue(card: dict) -> KanbanIssue:
    status = card.get("status")
    return KanbanIssue(
        issue_id=card["issue_id"],
        title=card.get("title") or "",
        status=status if isinstance(status, str) else None,
        # No short handle in this phase: there is no board rendering one.
        simple_id=None,
    )


#: Any board a run can be given. The dispatcher takes one of these and calls
#: it unconditionally; which one it got is `enabled`.
KanbanClient = VibeKanbanClient | LocalBoardClient | NullKanbanClient


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
