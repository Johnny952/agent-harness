# Five console screens re-render on a timer, whether or not anything was read

**When it applies:** you are judging whether a per-render computation in a
`front/` screen costs anything — an unstable hook dependency, a filter, a sort,
a `new Date()` — or you are about to call a recompute harmless because the data
only changes when a read answers.

**Status:** confirmed — read directly off
[`front/src/hooks/use-console.ts`](../../front/src/hooks/use-console.ts) and its
five call sites, by two phases of T-015 independently.

## Symptom

No error. A computation that looks like it runs once per fetch runs several
times a minute, so an unstable `useMemo` dependency costs on every tick rather
than only when the query answers.

## Why

`useNow(intervalMs = 2000)` holds a `setInterval` that calls `setNow` forever
while the component is mounted, so every screen that calls it re-renders on its
own clock with no input from React Query. Five screens do, and the interval is
**not** the same on all of them:

| screen | interval |
| --- | --- |
| [`front/src/routes/index.tsx`](../../front/src/routes/index.tsx) | 2000 ms (default) |
| [`front/src/routes/tasks.$taskId.tsx`](../../front/src/routes/tasks.$taskId.tsx) | 2000 ms (default) |
| [`front/src/routes/queue.tsx`](../../front/src/routes/queue.tsx) | 2000 ms (default) |
| [`front/src/routes/tokens.tsx`](../../front/src/routes/tokens.tsx) | 2000 ms, passed explicitly |
| [`front/src/routes/pool.tsx`](../../front/src/routes/pool.tsx) | **1000 ms** |

The hook exists because these screens render ages and clocks — `formatAge`,
`agoSeconds` — which have to move without a refetch. The re-render is the
feature; what it does to everything else on the screen is the surprise.

## What to do

Count the ticks, not the fetches. On the board that is 30 renders a minute and
on the pool 60, so "this only recomputes when the data changes" is false for any
screen in the table unless the dependency is referentially stable — which is why
[a-query-default-feeding-a-usememo-dep-depends-on-the-data-object](a-query-default-feeding-a-usememo-dep-depends-on-the-data-object.md)
is worth the memo it asks for.

It does not follow that every per-render traversal needs memoising. `unplaced`
and `byStatus` in `front/src/routes/index.tsx` are filters over a handful of
tasks, are nobody's hook dependency, and were deliberately left alone by T-015 —
`docs/implementations/T-015.md` *What was ruled out* says so, so that the next
reader does not take them for something missed. The thing to fix is a dependency
that destabilises a memo, not a loop.

And if you quote the interval, quote the right one: an earlier draft of this
entry said "every console screen, every two seconds", which is wrong twice over
— seven screens never call the hook, and `pool.tsx` is twice as fast.

## Evidence

T-015, whose arquitecto and implementador both recorded the 2000 ms tick as the
reason the board's `filtered` memo recomputed, and whose auditor found the
`pool.tsx` exception when filing this entry.
`front/src/hooks/use-console.ts:useNow` (`intervalMs = 2000` as its default) and
the five call sites in the table.
[`docs/decisions.md`](../decisions.md) **ADR 40** states the 2000 ms figure for
the board.
