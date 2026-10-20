# An account stub serves `current_task`, not `current_task_id`

**When it applies:** you write a front test that stubs `GET /api/accounts`
and expects a busy account to carry a task.

**Status:** unconfirmed. T-115's implementer reported it; the auditor checked
the code, not a failing run.

## Why

`observability/api/app.py` `accounts()` serves `current_task`.
`front/src/lib/api/client.ts` `listAccounts` renames it to the console's
`Account.current_task_id`. Front tests stub the fetch, so the stub carries the
served shape. A stub that writes `current_task_id` is read as no task: the
rename drops it, and nothing fails to typecheck, because the stub is JSON.

## Rule

Write `current_task` in a fetch stub, and `current_task_id` only where you
build an `Account` object directly.

## Evidence

`front/src/components/console/chat-panel.test.tsx` and
`front/src/routes/-index.test.tsx` stub accounts with `current_task` (T-115).
