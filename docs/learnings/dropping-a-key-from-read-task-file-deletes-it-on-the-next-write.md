# Dropping a key from `read_task_file` deletes it from every task file the dispatcher next writes

**When it applies:** you are removing, renaming or moving a frontmatter key
that `dispatcher/context_transfer.py`'s `read_task_file` carries into
`TaskFile`, or adding a caller of one of that module's task-file writers.

**Status:** unconfirmed — read off the code by T-026's arquitecto and
implementador and re-read by the auditor; no run lost a key, because T-026
guarded every entry point before the key went.

## What is true

Every task-file writer in `context_transfer` has one shape: read the file into
a `TaskFile`, change one field, hand the whole thing to `write_task_file`.
`acquire_lock`, `refresh_heartbeat`, `release_stale_lock`, `handoff`,
`set_kanban_issue_id` and `set_resolved_debt` all do it. `write_task_file`
writes only what `TaskFile` holds, so a key the reader no longer carries is
not preserved by any of them: it is gone after the first write, with no error
and no log line.

It reaches files the running task never named. `dispatcher/dispatcher.py`'s
`reap_expired_locks` walks every task id in the hive at the top of each cycle
and calls `release_stale_lock` on each expired one, so one cycle for T-x can
rewrite T-y's file.

## What to do

Before the reader stops carrying a key, decide where the value goes and move
it first: T-026 moved `description` into its own file with `dispatch migrate`
and made `open_cycle` and the `run-task`, `run-phase` and `release-account`
verbs refuse the whole hive while `tasks.unmigrated_ids` returns anything.
That guard is by enumeration, not structural: `docs/debt/T-026-D1.md` is the
row, and a new path to one of the six writers has to add the check itself
until the writes move onto `dispatcher/tasks.py`.

The same shape cuts the other way for a new key: a writer that builds a fresh
`TaskFile` rather than reading the old one drops every optional key it does
not set.

## Evidence

T-026: `dispatcher/context_transfer.py` (the six writers and
`write_task_file`), `dispatcher/dispatcher.py` `reap_expired_locks` and
`open_cycle`, `dispatcher/cli.py` `_require_migrated`,
`docs/implementations/T-026.md` *Verified first-hand* item 3 and *Decisions*
item 4.
