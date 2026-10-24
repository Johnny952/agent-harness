# A lock guard runs after two reapers that clear its evidence

**When it applies:** you are adding a check that refuses or allows taking a
task's lock based on an account's `BUSY` state or the card's expired
heartbeat — in `tasks.acquire_lock`, `dispatch_phase` or anywhere after the
cycle has opened.

**Status:** unconfirmed — proposed by T-168's arquitecto, implementador and
revisor; the two call sites were checked against `dispatcher/dispatcher.py` by
T-168's auditor.

## The trap

Two reapers run before any lock is taken, and each deletes what a guard would
read:

- `open_cycle` calls `reap_expired_locks`, which clears every `in_progress`
  card whose heartbeat is past `heartbeat_ttl_seconds` — after it the lock you
  meant to inspect has no owner and no heartbeat.
- `pick_idle_account` calls `reap_stale_busy_accounts` before the claim, which
  releases an account whose holder's card heartbeat expired — after it no
  account is `BUSY` with the task any more.

A guard written only in `acquire_lock` therefore never fires in a real cycle,
although a unit test that calls `acquire_lock` directly passes.

## Rule

Teach both reapers the same predicate as the guard (T-168 did it with
`_phase_may_be_running` and `orphan_holders`, `docs/decisions.md` ADR 411),
and test the guard through `reap_expired_locks` and
`reap_stale_busy_accounts` as well as `acquire_lock`.

## Evidence

`dispatcher/dispatcher.py` `open_cycle` calls `reap_expired_locks(cfg)`;
`pick_idle_account` calls `reap_stale_busy_accounts(cfg)`.
