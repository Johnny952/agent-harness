# A writer that refuses a plain migrated file cannot join `_WRITERS`

**When it applies:** you are adding a task-file writer to `dispatcher/tasks.py`
that raises on a migrated file lacking some state (no `merged` block, no lock,
no file), and are about to add it to `_WRITERS` in
`tests/dispatcher/test_tasks.py` as
[a-new-task-file-writer-joins-writers-in-test-tasks](a-new-task-file-writer-joins-writers-in-test-tasks.md)
says.

**Status:** unconfirmed — proposed by T-122's arquitecto and implementador, and
checked by the auditor against the test file.

## What is true

`_WRITERS` feeds two parametrized tests. The second,
`test_the_writers_refusal_is_per_file_a_legacy_neighbour_does_not_stop_it`,
calls each writer on `task-1`, a bare migrated file with no `merged` block and
no lock, and asserts the call rewrote it. A writer that refuses that file —
`mark_closed` raises `NotMergedError` there — fails it, though it refuses an
unmigrated file correctly.

## What to do

Leave such a writer out of `_WRITERS` and give it its own unmigrated-refusal
test, as `mark_closed` has in
`test_mark_closed_refuses_an_unmigrated_task_before_its_own_errors`. Still call
`_refuse_unmigrated` before the writer's own checks.

## Evidence

T-122: `mark_closed` and `NotMergedError` in `dispatcher/tasks.py`; the
`mark_closed` section of `tests/dispatcher/test_tasks.py`;
`docs/implementations/T-122.md` *Why this way*; `docs/decisions.md` ADR 255.
