# An approve on a `conflict` task is refused before the queue is read

**When it applies:** you write a test (or a console flow) that queues an
`approve` against a task in `conflict` to see the action queue answer — a 409
for a `resolve_conflict` already queued, say — and get a 400 instead.

**Status:** unconfirmed. Reported by T-133-S1's implementer, checked against
the code by its auditor; one task.

## Why

`board_service/app.py` `_review_action` writes the verdict line through
`dispatcher/tasks.py` `record_verdict` *before* `actions.create` runs the
one-per-task check. `record_verdict` refuses an `approve` on a `conflict` task
(it waits on resolve conflict); a `request_changes` is taken on `conflict`
(ADR 278). So the queue's 409 is never reached for an approve while the task is
in `conflict`, and a review action that does reach the queue and is refused
there has already written its verdict line (the 409 body carries `verdict`).

## Rule

To exercise the queue's refusal of an approve, queue the other action first,
then rewrite the task to `awaiting_review` before posting the approve, as
`tests/board_service/test_resolve_conflict_route.py`
`test_a_review_action_while_a_resolve_is_queued_is_409` does.

## Evidence

`dispatcher/tasks.py` `record_verdict` (its `under_review` check) and
`board_service/app.py` `_review_action`, at the end of T-133-S1.
