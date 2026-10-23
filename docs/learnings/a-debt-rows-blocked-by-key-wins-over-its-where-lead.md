# A debt row's `blocked_by` key wins over its **where** lead

**When it applies:** you build or test a debt row dict that reaches
`dispatcher/tasks.py` `file_debt`, or read why an index row is filed gated or
not.

**Status:** unconfirmed — proposed by T-164-S2's implementador, read against
the code by the auditor.

## What is true

`dispatcher/debt.py` `blocked_by(row)` returns the row's `blocked_by` key when
the key is present, **even when it is empty**, and only otherwise parses the
`Blocked by <value>;` lead of the row's `where` (ADR 396). Rows from
`index_rows` never have the key, so `dispatch import-debt` reads the lead;
`dispatcher.py` `_mirror_filed_debt` sets the key from the declaration, so the
cycle mirror ignores the lead. `blocker_kind` then reads a value matching
`TASK_ID_RE` as a task (the item gets `depends_on`) and anything else as a
plan step (nothing filed). The lead runs to the first `;` or the end of the
cell: a `where` opening "Blocked by T-163 until ..." with no `;` reads as a
plan step.

## What to do

In a test, pass `"blocked_by": ""` to mean "not gated" regardless of `where`,
or leave the key out to exercise the lead. When writing an index row by hand,
close the lead with `;`.

## Evidence

`dispatcher/debt.py`: `blocked_by`, `blocker_kind`, `TASK_ID_RE`,
`_BLOCKED_BY_RE`. `dispatcher/tasks.py`: `file_debt`.
