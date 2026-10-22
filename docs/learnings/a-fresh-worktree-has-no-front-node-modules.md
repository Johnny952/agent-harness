# A fresh worktree has no `front/node_modules`

**When it applies:** you are about to run `bun run typecheck`, `bun run test`
or `bun run lint` in `front/` of a worktree no phase has installed into yet.

**Status:** unconfirmed. Proposed by T-133-S2's implementador and revisor.

## Why

`front/node_modules` is untracked, so a new worktree (the first phase's, or the
revisor's rebuilt one) starts without it and every script fails to find its
binary. A later phase sharing the same worktree finds what an earlier one
installed, which is why the auditor of T-133-S2 found it present.

## Rule

Run `cd front && bun install --frozen-lockfile` first when
`front/node_modules/.bin` is missing; check with `ls`, do not assume either way.

## Evidence

T-133-S2 implementador and revisor handoffs in its task file; the auditor's
`ls front/node_modules/.bin/vitest` succeeded in the shared worktree.
