# A plan's present-tense claim about the tree is a citation: check it before acting on it

**When it applies:** a plan, spec or ADR tells you that something already exists
or already moved — "these tests moved to X in Phase 1", "that helper is already
there" — and your next step is to delete, skip or rely on it.

**Status:** unconfirmed — reported once, by T-010's arquitecto, against
`docs/plans/board.md` "Phase 2 — a read-only board".

## Symptom

No error, and the failure is silent by construction: the thing you were told is
already done does not happen, and nothing is left to notice it.
`docs/plans/board.md` says the dashboard's auth tests "moved to the shared
`observability/auth.py` in Phase 1". They had not. `tests/observability/`
held no `test_auth.py` at all, and `tests/observability/test_dashboard.py`
still carried all nine tests — seven of them auth tests that merely happened to
drive the dashboard app, because that was the app that had auth. Deleting the
file on the strength of that sentence would have deleted `observability/auth.py`'s
only coverage, with the suite still green.

## Why

A plan is written ahead of the work and then survives it. A sentence that was a
*prediction* when it was written reads as a *statement of fact* to the next
reader, and nothing in the document distinguishes the two. Phase 1 lifted the
auth *code* into `observability/auth.py` and left the tests where they were;
the plan's sentence had described the whole move.

## What to do

Treat every present-tense claim about the tree as a pointer to open, not a fact
to consume — one `ls` or one grep, before the deletion and not after. The
cheapest form is to count what you are about to delete: nine tests where the
plan implies two is the signal. Where the plan turns out to be wrong about the
present, leave the sentence alone and record the disagreement in the task's
implementation note: rewriting the plan to match the code deletes the record of
what was intended.

## Evidence

T-010, `docs/implementations/T-010.md` "The dashboard's auth tests were rehomed
by hand". The seven now live in `tests/observability/test_auth.py`, renamed off
`test_index_*`, with the bearer path's tests beside them; the two that were
genuinely about the dashboard went with it.
