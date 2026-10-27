# The browser never reaches the board service: the console relays everything

**When it applies:** you are building something in `board_service/` that
depends on what the browser sent — the `harness_session` cookie, the
`X-CSRF-Token` header, the client's address — or a console screen that needs
the board service to see one of them.

**Status:** unconfirmed — found by T-146's arquitecto, checked by its auditor
with the grep below.

## Symptom

No error. Every board service route answers 403 to a request carrying a
browser `Origin`, sign-in's own routes included:

```
grep -n "Origin" board_service/app.py board_service/signin.py
```

finds the refusal in `create_app`'s guards and in `signin.install`'s
`route_guards`, and `tests/board_service/test_signin.py` asserts the 403.

## Why

The console's server half (`front/src/lib/api/forward.ts`) is the board
service's only HTTP client: the browser talks to the console, which forwards.
So the board service sees the console's request, not the browser's. A cookie,
a CSRF header or an `X-Forwarded-For` the browser sent reaches the board
service only if the forward copies it.

## Rule

Whatever the board service must see of the browser, the console's forward has
to relay explicitly; until step 2 of `docs/plans/server-deployment.md` does,
`request.remote_addr` is the console and no request carries a session
(`docs/decisions.md` ADR 324; `docs/debt/T-146-D1.md`).

Since T-193-S2 (ADR 522) exactly `POST`, `GET` and `DELETE /api/session`
relay the `harness_session` cookie, the `X-CSRF-Token` and the client
address (`forward.ts` `forwardSession`); every other route still relays
none of them.

## Evidence

The grep above in T-146's worktree, and ADR 324's routes and *Consequences*.
