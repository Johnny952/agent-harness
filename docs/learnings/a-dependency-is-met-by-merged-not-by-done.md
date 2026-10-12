# A dependency is met by a `merged` block on its task file, not by `status: done`

**When it applies:** you are deciding whether a task may start because of its
`depends_on`, writing the gate that holds a sub-task, or setting up a test
hive in which one task waits for another.

**Status:** unconfirmed — proposed by T-029's implementador and revisor, and
checked by the auditor against the function. Amended by T-030's auditor: the
entry said nothing calls the reader or the writer, and since T-030
`open_cycle` and both merge paths do.

## What is true

`tasks.unmet_dependencies(hive_dir, task)` in `dispatcher/tasks.py` is the one
reader of the rule. For each id in `depends_on` it reads that task's file and
looks only at whether `merged` is truthy. `status` is not read: a dependency
that is `done` with no `merged` block is unmet, and reported as
`<id> is not merged`. A missing file, an unreadable one, an entry that is not
a task id and a `depends_on` that is not a list are each a line of their own.

`tasks.mark_merged` is the one writer of the block,
`{into: <branch>, at: <ISO-8601 UTC>}`. Since T-030 both merge paths call it
through `record_merge` in `dispatcher/dispatcher.py`, and `open_cycle`
refuses a task whose `unmet_dependencies` is not empty, logging the reasons
and marking nothing. The gate is on every task and on `run-phase`, not only
an epic's children. A task merged before T-030 has no block, so a
`depends_on` naming one is refused until `merge-task` is run on that
dependency again
([a-merge-outcome-that-is-not-refused-is-not-always-merged](a-merge-outcome-that-is-not-refused-is-not-always-merged.md)).

The block is not validated on read. `read_task_file` takes whatever the
frontmatter holds, so a hand-written `merged: true` counts as merged, and an
unquoted timestamp under `at` reads back as a `datetime`, not a string.

## What to do

Ask `unmet_dependencies`; do not compare `status` to `done`. In a test, make
a dependency met with `tasks.mark_merged`, not by writing `status: done`.
When a run is refused with `<id> is not merged` for a dependency you know
landed, it predates the record: run `merge-task` on it.
`docs/plans/task-split.md` section 3, last paragraph, is the design.

## Evidence

T-029: `unmet_dependencies` and `mark_merged` in `dispatcher/tasks.py`;
`test_unmet_dependencies_says_which_are_missing_and_which_are_not_merged` in
`tests/dispatcher/test_tasks.py`; `docs/implementations/T-029.md` *The other
writers* and *Ruled out*, first item. T-030: the gate in `open_cycle` and
`record_merge` in `dispatcher/dispatcher.py`;
`docs/implementations/T-030.md` *What a later task should know*.
