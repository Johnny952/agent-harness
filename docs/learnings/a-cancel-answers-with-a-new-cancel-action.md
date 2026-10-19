# `POST /api/actions/<id>/cancel` answers with a new cancel action, not the start

**When it applies:** you are reading the answer to a cancel in `front/` or
in a test, or wording what a successful cancel means on a screen.

**Status:** unconfirmed — proposed by every phase of T-107; checked against
the route by its auditor.

## Symptom

The 201 body is a record with `kind: "cancel"`, its own `id` and
`params.target` set to the start's id; the start itself is still `pending` or
`claimed` until the worker takes the cancel.

## Why

`board_service/app.py` `http_cancel` only calls `actions.create` with
kind `cancel`: the board-service queues the cancel, and the worker carries it
out on a later claim. A non-empty body is refused with a 400.

## Rule

Treat success as "a cancel was queued" (the Queue screen's toast says so,
`docs/decisions.md` ADR 163), send `{}`, and read the start's outcome from the
next queue poll, never from the cancel's answer.

## Evidence

`board_service/app.py` `http_cancel`; `front/src/lib/api/client.ts`
`cancelAction`; the test "posts an empty body to the action's cancel path"
in `front/src/routes/-queue.test.tsx`.
