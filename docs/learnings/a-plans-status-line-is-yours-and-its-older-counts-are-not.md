# In a plan, the lines your task wrote are yours to fix and the counts it already carried are not

**When it applies:** you are editing a `docs/plans/*.md` Status paragraph
because your task changed what the plan describes, and the same file carries
present-tense counts further down — "Seven of its twelve screens still resolve
from a fixture", "Five of the sixteen reads already have a route".

**Status:** unconfirmed — the division was worked out by T-016's revisor in
round 2 out of two project rules that point opposite ways, and applied in round
3.

## Symptom

No error. The plan ends up disagreeing with itself, and with the READMEs the
same commit swept:

```
docs/plans/front.md Status:  **tier 1 and tier 2 are both built ... and closed**
docs/plans/front.md Tier 2:  the configuration read is all of tier 2 that is left
docs/plans/front.md Status:  Seven of its twelve screens still resolve from a fixture.
docs/README.md front/ row:   the other six — approvals, tokens, session logs, role models, backlog and queue
```

## Why

Two rules meet in one file. [`docs/decisions.md`](../decisions.md) **ADR 38**
makes a plan a *record*: the lines a task writes today are a claim about today,
and that task is the one that can still fix them, while the file's older lines
are never asked again. [`a-plans-present-tense-claim-is-a-citation`](a-plans-present-tense-claim-is-a-citation.md)
*What to do* forbids rewriting a plan's stale sentence at all — leave it and
record the disagreement in the task's implementation note.

The two agree once you see what divides them, and it is not the topic and not
whether the sentence is true. It is whether **your diff wrote the line**. A line
your commit wrote is yours, including its consistency with every other line your
commit wrote; a line that was already there and your change merely falsified is
the other rule's, and editing it loses the reasoning a reversal would need.

The trap is that both kinds of sentence sit in the same paragraph. T-016 fixed
its Status sentence correctly and left two counts it had falsified in the three
sentences below it, which is right — but it had to say so somewhere, or the next
reader finds a paragraph that contradicts itself with no note explaining which
half is deliberate.

## What to do

Fix the lines your task wrote, and make them consistent with each other and
with the READMEs the same commit touches. For a pre-existing count your change
falsified, leave the sentence as written and record the disagreement in
`docs/implementations/<task-id>.md`, naming the count, its true value now, and
both rules — see
[`docs/implementations/T-016.md`](../implementations/T-016.md) *What the docs say
now* for the shape. That record is not a revision round and not a debt row:
[`a-stale-count-in-a-dated-paragraph-is-not-a-finding`](a-stale-count-in-a-dated-paragraph-is-not-a-finding.md)
sorts the four cases.

## Evidence

`git diff main...HEAD -- docs/plans/front.md docs/README.md` on T-016's branch
at `51e71e4`, read against ADR 38 *Decision* and
`a-plans-present-tense-claim-is-a-citation` *What to do*. Both halves are
written up in `/data/.hive/tasks/T-016/review-round-2.md`, blocking 1 and
blocking 2, and discharged in round 3. Inbox entry:
`/data/.hive/learnings/inbox/T-016-a-plans-status-line-is-yours-its-counts-are-not.md`.
