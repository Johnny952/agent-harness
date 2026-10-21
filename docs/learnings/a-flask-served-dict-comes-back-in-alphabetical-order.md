# A dict the board service serves comes back in alphabetical order

**When it applies:** a console screen lists the keys of an object one of the
board service's Flask routes serves (`GET /api/push/settings`'s `kinds`, say)
and the order on screen is meant to carry meaning.

**Status:** unconfirmed — reported by T-120's arquitecto and implementador.

## Symptom

The kinds arrive as `awaiting_review, blocked, command_request, conflict, …`,
never in the order `dispatcher/notifications.py` lists them.

## Why

`board_service/` sets no `sort_keys`, and Flask's default JSON provider sorts a
dict's keys when it serialises, so whatever order the server built the dict in
is lost on the wire.

## Rule

Never rely on a served object's key order; order on the console's side, as
`front/src/routes/notifications.tsx`'s `orderKinds` puts the four kinds that
need the person first (ADR 246).

## Evidence

T-120 arquitecto: `grep -rn sort_keys board_service` finds nothing; the
auditor re-ran it on 2026-10-21 with the same empty result.
