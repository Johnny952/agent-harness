# `record_merge` returns three values, and tests compare the whole tuple

**When it applies:** you are changing what `dispatcher/dispatcher.py`
`record_merge` returns, or adding a caller or a test of it.

**Status:** unconfirmed — proposed by T-160-S2's arquitecto and
implementador, checked against the tree by T-160-S2's auditor.

## Symptom

A caller unpacking `recorded, problem = record_merge(...)` raises
`ValueError: too many values to unpack`, and tests such as
`test_record_merge_keeps_the_first_record_when_the_branch_was_already_in`
fail on `== ([], None)`.

## Why

Since ADR 369 `record_merge` returns `(recorded, problem, warnings)`. Four
callers unpack it — `_merge_task_branch`, `_auto_approve_and_merge`,
`approve_reviewed` and the `merge-task` verb in `dispatcher/cli.py` — and
the `test_record_merge_*` tests in `tests/dispatcher/test_dispatcher.py`
unpack it or compare it whole (`== ([], None, [])`).

## Rule

Change the shape in every caller and every `test_record_merge_*` test in
the same commit; each caller also shows the warnings (ADR 369 decision 6).

## Evidence

`grep -n "record_merge(" dispatcher/ tests/dispatcher/test_dispatcher.py -r`
on the T-160-S2 branch lists the four callers and the tests.
