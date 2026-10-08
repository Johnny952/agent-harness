# `front/`'s lint runs prettier as an eslint rule, so a wide line is an error and not a warning

**When it applies:** you are editing a file under `front/src/` and reasoning
about what the `lint:` entry of [`docs/README.md`](../README.md)'s frontmatter
will say about your edit — in particular if you are treating the lint as
"warnings only" because `docs/charter.md` **C-10** makes a red lint a note.

**Status:** confirmed — `front/eslint.config.js` imports
`eslint-plugin-prettier/recommended`, and the 124 `prettier/prettier` findings
that made `eslint .` red on `main` before 2026-10-08 were **errors**, not
warnings (`docs/decisions.md` ADR 39, and
[`docs/debt/T-013-D1.md`](../debt/T-013-D1.md)'s measurements).

## Symptom

A lint that had been exiting zero with warnings exits 1 instead, and the new
finding is not about your logic:

```
bun run lint   →   exit 1, 134 problems (124 errors, 10 warnings)
```

Every one of those 124 errors was layout. They were formatted away in a single
commit, which is what made the gate's `lint:` entry usable at all.

## Why

`front/eslint.config.js`'s last entry is `eslint-plugin-prettier/recommended`,
which registers `prettier/prettier` at **error** severity. So prettier's opinion
about your line is an eslint error on the same run as the two rules that are
configured as warnings — `react-refresh/only-export-components` and
`react-hooks/exhaustive-deps`. The settings prettier judges it against are
`front/.prettierrc`: `printWidth` 100, semicolons, double quotes, trailing
commas everywhere.

The consequence is asymmetric and easy to get backwards. A *warning* count going
up or down leaves the lint green, because `eslint .` exits zero on warnings; a
single line past 100 columns turns the whole lint red. And "red lint is only a
note" (C-10) means nothing tells you — the round is not blocked, so an
unformatted line rides to `main` and the next task inherits a red gate.

One thing prettier will not do for you: it does not rewrap comments. A comment
block you write at 120 columns stays at 120 columns and is not an error either,
because `printWidth` is a target for code prettier reformats rather than a
hard limit it enforces on prose.

## What to do

Count the columns of any line you add under `front/src/`, including the closing
`;`, and keep code under 100 — by reading the file with the `Read` tool, since
`awk` over a file is refused in a phase and `bun run prettier` is not reachable
either (no phase of this project can run a `front/` script: see
`/data/.hive/learnings/inbox/T-015-bun-is-refused-in-a-phase.md`). Match the
four `.prettierrc` settings by hand: semicolon, double quotes, trailing comma.

When you declare what green means for a `front/` change, state the exit code
*and* the warning count separately, because they fail independently. T-015's
was: `bun run lint` exits 0 with nine warnings, every one
`react-refresh/only-export-components` — the exit code covers prettier, the
count covers the fix.

## Evidence

T-013-D1 step 3's measurements, taken by hand in a recreated `agent-cuenta1`:
the lint 3.2 s, red with 124 prettier errors and 10 warnings, green with 10
warnings after the formatting commit — `docs/debt/T-013-D1.md` and
`docs/decisions.md` **ADR 39**. T-015's implementador checked its new line at 67
columns against `printWidth` 100 by reading the file, having no way to run
prettier, and recorded the reasoning in
[`docs/implementations/T-015.md`](../implementations/T-015.md).
`front/eslint.config.js` and `front/.prettierrc` are the two files that settle it.
