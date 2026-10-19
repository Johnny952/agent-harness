# A turn's size limit is the route's, not the queue's

**When it applies:** you are writing a second creator of `turn` actions (the
thread's MCP dispatch tool of `docs/plans/main-thread.md` step 6, say), or
calling `actions.create(state_dir, "turn", ...)` from anywhere other than
`board_service/app.py` `http_turn`.

**Status:** unconfirmed — every phase of T-112 named it as a risk; checked
against the code by its auditor.

## Symptom

`actions.create(state_dir, "turn", params={"thread": tid, "text": "x" *
500_000})` succeeds: nothing in the queue refuses an oversized message, and
the worker then passes it as one argv element of `docker exec`, which Linux
refuses past 128 KiB.

## Why

`dispatcher/actions.py` `_validate_turn` only asks for a non-empty string.
The 20,000-character limit, `MAX_TURN_TEXT_CHARS`, lives in
`board_service/app.py` and is enforced by `http_turn` alone, and so are the
refusals of an unknown field, an all-blank text and an archived thread
(`docs/decisions.md` ADR 181, 3 and 4). ADR 181 ruled out moving it into
`_validate_turn` so a record already queued is never failed by a later limit.

## Rule

A new turn creator applies the same checks with the same constant — import
it or move it to `dispatcher/` with both callers — never a second literal.

## Evidence

`board_service/app.py` `MAX_TURN_TEXT_CHARS`, `http_turn`;
`dispatcher/actions.py` `_validate_turn`; ADR 181, *Ruled out*.
