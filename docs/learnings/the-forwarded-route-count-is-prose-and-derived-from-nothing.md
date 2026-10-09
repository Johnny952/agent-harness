# The console's forwarded-route count is an English word in six files and derived from `FORWARDED` nowhere

**When it applies:** you are adding or removing a route in
`observability/api/app.py` and `front/src/lib/api/forward.ts`'s `FORWARDED` has
to carry it — or you are reading any sentence in `front/` that counts the routes
or the screens.

**Status:** unconfirmed — reported by T-016's implementador, which moved the
count from "six" to "seven", and checked against the tree by its auditor.

## Symptom

Nothing fails, because the count is prose. Exactly one place fails, and it
fails with a word in it:

```
AssertionError: expected { error: '/api/learnings is not …' } to deeply equal { …'six routes'… }
```

## Why

`FORWARDED` is the one list of routes the console forwards, and no sentence
anywhere is computed from its length. The word is typed out instead, in
`front/src/lib/api/forward.ts` (the module docstring, the `%zz` comment inside
`resolveTarget`, and the rejection message `resolveTarget` returns),
`front/src/lib/api/client.ts` (its module docstring, the wired-reads banner
comment, and the sentence under it), `front/src/lib/api/forward.test.ts`,
`front/README.md`, `front/AGENTS.md` and `docs/README.md`'s `observability/api/`
and `front/` rows. The only automated check over any of them is
`forward.test.ts`'s assertion on the rejection message, so every other
occurrence drifts in silence.

The sweep has a second half, and grepping the count word is what hides it: the
same word counts other things. `docs/README.md`'s `front/` row carries both
counts in one sentence ("Six of them read the real api … a seventh since
T-016"); `front/src/lib/api/mock/fixtures.ts`'s docstring opens with "Seven of
these back nothing" about the fixtures; and `docs/plans/front.md` spells
"seven" three times without once meaning the forwarded routes ("Seven of its
twelve screens still resolve from a fixture", "Seven reads arrived after that
sweep", "The seven ADR 19 did not see"). T-016's sweep followed the route word,
left the screen sentences alone, and the plan's Status then disagreed with the
row the same commit had rewritten.

## What to do

Before touching `FORWARDED`, grep `front/ docs/` for the current count word
(`six`, `seven`, …) and for `wired reads`, and fix every hit in the same commit.
Then grep again for the *next* count word, because the screen count and the
route count move in opposite directions on the same change and sit in the same
sentences: a route wired means one fewer fixture screen.

Do not derive the word from `FORWARDED.length` to fix this. Nothing here is
generated prose and a route added without its documentation is the defect the
sentences exist to make visible; the fix is the sweep, not a template.

## Evidence

`cd front && bun run test` in T-016's worktree after adding
`"/api/learnings": []` to `FORWARDED` produced the assertion above from
`front/src/lib/api/forward.test.ts`. The file list was taken by
`grep -rn seven front/src/lib front/README.md front/AGENTS.md docs/README.md`
on T-016's branch at `617c7f3` — the count is stated eleven times across those
six files, and the grep is case-sensitive, so run it for `Seven` as well. The
other three files are `grep -rln 'seven' docs/plans/front.md
front/src/lib/api/mock/fixtures.ts`. The sweep's second half is
`/data/.hive/tasks/T-016/review-round-2.md` blocking 1. The inbox entry behind
this one is
`/data/.hive/learnings/inbox/T-016-a-routes-count-is-spelled-out-in-eight-places.md`.
