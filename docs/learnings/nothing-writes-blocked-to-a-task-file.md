# Nothing writes `blocked` to a task file, and nothing ever did

**When it applies:** you are reading a task file's `status` to tell how a
cycle ended, a card or a plan tells you a `dispatcher/` function "updates the
task file", or you are writing a test that has to prove a run stopped blocked.

**Status:** unconfirmed — read off the code by T-028's arquitecto,
implementador and revisor, and re-read by the auditor; three handoffs agreeing
is one reading, not three.

## What is true

A task file's `status` has two writers, both in `dispatcher/tasks.py`:
`acquire_lock` moves `pending` to `in_progress`, and `handoff` writes the
status it is given, which `dispatcher/dispatcher.py` passes as `done` for the
final phase and `pending` for every other. No code path writes `blocked`, and
none reads it.

T-028's card said `_update_task_status` "moves the card and updates the task
file" and asked that it keep the second half. Its whole body was a call on the
board client. It was deleted with its call sites (`docs/decisions.md` ADR 59),
and task files behave exactly as before.

So a cycle that stops blocked leaves two records only: the log line and the
exit code. With the fake board gone, a test has two kinds of evidence for how
a run ended and no third: the cycle's own return, `CycleOutcome.FINISHED`,
`HELD` or `BLOCKED`, and the task file's `status` (plus `run_single_phase`'s
result).

## What to do

Do not branch on `status == "blocked"`; it is never true. Assert a blocked run
through `CycleOutcome.BLOCKED`. When a card states what a function writes,
open the function first
(`docs/learnings/an-in-cycle-plan-or-cards-code-claim-is-a-claim-to-grep.md`).
The missing write is `docs/debt/T-028-D3.md`, and `docs/plans/board-service.md`
sections 4 and 6 own it.

## Evidence

T-028: `dispatcher/tasks.py` `acquire_lock` and `handoff`; the one
`tasks.handoff` call in `dispatcher/dispatcher.py`; a grep for `blocked` over
`dispatcher/tasks.py`, `dispatcher/context_transfer.py`,
`dispatcher/operator.py` and `observability/api/app.py` prints nothing;
`docs/implementations/T-028.md` *Verified first-hand*, item 1.
