# "No granted command writes X" is measured, not argued: run them all, then `git status --porcelain`

**When it applies:** a doc you are writing or correcting claims that some file
is left alone by the commands a phase may run — a generated file nothing
regenerates, a lockfile an install does not rewrite, a cache that stays out
of the tree — and your card grants those commands.

**Status:** unconfirmed — one task. Proposed by all three of T-019's phases,
from the same check.

## Symptom

None, which is the trouble. The argued form reads as settled: T-019 wanted
"no granted command regenerates `front/src/routeTree.gen.ts`", and it can be
argued from the plugin graph — `front/package.json`'s granted scripts are
`tsc --noEmit`, `eslint .` and `vitest run`; the route generator rides the
`tanstackStart` plugin `front/vite.config.ts` loads for `dev`, `build` and
`preview`, none of them granted; `front/vitest.config.ts` loads `react()`
alone. Every link in that chain is a reading of config, and one plugin added
to the vitest config breaks it without touching any doc that repeats it.

## Why

A claim that a command leaves a file alone is a claim about what the command
*does*, and the command is right there to run. The argument names which
mechanism should hold; the measurement shows the outcome, including through
mechanisms nobody thought to read — a postinstall hook, a type-check plugin,
a formatter run by lint. The two together are stronger than either: the
measurement says it is true now, the argument says why, so the next reader
knows what change would make it false.

## What to do

From a clean tree, run every command the card grants that could plausibly
touch the file — the install first, since the others run on its output —
then `git status --porcelain`. Nothing listed outside what you edited by hand
is the evidence; record it in your implementation note beside the argued
form, not instead of it.

`--porcelain` lists tracked and untracked files but not ignored ones. When the
file in question is gitignored — `front/node_modules`, a build cache — add
`--ignored`, or check the path directly; an empty porcelain says nothing
about it.

## Evidence

`docs/implementations/T-019.md` *Verified first-hand, not taken from the
card*: the argued row and the measured row sit side by side, and the measured
one — porcelain after `cd front && bun install --frozen-lockfile` and all
three scripts listed only the docs the task edited — is the one the note
calls the useful one of the seven. Proposed in the handoffs of T-019's
arquitecto, implementador and revisor.
