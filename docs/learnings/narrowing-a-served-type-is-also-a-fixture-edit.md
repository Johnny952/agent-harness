# Narrowing a type in `front/src/lib/api/types.ts` is also an edit to its fixture, and ADR 25 keeps the fixture

**When it applies:** you are removing or renaming a field on an interface in
`front/src/lib/api/types.ts` that a `mock*` function in
`front/src/lib/api/mock/` returns — which is every type a tier-1 or tier-2 task
wires to a real route, because each one had a fixture behind it first.

**Status:** unconfirmed — T-013 narrowed `Phase` from seventeen fields to six and
hit it once, on an edit no plan had named.

## What happens

`mock/fixtures.ts` declares its functions with the served type:
`export function mockPhases(): Phase[]`. Narrow `Phase` and the fixture's rows
stop conforming to their own annotation — seventeen keys against an interface
that now has six. There is no typecheck in the loop
([T-013-D1](../debt/T-013-D1.md)), so nothing says so: it ships as a landmine
for the next task, which will be the first to run `tsc` and will find a failure
in a file it did not touch.

The reflex — delete the fixture, since nothing calls it any more — is also
wrong here, and `docs/decisions.md` ADR 25 is why. T-012 kept five fixtures
whose reads had gone real, on the reasoning that a fixture still type-checks
against the served type and so is the cheapest thing that catches a drift
between the two, and that re-deleting generated code in a tree C-9 keeps syncing
to Lovable costs more than the dead code does.

## What to do

Keep the fixture and **conform it to the served shape** in the same edit that
narrows the type: the same number of rows, the new key set, and one row per edge
the route can answer — T-013's `mockPhases` kept four rows including a
`handoff: null`, which is a real answer from `/api/phases` and not a missing
file. Then move its entry in the module docstring from the paragraph about
fixtures that back a read to the one about fixtures that back nothing, because
that docstring is the only index of which is which.

Expect this edit not to be in your plan. It is forced by the type change rather
than chosen, which is also true of the helpers in `front/src/lib/format.ts` that
lose their last caller when a field retires: `formatBytes` and `formatDuration`
are kept callerless with the reason written on them, under the same ADR.

## Evidence

T-013: `front/src/lib/api/mock/fixtures.ts:mockPhases` narrowed from 221 lines
to four rows alongside `Phase` losing eleven fields, with
`front/src/lib/format.ts` (`formatBytes`, `formatDuration`) kept for the same
reason; `docs/decisions.md` ADR 25 for the rule and ADR 27 for the narrowing;
`docs/implementations/T-013.md` *Two files the plan did not name, and why they
had to move*. The fixture was correct before that task — `git show
bebe3fd:front/src/lib/api/mock/fixtures.ts` has it conforming to the
seventeen-field `Phase` — so this is not a defect an earlier task left: it is
one the narrowing itself creates, in the same commit, which is why the fix is
the same edit and not a later one.
