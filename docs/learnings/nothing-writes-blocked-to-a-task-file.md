# No cycle writes `blocked` to a task file yet: the writer exists and nothing calls it

**When it applies:** you are reading a task file's `status` to tell how a
cycle ended, a card or a plan tells you a `dispatcher/` function "updates the
task file", or you are writing a test that has to prove a run stopped blocked.

**Status:** unconfirmed — read off the code by T-028's arquitecto,
implementador and revisor, and re-read by the auditor; three handoffs agreeing
is one reading, not three. Amended by T-029's auditor, who re-read
`dispatcher/tasks.py` after that task added the writer: the title and the
first section were "nothing writes `blocked`, and nothing reads it", which
stopped being true of the module and is still true of a run.

## What is true

Since T-029 `dispatcher/tasks.py` has `mark_blocked`, which sets
`status: blocked` and releases the lock, and `acquire_lock` moves a `blocked`
task to `in_progress` as it moves a `pending` one: running a blocked task
again is the operator's decision to retry (the user's ruling of 2026-10-11,
`docs/business.md` *Task status*). Nothing calls `mark_blocked`.
`dispatcher/dispatcher.py` still ends a blocked cycle without touching the
task file, and its one `tasks.handoff` call passes `done` for the final phase
and `pending` for every other.

So on a real run `status` is still written by `acquire_lock` (`in_progress`)
and `handoff` (`done` or `pending`) only, and a cycle that stops blocked
leaves two records: the log line and the exit code. A `blocked` in a task
file today was put there by a test or by hand.

T-028's card said `_update_task_status` "moves the card and updates the task
file" and asked that it keep the second half. Its whole body was a call on the
board client. It was deleted with its call sites (`docs/decisions.md` ADR 59),
and task files behaved exactly as before.

With the fake board gone, a test has two kinds of evidence for how a run
ended and no third: the cycle's own return, `CycleOutcome.FINISHED`, `HELD`
or `BLOCKED`, and the task file's `status` (plus `run_single_phase`'s result).

## What to do

Until the dispatcher calls `mark_blocked`, do not branch on
`status == "blocked"` to learn how a cycle ended; no run makes it true.
Assert a blocked run through `CycleOutcome.BLOCKED`. Code that reads `status`
must already tolerate `blocked` and `epic`, since the module can write both.
When a card states what a function writes, open the function first
(`docs/learnings/an-in-cycle-plan-or-cards-code-claim-is-a-claim-to-grep.md`).
The missing call is `docs/debt/T-028-D3.md`, and `docs/plans/board-service.md`
sections 4 and 6 own it.

## Evidence

T-028: `dispatcher/tasks.py` `acquire_lock` and `handoff`; the one
`tasks.handoff` call in `dispatcher/dispatcher.py`;
`docs/implementations/T-028.md` *Verified first-hand*, item 1. T-029:
`mark_blocked` and `acquire_lock` in `dispatcher/tasks.py`; a grep for
`blocked` over `dispatcher/dispatcher.py`, `dispatcher/operator.py`,
`dispatcher/context_transfer.py` and `observability/api/app.py` finds
`CycleOutcome.BLOCKED`, `handoff.blocked` and log text, and no task-file
status; `docs/implementations/T-029.md` *The other writers*.
