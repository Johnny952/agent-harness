# A new task-file writer joins `_WRITERS`, and must refuse a legacy file before anything else

**When it applies:** you are adding a function to `dispatcher/tasks.py` that
writes a task file, or changing the order of the checks in one that does.

**Status:** unconfirmed — proposed by T-029's implementador and revisor, and
checked by the auditor against the test file.

## What is true

`tests/dispatcher/test_tasks.py` keeps a dict, `_WRITERS`, of one call per
task-file writer. Two parametrized tests run over it:
`test_every_task_file_writer_refuses_an_unmigrated_file_and_leaves_it_alone`
and
`test_the_writers_refusal_is_per_file_a_legacy_neighbour_does_not_stop_it`.
One entry in the dict buys both for a new writer. Nothing adds the entry for
you: a writer left out of `_WRITERS` passes the suite untested for the
refusal.

The first test expects `UnmigratedTaskError`. A writer with checks of its own
has to refuse the legacy file before those checks can raise something else,
which is why `file_split` calls `_refuse_unmigrated` on the parent and on
every sub-task id before it collects a single `SplitError` problem, and why an
unmigrated file is not reported as a `SplitError`.

A third test, `test_no_module_under_dispatcher_but_tasks_calls_write_task_file`,
walks `dispatcher/`: the writer ends in `tasks._write_task_file`, never in
`context_transfer.write_task_file`.

## What to do

Add the writer to `_WRITERS` in the same change, end it in
`_write_task_file`, and put the unmigrated check first. If the writer takes
more than one task id, check every id it may write.

One exception, added by T-030's auditor: `_WRITERS` calls each writer on the
file it rewrites, so a writer that rewrites another task's file does not fit
the table. `close_epic` is called with the child and writes the parent; it
stays out and has a test of its own,
`test_close_epic_refuses_an_unmigrated_parent_like_every_writer`. It also
refuses only at the write, after every other condition held, which is the
order this entry warns against and is harmless there because nothing is
written before it.

A second exception, added by T-122's auditor: a writer that refuses a plain
migrated file (`mark_closed`, which needs a `merged` block) fails the
neighbour test and stays out too; see
[a-writer-that-refuses-unmerged-state-cannot-join-writers](a-writer-that-refuses-unmerged-state-cannot-join-writers.md).

## Evidence

T-029: `_WRITERS` in `tests/dispatcher/test_tasks.py`, which gained
`file_split`, `mark_merged` and `mark_blocked`; `file_split` in
`dispatcher/tasks.py`; `docs/implementations/T-029.md` *Ruled out*, second
item. T-030: `close_epic` in `dispatcher/tasks.py` and its test above;
`docs/implementations/T-030.md` *Tests*.
