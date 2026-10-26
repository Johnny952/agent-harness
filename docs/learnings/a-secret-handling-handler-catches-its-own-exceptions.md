# A secret-handling handler catches its own exceptions

**When it applies:** you write or change a worker handler in
`dispatcher/worker.py` (an entry in `HANDLERS`) whose locals hold a secret —
a token, a login code, anything taken with `actions.take_secret`.

**Status:** unconfirmed — proposed by T-182's arquitecto, implementador and
revisor (one source); checked by its auditor against `Worker._guarded`.

## Symptom

No error was hit. An exception escaping such a handler would put its message
into the action's `error` and its traceback into the worker log, shaped as:

```
worker: <kind> <id> raised
```

## Why

`Worker._guarded` catches any exception a handler raises, calls
`logger.exception` (the whole traceback, with any secret in the message or a
frame's repr) and finishes the action `failed` with
`error=_one_line(f"{type(exc).__name__}: {exc}")`. An `OSError` or
`ValueError` built around a secret would carry it into both.

## Rule

Wrap the whole handler body in its own `try`, finish the action yourself with a
fixed sentence or the exception's type name only, and log with `logger.error`
naming the type, as `dispatcher/worker.py` `_git_token` and `_account_login` do
(ADR 465).

## Evidence

Read `Worker._guarded` in `dispatcher/worker.py` in the T-182 worktree at the
auditor phase; `tests/dispatcher/test_worker_credentials.py` asserts the token
and code appear in no `error`, `result` or log line.
