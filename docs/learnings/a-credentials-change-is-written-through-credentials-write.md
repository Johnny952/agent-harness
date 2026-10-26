# A credentials change reaches the snapshot through `credentials.write`

**When it applies:** your code ends something that changes a credential —
an `account_login` that ends `done`, a `git_token` write, a `keygen` — and
the console's credentials snapshot must show it before the worker's next
300-second pass.

**Status:** unconfirmed. Proposed by T-181's arquitecto and implementador
(one source: both read it off ADR 462 decision 1); checked against
`dispatcher/credentials.py` `write` by the auditor.

## Symptom

There is no error. Without a call, the snapshot shows the old state for up to
`CREDENTIALS_SNAPSHOT_SECONDS` (300 s), and a login's `logged_in_at` is never
set at all: the timer's `write(cfg)` only carries the previous value forward.

## Why

`logged_in_at` has one way in: the `logins` argument. The timer pass in
`dispatcher/worker.py` `_write_credentials` passes none.

## Rule

Call `credentials.write(cfg, logins={account: logged_in_at})` after an
`account_login` ends `done`, and `credentials.write(cfg)` after a `git_token`
or `keygen` ends (`docs/decisions.md` ADR 462 decision 1). `write` takes the
module lock, so a handler thread may call it while the timer runs.

## Evidence

`docs/decisions.md` ADR 462 decision 1; `docs/implementations/T-181.md`
*For task 3*; `tests/dispatcher/test_credentials.py`
`test_logged_in_at_is_carried_and_replaced`.
