# Prettier rewraps JSX text children, so editing one word of screen copy turns untouched lines red

**When it applies:** you edited a sentence of copy sitting **between tags** in
`front/src/` — a `Banner`'s children, a `<p>` — and `bun run lint` is one of
your gates.

**Status:** unconfirmed — reported by T-016's implementador, which hit it seven
times across two files. It sharpens
[`fronts-lint-turns-a-long-line-into-an-error-not-a-warning`](fronts-lint-turns-a-long-line-into-an-error-not-a-warning.md),
whose "prettier does not rewrap prose" is true of **comments** only.

This entry's trigger named "an `EmptyState` body" until 2026-10-10, when T-017
added five of them past `printWidth` 100 and the lint stayed green: a `title` or
`body` is a string in a JSX *attribute*, which prettier cannot break at all, and
it is a different shape from the text children this entry is about. That half is
now
[`an-emptystate-body-is-an-attribute-string-prettier-leaves-alone`](an-emptystate-body-is-an-attribute-string-prettier-leaves-alone.md);
nothing else here changed, and the `status` stays as it was —
[`correcting-an-index-entry-is-two-edits`](correcting-an-index-entry-is-two-edits.md).

## Symptom

```
/data/projects/ia-harness/worktrees/T-016/work/front/src/routes/tasks.$taskId.tsx
  239:99  error  Insert `·a`                                       prettier/prettier
  240:14  error  Replace `·a·phase·was·actually·handed·is·…·—` with `·phase·was·actually·handed·is·…·—·each`  prettier/prettier
  241:14  error  Delete `·each`                                     prettier/prettier
✖ 16 problems (7 errors, 9 warnings)
```

The lines named are not the line you edited. Two of the three above were
untouched by the diff.

## Why

`prettier/prettier` is registered at **error** severity by
`eslint-plugin-prettier/recommended`, and prettier reflows JSX *text children*
to `printWidth` 100 the way it reflows code. Change one word in the middle of a
paragraph and every following word shifts, so three or four lines of prose you
never opened become errors, each one phrased as a diff against the wrapping
prettier wants rather than as a statement about your sentence.

Comments are the exception and that exception is what misleads: a 120-column
comment block is not an error, so "prettier leaves prose alone" is a reasonable
thing to have concluded and is wrong of anything inside JSX.

The obvious fix is out of reach. `bunx prettier --write <files>` and
`eslint --fix` are both refused in a phase — "This Bash command contains
multiple operations… requires approval" — so `Edit` is the only route, even
though the lint itself runs (see
`/data/.hive/learnings/inbox/T-016-bun-runs-in-a-phase-when-the-task-grants-it.md`).

## What to do

Hand-apply the reflow out of the error text: `·` is a space and `⏎` a newline,
so each `Replace X with Y` is a literal `Edit`. Fix the whole paragraph in one
`Edit` rather than line by line — the per-line hints are only consistent with
each other as a set, and applying them one at a time produces a new set.

Then re-run `bun run lint` and read the warning count as well as the exit code:
green here is exit 0 with the nine `react-refresh/only-export-components`
warnings the console already carries, and the prettier errors are what the exit
code covers.

## Evidence

`cd front && bun run lint` in T-016's worktree after editing the learnings
region's copy in `front/src/routes/tasks.$taskId.tsx`: seven `prettier/prettier`
errors across two files, all gone after four `Edit` calls, back to nine
warnings and exit 0. Re-run by T-016's auditor at `617c7f3`: `9 problems (0
errors, 9 warnings)`, exit 0. Inbox entry:
`/data/.hive/learnings/inbox/T-016-prettier-reflows-jsx-prose-and-fix-is-not-in-the-grant.md`.
