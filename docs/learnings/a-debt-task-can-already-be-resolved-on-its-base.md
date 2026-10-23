# A debt task can already be resolved on its base

**When it applies:** your task has `source: debt` and a `debt_id`, and you
are about to plan its fix.

**Status:** unconfirmed — proposed by T-159's arquitecto, implementador and
revisor (one source), checked against the tree by T-159's auditor.

## Symptom

```
| `T-154-S1-D1` | **Resolved 2026-10-23 by T-154-S2.** |  | `dispatcher/queue.py` passes `on_held` and `start_at`/`start_round` to `run_task_cycle` (ADR 357). ...
```

T-159 was filed from `T-154-S1-D1`'s open row in `docs/debt/README.md`,
but a resolution row for the same id sat further down the table: the fix
(`dispatcher/queue.py` `_start_kwargs`, `_settle`) was already on the base.

## Why

The backlog item was filed from the open row before T-154-S2 appended the
resolution row, and nothing withdrew it. Since T-160-S2 a merge drops a
`proposed` or `deferred` card whose debt the task's `resolved_debt` names
(`docs/decisions.md` ADR 369), but a card already accepted as a task only
gets a warning.

## Rule

Before planning a debt task, `grep -n "<debt_id>" docs/debt/README.md` and
look for a `**Resolved` row; if one is there, check it with the tests and
hand off with nothing to change, filing no new row.

## Evidence

`grep -n "T-154-S1-D1" docs/debt/README.md` in the T-159 worktree prints the
open row and the resolution row; `python3 -m pytest -q
tests/dispatcher/test_queue.py` → 62 passed, including
`test_a_resumed_limit_reached_task_starts_where_the_limit_held_it`.
