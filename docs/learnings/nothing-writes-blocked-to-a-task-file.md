# `blocked` in a task file means a cycle ran a phase and stopped, not every blocked exit

**When it applies:** you are reading a task file's `status` to tell how a
cycle ended, a card or a plan tells you a `dispatcher/` function "updates the
task file", or you are writing a test that has to prove a run stopped blocked.

**Status:** unconfirmed — read off the code by T-028's arquitecto,
implementador and revisor, and re-read by the auditor; three handoffs agreeing
is one reading, not three. Amended twice, and the file keeps its first name.
T-029's auditor amended it when `tasks.mark_blocked` was added with no caller.
T-030's auditor rewrote the title and the two sections below when the
dispatcher began to call it: the entry used to say no run writes `blocked`,
which is no longer true.

## What is true

Since T-030 `run_task_cycle` in `dispatcher/dispatcher.py` calls
`tasks.mark_blocked`, through `_mark_blocked`, when it returns
`CycleOutcome.BLOCKED` after a phase of the run took the task's lock: a phase
that failed, the arquitecto's block, the rounds run out or the revisor's
block, the auditor failing. The file then reads `status: blocked` with no
owner, and `acquire_lock` takes a `blocked` task again as it takes a
`pending` one: running it again is the operator's decision to retry (the
user's ruling of 2026-10-11, `docs/business.md` *Task status*).

Not every blocked exit writes it. These leave the file as it was:

- `open_cycle` refused the task: no description, an unmigrated hive, an
  unmet dependency, a malformed epic. `run-task` exits `EXIT_BLOCKED`.
- The project's branch diverged, or the run met another owner's lock.
- A held cycle (`CycleOutcome.HELD`, `EXIT_HELD`).
- `run-phase`: `run_single_phase` never marks.
- A run that took the task after this one's last phase let go: an owner on
  the file is left alone.

So `status: blocked` says a cycle ran and stopped, and its absence does not
say the last run finished.

T-028's card said `_update_task_status` "moves the card and updates the task
file" and asked that it keep the second half. Its whole body was a call on the
board client. It was deleted with its call sites (`docs/decisions.md` ADR 59),
and task files behaved exactly as before.

## What to do

To learn how a cycle ended, read `CycleOutcome` or the exit code; read
`status == "blocked"` only for "a cycle of this task ran and stopped". A test
may assert it after a blocked run, with the caveat of
[a-faked-dispatch-phase-never-creates-the-task-file](a-faked-dispatch-phase-never-creates-the-task-file.md).
Code that reads `status` must tolerate `blocked` and `epic`. When a card
states what a function writes, open the function first
(`docs/learnings/an-in-cycle-plan-or-cards-code-claim-is-a-claim-to-grep.md`).
The table of return points is `docs/implementations/T-030.md` *Blocked:
which return points mark, and why*; the debt it closed is
`docs/debt/T-028-D3.md`.

## Evidence

T-028: `dispatcher/tasks.py` `acquire_lock` and `handoff`;
`docs/implementations/T-028.md` *Verified first-hand*, item 1. T-029:
`mark_blocked` and `acquire_lock` in `dispatcher/tasks.py`;
`docs/implementations/T-029.md` *The other writers*. T-030: `_mark_blocked`
and the local `blocked()` in `run_task_cycle` in `dispatcher/dispatcher.py`;
the tests from `test_a_cycle_that_ran_out_of_rounds_leaves_the_task_blocked`
to `test_a_single_phase_that_failed_does_not_mark_the_task_blocked` in
`tests/dispatcher/test_dispatcher.py`.
