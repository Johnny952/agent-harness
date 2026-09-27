# Retargeting a `docs/ROADMAP.md` check in place keeps its id, so the results log starts answering PASS for a check nobody ran

**When it applies:** your change deleted or replaced the thing a numbered check
in `docs/ROADMAP.md` verifies, and you are about to rewrite that check's steps
under its existing id.

**Status:** unconfirmed — reported once, by T-010's revisor, as blocking finding
B3 on `V0.6`.

## Symptom

No error, and the record reads green. `V0.6` was the events dashboard's auth on
`127.0.0.1:8788`, and the results log carries `2026-09-16 | V0.6 | PASS` for it.
T-010 deleted that dashboard and rewrote the `V0.6` recipe to cover the board on
`8790` over the api. Nothing else changed, so the log's PASS row — accurate
about a check that no longer exists — was the newest word on a check no human
had ever run.

## Why

The id is the join between the recipe and the results log, and the log is
append-only by design: rows are never edited, because a row is a record of what
was observed on a date. Editing the recipe therefore silently re-points every
historical row at the new steps. The two halves rot in opposite directions —
the recipe is current and the evidence is not.

## What to do

Give the retargeted check a new id — `V0.6b` beside `V0.6`, not `V0.6`
rewritten — and do four things with it: say in the recipe's first bullet which
id the old PASS belongs to and that it is superseded, leave the old results row
exactly as written, append a `NOT RUN` row under the new id explaining why there
is no evidence yet, and follow the new id everywhere it is cited (the stage
table near the end of the file, and the task's implementation note). Deleting or
rewriting the old row instead is the finding, not the tidying.

## Evidence

T-010's revisor round 1, finding B3, answered in `d46c614`. `docs/ROADMAP.md`
now holds **V0.6b** with its superseded-id bullet, the untouched `2026-09-16 |
V0.6 | PASS` row, and `2026-09-27 | V0.6b | NOT RUN`.
