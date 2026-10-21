# A split task's debt id carries its subtask

**When it applies:** you file a debt row whose id names a split subtask
(`T-<n>-S<m>-D<k>`) in `docs/debt/README.md`, or change a test that checks the
index's ids.

**Status:** unconfirmed. Hit once, by T-125-S1's auditor.

## Symptom

```
FAILED tests/dispatcher/test_debt.py::test_the_fingerprints_of_the_projects_own_index_did_not_move
E       AssertionError: ['T-008-D1', 'T-008-D2', 'T-008-D3', 'T-009-D2', 'T-009-D3', 'T-009-D4', ...]
```

## Why

`dispatcher/debt.py` builds an id as `{task_id}-D{n}` and
`dispatcher/tasks.py` `_DEBT_ID_RE` accepts a subtask's id, but the test pinned
the index's ids to `T-\d+-D\d+`. `T-125-S1-D1` was the first split-task row.

## Rule

The id is the dispatcher's: keep it. T-125-S1 widened the test's pattern to
`T-\d+(-S\d+)?-D\d+`; if a pattern is narrow again, widen it, never rename the id.

## Evidence

`python3 -m pytest -q` in the T-125-S1 worktree after adding the `T-125-S1-D1`
row, red before the widening and green after.
