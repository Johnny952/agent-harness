# A plan's Status paragraph is the one paragraph any task may fix out of cycle

**When it applies:** your task made a `docs/plans/*.md` Status paragraph false —
it says unbuilt what you just built — and your task never named that file.

**Status:** confirmed — filed as a defect by T-008 (`docs/debt/T-008-D3`, on
`docs/plans/board.md`), prescribed by T-010, and done unasked by T-011 on
`docs/plans/front.md` with the revisor ruling it allowed.

## The edit you may make

Make it. `docs/charter.md` C-6 says docs are part of the work and "the task did
not ask for docs" is not a reason, and `docs/debt/T-008-D3` is a filed debt whose
entire subject is a plan's Status paragraph denying work that exists. That row
also says who owns it: the first task allowed to edit the plan. If your task
falsified the paragraph, that is you.

The paragraph is what a later reader uses to decide whether a phase exists at
all, so a stale one is not cosmetic: it is the sentence on which somebody skips
or redoes a phase.

## The edit you may not make

The *sections* stay as written. Rewriting a spec section to match later code
deletes the reasoning a reversal of the decision would need —
[a-toolchain-ruling-can-outlive-a-specs-state-list](a-toolchain-ruling-can-outlive-a-specs-state-list.md)
is the long form, and `docs/plans/board.md` *The shapes* is the standing example
([board-md-the-shapes-is-not-the-current-key-list](board-md-the-shapes-is-not-the-current-key-list.md)).
The record of what grew past a spec is an ADR, not an edit to the spec.

A section bound to a task that has not run is especially not yours: front.md's
*Decisions this tier's tasks make* hands named items to named future tasks, and
answering one of those early is taking a decision out of the hands of the phase
the plan gave it to.

The one thing T-011 did beyond the Status paragraph, and which was ruled
allowed: a closing paragraph appended to the section whose work its own task
finished, stating the contract in numbers and citing the ADRs that settled it.
Appending to the section you completed is not the same move as rewriting a
section somebody else will build.

T-020 took the same move on `docs/plans/token-economy.md` **P8**, the section
its card told it to implement, and its revisor approved it: the Status
paragraph now says which phase is built and that P1–P7 are still proposals,
and one appended paragraph names what the code does, in the plan's own numbers,
citing ADR 48. Not one of P8's own bullets was rewritten — including the ones
whose guesses the implementation refined.

## Evidence

`docs/debt/T-008-D3.md`; `docs/plans/front.md` Status paragraph and *The api's
share* closing paragraph as of T-011; `docs/implementations/T-011.md` *What was
built*, last paragraph, and the ruling in
`/data/.hive/tasks/T-011/revisor-round-1.md` *The out-of-cycle front.md edit*
(working notes, not on a branch).
