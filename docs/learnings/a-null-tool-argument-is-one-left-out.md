# A null tool argument is one left out, so a clearable field clears with `""`

**When it applies:** you add a field to a board service tool (`task_edit`,
say) that a caller must be able to clear, and are about to make `null` the
clearing value.

**Status:** unconfirmed — proposed by T-178-S1's arquitecto, implementador
and revisor, checked against the tree by T-178-S1's auditor.

## What happened

T-178-S1 added a clearable `plan` field to `task_edit` (`docs/decisions.md`
ADR 450). `board_service/app.py` `_call` runs `_check`, then drops every
argument whose value is `None` before calling the writer ("An argument given
as null is one left out"). A `{"plan": null}` call reaches
`dispatcher/tasks.py` `edit_task` as no `plan` at all, so it changes nothing.

## Rule

Clear a string field with `""` (a list with `[]`): let the writer's
validation accept the empty value only on edit, as `_plan_problems`
`allow_empty` does, and test the clear over MCP
(`tests/board_service/test_writes.py`
`test_plan_over_mcp_propose_edit_and_clear`).
