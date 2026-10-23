# A restore in a test sets aside the empty `actions/` dirs its own worker lock created

**When it applies:** you are writing a test that runs `backup.restore` and
want to assert that nothing was set aside, on a fixture state that has no
`actions/tmp`, `actions/claimed` or `actions/done` when the copy is taken.

**Status:** unconfirmed — proposed by T-156's implementador with the wrong
cause (it named `actions/worker.lock`); T-156's auditor measured the real one.

## Symptom

`result.asides` lists the state root's `.pre-restore-<stamp>/` after a
restore of an unchanged tree, so `assert result.asides == []` fails.

## Why

`dispatcher/backup.py` `restore` takes `worker.acquire` before it syncs, and
`dispatcher/worker.py` `acquire` calls `dispatcher/actions.py` `ensure_dirs`,
which creates `actions/tmp`, `pending`, `claimed` and `done`. The copy, taken
from a fixture that lacked them, has no such names, so `_sync` moves the empty
directories aside. `actions/worker.lock` itself is not the cause: `*.lock` is
in `NOT_RECORDS`, so `_skipped` leaves it in place. A real state has those
directories before any backup, so production restores do not see this.

## Rule

Assert on the hive's aside (or on files, as `_tree` lists only files), not on
`result.asides == []`; or create the four `actions/` dirs in the fixture.

## Evidence

T-156 auditor, 2026-10-23: a throwaway test outside the tree (backup then
restore of a state holding only `actions/pending/a.json`) printed
`ASIDES ['…/state/.pre-restore-20261022T040000Z']` and that aside held only
the empty `actions/claimed`, `actions/done`, `actions/tmp`, while
`actions/worker.lock` stayed live.
