# A dependency is met by a `merged` block on its task file, not by `status: done`

**When it applies:** you are deciding whether a task may start because of its
`depends_on`, writing the gate that holds a sub-task, or setting up a test
hive in which one task waits for another.

**Status:** unconfirmed — proposed by T-029's implementador and revisor, and
checked by the auditor against the function. Nothing calls it yet, so no run
has exercised the rule.

## What is true

`tasks.unmet_dependencies(hive_dir, task)` in `dispatcher/tasks.py` is the one
reader of the rule. For each id in `depends_on` it reads that task's file and
looks only at whether `merged` is truthy. `status` is not read: a dependency
that is `done` with no `merged` block is unmet, and reported as
`<id> is not merged`. A missing file, an unreadable one, an entry that is not
a task id and a `depends_on` that is not a list are each a line of their own.

`tasks.mark_merged` is the one writer of the block,
`{into: <branch>, at: <ISO-8601 UTC>}`. Nothing calls it either: until task 3
of `docs/plans/task-split.md` section 8 wires the merge paths, no task file
has the block, so every dependency of every task is unmet by this function.

The block is not validated on read. `read_task_file` takes whatever the
frontmatter holds, so a hand-written `merged: true` counts as merged, and an
unquoted timestamp under `at` reads back as a `datetime`, not a string.

## What to do

Ask `unmet_dependencies`; do not compare `status` to `done`. In a test, make
a dependency met with `tasks.mark_merged`, not by writing `status: done`.
When wiring the gate, remember that tasks finished before the wiring have no
block: `docs/plans/task-split.md` section 3, last paragraph, is the design.

## Evidence

T-029: `unmet_dependencies` and `mark_merged` in `dispatcher/tasks.py`;
`test_unmet_dependencies_says_which_are_missing_and_which_are_not_merged` in
`tests/dispatcher/test_tasks.py`; `docs/implementations/T-029.md` *The other
writers* and *Ruled out*, first item.
