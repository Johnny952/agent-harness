# A subject's first card falsifies every "no card owns this yet" line

**When it applies:** your task writes the first card, plan section or screen
for a subject the docs say has none — spend, a route with no screen, a field
nothing reads — and you are about to hand off with only the new card edited.

**Status:** unconfirmed — found by T-042's round-1 review and fixed in round
2; one task, one subject.

## Symptom

No error, and the suite and the pointers gate stay green. Two present-tense
docs keep telling the next task the subject has no home: T-042 carded the
usage route's screen in `docs/plans/front.md` *The usage task*, and
`docs/README.md`'s row for that plan still called Role models the one tier-2
piece left, while `docs/learnings/README.md`'s row for
`the-tokens-screen-is-logins-not-spend` still said "spend has no screen and no
card — say 'no card owns this yet'".

## Why

An absence is stated wherever someone needed to warn about it, not in one
place. `dispatcher/project_docs.py` `PRESENT_DOCS` names the docs that must
stay true now — the docs index, the learnings and debt indexes among them —
and a learning or debt row that warns of the gap is exactly where the claim
sits. Nothing checks prose against the plans, so only a grep finds them.

## Rule

Before handing off, grep the `PRESENT_DOCS` files for the absence in its own
words ("no card", "no screen", "nothing reads", the subject's name) and
correct each hit; a learning entry gets a one-clause amendment in its file and
its row (`correcting-an-index-entry-is-two-edits.md`). Leave the hits in
record docs — plans, ADRs, implementation notes, debt entry files — alone.

## Evidence

`<hive_tasks_dir>/T-042/review.md` findings F1 and F2, fixed in
`docs/implementations/T-042.md` *Round 2*. The auditor's own grep for
`no card owns` afterwards found one more, in the debt index's `T-024-D1` row,
and amended it.
