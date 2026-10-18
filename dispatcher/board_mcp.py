"""The board service's role table, and the `--mcp-config` a phase gets from it.

The table lives here and not in `board_service/` because both sides need it:
the service checks a call against it, and the dispatcher reads it to decide
which phases get the server at all and which tool names to allow
(`docs/plans/board-service.md` section 6 and step 7, `docs/decisions.md`
ADR 128). `board_service` imports `dispatcher`, so the dispatcher cannot
import the service back; one copy here means the two can never disagree.

Nothing here imports the rest of the package, so `dispatcher.config` can read
`DEFAULT_URL` from it without a cycle.
"""
from __future__ import annotations

import json
import urllib.parse

#: The name the phase's CLI knows the server by. A tool reaches the model as
#: `mcp__<server>__<tool>`, so this is also the middle of every allowed name.
SERVER_NAME = "board"

#: The operator's Claude Code session. Not a phase: the dispatcher never
#: launches it, so nothing here writes a config for it.
OPERATOR = "operator"

#: Which tools each role may list and call (section 6). A role not named gets
#: none, and the dispatcher gives its phases no server either.
ROLE_TOOLS: dict[str, tuple[str, ...]] = {
    OPERATOR: ("board_list", "board_get"),
    "arquitecto": ("board_list", "board_get"),
}

#: Where the phases reach the service: by its compose name on ia_harness_net,
#: the network the agent containers are on, not the loopback port the
#: operator's session uses. `board_mcp_url` in the config overrides it.
DEFAULT_URL = "http://board-service:8792/mcp"


def allowed_tools(role: str) -> list[str]:
    """The `--allowed-tools` names for `role`'s board tools, empty for none."""
    return [f"mcp__{SERVER_NAME}__{tool}" for tool in ROLE_TOOLS.get(role, ())]


def mcp_config(url: str, role: str) -> str:
    """The `--mcp-config` value for a phase of `role`, as one compact JSON string.

    The role goes in the query, which is the only thing the service reads it
    from; the URL is expected to carry no query of its own, since the service
    refuses any parameter but `role`.
    """
    query = urllib.parse.urlencode({"role": role})
    server = {"type": "http", "url": f"{url}?{query}"}
    return json.dumps({"mcpServers": {SERVER_NAME: server}}, separators=(",", ":"))
