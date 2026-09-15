# dispatcher/vibe_kanban_client.py
from __future__ import annotations

import asyncio
import dataclasses
import json

from mcp import ClientSession
from mcp.client.sse import sse_client


@dataclasses.dataclass
class KanbanTask:
    id: str
    project: str
    title: str
    status: str


class VibeKanbanClient:
    """Thin wrapper around the Vibe Kanban MCP server.

    Vibe Kanban's MCP server is never exposed beyond this host: compose
    publishes it on 127.0.0.1 only, and containers reach it over the private
    `ia_harness_net` bridge. `mcp_url` is caller-supplied — from inside a
    container "http://vibe-kanban:9100/sse", from the host itself
    "http://127.0.0.1:9100/sse". This client never hardcodes or defaults to a
    public or Tailnet-routable address.
    """

    def __init__(self, mcp_url: str):
        self.mcp_url = mcp_url

    def _call(self, tool_name: str, arguments: dict):
        return asyncio.run(self._call_async(tool_name, arguments))

    async def _call_async(self, tool_name: str, arguments: dict):
        async with sse_client(self.mcp_url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                text = result.content[0].text if result.content else "{}"
                return json.loads(text)

    def list_tasks(self, project: str | None = None) -> list[KanbanTask]:
        arguments = {"project": project} if project else {}
        items = self._call("list_tasks", arguments)
        return [
            KanbanTask(id=i["id"], project=i["project"], title=i["title"], status=i["status"])
            for i in items
        ]

    def create_task(self, project: str, title: str, description: str) -> str:
        data = self._call("create_task", {"project": project, "title": title, "description": description})
        return data["id"]

    def update_task_status(self, task_id: str, status: str) -> None:
        self._call("update_task", {"id": task_id, "status": status})
