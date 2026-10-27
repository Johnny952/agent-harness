# `task_edit` refuses `depends_on` on a task with handoffs; `tasks.handoff` does not

**When it applies:** you need to rewrite a board task's `depends_on` from
dispatcher code, and the task's file may already carry a handoff or comment
body.

**Status:** unconfirmed — proposed by T-188's arquitecto; checked by its
auditor against `dispatcher/tasks.py`.

## Symptom

```
it has started (its task file has handoffs); task_edit changes a task only before that
```

## Why

`dispatcher/tasks.py` `edit_task` refuses any change once `_started` says so,
and a `pending` task whose file has a non-empty body counts as started.
`tasks.handoff(..., depends_on=)` sets `depends_on` with no such check — but it
also clears `owner`, `heartbeat` and `phase` and writes the status it is given.

## Rule

Re-point a pending, unstarted task's `depends_on` through `tasks.handoff` (the
queue's `_comment(..., depends_on=)` does, ADR 495); never on a task a live
run holds, since the handoff clears its lock.

## Evidence

T-188: read `tasks.edit_task`, `tasks._started` and `tasks.handoff`.
