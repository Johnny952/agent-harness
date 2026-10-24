# The front's task row type is `Task`, not `TaskRow`

**When it applies:** a card, a debt row or a handoff tells you to change the
front's `TaskRow` type, and you are about to search for it or add it.

**Status:** unconfirmed — proposed by T-165's arquitecto and implementador; the
auditor checked it on the T-165 branch on 2026-10-24.

## What is true

No `TaskRow` symbol exists in `front/src`. The row `/api/tasks` serves is
`interface Task` in `front/src/lib/api/types.ts`. The name `TaskRow` comes
from prose: debt entry `T-163-D1` in `docs/debt/README.md` and the T-165
card. `docs/decisions.md` ADR 402 read it as `Task` and added no alias.

## What to do

Edit `Task` in `front/src/lib/api/types.ts`. Do not add a `TaskRow` alias to
match the card. Each `Task` literal (`front/src/lib/api/mock/fixtures.ts` and
the `task(...)` helper in each test file) must set a new non-optional field,
or `bun run typecheck` fails.

## Evidence

`grep -rn "TaskRow" front/src` on the T-165 branch: no hits.
