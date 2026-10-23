# `ToolError` lives in `board_service/app.py`, not `dispatcher/board_mcp.py`

**When it applies:** you write a test or caller that catches the board
service's `ToolError` (a `task_start` refusal, a tool's bad argument).

**Status:** unconfirmed — proposed by T-162's implementador, checked against
the tree by T-162's auditor.

## What happened

`board_service/app.py` imports `OPERATOR`, `ROLE_TOOLS` and `THREAD` from
`dispatcher.board_mcp`, which makes that module look like the home of the
tool error too. It is not: `ToolError` (and its subclass `NotFoundError`) is
defined in `board_service/app.py`.

## Rule

Import it as `from board_service.app import ToolError`.
