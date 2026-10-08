# Park the index row's exact text where a hand resume will find it, because the auditor may never run

**When it applies:** your cycle may end before its auditor — it is late, the
account is near its quota — and an ADR or implementation note you are writing
claims that a row in [`docs/debt/README.md`](../debt/README.md) or
[`docs/learnings/README.md`](README.md) changed. Also when you *are* the auditor
of a cycle a human resumed by hand, and are about to re-derive such a row from
scratch.

**Status:** unconfirmed — reported once, by T-014's auditor, and not reproduced
since. Shared-inbox entry:
`/data/.hive/learnings/inbox/T-014-park-the-index-text-where-a-hand-resume-finds-it.md`.

## Symptom

No error, and no failing gate. The branch lands with `docs/decisions.md` ADR 34
*Consequences* stating:

```
The `T-012-D1` row closes with this entry, and the entry file's `**Status:**`
line is the one line of it that changes
```

while `docs/debt/README.md` still says `**Open, the first half of its fix
landed by hand 2026-10-05...**` and the diff contains no `docs/debt/` file at
all. The ADR is on the branch; the row it describes is not, because the auditor
is the only phase that writes the indexes and the run ran out of accounts
before it.

## Why

A writer phase cannot close the row. `docs/debt/README.md`'s own header and
`dispatcher/dispatcher.py:close_resolved_debt`'s docstring both settle that the
auditor writes it, on the branch. So any ADR sentence about an index row is a
*promise made on another phase's behalf*, and it is false for as long as that
phase has not run — and on this project the auditor is the phase most likely not
to run, because it is last and quota runs out at the end.

## What to do

Park the exact row text and the exact `**Status:**` line in
`/data/.hive/tasks/<task-id>/plan.md`, so the auditor applies it instead of
re-deriving it and an operator landing the branch by hand has it too. If you are
that auditor or that operator, read the plan section before re-deriving
anything: the wording was decided with the ADR in front of it.

## Evidence

T-014's arquitecto wrote the row's *what*, *fix* and *card* cells and the
one-line `**Status:**` change into `/data/.hive/tasks/T-014/plan.md` §5, *Docs
owed, and who owes which*, naming the auditor as the owner. The cycle did then
die after the revisor's round 2 for want of an account, and the resumed auditor
phase applied §5's text directly; `python3 -m pytest` stayed at 1117 passed, 10
skipped across the docs edits. Three of the four phases had declared the same
"if this cycle dies before its auditor" risk in their handoffs, which is what
made the parking deliberate rather than lucky.

Carried into this index by T-015, whose own arquitecto parked its doc text the
same way in `/data/.hive/tasks/T-015/plan.md` — and whose cycle did reach its
auditor, so nothing here fired a second time.
