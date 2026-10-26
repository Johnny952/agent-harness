# A board POST route with an id is one actionRoutes entry

**When it applies:** you add a `POST /api/<route>/<id>/<tail>` write of the
board-service to `front/src/lib/api/forward.ts`.

**Status:** unconfirmed — proposed by T-186-S1's arquitecto and implementador;
the auditor checked it against `boardTarget` and `forwardBoard`.

## Why

`boardTarget` loops over its `actionRoutes` table (`pattern`, `route`, `tail`,
`what`, `action`); a hit gets the id decoded and re-encoded (a `%2f` stays one
segment) and an undecodable `%zz` answers 404, for free. `forwardBoard` keys
on `Resolution.action`: it rebuilds the body only for `start`, `cancel`,
`resolve_conflict`, `approve` and `request_changes`, uses the larger cap only
for `turn`, and names the 503 by action for `project`, `push` and
`credential`. Any other action crosses with the body unchanged.

## Rule

Add one `actionRoutes` entry and, if the 503 should name its own routes, a new
`Resolution.action` value added to the 503's list; then bump the "twenty-…"
write count everywhere (see
[forward-ts-counts-api-reads-and-board-writes-with-one-word](forward-ts-counts-api-reads-and-board-writes-with-one-word.md)).
A route whose body holds a secret must not take an action that rebuilds the
body (ADR 483).

## Evidence

T-186-S1 added `/api/accounts/<name>/login` and `/api/actions/<id>/login-code`
this way with `action: "credential"`; `forward.test.ts` "the board-service
credential routes" covers the re-encoding and the `%zz` 404.
