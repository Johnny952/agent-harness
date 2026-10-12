# A test that fakes `dispatch_phase` never creates the task file

**When it applies:** you are writing a cycle test in
`tests/dispatcher/test_dispatcher.py` that asserts on the task file after a
phase failed, or reading one to learn what a real run leaves on disk.

**Status:** unconfirmed — two halves, one from T-030's implementador and one
from its revisor, joined and checked by the auditor against `dispatch_phase`
and `run_phase` in `dispatcher/dispatcher.py`.

## What is true

In a real run the task file exists from the first phase:
`tasks.acquire_lock`, called inside `dispatch_phase`, creates it. So a first
phase that fails leaves a file, `run_phase` has set `took_lock`, and
`_mark_blocked` writes `status: blocked` on it.

The cycle tests replace `dispatch_phase` with a fake that returns a
`DispatchResult`. No lock is taken, so the file appears only when `run_phase`
reaches `tasks.handoff` for a phase that succeeded. A fake first phase that
fails leaves no task file at all, and `_mark_blocked` meets
`FileNotFoundError` and writes nothing.

`test_a_blocked_cycle_with_no_task_file_creates_none` pins that second
behaviour. It proves `_mark_blocked` does not create a file; it does not
describe a real run, where the file is there and is marked.

## What to do

To assert `blocked` after a failed phase under a faked `dispatch_phase`, let
an earlier phase succeed first, as
`test_a_phase_that_failed_after_one_that_landed_leaves_the_task_blocked`
does, or write the task file in the test's setup. Do not read "no task file"
out of such a test as what production does.

## Evidence

T-030: `tasks.acquire_lock` in `dispatch_phase`, the `took_lock` assignment
in `run_phase` and `_mark_blocked` in `dispatcher/dispatcher.py`; the two
tests named above in `tests/dispatcher/test_dispatcher.py`.
