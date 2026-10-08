# A `front/` change with no DOM difference answers `tests-in-diff` rather than satisfying it

**When it applies:** your change under `front/src/**` leaves the rendered output
byte-identical — a memoisation, a hook dependency, a `useCallback`, a key — and
the dispatcher's `tests-in-diff` gate is asking you for a test beside it.

**Status:** unconfirmed — one instance, T-015. The gate did fire as expected and
the answer was accepted by that task's revisor, but the shape has not recurred.

## Symptom

The gate's note, on a diff whose only code file is a `.tsx`:

```
`tests-in-diff` (ask) — code changed and no test in the same language did:
`front/src/routes/index.tsx`. Add a test beside it, or say in one line why this
change does not need one.
```

It fires because `tests-in-diff` pairs a changed file with a test in the **same
language** since `docs/decisions.md` ADR 31, so a Python suite that passes is no
cover for a TypeScript edit. This is working correctly — the gate is `ask`, not
block, and it has one job: to make you say the sentence.

## Why

There is nothing for a test in this repo's idiom to assert. The only test file
beside the board is
[`front/src/routes/-index.test.tsx`](../../front/src/routes/-index.test.tsx),
and it mounts `TaskCard` on a hand-built single-route router and reads
`textContent`. A referential-stability change produces no `textContent`
difference, so the thing to pin would be that `useMemo` returned the same
reference across two renders — which is React's promise rather than this
screen's. Observing it would take a `BoardPage` mount, a query client, a
two-route tree, three mocked reads and a render-counting spy, and the assertion
at the end of all that is about React.

Note what the test file does *not* do, because it is easy to misread as cover:
it imports the changed module (`import { TaskCard } from "./index"`) but never
mounts `BoardPage`, so it exercises neither hook on the screen you changed. An
import is not a test of what you touched.

## What to do

Answer the gate in one line and put the same answer somewhere that outlives the
task, because the handoff does not: the gate's question gets asked again by the
next reader of the diff. T-015 put it in two places — `docs/decisions.md`
**ADR 40**'s last paragraph and
[`docs/implementations/T-015.md`](../implementations/T-015.md) *What was ruled
out* — and its revisor accepted the change as answered rather than satisfied on
exactly that ground.

Do not reach for the render-counting test to clear the gate. And do not treat
this as licence for `front/` changes generally: the rule is "no DOM difference",
and a change that moves markup, state or a branch owes a real test, which is
the whole of [`docs/debt/T-013-D1.md`](../debt/T-013-D1.md).

## Evidence

T-015. The gate note is in `/data/.hive/tasks/T-015.md` under the implementador's
section, the answer in ADR 40 and `docs/implementations/T-015.md`, and the
revisor's acceptance in `/data/.hive/tasks/T-015/review.md` *The `tests-in-diff`
gate answer*. ADR 31 is where the same-language pairing landed.
