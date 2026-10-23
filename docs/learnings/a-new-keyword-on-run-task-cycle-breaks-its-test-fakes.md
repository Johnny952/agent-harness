# A new keyword on `run_task_cycle` breaks its test fakes

**When it applies:** you add a parameter to `dispatcher/dispatcher.py`
`run_task_cycle` or `open_cycle`, or to `dispatcher/command_run.py`
`run_command_request`, and a caller passes it.

**Status:** confirmed. Reported by T-116's implementer; hit again by T-157,
whose `before_merge` keyword needed the same fakes widened.

## Why

Tests replace these functions with fakes of a fixed signature —
`tests/dispatcher/test_worker.py` `Cycle` and `Verdict`,
`tests/dispatcher/test_queue.py` `FakeCycle`, and
`tests/dispatcher/test_parallel_queue.py` `ScheduledCycle` and the
`open_cycle` stub in its `cycle` fixture. When the worker or the queue starts
passing a new keyword (T-116's `project_lock`, `via`), each fake raises
`TypeError` on the unexpected argument, far from the change that caused it.

## Rule

Before adding the keyword, grep `tests/` for the function's name in quotes
(`"run_task_cycle"`, `"open_cycle"`, `"run_command_request"`) and give every
fake the new parameter.

## Evidence

`git diff 30cdfd8 HEAD -- tests/dispatcher/` on T-116: those five fakes'
signatures gained `project_lock=None` (and the `open_cycle` stub `via`).
