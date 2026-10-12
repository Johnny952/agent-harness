# A `MergeOutcome` that is not refused is not always merged

**When it applies:** you are branching on what `docker_exec.merge_task_branch`
returned, adding something that must happen "when a task merges", or reading
`merge-task`'s output in a script.

**Status:** unconfirmed — proposed by T-030's arquitecto and implementador;
checked by the auditor against `MergeOutcome` in `dispatcher/docker_exec.py`
and `record_merge` in `dispatcher/dispatcher.py`.

## What is true

`MergeOutcome.status` has three values. `merged` and `refused` are properties
over two of them; the third, `UP_TO_DATE`, means the target already contains
the branch, and both properties are False for it. Both merge paths treat it
as landed: `_merge_task_branch` returns True and `merge-task` exits 0 and goes
on to `learnings.drop_promoted`. `target` is None only on some refusals.

`record_merge` is the one function both paths call, with any outcome that is
not refused. On `MERGED` it calls `tasks.mark_merged`. On `UP_TO_DATE` it
calls it only when the task file has no `merged` block, so a second
`merge-task` keeps the first record's `at` and repairs a record that was
never written. A branch with no commits past its base is also `UP_TO_DATE`,
and is recorded as merged the same way. `tasks.close_epic` runs on both.

`record_merge` never raises and prints nothing. It returns the lines it
recorded and the one problem, or None. `merge-task` prints the lines to
stdout, among them `task <id>: recorded as merged into <branch>`, the
problem to stderr, and still exits 0.

## What to do

Test `not outcome.refused` for "the branch is in the target", and
`outcome.merged` only for "this call moved it". Put a new after-merge step in
`record_merge`, not in one of its two callers. To give a task merged before
T-030 its `merged` block, run `merge-task` on it again.

## Evidence

T-030: `MergeOutcome` in `dispatcher/docker_exec.py`; `record_merge` and
`_merge_task_branch` in `dispatcher/dispatcher.py`; the `merge-task` branch
of `main` in `dispatcher/cli.py`;
`test_record_merge_keeps_the_first_record_when_the_branch_was_already_in` in
`tests/dispatcher/test_dispatcher.py`; `docs/implementations/T-030.md`
*Merge: one function both paths call*.
