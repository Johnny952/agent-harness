# A picker whose options come from the rows it filters can never be set to a value that matches nothing

**When it applies:** you are writing a step that has to put a `front/` screen
into its no-match state, or reasoning about whether a filter on one is
reachable, and the control is a `<select>` whose options are derived from the
data.

**Status:** unconfirmed — reported by T-017's revisor as a blocking finding in
round 1 and taken in round 2; the step as first written was unwalkable by
construction.

## Symptom

The step cannot be performed. T-017's **V0.6f** asked a walker to set
`/tail`'s `source` picker to a value matching no buffered event — and every
value the picker offers matches at least one, because the option list is built
from the buffer:

```tsx
const sources = Array.from(new Set(events.map((e) => e.source_app)));
const types = Array.from(new Set(events.map((e) => e.event_type)));
```

So the picker has no reachable value for the state the step is checking, and a
walker either gives up or invents a different action.

## Why

An option list derived from the unfiltered rows is, by construction, a list of
values each of which selects a non-empty subset. One such picker can narrow but
never empty. Two of them can, because `tail.tsx` **ANDs** them: a `source` that
exists and a `type` that exists can still have no event carrying both, so a
*non-covering pair* reaches the state while no single value does.

That has a corollary worth keeping: on a buffer where every source happens to
carry every type — a cross product — even the pair cannot reach it, and the step
is genuinely not applicable rather than failed. A check over derived pickers
needs that fallback spelled out or it reads as a defect on a quiet harness.

The free-text filter beside them is not derived from anything, so it reaches
no-match with any three characters. That is why a screen's *typed* filter is the
easy half of such a check and its pickers are the half that needs thought.

## What to do

Reach no-match through the typed filter, or through a **pair** of derived
pickers chosen against the rows actually on screen — and say in the step how to
pick the pair, since it depends on the buffer the walker happens to have. Give
the step a not-applicable verdict for the cross-product case rather than a
failure.

When resetting afterwards, reset **every** control the step touched: a check
that sets two pickers and clears one leaves the screen in a state the next step
did not expect.

## Evidence

T-017's V0.6f steps 3–4 and Pass bullets 3–4, `docs/ROADMAP.md`, found by its
revisor in round 1 (F2) and corrected in round 2 — the implementador confirmed
against `front/src/routes/tail.tsx` that both option lists are built off the
unfiltered `events` and that the two predicates are ANDed, and the revisor
re-checked that the rows show both columns so a walker can choose the pair.
`docs/implementations/T-017.md` *V0.6f as first written was three checks a
walker could not pass* has the paragraph.
