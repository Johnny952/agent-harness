# A resolve conflict's outcome comment opens `resolve conflict, asked by`

**When it applies:** you want the console to show what became of a queued
*resolve conflict* and are about to filter a task's comments for it.

**Status:** unconfirmed. Read from the code by T-133-S1's auditor; one task.

## Why

`dispatcher/worker.py` `_resolve_conflict` writes exactly one comment per
outcome, author `dispatcher`, text `resolve conflict, asked by <actor>:
<detail>` — a comma after `conflict`, not a colon, unlike approve's
`approve at <commit>:`. A `request_changes` writes none at all (see
[request-changes-writes-no-outcome-comment](request-changes-writes-no-outcome-comment.md)).
The outcome itself (`resolved`, `not_resolved`, `stale`, `not_run`) is only in
the action's `result`, not in the comment's words (ADR 285, point 5).

## Rule

Match on author `dispatcher` and the prefix `resolve conflict, asked by `; read
the outcome from the action's `result.outcome`, not from the comment.

## Evidence

`dispatcher/worker.py` `_resolve_conflict` and `tests/dispatcher/test_worker.py`
`test_a_resolve_comments_once_and_ends_done_with_its_result`, at the end of
T-133-S1.
