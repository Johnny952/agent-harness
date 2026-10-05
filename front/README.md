# `front/` — the operations console

The screens a human opens to watch the harness run: the board, a task's
detail, the pool of accounts, debt, learnings, the live tail, and the queue,
approvals, tokens, session logs, role models and backlog the Flask board never
had. A TanStack Start app in TypeScript, served on its own Node runtime.

It is the console under [`docs/charter.md`](../docs/charter.md) **C-8**, which
retired the Jinja board in `observability/board/` as the thing to build on and
bought Node for this directory alone — the four Python services stay as they
are. The board is not gone yet: C-8 keeps it until the console serves the four
screens it duplicates against the real api, and until then it is the
tie-breaking reference, because it has run.

**Six reads are live.** The board, the task detail, the pool, the debt index, the
live tail and the task detail's phase timeline read `observability/api/` through
this console's own server half; every other screen still reads mock fixtures, and
says so on itself where a region is waiting for a route. See *What is real and
what is not* below.

## Where the contract lives

Four documents, and none of them is this file:

| Doc | What it settles |
|---|---|
| [`docs/plans/front.md`](../docs/plans/front.md) | Which screens are coming, in what order, over which routes — and which reads the api will never serve. Read it before adding a screen or a query. |
| [`docs/ui.md`](../docs/ui.md) | The cross-screen vocabulary: tone per state, absent vs empty vs error, dates and ages, the nav and its chords. Binding — a screen that contradicts it is wrong, not different. |
| [`docs/decisions.md`](../docs/decisions.md) | The ADRs the console has to honour, ADR 14–28 in particular: the envelope, the warnings, the bearer forward, what each route answers, the four statuses a task actually has, and what a phase row is. |
| [`AGENTS.md`](AGENTS.md) | The four project rules, and the Lovable caveat below. |

A decision true of one screen belongs in that screen's task, not in any of
them. `docs/plans/front.md` *Where a UI definition lives* is the test.

## Running it

**Use `bun`, not `npm`.** The lockfile is `bun.lock` and
[`bunfig.toml`](bunfig.toml) carries a supply-chain guard this project relies
on:

```sh
bun install
bun run dev        # vite dev
bun run build      # vite build
bun run typecheck  # tsc --noEmit
bun run lint       # eslint .
bun run test       # vitest run
bun run test:watch # vitest
bun run format     # prettier --write .
```

`minimumReleaseAge = 86400` in `bunfig.toml` refuses any package version
published in the last 24 hours, which is the window most registry compromises
are caught in. Four `@lovable.dev/*` packages are excluded because the
toolchain itself ships that fast. **Adding a fifth exclusion is a decision to
make with the operator, not a lockfile fix.**

### The three environment variables

The console reads three names from its environment, and **none of them may take a
`VITE_` prefix**: Vite inlines every `import.meta.env.VITE_*` into the client
bundle, and one of the three is a credential. They are read in
`src/lib/api/server-env.ts`, on the server, once, at import.

| Name | Default | When it is missing |
|---|---|---|
| `API_BASE_URL` | `http://api:8789` — the compose network name, the same default the Jinja board takes | the default is used. Running against the api on the host means `API_BASE_URL=http://127.0.0.1:8789` |
| `API_TOKEN` | none | **the console does not start.** The module throws at import and `src/server.ts` imports it, so a missing token is a refusal to boot rather than a 401 from a screen. It is the same value `observability/api/` and the board read from `docker/compose/.env` |
| `CONSOLE_PROJECT` | none | `?project=` is omitted from the debt call and the api picks the project — right for a harness with one checkout, and a self-describing 400 for the other kind |

```sh
API_TOKEN=<the same token observability/api reads> \
API_BASE_URL=http://127.0.0.1:8789 \
CONSOLE_PROJECT=ia-harness \
bun run dev
```

`docs/decisions.md` ADR 15 and ADR 24 are the decisions; this table is here because
an env key only an ADR names is a key nobody sets
(`docs/learnings/an-env-key-only-an-adr-names-is-invisible.md`).

There is no compose service for this directory and the top-level
[`README.md`](../README.md) setup does not have one yet: how the console is
deployed is a task of its own, and the forward lives in `src/server.ts`, which is
the Node entry — a static build behind nginx cannot serve it.

## How it is put together

- **One client, one file.** Every read and write goes through
  `src/lib/api/client.ts`, one exported function per endpoint. Six of them —
  `listTasks`, `getTask`, `listAccounts`, `listEvents`, `listDebt`, `listPhases` —
  go through one boundary function to the real api and resolve to `ApiResult<T>`,
  the rows plus the envelope's `warnings`. The rest still resolve from
  `src/lib/api/mock/` after a fake 120ms. A component that fetches on its own has
  bypassed the seam.
- **The server half is two files.** `src/lib/api/server-env.ts` reads the three
  environment variables above and throws if the token is absent;
  `src/lib/api/forward.ts` is the whitelist that presents it. `src/server.ts` calls
  the forward before SSR — that import is also the boot-time refusal, so do not
  make it lazy. `src/start.ts`'s CSRF middleware filters
  `handlerType === "serverFn"`; the forward is not a server function and all six
  calls are reads, so its comment stays true. A write that ever goes this way
  re-opens that sentence.
- **Query keys and intervals are central.** `src/lib/api/queries.ts` holds
  every key and every `refetchInterval`. `POLL_MS` is 2500 and **nothing polls
  faster than 2s** — the harness is four containers and a SQLite file, not a
  CDN.
- **Types mirror the api, not the screens.** `src/lib/api/types.ts` is the
  served contract. `src/lib/api/ops-types.ts` is the half that has no backend
  at all — approvals, tokens, sessions, role models, backlog — and the
  separation is deliberate: it is the list of what would have to be built.
- **One shell.** Every screen renders inside
  `src/components/console/app-shell.tsx`, which owns the rail, the keyboard
  map and the chat dock. Routes are file-based under `src/routes/`; see
  `src/routes/README.md` and do not hand-edit `routeTree.gen.ts`.
- **The design system is `src/styles.css`.** Dark-first, dense, tabular, all
  colours as oklch tokens. `docs/ui.md` is its prose half.
- **Do not hand-extend `vite.config.ts`.** `@lovable.dev/vite-tanstack-config`
  already bundles the TanStack, React, Tailwind, nitro and path-alias plugins;
  adding them again breaks the build on duplicates. The file says so at the
  top.

## What is real and what is not

Of the twelve screens, five now read the api: **Board, task detail, Pool, Debt
and the live tail** — the task detail over two routes since T-013, the task card
and the phase timeline. The other seven are built against `ops-types.ts` or against
routes that do not exist, and have no backend of any kind: **Learnings,
Approvals, Tokens, Session logs, Role models, Backlog and Queue.** Writes are the
same story; `docs/plans/front.md` tier 3 puts the write surface in a different
service, for reasons that are about blast radius rather than convenience.

Tier 1 of that plan is parity with the Flask board's four screens against the real
routes. The api's half was built by T-011 and the console's by T-012 — the five
`client.ts` bodies, the warning banners and the server-side bearer forward — and
T-013 added the sixth, `/api/phases`, which fills the task detail's phase
timeline. **Parity is not reached**: the detail screen's learnings region still
wants `/api/learnings`, which is tier 2 and does not exist. That region names the
route it is waiting for instead of showing a fixture, so `observability/board/`
stays and C-8 keeps it the tie-breaking reference.

The Board is no longer waiting on a route, and its banner no longer says it is.
Its five role lanes wanted the role of the phase *running*, and no file in the
harness records that — the dispatcher knows it while the phase runs and persists
only a heartbeat, while `/api/phases` answers phases that have **ended**. So In
progress stays one column, the banner names the missing record rather than a
route, and the card carries `owner` and the api's `lock_expired` instead.
`docs/decisions.md` ADR 28.

The console authenticates as a service, not as a human: a bearer token read
server-side, never under a `VITE_` prefix, so Vite cannot inline it into the
browser bundle. See *The three environment variables* above and
`docs/decisions.md` ADR 15.

**Nothing under `front/` runs in the loop.** There is a `typecheck`, a `lint`
and a `test` since T-013, but the harness's `python3 -m pytest` does not see
this directory and no gate in `dispatcher/gates.py` *runs* any of the three —
so a green test gate on a commit that touches `front/src/` still says nothing
about it. **Run `bun run typecheck`, `bun run lint` and `bun run test` by hand
after editing.**

The gate does at least know this directory exists. Since T-013 `tests-in-diff`
pairs a changed file with a test in the **same language**, so a `front/src/**`
edit arriving beside nothing but a Python test is asked about instead of
cleared (`docs/decisions.md` ADR 31) — it never was blind to `.ts` and `.tsx`,
it just stopped looking once it had found any test at all. Making a gate
*execute* the three scripts is the open half of
[`docs/debt/T-013-D1.md`](../docs/debt/T-013-D1.md) step 3, and it is blocked
on an agent image with `bun` in it. What a red result costs is already ruled:
[`docs/charter.md`](../docs/charter.md) **C-10** — the typecheck blocks the
phase, the lint rides along as a note.

### Writing a test

Tests live beside what they test, as `*.test.tsx`, and two things about this
tree constrain where that can be:

- **Not under `src/routes/`.** `@tanstack/router-plugin` turns every file it
  finds there into a route and errors on one it cannot; the knob that would
  excuse a test file, `routeFileIgnorePattern`, has no default and is set in
  the Lovable-generated `vite.config.ts`, which C-9 keeps off-limits. A
  renderer worth a test moves to `src/components/console/` instead —
  `docs/decisions.md` ADR 30, and `src/components/console/payload.tsx` is the
  first one that did.
- **Import `describe`/`it`/`expect`/`afterEach` from `"vitest"`, and call
  `afterEach(cleanup)` yourself.** [`vitest.config.ts`](vitest.config.ts) sets
  `globals: false`, and `@testing-library/react` registers its own cleanup only
  when it finds a global `afterEach` — without that line every render in a file
  stacks in one document.

The config is a file of its own rather than a `test` key in `vite.config.ts`,
for the same C-9 reason; vitest prefers it over `vite.config.ts` and does not
merge the two, so it repeats the `tsconfigPaths()` and `react()` plugins.

## The Lovable round-trip

This directory was generated in [Lovable](https://lovable.dev) and adopted
whole. [`docs/charter.md`](../docs/charter.md) **C-9** closed the ruling C-8 left
open: `front/` lives in this repo, whole, and **the round-trip stays live**.

> Avoid rewriting published git history — force pushing, or
> rebasing/amending/squashing commits that are already pushed. Commits pushed
> to the connected branch sync back into the editor, so keep the branch in a
> working state.

That is now a constraint on `main` in this repo, not just on a side branch.
