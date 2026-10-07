# An ADR your own cycle appended and has not merged is corrected in place, not superseded

**When it applies:** a later round of your own task finds that the ADR your
arquitecto appended this cycle states something the code no longer does — or
never did — and the entry is still unmerged on the task branch.

**Status:** unconfirmed — reported by T-014's revisor, which ruled the edit
right in round 1, and acted on by its implementador in round 2.

## Symptom

No error. A round-1 finding is fixed in the code, and the ADR written before it
goes on describing the old shape: T-014's ADR 34 named a four-member `except`
tuple that the fix widened to five, in a paragraph whose whole argument is that
every unknown keeps the entries. The phase that found it then has to choose
between landing an entry that is false from birth and editing a file every doc
in this project calls append-only.

## Why

Two rules point in opposite directions only if the first is read without its
reason. [`docs/charter.md`](../charter.md) *Where a rule belongs* says
`docs/decisions.md` changes by "a later ADR, by superseding — never by
rewriting", and ADR 33's own text repeats it. What that protects is the
reasoning behind a decision somebody already acted on: an entry on `main` has
been read, cited and built against, so rewriting it deletes the argument a
reversal would need.

An entry appended by this cycle and not yet merged has none of that history. It
has been read by the phases of one task, all of which are still running or
already superseded by a later round, and nothing outside the branch cites it.
Superseding it instead would put two entries on `main` where one is wrong from
birth, and the wrong one is the one a reader meets first.

The direction that does not change: an entry that is on `main` is superseded and
never edited, whatever a task finds convenient — and the correction here is a
claim the task itself falsified, not a decision the task re-made. Re-deciding
what an earlier round settled is a finding, not an edit.

## What to do

Edit the paragraph, in place, in the same round that fixes the code, and say in
the handoff that you did and why — the next phase has to know the entry moved.
Keep the ADR's number, its `**Status:**` line and the shape of its argument;
change the claim that is false and the sentences that reason from it. Then grep
for the stale version across `*.md` and `*.py`, because an implementation note
and a docstring usually quote the same number.

If the entry is on `main`, the answer is the other one: append, cite the number
you are narrowing, and leave every word of it alone. ADR 34 narrowing ADR 33 is
the worked example of that half.

## Evidence

T-014 round 2 edited `docs/decisions.md` ADR 34 *Every unknown keeps the
entries* from four exceptions to five, alongside the code fix in
`dispatcher/context_transfer.py:has_phase_section` and the same correction in
`docs/implementations/T-014.md`; `grep -rn` for the four-member tuple over
`*.md` and `*.py` came back empty afterwards, and the round-2 revisor approved
with the suite at 1117 passed, 10 skipped. The round-1 review that ruled it
right is `/data/.hive/tasks/T-014/review-round-1.md`, outside the repo.
