# The resolve-conflict 201 is the bare action record

**When it applies:** you read the answer to `POST
/api/tasks/<id>/resolve-conflict` in `front/` or a test, and are modelling it
on approve or request changes.

**Status:** unconfirmed. Proposed by every phase of T-133-S2; checked against
`board_service/app.py` by its auditor.

## Symptom

Approve and request changes answer `{verdict, action}` (and a refusal of theirs
carries `verdict` beside `error`); a resolve conflict answers the queued action
record itself, so `res.action.id` is `undefined`.

## Why

`http_resolve_conflict` goes through `_action`, which returns
`jsonify(record), 201` and refuses with `{error}` only. The two review routes go
through `_review_action`, which records a verdict first and wraps both (ADR
285: a resolution is neither an approve nor a request for changes).

## Rule

Type it as `ActionRecord` (`front/src/lib/api/client.ts` `resolveConflict`),
read `res.id`, and expect no `verdict` on a refusal.

## Evidence

`board_service/app.py` `_action` and `http_resolve_conflict`; `docs/decisions.md`
ADR 288; the toast in `front/src/routes/approvals.tsx` `Verdicts` `sendResolve`.
