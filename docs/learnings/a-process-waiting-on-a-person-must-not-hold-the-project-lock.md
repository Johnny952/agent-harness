# A process waiting on a person must not hold the project lock

**When it applies:** you write dispatcher code that holds a `ProjectLock`
(ADR 200) and then waits for something a person does on the same project — a
merge, an approve, a request for changes.

**Status:** unconfirmed. T-189's arquitecto and implementador both reported it,
which is one source; the auditor read `dispatcher/backup.py` `before_merge`
against it.

## Why

Both merge paths (`merge-task` in `dispatcher/cli.py` and the worker's
`_approve` in `dispatcher/worker.py`) call `backup.before_merge` without
`shared`, and the backup takes every project lock non-blocking: any lock held
by another process is `ProjectBusy`, so the merge goes on with "no backup
before the merge" (see
[a-backup-from-inside-the-worker-is-refused-while-it-runs-any-cycle](a-backup-from-inside-the-worker-is-refused-while-it-runs-any-cycle.md)).
A worker *request changes* on the awaited task runs a cycle that needs the
same project lock, so a waiter holding it would deadlock the very change it
waits for.

## Rule

Release the project lock before a wait on a person and take it again through
`dispatcher/queue.py` `_take_project` when the wait ends, as `queue.run` does
around `_wait_for_merges` (ADR 498 decision 2).

## Evidence

`docs/decisions.md` ADR 498, *Context* and *Ruled out*;
`tests/dispatcher/test_queue.py` `test_the_wait_releases_the_project_lock`
(green in the auditor's full `python3 -m pytest -q` run, 4955 passed).
