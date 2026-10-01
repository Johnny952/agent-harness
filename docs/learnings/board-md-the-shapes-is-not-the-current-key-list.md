# `docs/plans/board.md` *The shapes* is Phase 1's specification, not what the routes answer today

**When it applies:** you are reading *The shapes* in `docs/plans/board.md` to
find out what a route answers now, or you are about to rewrite it because you
noticed it is short.

**Status:** confirmed — outgrown since ADR 10 and outgrown further by T-011; no
test compares it with any route.

## What is missing from it

Its **Task** list has seven `TaskFile` fields plus `card`, eight in all;
`/api/tasks` answers ten and `/api/tasks/<task_id>` eleven. Its **Account** list
has five; `/api/accounts` answers nine. The gaps, oldest first:

- `lock_expired` on a Task — `docs/decisions.md` ADR 10, the one fact the
  endpoint derives rather than reads.
- `depends_on` on a Task, and `body` on the detail route only — ADR 17 and
  ADR 21.
- `is_primary`, `quota_threshold_pct`, `reserve_pct` and
  `quota_cooldown_seconds` on an Account — ADR 17, ADR 18 and ADR 20.

Nothing fails when the list falls behind, which is why it has three times.

## What to read instead

`docs/decisions.md` is the live contract — ADR 3–5 and ADR 10 for Phase 1,
ADR 11 for the caps, ADR 17, 18, 20 and 21 for what tier 1 of
`docs/plans/front.md` added. The enforcement is in
`tests/observability/test_api.py`, which asserts whole rows as dicts
(`test_tasks_reports_the_fields_the_task_file_carries`,
`test_accounts_reports_the_pool_from_the_state_directory`): those are what
actually fail when a key appears or moves, and they are the key list that cannot
drift.

## Do not rewrite it

It is Phase 1's specification of what the phase *was*, and the reasoning a
reversal would need lives in it —
[a-toolchain-ruling-can-outlive-a-specs-state-list](a-toolchain-ruling-can-outlive-a-specs-state-list.md)
and
[a-plans-status-paragraph-is-the-one-out-of-cycle-edit](a-plans-status-paragraph-is-the-one-out-of-cycle-edit.md)
on which part of a plan a later task may touch. The record of what grew past it
is the ADR. T-011 left it alone on ADR 10's precedent and said so in ADR 20
*Consequences*, which is the pattern to copy.

## Evidence

T-011: `docs/plans/board.md` *The shapes*, against `observability/api/app.py`'s
`_task` and `accounts` view; `docs/decisions.md` ADR 20 *Consequences* and
ADR 21; `docs/implementations/T-011.md` *What was not done, and is not debt*.
