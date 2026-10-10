# An `EmptyState` body is an attribute string, and prettier cannot reflow it however long it gets

**When it applies:** you are planning around prettier reflowing new console copy
in `front/src/`, and the copy you are adding is an `EmptyState`/`ErrorState`
`title` or `body` — a string or template literal in a JSX **attribute** —
rather than prose between tags.

**Status:** unconfirmed — reported by T-017's implementador, which added five
`EmptyState` bodies across three route modules and never saw a
`prettier/prettier` error. It narrows
[`prettier-rewraps-jsx-text-children`](prettier-rewraps-jsx-text-children.md),
whose trigger names "an `EmptyState` body" alongside a `Banner`'s children and a
`<p>`; that entry is still right about text children, which is the shape it was
written against.

## Symptom

No error. `cd front && bun run lint` stays at the project's standing nine:

```
✖ 9 problems (0 errors, 9 warnings)
```

after adding five new `EmptyState` bodies across `front/src/routes/learnings.tsx`,
`debt.tsx` and `tail.tsx`, three of them template literals well past
`printWidth` 100 — e.g. ``body={`Clear the filter box to see ${served === 1 ?
"the one entry" : `all ${served} entries`} again. The filter reads the entry, the
task, the trigger line and the rule.`}``.

## Why

Prettier reflows JSX **text children** because the whitespace between tags is
its to rewrap. An `EmptyState` `title` or `body` is a string or template literal
inside an attribute expression, and prettier never breaks a string literal: it
has nowhere to put the newline without changing the value. A long one simply
overruns `printWidth` and `prettier/prettier` has nothing to report.

So the two shapes behave oppositely, and the project's shared vocabulary for
bare states is entirely the attribute shape: `EmptyState` and `ErrorState` in
`front/src/components/console/primitives.tsx` both take `title` and `body` as
props. A `Banner`'s children and a `<p>` are the text-children shape and do
reflow — the same file, the same screens, a different fight.

## What to do

Budget a reflow fight for JSX text children only. Write the `title`/`body` copy
at whatever length the sentence wants and do not pre-wrap it to 100 columns in
anticipation: the wrap would not survive a later `prettier --write` anyway, and
hand-breaking a template literal across lines changes the string.

Still run `bun run lint` — the copy being safe is not the diff being safe, and a
comment or a `<p>` in the same edit is the half that does reflow.

## Evidence

`cd front && bun run lint` in `/data/projects/ia-harness/worktrees/T-017/work`,
after the three route edits and before any hand-reflow, with `bun run typecheck`
exit 0 and `bun run test` 73 passed in 8 files on the same tree. Re-run by
T-017's auditor on the final tree: `✖ 9 problems (0 errors, 9 warnings)`, exit
0, all nine `react-refresh/only-export-components` under
`front/src/components/` and none from this diff. Inbox entry:
`/data/.hive/learnings/inbox/T-017-an-emptystate-body-is-a-string-prettier-cannot-reflow.md`.
