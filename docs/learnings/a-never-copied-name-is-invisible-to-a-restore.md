# A NEVER_COPIED name is invisible to a restore too

**When it applies:** you add a name to `dispatcher/backup.py` `NEVER_COPIED`
(or `NOT_RECORDS`), or you reason about what a restore does to a live file or
directory under the hive or `<state_dir>`.

**Status:** unconfirmed — proposed by T-180's arquitecto, read against the
code by its auditor.

## Symptom

None: a design fact, not an error.

## Why

`dispatcher/backup.py` `_sync` checks `_skipped(name)` on the live side before
anything else, and `_skipped` matches the basename against `NEVER_COPIED +
NOT_RECORDS` at any depth. So a skipped name is neither copied into a backup
nor set aside, overwritten or deleted by a restore: the live one survives
untouched. That is why T-180 added `secrets` (ADR 456 decision 2, ADR 459): a
waiting action secret is never copied, and a restore never moves one aside.
The other side of the same match: any directory named `secrets` anywhere
under the hive or `<state_dir>` is now never backed up.

## Rule

A `NEVER_COPIED` entry means "never in a backup and never touched by a
restore", by basename at any depth; pick a name no record directory shares.

## Evidence

`dispatcher/backup.py` `_sync` and `_skipped`, read in the T-180 worktree at
the auditor phase; `tests/dispatcher/test_backup.py`
`test_a_waiting_action_secret_is_not_in_a_copy`.
