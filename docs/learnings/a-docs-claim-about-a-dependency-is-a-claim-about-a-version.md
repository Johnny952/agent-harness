# A doc's claim about a dependency's default is a claim about a version — read the `.default()` out of `front/node_modules`

**When it applies:** you are about to write, repeat or plan around a sentence
saying what a `front/` dependency does — a knob's default, a plugin's scan
rule, what a build step rejects — and your source is another doc rather than
the installed package. Also when a doc tells you something in `front/` is
impossible and the tree contains files that would be impossible if it were.

**Status:** unconfirmed — one task. T-018 found the claim wrong in five places
and the counter-evidence sitting in the same repository for six tasks.

## Symptom

No error, which is the problem. `front/README.md` *Writing a test* said:

```
- **Not under `src/routes/`.** `@tanstack/router-plugin` turns every file it
  finds there into a route and errors on one it cannot; the knob that would
  excuse a test file, `routeFileIgnorePattern`, has no default and is set in
  the Lovable-generated `vite.config.ts`, which C-9 keeps off-limits.
```

Four files under `front/src/routes/` — `-index.test.tsx`,
`-learnings.test.tsx`, `-debt.test.tsx`, `-tail.test.tsx` — had been passing
all along, and `front/src/routeTree.gen.ts` mentions none of them. The bullet
sat there from T-013 to T-018, was the prose form of `docs/decisions.md`
**ADR 30**, and propagated to four more surfaces, because each new one cited
the bullet rather than the package.

## Why

Two knobs, one letter apart in meaning. Read with `front/node_modules` filled:

| Fact | Where |
|---|---|
| `routeFileIgnorePrefix` is `z.string().optional().default("-")` | `@tanstack/router-generator/dist/esm/config.js`, `baseConfigSchema` |
| `routeFileIgnorePattern` is `z.string().optional()` — no default | same schema, the next line |
| a dirent whose name `startsWith` the prefix is dropped from the `readdir` listing before anything parses it; the pattern is consulted only where it is set | `@tanstack/router-generator/dist/esm/filesystem/physical/getRouteNodes.js`, `getRouteNodes` |
| a file read as a route that exports no `Route` is a **warning** suggesting the `-` prefix, and is omitted from the tree — not an error | same package, `dist/esm/generator.js`, the `no-route-export` branch |

So the doc named the one knob of the two that is not in use, and was right
about *that* knob's missing default — which is what made the sentence
plausible. Nothing in this repo sets either: `front/vite.config.ts` passes
`tanstackStart: { server: { entry: "server" } }` and nothing else, and a grep
for `routeFileIgnore` across `front/node_modules/@lovable.dev/` is empty. The
four test files rely on a **default**, so `docs/charter.md` **C-9**'s
off-limits config file was never in the way.

A default is a property of an installed version, and a doc repeating one has no
way to go red when the version moves. Here three copies of the generator are
installed — 1.167.21 hoisted, 1.167.40 nested twice — and all three agree, but
that is a fact about today's lockfile and not about the sentence.

## What to do

Install and read it. A phase whose card grants the `front/` scripts can:

```
cd front && bun install --frozen-lockfile
```

and then grep the package for the identifier — the zod schema is where a
default is declared, and the function that consumes it is where the behaviour
is. Two phases of T-018 did this independently and got the same four rows.
`docs/decisions.md` **ADR 24** still reads as though a phase cannot do this,
and **ADR 46** is the entry that narrows it: the install is any phase's whose
card grants it, the form is above, and the row that asked for the narrowing is
[`docs/debt/T-018-D1.md`](../debt/T-018-D1.md), resolved by T-019. Read your own
card's command grant first either way — it, and not a doc, is what says whether
you may install.

Then write the doc so the mechanism lives in **one** place. T-018's bullet
names the real knob and its `-` default and then points at
`docs/decisions.md` **ADR 45** for the reasoning, instead of five surfaces each
carrying their own copy — a version-bound fact is cheapest to correct where
there is one of it.

Prefer a claim the build would falsify loudly. `routeFileIgnorePrefix`'s
default is load-bearing for four filenames and for `front/src/routeTree.gen.ts`,
which no phase can regenerate, so a bump that changed it breaks the build
rather than rotting a sentence.

## Evidence

T-018, whose whole subject was this one bullet. The decision is
[`docs/decisions.md`](../decisions.md) **ADR 45** and the record is
[`docs/implementations/T-018.md`](../implementations/T-018.md) *The mechanism,
read rather than repeated*, which carries the table above with line numbers as
measured. The counter-evidence that was always in the tree is the header of
`front/src/routes/-index.test.tsx`, added by T-012 in `bc93b1d`, which states
the mechanism correctly. T-017's auditor spotted the disagreement and correctly
refused to fix it in passing:
[`docs/implementations/T-017.md`](../implementations/T-017.md), *One live
instruction disagrees with this tree*.
