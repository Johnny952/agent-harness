# "Write the answer into ADR *n* later" is never a valid fix — `decisions.md` forbids rewriting an entry

**When it applies:** you are writing the `fix` of a debt entry, a *Pending*
line or a plan's next step, and it asks a later task to add a finding, a number
or an answer to an ADR that already exists.

**Status:** unconfirmed — raised by T-024's revisor in round 1 against that
task's own second debt entry, and applied in round 2.

## Symptom

No error. A debt entry whose `fix` cannot be carried out as written: T-024's
second entry asked a later task to "write the answer into ADR 53's
*Consequences*" once it had probed whether a resumed call's `usage` is
cumulative.

## Why

`docs/decisions.md`'s preamble is the whole protocol for that file:

> Append; never rewrite an entry that is already here — to replace one, strike
> its heading through and point at the number that supersedes it.

What that protects is the reasoning behind a decision somebody already acted
on. An ADR on `main` has been read, cited and built against, so editing it
deletes the argument a reversal would need. A deferred *answer* is exactly the
thing that would arrive later, from outside, after the entry had been merged —
so an instruction to put it there is an instruction to break the rule.

The trap is that it reads as the helpful thing to do: the ADR is where the
question was raised, so it feels like where the answer belongs.

## Rule

Send the answer somewhere append-only or somewhere owned:

- **a new ADR** that narrows the old one and names its number, which is the
  move `docs/decisions.md` actually prescribes;
- **the implementation note** of whichever task does the work,
  `docs/implementations/<task-id>.md`, which that task owns outright;
- **a plan's Status paragraph**, the one paragraph any task may fix out of
  cycle ([a-plans-status-paragraph-is-the-one-out-of-cycle-edit](a-plans-status-paragraph-is-the-one-out-of-cycle-edit.md)).

The one exception is an entry **your own cycle appended and has not merged**,
which is corrected in place and not superseded — see
[an-adr-your-own-cycle-appended-is-not-yet-append-only](an-adr-your-own-cycle-appended-is-not-yet-append-only.md).
That is a correction of a claim your own task falsified, not the arrival of a
deferred answer, and the two should not be confused: the moment the branch
merges, only the first list above is available.

## Evidence

`docs/decisions.md`'s preamble, quoted above. T-024: the round-1 review found
the wording in the second proposed debt entry, round 2 redirected it, and
[`docs/debt/T-024-D2`](../debt/T-024-D2.md)'s *Fix* now asks for a new entry or
an implementation note. The same round applied the other half of the rule in
the other direction, correcting ADR 53 in place while it was still unmerged.
