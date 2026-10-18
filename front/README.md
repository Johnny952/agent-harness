# `front/` — the operations console

The screens a human opens to watch the harness run: the board, a task's
detail, the pool of accounts, debt, learnings, the live tail, and the queue,
approvals, tokens, session logs, role models and backlog the Flask board never
had. A TanStack Start app in TypeScript, served on its own Node runtime.

It is the console under [`docs/charter.md`](../docs/charter.md) **C-8**, which
retired the Jinja board in `observability/board/` as the thing to build on and
bought Node for this directory alone — the three Python services stay as they
are. **The board is gone.** C-8 kept it until the console served the four screens
it duplicated against the real api; those four now do, so it was deleted on
2026-10-04 (`docs/decisions.md` ADR 32). It is no longer the tie-breaking
reference, and `git log` is where it went.

**Eight reads are live.** The board, the task detail, the pool, the debt index,
the live tail, the task detail's phase timeline, the learnings inbox and the
task detail's usage region read
`observability/api/` through
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

### The five environment variables

The console reads five names from its environment, and **none of them may take a
`VITE_` prefix**: Vite inlines every `import.meta.env.VITE_*` into the client
bundle, and two of the five are credentials. They are read in
`src/lib/api/server-env.ts`, on the server, once, at import.

| Name | Default | When it is missing |
|---|---|---|
| `API_BASE_URL` | `http://api:8789` — the compose network name, the same default the Jinja board took | the default is used. Running against the api on the host means `API_BASE_URL=http://127.0.0.1:8789` |
| `API_TOKEN` | none | **the console does not start.** The module throws at import and `src/server.ts` imports it, so a missing token is a refusal to boot rather than a 401 from a screen. It is the same value `observability/api/` reads from `docker/compose/.env` |
| `CONSOLE_PROJECT` | none | `?project=` is omitted from the debt call and the api picks the project — right for a harness with one checkout, and a self-describing 400 for the other kind |
| `CONFIG_WRITER_URL` | `http://127.0.0.1:8791` — the config writer on the host; a trailing slash is trimmed | the default is used |
| `CONFIG_WRITER_TOKEN` | none | **the console still starts.** Every `/api/settings` call answers a JSON 503 naming this variable instead, so the Settings screen says what to set rather than the console refusing to boot over one screen. Unset, empty and whitespace all count as unset. It is the token `config_writer/` reads |

```sh
API_TOKEN=<the same token observability/api reads> \
API_BASE_URL=http://127.0.0.1:8789 \
CONSOLE_PROJECT=ia-harness \
CONFIG_WRITER_TOKEN=<the token config_writer reads> \
bun run dev
```

`docs/decisions.md` ADR 15 and ADR 24 are the decisions, and ADR 112 for the two `CONFIG_WRITER_*` names; this table is here because
an env key only an ADR names is a key nobody sets
(`docs/learnings/an-env-key-only-an-adr-names-is-invisible.md`).

There is no compose service for this directory and the top-level
[`README.md`](../README.md) setup does not have one yet: how the console is
deployed is a task of its own, and the forward lives in `src/server.ts`, which is
the Node entry — a static build behind nginx cannot serve it.

Since 2026-10-17 it does have one: the `console` service in both compose files,
on `127.0.0.1:8790`, built for Node by `docker/console/Dockerfile`
(`docs/decisions.md` ADR 97).

## How it is put together

- **One client, one file.** Every read and write goes through
  `src/lib/api/client.ts`, one exported function per endpoint. Six of them —
  `listTasks`, `getTask`, `listAccounts`, `listEvents`, `listDebt`, `listPhases` —
  go through one boundary function to the real api and resolve to `ApiResult<T>`,
  the rows plus the envelope's `warnings`. The rest still resolve from
  `src/lib/api/mock/` after a fake 120ms. A component that fetches on its own has
  bypassed the seam.
- **The server half is two files.** `src/lib/api/server-env.ts` reads the five
  environment variables above and throws if the token is absent;
  `src/lib/api/forward.ts` is the whitelist that presents it. `src/server.ts` calls
  the forward before SSR — that import is also the boot-time refusal, so do not
  make it lazy. `src/start.ts`'s CSRF middleware filters
  `handlerType === "serverFn"`; the forward is not a server function and all eight
  calls are reads, so its comment stays true. A write that ever goes this way
  re-opens that sentence.
- **One prefix goes elsewhere.** `/api/settings` (GET) and
  `/api/settings/<key>` (PUT, DELETE) are forwarded to the config writer, not the
  api, with `CONFIG_WRITER_TOKEN`, `X-Actor: operator` and — for a write — the
  body bytes, and nothing else of the browser's request. Those are the first
  writes through the forward; what keeps them from being cross-site is
  `docs/decisions.md` ADR 112 part 7: no CORS header is ever sent and OPTIONS and
  POST are a 405, so a foreign page cannot send a PUT or a DELETE here at all.
  The Settings screen at `/models` reads it (ADR 113) and writes through it: a
  save is a PUT and a reset a DELETE, each carrying the revision it was read at
  (ADR 114).
- **Query keys and intervals are central.** `src/lib/api/queries.ts` holds
  every key and every `refetchInterval`. `POLL_MS` is 2500 and **nothing polls
  faster than 2s** — the harness is four containers and a SQLite file, not a
  CDN.
- **Types mirror the api, not the screens.** `src/lib/api/types.ts` is the
  served contract. `src/lib/api/ops-types.ts` is the half that has no backend
  at all — approvals, tokens, sessions, role models — and the
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

Of the twelve screens, seven now read the api: **Board, task detail, Pool, Debt,
the live tail, Learnings and Backlog** — the task detail over three routes, the
task card and the phase timeline since T-013 and its learnings region since
T-016, and the Backlog over `/api/tasks` since T-093, writing its accept, defer,
drop and reorder through the board-service (ADR 138). The other five are built
against `ops-types.ts` or against routes that do not exist, and have no backend
of any kind: **Approvals, Tokens, Session logs, Role models and Queue.** Their
writes are the same story; `docs/plans/front.md` tier 3 puts the write surface in a different
service, for reasons that are about blast radius rather than convenience.

Tier 1 of that plan is parity with the Flask board's four screens against the real
routes. The api's half was built by T-011 and the console's by T-012 — the five
`client.ts` bodies, the warning banners and the server-side bearer forward — and
T-013 added the sixth, `/api/phases`, which fills the task detail's phase
timeline. **Parity is reached and the board is gone**: the four screens it had —
the board, a task's detail, debt and the events tail — all read the real api, so
C-8's condition fired and `observability/board/` was deleted on 2026-10-04
(`docs/decisions.md` ADR 32).

Tier 2's second route, `/api/learnings`, landed with T-016 (ADR 41 and ADR 42):
the Learnings screen reads the hive's trap entries in the order a phase's own
prompt table is in, and the detail's learnings region shows the entries the task
filed or carries. What that region still cannot show is which table a phase was
*handed* — that is recomputed at every dispatch and written down nowhere — so it
names the missing record rather than a route.

The Board draws accepted tasks only, in one swimlane per epic and a no-epic
lane last, each lane's header linking to its epic with "n of m merged". Its
columns are Ready, in `/api/ready`'s order, and Waiting, the pending tasks
Ready does not name; one lane per role, from the `phase` an `in_progress` task
carries (ADR 126); No live phase, for an `in_progress` task with no phase or an
expired lock, which says which; Awaiting review, which no status fills yet;
Conflict, Blocked and Merged; and Other, when some card has a status none of
those place. A failed ready read folds Ready and Waiting into one Pending
column and says so. Backlog items are the Backlog screen's, not cards here.
`docs/decisions.md` ADR 140, which replaces ADR 26's columns and ADR 28's banner.

The Debt screen stays the index's view of `docs/debt/`, open entries first and
resolved below. Each open entry has an *item* column: a link to the backlog
item filed from it (paired on `debt_id`, as the dispatcher pairs them) and
whether it was accepted — not imported; proposed, deferred or dropped (not
accepted); or accepted, with the board status or merged. Two items with one
`debt_id` are both shown under a warning. If `/api/tasks` fails, the index
still renders and the column says the pairing is unavailable. The screen
writes nothing. `docs/decisions.md` ADR 141.

The console authenticates as a service, not as a human: a bearer token read
server-side, never under a `VITE_` prefix, so Vite cannot inline it into the
browser bundle. See *The five environment variables* above and
`docs/decisions.md` ADR 15.

**The loop runs all three.** `docs/README.md`'s frontmatter names them —
`install: cd front && bun install --frozen-lockfile`, then `cd front && bun run
typecheck`, `cd front && bun run test` and `cd front && bun run lint` among its
`test:` entries — and the dispatcher's test gate has run them since 2026-10-08
(`docs/decisions.md` ADR 39); the lint was a `lint:` entry until 2026-10-10 (ADR 50).
So a green test gate on a commit touching `front/src/` does say something about
it; `python3 -m pytest` still imports nothing under this directory and is no
longer the whole gate. **Run the three yourself as well, when your card grants
them** — a phase can: `front/node_modules` is git-ignored and absent from a
fresh worktree, and `cd front && bun install --frozen-lockfile` fills it in
under a second from bun's install cache on the projects mount
(`docs/decisions.md` ADR 46).

The gate also knows which language a test is in. Since T-013 `tests-in-diff`
pairs a changed file with a test in the **same language**, so a `front/src/**`
edit arriving beside nothing but a Python test is asked about instead of
cleared (`docs/decisions.md` ADR 31) — it never was blind to `.ts` and `.tsx`,
it just stopped looking once it had found any test at all. The gate that
*executes* the three scripts was the open half of
[`docs/debt/T-013-D1.md`](../docs/debt/T-013-D1.md) step 3, blocked on an agent
image with `bun` in it; both landed on 2026-10-08 and that row is resolved.
What a red result costs is ruled:
[`docs/charter.md`](../docs/charter.md) **C-11**, which superseded C-10 on
2026-10-10 — a red typecheck, test or lint blocks the phase.

### Writing a test

Tests live beside what they test, as `*.test.tsx`, and two things about this
tree constrain where that can be:

- **Under `src/routes/`, prefix the filename with `-`.** The route generator
  reads every other file in that directory as a route, and the knob that
  excuses one is `routeFileIgnorePrefix`, which **defaults to `-`** and is set
  nowhere in this repo — so `-index.test.tsx`, `-learnings.test.tsx`,
  `-debt.test.tsx`, `-tail.test.tsx` and `-tasks.test.tsx` sit beside the
  routes they test and cost nothing in `routeTree.gen.ts`. Export the renderer from its route
  module and render it plainly. For when a component moves to
  `src/components/console/` instead, and what `vite.config.ts` has to do with
  it (nothing): `docs/decisions.md` **ADR 45**, which narrows **ADR 30**.
- **Import `describe`/`it`/`expect`/`afterEach` from `"vitest"`, and call
  `afterEach(cleanup)` yourself.** [`vitest.config.ts`](vitest.config.ts) sets
  `globals: false`, and `@testing-library/react` registers its own cleanup only
  when it finds a global `afterEach` — without that line every render in a file
  stacks in one document.

A component test cannot see what its screen hands it. To assert that — which
count a route passes its empty state, say — render the route whole with
`renderRoute(Route, path, served)` from
[`src/test/render-route.tsx`](src/test/render-route.tsx): it stubs `fetch` with
the `{data, warnings}` envelope per api path, answers 404 for any path not
served, and mounts the route's component under a memory-history router of its
own. Wait on what the screen draws (`findByText`), not on the call. A route
whose path has a parameter takes its own path and, as a fourth argument, the
location to open it on: `renderRoute(Route, "/tasks/$taskId", served,
"/tasks/T-101")`, as `-tasks.test.tsx` does.
`docs/debt/T-017-D1.md` *How it was resolved* says why the seam is `fetch` and
not a seeded query cache.

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
