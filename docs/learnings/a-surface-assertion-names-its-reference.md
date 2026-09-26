# A parity assertion against the union of both sides under test proves nothing

**When it applies:** you are about to plan around a test failure a doc or a task
description predicts, or you are writing any "this class exposes exactly that
surface" assertion.

**Status:** unconfirmed — reported once, by T-009's arquitecto.

## Symptom

No error. `test_the_local_board_adds_nothing_to_the_shared_surface` passed
unchanged after `LocalBoardClient.unreadable()` was added, although
`docs/learnings/the-kanban-seam-is-a-closed-surface.md` and T-009's own task
description both stated it would fail:

```
tests/dispatcher/test_vibe_kanban_client.py .......... [100%]
49 passed
```

## Why

The assertion compared the class's own public names against the **union** of
both clients' names:

```python
public = {name for client in (VibeKanbanClient, LocalBoardClient) for name in vars(client) ...}
assert {name for name in vars(LocalBoardClient) ...} == public
```

A set is always a subset of a union it is part of, so the equality only tested
`VibeKanbanClient`'s names ⊆ `LocalBoardClient`'s — the *other* parity test's
job. Any local-only addition passed.

## What to do

Name the reference surface explicitly and put the exceptions in a named
constant with the condition that justifies each one:
`assert local == shared | LOCAL_BOARD_ONLY`, as
`tests/dispatcher/test_vibe_kanban_client.py` now reads. And run the test before
planning around a failure a doc predicts: a spec that says "this will fail" is a
claim about a test, not the test itself. See
[the-kanban-seam-is-a-closed-surface](the-kanban-seam-is-a-closed-surface.md)
for what that surface is and what an exception to it owes.

## Evidence

`python3 -m pytest tests/dispatcher/test_vibe_kanban_client.py -q` before and
after adding `unreadable()`, in
`/data/projects/ia-harness/worktrees/T-009/work`. Inbox entry
`T-009-a-parity-assertion-against-a-union-is-vacuous.md`; recorded in
`docs/decisions.md` ADR 4.
