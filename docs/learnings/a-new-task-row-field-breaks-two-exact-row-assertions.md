# A new field on a task row breaks two exact-row assertions

**When it applies:** you are adding a key to the task row that
`observability/api/app.py`'s `_task` serves on `/api/tasks`, or to the
`board_list` row in `board_service/app.py`, and are budgeting which tests
the change touches.

**Status:** unconfirmed — proposed by T-160-S1's implementador, checked
against the tree by T-160-S1's auditor.

## Symptom

Tests you never meant to touch go red with an `assert {...} == {...}` whose
only difference is the new key, missing from the expected dict.

## Why

Both rows are pinned whole, as dict literals listing every key:
`tests/observability/test_api.py` (the task-row tests, two literals plus a
key-name list) and `tests/board_service/test_app.py` (the `board_list`
items, one literal per card). A new key is absent from every literal.

## Rule

Add the key, with its default (`None` for an unset `TaskFile` field), to
every whole-row literal in both files in the same commit, then add one test
that sets the field and reads it back off the row.

## Evidence

On the T-160-S1 branch, `grep -n dropped_reason tests/observability/test_api.py
tests/board_service/test_app.py` prints the key in each whole-row literal
the implementador had to extend for `dropped_reason` and `dropped_by`
(ADR 366).
