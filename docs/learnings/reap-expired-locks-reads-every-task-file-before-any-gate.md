# `reap_expired_locks` reads every task file before any gate in `open_cycle`

**When it applies:** you are adding a check to `open_cycle` that reads a task
file and deciding what a file that will not parse does there, or a run died
with a traceback out of `read_task_file` and you are looking for which task
caused it.

**Status:** unconfirmed — proposed by T-030's revisor; checked by the auditor
against `open_cycle` and `reap_expired_locks` in `dispatcher/dispatcher.py`.

## What is true

`open_cycle` calls `tasks.unmigrated_ids` and then `reap_expired_locks`
before it looks at the task it was asked to run. `reap_expired_locks` calls
`context_transfer.read_task_file` on every `.md` file in the hive tasks dir
with no `try` around it. So one task file that will not parse, any task's,
raises out of `open_cycle` for every run, before the description is read,
before the dependency and epic gate and before any lock.

The comment T-030 added above the gate in `open_cycle` says a file that will
not parse "raises from the first phase's lock, as it always did". That is
the wrong place: the file being run was already read by the reaper, so it
raised there. The behaviour is the same, an exception and no refusal line;
only the comment's location is off.

The gate's own readers do not raise. `tasks.unmet_dependencies` and
`tasks.epic_problems` go through `_read_for_check` in `dispatcher/tasks.py`,
which turns a missing or unreadable file into a line of the report. That
branch is reachable only for a file that became unreadable after the reaper
passed it.

## What to do

In a new `open_cycle` check, catch `FileNotFoundError` for the task being run
and nothing wider: a file that does not parse cannot reach you. When a run
dies in `read_task_file`, look at every file in the hive tasks dir, not only
the task on the command line.

## Evidence

T-030: `open_cycle` and `reap_expired_locks` in `dispatcher/dispatcher.py`;
`list_task_ids` and `read_task_file` in `dispatcher/context_transfer.py`;
`_read_for_check` in `dispatcher/tasks.py`. Read off the code; no test feeds
`open_cycle` a file that will not parse.
