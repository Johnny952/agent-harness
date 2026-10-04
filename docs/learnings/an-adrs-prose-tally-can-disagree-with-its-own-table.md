# An ADR that sorts a fixed set into buckets: count the table, because the prose can sum right and sort wrong

**When it applies:** you are writing, reviewing or relying on an entry in
`docs/decisions.md` that sorts a fixed number of items into named buckets — a
field-by-field sort, a screen-by-screen tier, a key list — and then counts them
in a *Consequences* paragraph.

**Status:** unconfirmed — one occurrence, caught by T-013's revisor as finding 2
and corrected by hand in commit `aba17b2` before the entry was ever read by
another task.

## Symptom

ADR 27's table sorts twenty-two fields into five buckets. Counted from the
table, the sort is **3 / 7 / 4 / 7 / 1**. Its *Consequences* paragraph said
**4 / 6 / 3 / 8 / 1**.

Both sum to 22. That is what makes it invisible: the arithmetic check a reader
actually performs — do the parts add up to the whole — passes, so nothing looks
wrong unless you count each bucket in the table by hand.

## Why it matters more than a typo

The prose tally is the part a later task quotes. ADR 27's own closing paragraph
reasons *from* the numbers — "five of those fields have one fix between them…
that reaches four of the seven *present nowhere* rows" — and three further
places on the branch had inherited the same claim in a stronger form, saying one
dispatcher state line would serve *every* `present nowhere` row. It would not:
it reaches five fields and leaves `commit_sha` and `envelope.to_role`, and
`write_rejected` has nothing to persist at all because the harness prevents that
write rather than recording it. One wrong tally had propagated into
`docs/implementations/T-013.md` and into ADR 28's closing paragraph, which is
where the next task actually looks for "is this field coming?".

## What to do

Count the table, bucket by bucket, and write the tally from that count — then
read every sentence in the entry that reasons from a number and check it against
the same count. The ones to re-read are the forward-looking claims: *n of these
are fixed by X* is a promise to a later task, and it is the sentence a reader
trusts without re-deriving.

If you are *reviewing*: a tally that sums correctly has told you nothing. The
only check that works is counting the buckets.

If a landed ADR's tally is wrong, this does not license rewriting it.
`docs/decisions.md` is appended and never rewritten, and the one correction here
was made by the operator inside the same cycle that wrote the entry, before it
landed on `main` —
[a-new-doc-needs-its-row-and-an-adr-does-not](a-new-doc-needs-its-row-and-an-adr-does-not.md)
for what an ADR does and does not owe, and `docs/charter.md` *Where a rule
belongs* for the superseding path a later task has to use instead.

## Evidence

T-013: `docs/decisions.md` ADR 27's twenty-two-row table against its own
*Consequences*; revisor round 1's finding 2 in
`/data/.hive/tasks/T-013/revisor-round-1-findings.md`; the correction and the
three propagated claims are commit `aba17b2`, whose message lists them.
