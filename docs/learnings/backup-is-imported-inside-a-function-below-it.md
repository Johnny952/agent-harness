# `backup` is imported inside a function by the modules it imports

**When it applies:** you want to call `dispatcher/backup.py` (`before_merge`,
`merge_hook`, `backup`) from `dispatcher/queue.py`, `dispatcher/worker.py` or
`dispatcher/dispatcher.py`.

**Status:** unconfirmed. T-157's arquitecto and implementador both reported
it, which is one source; the auditor checked it against the imports.

## Why

`backup` imports `worker` at module level, `worker` imports `queue` and
`dispatcher`, so a module-level `from dispatcher import backup` in any of
those three is a circular import.

## Rule

In `queue.py` and `worker.py` import `backup` inside the function that uses it
(`queue._merge_hook`, `worker._merge_hook`, `worker._approve`); in
`dispatcher.py` never import it — take an injected callable instead, as
`run_task_cycle`'s and `approve_reviewed`'s `before_merge` keyword do
(`docs/decisions.md` ADR 342, ADR 345).

## Evidence

`grep -rn "from dispatcher import backup" dispatcher` on T-157's branch: the
three hits are all function-local, in `worker.py` and `queue.py`.
