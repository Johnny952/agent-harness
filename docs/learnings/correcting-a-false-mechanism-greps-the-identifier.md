# Correcting a false mechanism greps the identifier, not the prose — and the one surface that misses names no identifier at all

**When it applies:** your task's job is to correct a claim that is wrong rather
than merely out of date, and you are working out how many places repeat it. Also
when you are about to declare a correction complete on the strength of a search
for the sentence you were pointed at.

**Status:** unconfirmed — one task. T-018 was given one bullet to fix and found
five surfaces plus a sixth that no search could reach.

## Symptom

The card named one sentence. A grep for the sentence found exactly it:

```
$ grep -rn "Not under" docs/ front/ skills/
front/README.md:188:- **Not under `src/routes/`.** `@tanstack/router-plugin` …
```

A grep for the *identifier* inside the claim found the rest of it:

```
$ grep -rln routeFileIgnorePattern docs/ front/
front/README.md
front/src/components/console/payload.tsx          (docblock)
front/src/components/console/learnings.tsx        (docblock)
docs/decisions.md                                 (ADR 30, append-only)
docs/learnings/a-route-can-export-the-component-…md
docs/implementations/T-017.md                     (a dated record)
```

Plus one more a grep for either term misses entirely:
`front/src/routes/README.md` said "Every `.tsx` file in this directory defines
a route", which is the same false mechanism with no knob named — found by asking
what *else* would have to be true if the claim were.

## Why

A false mechanism spreads by citation, and each citation rewords it. The prose
is the thing that varies; the identifier is what a copy keeps, because the
identifier is the part the writer could not paraphrase. Two docblock comments,
a Lovable-generated README, a learnings entry and its index row all carried the
wrong knob in different sentences.

The surfaces that name no identifier at all are the limit of this method, and
they are not all the same kind. `front/src/routes/README.md` was live prose
describing the directory as it is, so T-018 corrected it. But
`docs/implementations/T-016.md` states the retired mechanism in prose too, and
was deliberately left: `dispatcher/project_docs.py` puts `IMPLEMENTATIONS_DIR`
in `RECORD_DOCS`, whose comment is this project's own ruling — "leave the
sentence alone and record the disagreement somewhere newer". Read that split in
the code before classifying a stale sentence by instinct;
[a-stale-count-in-a-dated-paragraph-is-not-a-finding](a-stale-count-in-a-dated-paragraph-is-not-a-finding.md)
is the four classes it implies.

## What to do

Three greps, in this order, before you call a correction scoped:

1. The **identifier** inside the false claim — the config key, the function,
   the flag — across `docs/` and `front/` and any other subproject. Case
   sensitively, and without a `{n,}` quantifier or a backtick in the pattern,
   which the Bash permission layer refuses.
2. The **consequence** the claim implies, worded as the doc would word it
   ("every file … defines a route"). This is what reaches a surface that cites
   no knob.
3. The identifier again, once the edits are in, and check that every remaining
   hit is legitimate: an append-only ADR, the narrowing entry itself, a dated
   record, an entry naming which knob was chosen wrongly, the implementation
   note, these learnings. After T-018 no hit is left under `front/` at all and
   every one under `docs/` is one of those kinds — say which in the handoff, so
   the next reader does not re-triage them.

Then classify each hit before editing it: live prose is yours, a record takes
at most one clause naming your task and your ADR, and an ADR on `main` is
narrowed and never edited.

## Evidence

T-018. The five corrected surfaces and the sixth that took one clause are
[`docs/implementations/T-018.md`](../implementations/T-018.md) *The five
surfaces that repeated the retired mechanism*; the decision they all point at
is [`docs/decisions.md`](../decisions.md) **ADR 45**. The surface left as
written, with the rule it was left on, is
`/data/.hive/tasks/T-018/review.md` *Weighed and deliberately not raised as
blocking findings*, item 1. The code that rules on it is
`dispatcher/project_docs.py:PRESENT_DOCS`, `RECORD_DOCS` and `is_record`.
