# Request changes writes no outcome comment

**When it applies:** you want to show, on `/approvals` or anywhere else, what
became of a *request changes* the console queued, and are looking for a
dispatcher comment like approve's `approve at <commit>:`.

**Status:** unconfirmed. Proposed by T-129's arquitecto and implementador;
checked against `dispatcher/worker.py` `_request_changes` by its auditor.

## Why

`_request_changes` (ADR 272) moves the task from `awaiting_review` to `pending`
through `tasks.reopen_for_changes`, runs the cycle from the arquitecto and
finishes the action record with `{commit, outcome, blocked_reason}`. It never
calls `tasks.add_comment`, so the task's comments carry nothing a filter like
`front/src/lib/review.ts` `approveOutcomes` could pick up.

## Rule

Read the outcome as the task leaving the review lane and coming back to
`awaiting_review` at a new tip (ADR 276), or from the action record's `result`
under `GET /api/actions`; do not write a filter for a comment that is never
written.

## Evidence

`dispatcher/worker.py` `_request_changes`; `docs/decisions.md` ADR 276
*Context*; `docs/implementations/T-129.md` *Not done*.
