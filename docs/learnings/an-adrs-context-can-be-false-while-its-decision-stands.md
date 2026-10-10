# An ADR's *Context* can be false while its *Decision* still stands — narrow it by naming which half was the premise

**When it applies:** you found a claim in an accepted `docs/decisions.md`
entry's *Context* to be wrong, and you are deciding what to write — or two
accepted entries disagree about what is possible and your task has to reconcile
them. Also when the doc you were asked to correct turns out to be the prose form
of an ADR, which makes the edit a decision first.

**Status:** unconfirmed — one task. T-018 was given one README bullet and could
not write it without ruling on two entries first.

## Symptom

Two accepted entries, both in force, describing the same thing incompatibly.
`docs/decisions.md` **ADR 30** says a test cannot live under
`front/src/routes/`, so a renderer worth a test moves to
`front/src/components/console/`. **ADR 44**'s third bullet has each route export
the component holding its own branches, "tested with a plain `render`", and
T-017 shipped three such tests *under* `src/routes/`. Four files existed where
one entry said none could.

No gate reports this. The suite is green either way, and the disagreement shows
up as a doc telling a phase its own tree is impossible.

## Why

An ADR has two separable halves and they fail independently.

ADR 30's *Context* was a mechanism — the route generator scans every file and
the escape hatch lives in a config file `docs/charter.md` **C-9** keeps
off-limits. That was wrong in three of its four clauses
([a-docs-claim-about-a-dependency-is-a-claim-about-a-version](a-docs-claim-about-a-dependency-is-a-claim-about-a-version.md)).

ADR 30's *Decision* was a rule about when a component is promoted: being
unreachable from a test is a reason, with the same standing as a second screen
needing it. That rule was never about the generator. It survives intact — what
changes is the condition it fires on, from "always, under `src/routes/`" to
almost never.

Reading the entry as one thing makes it look superseded, and a strikethrough
would delete a rule nothing was wrong with, plus the record of how a plausible
sentence survived six tasks.

## What to do

Append a narrowing entry. It keeps its own number, says **narrows X and does not
supersede it** in its `**Status:**` line, and X keeps its number, its status,
its wrong *Context* and an unstruck heading
([a-stale-count-in-a-dated-paragraph-is-not-a-finding](a-stale-count-in-a-dated-paragraph-is-not-a-finding.md),
*an ADR on `main`*). Three things the new entry owes that a plain ADR does not:

- **Which clauses of the old *Context* were wrong, and which was right.** ADR 45
  says ADR 30 was accurate on one clause of three, and that the accurate one is
  what made the sentence plausible. A reader who finds the old entry first needs
  to know what to keep.
- **Premise versus decision, in those words.** Name what is retired as the
  *premise* and what survives as the *decision*, then say what the surviving
  rule's trigger now is. An ADR whose *Decision* reads as a bare rule survives
  its own reasoning; one whose *Decision* restates the mechanism does not, and
  then you are superseding rather than narrowing.
- **Every surface that transcribed the old reasoning.** The prose copies are the
  task's actual work, and the new entry's *Consequences* is where they are
  enumerated, so the next reader can check the list was finished
  ([correcting-a-false-mechanism-greps-the-identifier](correcting-a-false-mechanism-greps-the-identifier.md)).

If the entry also reconciles a *second* ADR that had already decided the other
shape without saying so, say that too: ADR 44's third bullet was the right
answer before ADR 45 generalised it from three screens to the rule, and a reader
of ADR 44 alone cannot tell it overrode anything.

One thing not to do: do not treat the wrong *Context* as the task's only
finding. ADR 30's mechanism being false is why the rule almost never fires, so
a component already promoted under the retired trigger does not move back —
re-deriving green tests to undo a move is churn, and the new entry should rule
on that explicitly rather than leaving it to the next reader to wonder.

## Evidence

T-018. The narrowing entry is [`docs/decisions.md`](../decisions.md) **ADR 45**,
appended by the arquitecto before any edit was made, because the bullet the card
named could not be written without it; the two entries it reconciles are **ADR
30** and **ADR 44**, both untouched. Which half was wrong and which stands is in
that entry and in
[`docs/implementations/T-018.md`](../implementations/T-018.md) *The mechanism,
read rather than repeated*. The one case where editing an entry in place is
right instead is
[an-adr-your-own-cycle-appended-is-not-yet-append-only](an-adr-your-own-cycle-appended-is-not-yet-append-only.md).
