# A backup from inside the worker is refused while it runs any cycle

**When it applies:** you call `dispatcher/backup.py` `backup` (or anything
else that takes every `ProjectLock`) from code running in the worker process —
a handler in `dispatcher/worker.py` `HANDLERS` — and expect it to succeed.

**Status:** unconfirmed. T-153's arquitecto and implementador both reported
it, which is one source; the auditor checked it against the code.

## Why

`backup` takes every project lock with a fresh `os.open` and a non-blocking
`flock` (`dispatcher/project_lock.py` `ProjectLock.take`, via
`dispatcher/backup.py` `_Locks`). The worker holds a project lock in-process
for each start group it runs (ADR 200, ADR 306). A `flock` belongs to the open
file description, so the worker's own lock refuses the worker's own backup
with `ProjectBusy`, exactly as another process's would (see
`a-second-flock-in-one-process-stands-in-for-another-process.md`).

## Rule

Treat an in-worker copy as best effort: catch `ProjectBusy` and go on, as
`backup.before_merge` does (ADR 342); never make a merge or start wait on it.

## Evidence

`tests/dispatcher/test_backup.py`
`test_before_merge_under_a_held_project_lock_says_busy_and_writes_nothing`
holds a `ProjectLock` in the test process and gets the `no backup before the
merge:` line; green in T-153's auditor run of the backup, worker, compose and
approve tests.
