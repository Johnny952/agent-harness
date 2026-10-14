# A `MergeOutcome` that is not refused is not always merged

**When it applies:** you are branching on what `docker_exec.merge_task_branch`
returned, adding something that must happen "when a task merges", or reading
`merge-task`'s output in a script.

**Status:** confirmed 2026-10-14 against the running stack — seven
`merge-task` runs by hand on branches already merged into `main` gave
`UP_TO_DATE`, exit 0, a new `merged` block and `drop_promoted`; none ran
`merge-task` on a task that already had a `merged` block, so "keeps the
first record's `at`" still rests on T-030's test alone — see *Evidence*.

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
problem to stderr, and still exits 0. The cycle's own merge does not print
them: `_merge_task_branch` sends the same lines to the log, so a script
finds `recorded as merged into` on stdout only after `merge-task` (added by
T-032's auditor; the root `README.md` says so since that task).

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

The operator, by hand outside any cycle, 2026-10-13 and 2026-10-14:
`merge-task` on T-035, T-036, T-037, T-039, T-040, T-041 and T-042, each
branch merged into `main` on the host with `git merge --no-ff` beforehand
and each task file with no `merged` block. Every run exited 0, printed
`main already contains agent/task/<id>` and
`task <id>: recorded as merged into main` on stdout, and left a `merged`
block in the task file. T-035's run moved
`inbox/T-035-render-route-opens-a-route-on-its-own-path.md` to `dropped/`
and T-042's moved four filed inbox entries there: `drop_promoted` after an
`UP_TO_DATE`. T-037's `status` stayed `pending`, since `tasks.mark_merged`
writes the `merged` block and nothing else. Not exercised: a second
`merge-task` on a task that already has a `merged` block, and a branch
with no commits past its base.
