# `queue.account_open` can probe every parked account

**When it applies:** you are calling `dispatcher/queue.py` `account_open` (it
was `_account_open` before T-100) from a loop, a poll or a request path.

**Status:** unconfirmed — proposed by T-100's arquitecto and implementador,
checked against the code by its auditor.

## Why

`account_open` returns true when `dispatcher.dispatcher` `pick_idle_account`
finds an idle account. Otherwise it calls `_recheck_cooling_accounts`, which
runs a fresh `/usage` probe on every account parked in `PRE_COOLDOWN` or
`COOLING_DOWN`. With every account parked, each call is one probe per account.

## Rule

Never call it unpaced on a short loop: pace a false answer, as
`dispatcher/worker.py` `Worker._account_open` does, closing the check for
`account_poll` (`queue.WAIT_POLL_SECONDS`) — `docs/decisions.md` ADR 148,
decision 5.

## Evidence

T-100 (2026-10-19): read `queue.account_open` and
`dispatcher.py` `_recheck_cooling_accounts`; the worker's 2s loop would
otherwise probe each tick while no account is open.
