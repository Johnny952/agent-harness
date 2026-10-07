# A precondition that reads the task card turns a `dispatcher/` function's existing tests red at once

**When it applies:** you are adding a precondition to a function under
`dispatcher/` that reads `.hive/tasks/<task-id>.md` — a task card — and that
function already has tests. Also when you are reading a `dispatcher/` test
failure that looks like unrelated breakage in a file your change never touched.

**Status:** unconfirmed — reported by T-014's arquitecto, which predicted the
four failures before the gate landed, and by its implementador, which got
exactly those four.

## Symptom

Four tests in two files, none of them about the thing that changed:

```
FAILED tests/dispatcher/test_learnings.py::test_drop_promoted_moves_only_what_the_merged_branch_filed
FAILED tests/dispatcher/test_cli.py::test_cli_merge_task_drops_the_entries_the_merged_branch_filed
FAILED tests/dispatcher/test_cli.py::test_cli_merge_task_keeps_the_entries_another_task_is_carrying
FAILED tests/dispatcher/test_learnings.py::test_drop_promoted_does_not_overwrite_an_earlier_dropped_entry
```

## Why

The helpers that set those tests up build the *learnings root* and never the
card. `tests/dispatcher/test_learnings.py:_entry` and
`tests/dispatcher/test_cli.py:_learning` both go through
`learnings.ensure_dirs`, which creates `<hive>/../learnings/{inbox,harness}` —
a **sibling** of `hive_tasks_dir`, not a child. So `<hive>/` itself need not
exist, and `<hive>/<task-id>.md` certainly does not.

A new read of the card therefore finds nothing. A precondition that resolves
"no card" towards its safe answer — which is the right default, because a
missing card is absence of evidence
([`docs/decisions.md`](../decisions.md) ADR 34) — then makes every existing test
of that function take the safe branch and assert the old one. The failures are
the precondition working, and they arrive in files the diff does not mention.

## What to do

Write the card in the test; do not loosen the precondition to get green. Add a
`_card` helper beside the entry helper, giving it the frontmatter
`context_transfer.read_task_file` requires (`task_id` and `status`) and a body
after the delimiter, and `mkdir(parents=True, exist_ok=True)` the hive directory
first, because `ensure_dirs` never creates it. Then say in each repaired test's
docstring that the card is what admits the behaviour it asserts, so the next
reader does not have to re-derive why a drop test writes a task file.

Check the count before you start: grep the function's name across
`tests/dispatcher/` and expect one failure per test that reaches it. A number
that does not match is a real finding, and the ones that do match are not.

## Evidence

`python3 -m pytest tests/dispatcher/ -q` in the T-014 worktree, immediately
after the `## auditor` card gate landed in
`dispatcher/learnings.py:drop_promoted`. The suite went from 1106 passed to
1113 once each of the four wrote a card, with no change to the gate.
Inbox entry: `T-014-a-card-precondition-turns-a-functions-existing-tests-red.md`.
