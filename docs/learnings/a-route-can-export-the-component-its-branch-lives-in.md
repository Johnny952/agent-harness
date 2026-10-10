# A route module can export the component its branch lives in, which makes the branch testable and moves the gap to the call site

**When it applies:** you want to assert something about a branch inside a
`front/src/routes/` screen — which empty state it picks, which banner, which
sentence — and the obvious route is a route render with a router and a
`QueryClient`, which this suite has no harness for.

**Status:** confirmed — `front/src/routes/index.tsx` has exported `TaskCard`
beside its `Route` since T-012 and `front/src/routes/-index.test.tsx` renders
it; T-017 added three more such exports and the lint count did not move.

## Symptom

There is nothing to render. Every test file under `front/src/` mounts a
component directly, because mounting a route means building a router, a memory
history and a `QueryClient` seeded with a response body — and no file in the
suite does that. So a branch written inside `function LearningsPage()` is
unreachable from a test, and the decision it makes ships unobserved. That is how
`docs/debt/T-016-D1.md` got onto two screens and stayed there.

## Why

Nothing requires a route module to export only `Route`. Pull the branch into a
small component in the same file, take the two or three *values the branch turns
on* as props rather than the query result, and export it: the test imports it
and calls `render` with no router, no provider and no fetch.

Two things make this cheap in this project specifically. The filename prefix
`-` keeps the test file out of the generated route tree
(`@tanstack/router-plugin` reads every other file under `src/routes/` as a
route, and `routeFileIgnorePrefix` defaults to `-`), so a test beside a route
costs nothing in `routeTree.gen.ts` — which no phase here can regenerate. And
`react-refresh/only-export-components` does not fire: T-017 added three
component exports beside three `Route`s and `bun run lint` stayed at its
standing nine, all in `front/src/components/`
(`docs/debt/T-015-D1.md` *What* has the mechanism — the rule reads a PascalCase
`const` initialised from a call expression as a component, so TanStack's own
export is excused).

The catch is where the untested seam ends up. Extracting the branch does not
remove the gap, it **moves it up one level**: the arms are now pinned and the
argument the route passes is not. T-017's three call sites pass
`served={served.length}` and `buffered={events.length}`, and passing
`rows.length` or `filtered.length` instead reinstates the original defect with
the whole suite green — `docs/debt/T-017-D1.md`.

## What to do

Export the component, take the decided-on values as props, and put the test
beside the route with a `-` prefix on the filename.

`front/README.md` *Writing a test* will tell you the opposite — *Not under
`src/routes/`*, with "a renderer worth a test moves to
`src/components/console/` instead" and `docs/decisions.md` **ADR 30** behind it.
That bullet names the wrong knob: `routeFileIgnorePattern` has no default and is
not set anywhere in this repo, but `routeFileIgnorePrefix` defaults to `-` and
is what the four `-*.test.tsx` files under `src/routes/` rely on. It was already
false before T-017 — `bc93b1d` added `-index.test.tsx` — so the sentence is
nobody's to correct in passing: reconciling it with ADR 30 and ADR 43 is a
decision and wants an ADR. Until then, read this entry and the header of
`front/src/routes/-index.test.tsx` over that bullet.

Then say out loud, in the handoff and in the implementation note, that the call
site is not covered, and name the argument. A comment above the call site is
what stands in the way of a refactor undoing the fix, and a comment is not a
test. If the screen's correctness actually lives in that argument — as it did
here — the route-render helper is the real fix and it is a task of its own.

## Evidence

`front/src/routes/index.tsx` exports `TaskCard` beside `Route`, tested by
`front/src/routes/-index.test.tsx`. T-017 added `LearningsEmpty`, `DebtEmpty`
and `TailNoMatch` the same way, with `-learnings.test.tsx`, `-debt.test.tsx`
and `-tail.test.tsx` rendering them plainly: `bun run test` went from 60 tests
in 5 files to 73 in 8, `bun run lint` stayed at `9 problems (0 errors, 9
warnings)`, and `git status --short` showed no change to `routeTree.gen.ts`.
`docs/decisions.md` **ADR 43**, third bullet, is where the shape was decided.
