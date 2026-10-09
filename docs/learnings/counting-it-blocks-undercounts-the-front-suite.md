# Counting `it(` blocks undercounts the `front/` suite, because one file generates six of its tests

**When it applies:** you are stating how many tests `front/`'s suite has — in a
handoff, a ROADMAP results row, an implementation note — and you counted them by
grepping `it(` instead of reading what `bun run test` printed.

**Status:** unconfirmed — found by T-016's revisor in round 1 while checking the
implementador's "60 passed, up from 50", and the arithmetic reconciled in round
2.

## Symptom

No error, and the number is close enough to look right:

```
claimed:   bun run test: 60 passed (60) in 5 files, up from 50 in 4
printed:   Test Files  5 passed (5)
              Tests  60 passed (60)
```

The 60 is what vitest printed; the 50 was counted by hand and is 51.

## Why

`front/src/lib/api/forward.test.ts` ends its task-detail block with an
`it.each` over six dot-segment cases — `/api/tasks/..`, `%2e%2e`, `.%2e`,
`%2E%2E`, `.`, `%2e` — asserting each is resolved away before the forward sees
it. That is one `it.each` in the file and six tests in the run, so a grep for
`it(` finds fourteen named blocks in a file that contributes twenty tests.

The suite on `main` before T-016 was therefore 51, not 50: 8 in
`front/src/components/console/learnings.test.tsx` plus 14 named and 6 generated
in `forward.test.ts` plus 9 elsewhere, less the one `forward.test.ts` case T-016
added. A number counted by grep and a number printed by the runner differ by
exactly the generated cases, which is invisible if you only ever check the
total against itself.

## What to do

Quote what `bun run test` printed, both lines — `Test Files` and `Tests` — and
never a hand count. When you have to say "up from N", get N by running the suite
on the base commit rather than by subtracting your new `it` blocks.

The same applies in reverse when you add a test file: the count you are owed is
the runner's, and a `describe.each` or `it.each` you write makes your own
contribution larger than the blocks you typed.

## Evidence

`cd front && bun run test` on T-016's branch at `617c7f3`:
`Test Files 5 passed (5)`, `Tests 60 passed (60)`, exit 0 — re-run by the
auditor. The generator is `front/src/lib/api/forward.test.ts`'s `it.each` in the
`"the task detail route"` block, whose comment says why the cases are pinned
there rather than asserted in prose. The reconciliation is
`/data/.hive/tasks/T-016/review-round-2.md`.
