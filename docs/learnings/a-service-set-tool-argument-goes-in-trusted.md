# A service-set tool argument goes in `_call`'s `trusted`, not the schema

**When it applies:** you want the board service to hand a tool an argument
that only the service may set (who the signed-in person is, say), and are
about to add it to the tool's `inputSchema` or read it from the request body.

**Status:** unconfirmed — proposed by T-163's implementador, checked against
the tree by T-163's auditor.

## What happened

T-163 needed `backlog_propose` to record the signed-in person as
`proposed_by` (`docs/decisions.md` ADR 390). `board_service/app.py` `_call`
runs `_check` against the tool's `inputSchema` first, then merges its
`trusted` dict into the arguments. `_http` passes
`trusted={"proposed_by": _person()}`, so the tool gets it while the schema
never names it: a body or a phase's MCP call that names `proposed_by` is
refused as an argument the tool does not take
(`tests/board_service/test_person_actor.py`
`test_a_body_naming_proposed_by_is_refused`).

## Rule

Pass a service-set argument through `_call(..., trusted={...})` and keep it
out of `TOOLS[name]["inputSchema"]`; the tool function takes it as a keyword
with a `None` default.
