# Your own cycle's plan and card name symbols: each one is a claim to grep

**When it applies:** a document written *inside* your own task — the
arquitecto's plan under `/data/.hive/tasks/<id>/`, or the card's "where the
code is today" — names a function, a test or the function a comment sits in,
and your next step is to edit it, cite it or rely on it.

**Status:** unconfirmed — one task, three separate instances in it. T-020's
implementador hit two and its revisor the third, each on a different document.

## Symptom

Three claims inside one cycle, none of them true of the tree:

- The plan asked for an edit to `dispatcher/operator.py:_render_accounts`.
  `grep -rn _render_accounts` over the repo returns nothing but the
  implementation note saying so. The symbol is `format_status`.
- The plan said an existing test pins the wording of the park log, and planned
  an update to it. No test pinned that message; the named test did not exist.
- The card ascribed a stale comment to a function it is not in.

None of these breaks anything loudly. A plan's symbol that does not exist
costs a phase the time to find the real one, or — worse — gets written as a
new function beside the one that already did the job.

## Why

A plan is written by a phase reading the same tree you are about to edit, which
is exactly what makes its citations feel settled. But its author was
summarising from memory of a read, and a card's "where the code is today" was
written against the **base commit** and is a snapshot: later rounds of your own
task move the thing it describes. Neither document is executed, so nothing
tells either author they were wrong.

The in-cycle case is the one this project kept missing, because
[a-plans-present-tense-claim-is-a-citation](a-plans-present-tense-claim-is-a-citation.md)
reads as being about `docs/plans/`, specs and ADRs — documents from other
tasks. The same rule binds the plan your own arquitecto handed you.

## What to do

Grep the **identifier**, not the sentence — the one part of a citation a
paraphrase cannot change
([correcting-a-false-mechanism-greps-the-identifier](correcting-a-false-mechanism-greps-the-identifier.md)).
One `grep -rn <symbol>` per symbol a plan tells you to edit, before you plan
the edit around it, and one per test a plan says already asserts something.

For a card's base-state facts, read them out of the base rather than the
working tree: `git show <base>:<file> | grep -n <symbol>`. That is what catches
a comment ascribed to the wrong function, and it stays correct in a later round
when your own diff has moved the line.

A claim that turns out false is a line in `docs/implementations/<task-id>.md`,
under the heading that records what was verified rather than taken from the
card — not a correction to the plan, which is working notes and is dropped with
the task.

## Evidence

`docs/implementations/T-020.md` *Verified first-hand, not taken from the card*,
which lists all three with the greps that found them; the plan is
`/data/.hive/tasks/T-020/plan.md` sections 5 and 6 (working notes, not on a
branch). Re-checked while filing: `grep -rn "_render_accounts"` over this
worktree still matches nothing but that note.
