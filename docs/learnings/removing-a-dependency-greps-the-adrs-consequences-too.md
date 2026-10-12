# Removing a dependency or a feature greps the ADRs' *Consequences* too

**When it applies:** your task removes something an earlier task left
behind on purpose (a package, a config key, a file) and you have updated the
root `README.md`, `docs/README.md` and the debt entry, and are about to call
the docs done.

**Status:** unconfirmed — it cost T-032 a revision round (finding F2); the
auditor read the diff of `docs/decisions.md`.

## What is true

An ADR's *Consequences* paragraph states what its change left standing, in
the present tense: ADR 59 said the `mcp` package "is still installed by
`pyproject.toml` and the agent image and nothing imports it". The task that
later removes the leftover makes that sentence false, and no gate reads it:
`docs/decisions.md` is a record, so the pointers gate checks only lines a
task adds.

T-032 dropped `mcp`, corrected `docs/README.md` *Stack* and the debt entry,
and left ADR 59 saying it was installed. The revisor sent it back.

## What to do

Grep `docs/decisions.md` for the name of what you remove. Do not rewrite the
sentence: it is a record of the day it was written. Append one dated clause
to it naming the task and where the removal is recorded, as ADR 59 now
reads: "(removed from both by T-032, 2026-10-12; `docs/debt/T-028-D2.md`)".
That is the fourth class of
[a-stale-count-in-a-dated-paragraph-is-not-a-finding](a-stale-count-in-a-dated-paragraph-is-not-a-finding.md).
A decision that changed, not a leftover that went, needs a new ADR instead.

## Evidence

T-032: `docs/decisions.md` ADR 59 *Consequences*;
`docs/implementations/T-032.md` *Round 2: the two review findings*.
