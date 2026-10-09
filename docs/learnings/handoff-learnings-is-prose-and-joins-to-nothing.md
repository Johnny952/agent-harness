# A handoff's `learnings` field is free prose and joins to no entry in the learnings tree

**When it applies:** you are about to join a phase's `handoff.learnings` against
the learnings tree, or to render it as a list of refs — in particular because
`front/src/lib/api/types.ts` once called that field "inbox filenames as the role
wrote them" and [`docs/decisions.md`](../decisions.md) **ADR 27** said the join
was only waiting for `/api/learnings` to exist.

**Status:** unconfirmed — established by T-016's arquitecto by reading every
handoff this harness has written, and recorded as **ADR 42**, which narrows ADR
27 on this one clause.

## Symptom

No error. A type comment and an ADR describe a relation that was never built.
What the type claimed:

```
  /** Inbox filenames as the role wrote them, not ids: the join needs `/api/learnings`. */
  learnings?: string[];
```

What four cycles of handoffs on disk actually hold:

```
/data/.hive/tasks/T-015/handoffs/arquitecto.json:    "learnings": [
      "`bun` is refused in a phase and a fresh worktree has no node_modules, so a front/ change is observed by the dispatcher's test gate, never by the phase that wrote it",
/data/.hive/tasks/T-013/handoffs/revisor.json:    "learnings": [
      "format.ts holds two readers of a served timestamp and only agoSeconds is guarded; formatClock throws RangeError",
```

## Why

The field is free prose by its own schema. `dispatcher/handoff.py` describes it
as "Proposed learnings: something true of this project that the next task would
want to know" and validates it as a list of strings, so a role writes sentences.
A few sentences happen to quote a ref inside them; most do not, and nothing has
ever required one. The filename reading was a guess made when the console's
fixture was the only instance anyone had looked at, and it reached a type
comment and an ADR without anyone opening a handoff file.

## What to do

Treat `handoff.learnings` as prose to render, and get a task's real entries from
the tree instead, by the entry's own `task` and `carried_by` frontmatter — the
only task-scoped relation the learnings tree has. That is what the task detail's
learnings region does.

Do not cite `dispatcher/learnings.py:droppable` as the rule for that filter.
`droppable` is four conditions — the entry is in the inbox, it is not reviewed,
its `scope` is `project`, and the task id matches `task` or `carried_by` — and
only the last is the relation you want. On this hive the difference is most of
the data: 34 of the 41 inbox entries are `scope: harness`, so a filter narrowed
to `droppable`'s four conditions would hide harness-scoped entries a task really
did file.

## Evidence

`grep -rn '"learnings"' -A 6 /data/.hive/tasks/T-015/handoffs/arquitecto.json
/data/.hive/tasks/T-013/handoffs/*.json`, run in T-016's arquitecto phase over
every handoff this harness has written, with `dispatcher/handoff.py`'s
`_PROPERTIES` as the schema side. The `droppable` half is T-016's revisor round
1, finding 2 (`/data/.hive/tasks/T-016/review-round-1.md`); the scope counts are
`ls *.md | wc -l` and `grep -l 'scope: harness' *.md | wc -l` in
`/data/.hive/learnings/inbox`, re-run by T-016's auditor. Inbox entry:
`/data/.hive/learnings/inbox/T-016-handoff-learnings-is-prose-not-refs.md` —
note that its own Rule still cites `droppable`, which is the overstatement the
revisor caught a round later.
