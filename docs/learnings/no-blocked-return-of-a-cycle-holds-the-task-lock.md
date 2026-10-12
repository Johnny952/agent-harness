# No BLOCKED return of `run_task_cycle` holds the task's lock

**When it applies:** you are adding code that runs at the end of a cycle and
writes the task file (a status, a mark, a comment), or a card asks for
something "when the cycle held its lock".

**Status:** unconfirmed — proposed by T-030's arquitecto and implementador in
the same words, which is one reading; checked by the auditor against
`dispatch_phase` and `run_phase` in `dispatcher/dispatcher.py`.

## What is true

Every phase lets go of the lock before `run_phase` returns. A phase that
landed releases it in `tasks.handoff`. One that did not is released inside
`dispatch_phase` by `tasks.release_stale_lock`: on a rate-limit refusal, on a
failed exec and on an exception after the lock was taken. So at any return of
`run_task_cycle` the task file has `owner: null`, whether this run ever took
the lock or not, and "the cycle held its lock" cannot be read off the file.

It is remembered instead. `CycleContext.took_lock` is set in `run_phase` when
`dispatch_phase` returned a result that is not `no_account`: that result is
its only return before `tasks.acquire_lock`, so anything else proves the lock
was taken. A `LockHeldError` sets `foreign_lock` and leaves `took_lock` as it
was.

An owner on the file at the end of a cycle is therefore another run, one that
took the task after this run's last phase let go. `tasks.mark_blocked`
releases the lock it finds, so calling it there would release that run's.

## What to do

Read `ctx.took_lock` and `ctx.foreign_lock` to know whether the task was this
run's, and read the file's `owner` and write only when it is None.
`_mark_blocked` in `dispatcher/dispatcher.py` is the model: both checks, a
missing file is nothing to do, and it never raises.

## Evidence

T-030: `dispatch_phase` (the `no_account` return, `tasks.acquire_lock`, the
three `tasks.release_stale_lock` calls), `run_phase` and `_mark_blocked` in
`dispatcher/dispatcher.py`;
`test_a_cycle_that_bounced_off_another_owners_lock_does_not_mark_it_blocked`
in `tests/dispatcher/test_dispatcher.py`; `docs/implementations/T-030.md`
*Verified first-hand*, difference 1.
