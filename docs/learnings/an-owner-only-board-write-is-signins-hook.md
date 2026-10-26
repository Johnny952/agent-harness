# An owner-only board write is signin's hook, not the route's

**When it applies:** you are adding a write route to `board_service/app.py`
that only the owner may call, and are about to check the session's role
inside the view.

**Status:** unconfirmed — proposed by T-183's arquitecto and implementador
(one source), checked by its auditor against `board_service/signin.py`.

## Symptom

None: this is a check you do not need to write. A reviewer's session write is
answered before the view runs:

```
403 {"error": "this needs the owner's role"}
```

## Why

`signin.py`'s `before_request` hook `check_session` refuses any write method
under `/api/` from a signed-in non-owner unless `request.endpoint` is in
`REVIEWER_WRITES` (today `session_delete` and `http_comment`), after the CSRF
check, and audits it as `role_refused`. A bearer-only call with no session
cookie skips the role check entirely; the route's own bearer guard
(`_action`) is what stops that one.

## Rule

Leave the role check out of the view and never add an owner-only endpoint to
`REVIEWER_WRITES`; pin it with a reviewer-session 403 test, as
`tests/board_service/test_credentials_routes.py`
`test_a_reviewer_may_not_use_the_routes` does.

## Evidence

Read `board_service/signin.py` `check_session` in the T-183 worktree at the
auditor phase; `python3 -m pytest tests/board_service` — 631 passed, 4 skipped.
