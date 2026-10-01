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

**Nothing here talks to the harness yet.** Every screen reads mock fixtures.

## Where the contract lives

Four documents, and none of them is this file:

| Doc | What it settles |
|---|---|
| [`docs/plans/front.md`](../docs/plans/front.md) | Which screens are coming, in what order, over which routes — and which reads the api will never serve. Read it before adding a screen or a query. |
| [`docs/ui.md`](../docs/ui.md) | The cross-screen vocabulary: tone per state, absent vs empty vs error, dates and ages, the nav and its chords. Binding — a screen that contradicts it is wrong, not different. |
| [`docs/decisions.md`](../docs/decisions.md) | The ADRs the console has to honour, ADR 14–21 in particular: the envelope, the warnings, the bearer forward, what each route answers. |
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
bun run lint       # eslint .
bun run format     # prettier --write .
```

`minimumReleaseAge = 86400` in `bunfig.toml` refuses any package version
published in the last 24 hours, which is the window most registry compromises
are caught in. Four `@lovable.dev/*` packages are excluded because the
toolchain itself ships that fast. **Adding a fifth exclusion is a decision to
make with the operator, not a lockfile fix.**

There is no compose service for this directory and the top-level
[`README.md`](../README.md) setup does not need one. It runs on the host,
against whatever the fixtures say.

## How it is put together

- **One client, one file.** Every read and write goes through
  `src/lib/api/client.ts`, one exported function per endpoint. Today each body
  resolves from `src/lib/api/mock/` after a fake 120ms; swapping to the real
  REST backend is a change to that one file. A component that fetches on its
  own has bypassed the seam.
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

Of the twelve screens, six sit over routes the read API actually serves —
board, task detail, pool, debt, learnings and the live tail — and even those
read fixtures today. The other six are built against `ops-types.ts` and have
no backend of any kind: **Approvals, Tokens, Session logs, Role models,
Backlog and Queue.** Writes are the same story; `docs/plans/front.md` tier 3
puts the write surface in a different service, for reasons that are about
blast radius rather than convenience.

Tier 1 of that plan is parity with the Flask board's four screens against the
real routes. The api's half of it is built (T-011); the console's half — the
five `client.ts` bodies, the warnings every listing screen must show, and the
server-side bearer forward — is not.

There is also no authentication here, by design: the console runs on a private
network behind one trusted operator. The token the console forwards to the api
is a service credential, read server-side and deliberately **not** under a
`VITE_` prefix, so Vite cannot inline it into the browser bundle.

## The Lovable round-trip

This directory was generated in [Lovable](https://lovable.dev) and adopted
whole. Whether it keeps that round-trip is an open ruling — C-8 names it and
declines to decide it — so until it is decided, treat the sync as live:

> Avoid rewriting published git history — force pushing, or
> rebasing/amending/squashing commits that are already pushed. Commits pushed
> to the connected branch sync back into the editor, so keep the branch in a
> working state.

That is now a constraint on `main` in this repo, not just on a side branch.
