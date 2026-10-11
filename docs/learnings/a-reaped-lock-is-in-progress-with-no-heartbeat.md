# A reaped lock is `in_progress` with no heartbeat, and `is_lock_expired` calls that not expired

**When it applies:** you are writing a check that decides whether a task's
lock is live (a refusal, a reaper, a status view), in `dispatcher/tasks.py`
or anywhere that reads a task file's `status`, `owner` and `heartbeat`.

**Status:** unconfirmed — reported by T-027's implementador and checked by the
auditor against `dispatcher/tasks.py`.

## What is true

`tasks.release_stale_lock` clears `owner` and `heartbeat` but leaves `status`
alone, so a reaped task stays `in_progress` with no heartbeat. And
`tasks.is_lock_expired` returns `False` when `heartbeat` is `None`. A check
written as "`in_progress` and not expired" therefore calls every reaped lock
live, and would refuse forever on a task nobody holds.

## What to do

Test the heartbeat first: live means `in_progress`, a heartbeat present, and
not expired. `tasks._lock_is_live` (T-027, used by `tasks.migrate`) is that
check; call it rather than writing another.

## Evidence

T-027: `dispatcher/tasks.py` `release_stale_lock`, `is_lock_expired` and
`_lock_is_live`; `docs/debt/T-026-D2.md` *How it was resolved*.
