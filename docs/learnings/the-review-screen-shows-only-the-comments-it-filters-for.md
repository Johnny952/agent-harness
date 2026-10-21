# The review screen shows only the comments it filters for

**When it applies:** you want a comment someone else writes on a task under
review (a dispatcher outcome, a worker note) to be visible on `/approvals`.

**Status:** unconfirmed. Reported by T-125-S3's arquitecto; checked against the
tree by its auditor.

## Why

`front/src/routes/approvals.tsx` does not list a task's comments: it draws the
verdicts (`front/src/lib/review.ts` `reviewVerdicts`), the comments pinned to a
line, and since T-125-S3 the dispatcher's `approve at <commit>:` comments
(`approveOutcomes`, ADR 264, ADR 270). Any other plain comment lives only on the
task page.

## Rule

Give the new comment a filter in `front/src/lib/review.ts` beside
`approveOutcomes` and draw it in `Verdicts`; do not expect it to appear on its own.

## Evidence

`docs/decisions.md` ADR 270 (the outcomes list) and `approveOutcomes` with its
test in `front/src/lib/review.test.ts`, T-125-S3.
