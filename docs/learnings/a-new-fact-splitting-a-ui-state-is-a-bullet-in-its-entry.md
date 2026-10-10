# A new fact that splits an existing `docs/ui.md` state is a bullet inside that entry, not a new heading

**When it applies:** your task adds a console state, tone or rendering rule to
`docs/ui.md`, and it is a distinction *within* something that file already
names rather than a new subject.

**Status:** unconfirmed — reported by T-017's arquitecto and again by its
implementador, which added **No match** to a heading whose own title counts its
contents.

## Symptom

Nothing fails. The heading just stops being true of itself:
`docs/ui.md` *Absent, empty and broken are three different things* says
**three**, and a fourth bullet under it makes the title wrong — while a new
`###` heading beside it would make the file claim two independent vocabularies
for the same question, which is the thing `docs/ui.md` exists to prevent.

## Why

`docs/ui.md` is binding and **edited in place rather than appended to**
(`docs/README.md`'s row for it says so), and its headings are sentences rather
than labels: *Absent, empty and broken are three different things*, *A region
with no route says which route, and when*. A heading that counts is a claim, so
where the new fact goes decides whether the count survives.

The test is whether the new state can be worded as an existing one's **other
half**. *No match* is a successful query over a collection that holds rows, with
a filter the operator typed removing all of them — which is Empty's own sentence
with a different subject, so it is a bullet beside *Empty* and the heading's
three stand. A genuinely new subject — a state no existing bullet is the
complement of — is a new heading, and then the surrounding prose that counts or
enumerates has to move with it.

The reasoning that a later task would otherwise have to re-derive does not go in
`docs/ui.md` either way: it goes in an ADR. T-017 put the four sub-decisions a
refactor could silently undo — the order of the two tests, words rather than a
clear button, no new primitive, no api half — in `docs/decisions.md` **ADR 43**,
and left `docs/ui.md` holding only what a screen transcribes.

## What to do

Write the entry first and the screens second: they are a transcription of it,
and writing them first is how the console ends up with two wordings — the
condition `docs/debt/T-016-D1.md` was filed for.

Then check the entry's own frame, which is three edits and easy to miss two of:
the bullet, the **Set by:** line (which names the task and the ADR, and whose
*Source of truth* file list grows), and any prose under the heading that counts
or orders the bullets. Grep `docs/` for the heading's own count word before you
decide you are done.

## Evidence

T-017 added **No match** to *Absent, empty and broken are three different
things* in `docs/ui.md` as a fourth bullet worded as Empty's other half, plus a
paragraph after the bullets fixing the order of the two tests, the form a screen
with more than one filter takes, and the fixture exclusion; **Set by:** gained
T-017 and ADR 43 and three route files. Its arquitecto grepped `docs/` and
`front/` first: nothing counts that file's entries or empty states, so the new
bullet falsified no count anywhere. `docs/decisions.md` **ADR 43** carries the
reasoning, and `docs/implementations/T-017.md` *The entry first, because the
screens are a transcription of it* records the order.
