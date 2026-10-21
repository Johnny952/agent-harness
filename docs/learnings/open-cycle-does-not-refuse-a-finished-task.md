# `open_cycle` does not refuse a `done` or `awaiting_review` task

**When it applies:** you are reasoning about what happens when a finished
task (`done`, `awaiting_review`, `conflict`) is run again, or you are
trusting `docs/plans/board-service.md`'s line that `open_cycle` "refuses any
status other than `pending`".

**Status:** unconfirmed — proposed by T-118-S1's arquitecto and implementador,
and checked by its auditor against the two functions.

## What is true

`tasks.dispatch_problems` in `dispatcher/tasks.py` refuses on status only for
`BACKLOG_STATUSES` (`proposed`, `deferred`, `dropped`); the rest of what it
returns is `field_problems`. `open_cycle` in `dispatcher/dispatcher.py` adds
refusals for a draft id, an unmigrated hive, an `epic`, unmet dependencies and
a malformed epic. Nothing refuses a task because it is `done`,
`awaiting_review` or `conflict`: `dispatch run` on one opens a new cycle.

The `board-service.md` sentence (its backlog section, "Nothing in the backlog
may be dispatched") is the plan, not the code.

## What to do

Do not count on the dispatcher to stop a re-run of a finished task. If your
task needs that refusal, add it to `dispatch_problems` with an ADR; if it
needs a re-run to work, you do not have to lift a refusal first.

## Evidence

`dispatch_problems` and `is_backlog` in `dispatcher/tasks.py`; the docstring
and the checks of `open_cycle` in `dispatcher/dispatcher.py`, read by T-118-S1's
auditor.
