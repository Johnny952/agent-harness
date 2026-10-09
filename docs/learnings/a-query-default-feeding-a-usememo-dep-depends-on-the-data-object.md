# A `?? []` over a React Query read is a fresh identity, and the dependency to declare is the `data` object

**When it applies:** you are writing or reviewing a `useMemo`, `useEffect` or
`useCallback` in a `front/` screen whose dependency is a value unwrapped from a
React Query read with a `?? []` or `?? {}` default — or
`react-hooks/exhaustive-deps` is telling you that a logical expression could
make a hook's dependencies change on every render.

**Status:** confirmed — the rule's behaviour was read off three in-tree
precedents that a real lint run left unwarned (`docs/decisions.md` ADR 39's
ten-warning tally), and T-015's own instance went unexecuted by any phase of
that task, which is why this entry was filed `unconfirmed`. T-016 executed it:
`cd front && bun run lint` in a phase, at `617c7f3`, printed
`9 problems (0 errors, 9 warnings)` with every warning
`react-refresh/only-export-components` and not one
`react-hooks/exhaustive-deps` — so the memo in `front/src/routes/index.tsx` and
the three precedents are all unwarned under a real run. The reason no phase
could observe it then, and can now, is
`/data/.hive/learnings/inbox/T-016-bun-runs-in-a-phase-when-the-task-grants-it.md`,
which refutes `T-015-bun-is-refused-in-a-phase.md` and
`T-015-node-is-allowed-but-node-modules-is-still-out-of-reach.md` (harness
scope, still unconfirmed: read all three).

## Symptom

One `react-hooks/exhaustive-deps` warning, on the hook that *reads* the value
rather than on the line that wrote it. As T-015's task text put it:

```
the `rows` logical expression (around line 63) can change on every render, so
the `useMemo` that depends on it (around line 81) recomputes every time
```

The rule's own remedy is in its message: wrap the initialization of that value
in its own `useMemo`. Nobody on T-015 saw the warning's verbatim text —
`docs/decisions.md` ADR 40 *Context* paraphrases it for that reason, and the
paraphrase was checked for substance rather than wording.

## Why

`tasks.data?.data ?? []` allocates a new array on every render for as long as
the read has not answered, so the identity changes even though nothing about
the screen did. Anything downstream keyed on it — in
[`front/src/routes/index.tsx`](../../front/src/routes/index.tsx) that is the
`filtered` `useMemo` — recomputes on every render, and the lint warns at the
downstream hook.

The fix is the one the rule asks for, and the dependency to declare is the
React Query `data` object, not the unwrapped array: `exhaustive-deps` accepts a
declared dependency that is an ancestor path of the one actually used, and
React Query holds `data` identical between renders, so the memo recomputes when
the read answers and not otherwise.

```tsx
const rows = useMemo(() => tasks.data?.data ?? [], [tasks.data]);
```

This repo carries three independent proofs of the ancestor-path form at the
plugin version it pins, all of them unwarned in ADR 39's tally: `debtByTask` in
[`front/src/routes/index.tsx`](../../front/src/routes/index.tsx) reads
`debt.data?.data ?? []` against `[debt.data]`,
[`front/src/routes/debt.tsx`](../../front/src/routes/debt.tsx)'s `rows` declares
`[debt.data, q]`, and
[`front/src/routes/approvals.tsx`](../../front/src/routes/approvals.tsx)'s `rows`
declares `[q.data, tab]`.

## What to do

Memoise the unwrapped value, keyed on the query's `data`, and leave the readers
alone. Check two things before you inline the default into the one hook that
warned instead: how many other readers the value has — `rows` has three, and
each would get its own copy of the `[]` — and whether every reader is read-only,
because memoising makes the fallback a *shared* instance. On the board every
reader is `.filter` or `.length`, so sharing it is safe; a reader that pushed
into it would not be.

Write the reason on the line. `useMemo(() => tasks.data?.data ?? [], [tasks.data])`
reads as ceremony around a default, and the one plausible way it gets undone is
a later reader deleting it for tidiness — it exists for the array's *identity*,
not for the cost of `??`. ADR 40 is the decision and the in-file comment is the
guard.

Do not reach for a test of it. See
[a-referential-stability-fix-in-front-is-answered-not-tested](a-referential-stability-fix-in-front-is-answered-not-tested.md).

## Evidence

T-015, which took the board's `rows` from ten lint warnings to nine.
[`docs/decisions.md`](../decisions.md) **ADR 40** is the decision,
[`docs/implementations/T-015.md`](../implementations/T-015.md) the note, and the
three precedents above were spot-checked by that task's revisor in
`/data/.hive/tasks/T-015/review.md` *The code change*. The cost it removed is
real rather than theoretical because of
[every-console-screen-re-renders-on-a-timer](every-console-screen-re-renders-on-a-timer.md).
