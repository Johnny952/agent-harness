# A debt id a task text cites names the row that *was filed*, not the condition the text is describing

**When it applies:** your task text tells you to cite an existing
`docs/debt/<id>` row instead of declaring something again — and the condition it
describes was declared by a cycle whose auditor never ran, which on this project
is every run recovered by hand.

**Status:** unconfirmed — found by T-013's arquitecto, re-checked independently
by its implementador and its revisor, and acted on once. Shared-inbox entry:
`/data/.hive/learnings/inbox/T-013-a-task-text-can-cite-a-debt-id-that-is-a-different-row.md`.

## Symptom

No error. T-013's task text said, of the console having no test runner and no
typecheck:

```
That is already filed as `docs/debt/T-012-D1`; cite it, do not file it a second time
```

`docs/debt/T-012-D1.md`'s own title is:

```
# T-012-D1 — `merge-task` drops a task's project-scope inbox entries whether or not its auditor ever ran
```

Two different defects. Citing the id as instructed would have pointed a later
reader at a `dispatcher/learnings.py` bug while claiming the `front/` gap was
filed, and the `front/` gap would have stayed in no index at all. It is now
[T-013-D1](../debt/T-013-D1.md), declared once rather than cited.

## Why

The auditor is the only phase that writes `docs/debt/README.md`. T-012's run
stopped for quota after its implementador, so its auditor never ran and **none**
of that cycle's declarations were filed. The one `T-012-D1` row that exists was
written by the operator, for a different defect found in the by-hand review of
that branch, taking the first free id in T-012's sequence.

An id is assigned in the order things are **filed**, not in the order they were
declared — `dispatcher/debt.py:entry_id` and the re-derivation precedent the
two `T-010` rows set — so a declaration that was never filed leaves its expected
id free for the next thing filed under that task's prefix. The id in a task
description was written by a human reading a handoff, which holds declarations;
the index holds filings. Those two lists diverge for exactly as long as a cycle
goes unaudited.

## What to do

Open the entry before citing it. If the row says something else, the condition
is unfiled: declare it in your own handoff once, with origin `found`, and say in
the handoff that the id the task text cited is a different row — so the auditor
files one entry and does not spend a turn working out which of you is wrong.

Do not renumber or rewrite the row that is there. It is a correct record of
something else, and
[correcting-an-index-entry-is-two-edits](correcting-an-index-entry-is-two-edits.md)
is about a claim that is *false*, which this is not.

## Evidence

T-013: `docs/debt/T-012-D1.md`'s title against its own task description's
settled point 4. `/data/.hive/tasks/T-012/handoffs/arquitecto.json` holds the
original declaration ("The console's code has no test runner, no typecheck in
the loop and no gate…") and there is no `auditor.json` beside it — two files in
that directory where a completed cycle leaves four. The same gap is why
`docs/debt/README.md`'s closing paragraphs have to explain four separate reasons
a `card` cell reads `none`.
