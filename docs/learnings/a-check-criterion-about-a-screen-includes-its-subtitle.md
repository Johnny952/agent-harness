# A `docs/ROADMAP.md` Pass criterion phrased "no sentence on the screen says X" is a claim about the whole screen

**When it applies:** you are writing or reviewing a numbered check in
`docs/ROADMAP.md` whose Pass criterion forbids a word or a sentence on a
console screen, and what you actually mean is the one state the step put the
screen into.

**Status:** unconfirmed — reported by T-017's revisor as a blocking finding in
round 1 and taken in round 2; the criterion as first written could not be passed
by a correct console.

## Symptom

A walker following the recipe fails a step that is not wrong. T-017's **V0.6f**
Pass bullet 1 read, in effect, *no sentence on either screen says a phase wrote
anything* — and `/learnings`' `PageHeader` subtitle says it in both arms:

```
cap === undefined
  ? "Every trap this project's phases can be handed, in the order a phase sees them."
  : `In the order a phase sees them. Only the first ${cap} are handed to one.`
```

So does the over-cap `Banner` right below it, which counts entries "past the cap
that reach no phase". Neither has anything to do with the empty state the check
is about, and both are correct.

## Why

A console screen is a shell, a header with a subtitle, zero or more banners, a
filter row and then the content. `docs/ui.md` *Absent, empty and broken are
three different things* binds **the state's own two sentences** — an
`EmptyState`'s `title` and `body` — and says nothing about the rest of the
screen, which has its own rules and its own reasons to name the same nouns.
"No sentence on either screen" quantifies over all of it.

The trap is that the criterion reads as a strengthening — more cautious, harder
to pass — when it is actually a different claim, about parts of the screen the
step never touched. And the gap is invisible from the recipe: finding it means
opening the screen and reading every sentence it renders, which is why it
survived being written and reviewed once.

## What to do

Scope the criterion to the thing the step produced and name what is outside it.
V0.6f's bullet now binds the empty state's own title and body and says in the
same breath that `/learnings`' subtitle and its over-cap banner are correct and
out of scope — so the walker who reads a phase mentioned on screen is told
whether it matters.

When writing any Pass bullet about copy, ask what renders above the thing you
changed. On these screens that is always a subtitle and sometimes a banner.

## Evidence

T-017's V0.6f, `docs/ROADMAP.md`, first Pass bullet, found by its revisor in
round 1 (F1) and corrected in round 2 — the implementador confirmed the subtitle
against `front/src/routes/learnings.tsx` before taking the finding, and
`front/src/routes/debt.tsx`'s subtitle is clean, so "either screen" could not
stand even reworded. `docs/implementations/T-017.md` *V0.6f as first written was
three checks a walker could not pass* has the paragraph.
