# Adding a `docs/ROADMAP.md` check id means editing the prose that enumerates the ids too

**When it applies:** you are appending a numbered check to `docs/ROADMAP.md` —
a `V0.6f` beside `V0.6e` — and have written the recipe, the stage table row and
the Results-log row.

**Status:** unconfirmed — reported by T-017's revisor as a blocking finding in
round 1 and taken in round 2; it had already happened once, in T-016.

## Symptom

An earlier check's prose silently goes short by one. `docs/ROADMAP.md`
**V0.6b**'s first bullet hands the reader forward to the checks that cover the
screens it no longer covers itself:

```
V0.6c, V0.6d and V0.6e are the checks that cover those screens from here on
```

T-017 appended **V0.6f**, which covers `/learnings` and `/debt` — two of exactly
those screens — and that sentence still named three. Nothing failed: the gate
reads citations, not enumerations, and a list that is short by one is
indistinguishable from a list that is deliberately partial.

## Why

A check id appears in more places than the three a task thinks about. The
recipe, the stage table and the Results log are the mechanical set, and the
project also has **prose that enumerates ids** — a retargeted check pointing
forward at its successors, a stage's summary paragraph. Those sentences were
true when written and go stale on the next append, and nothing derives them from
anything.

This is the same shape as `the-forwarded-route-count-is-prose-and-derived-from-nothing`
one directory over: a count or a list spelled in English, in a file nobody opens
because of the change they are making.

## What to do

After appending the id, grep `docs/ROADMAP.md` for the id **before** yours
(`V0.6e`) and read every hit that is not your own new text. An enumeration ending
in that id is the shape to fix, and the fix is one clause.

Then check what each enumeration is actually about before extending it: V0.6b's
is "the checks that cover those screens", so a new check belongs in it only if
it covers one of them. A new check about an unrelated screen does not go in that
sentence, and adding it would be the opposite error.

## Evidence

T-016 made this exact edit on the same bullet — `git show f2937aa -- docs/ROADMAP.md`
is `V0.6c and V0.6d` → `V0.6c, V0.6d and V0.6e` — and T-017 had to make it
again, found by its revisor (F3) rather than by its author. The bullet now reads
`V0.6c, V0.6d, V0.6e and V0.6f`. Related:
[`retargeting-a-roadmap-check-keeps-its-id`](retargeting-a-roadmap-check-keeps-its-id.md),
which is where that pointer sentence comes from in the first place, and
[`the-forwarded-route-count-is-prose-and-derived-from-nothing`](the-forwarded-route-count-is-prose-and-derived-from-nothing.md).
