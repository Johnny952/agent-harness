# A new parallel caller of `run_task_cycle` only passes a `WriteScheduler`

**When it applies:** you are making some new path run several cycles of one
project at once, and are about to write its own single mapping, write
windows, base merge, stale-plan note or ADR number handling.

**Status:** unconfirmed. T-140's arquitecto and implementador both proposed
it, which is one source; the auditor checked it against the code.

## Why

`dispatcher/dispatcher.py` `run_task_cycle` takes `scheduler=`. Given one, it
already does what a parallel cycle needs: the single mapping
(`scheduler.mapping_lock`, `scheduler.mapped_by`), the write window
(`scheduler.enter_write_window` / `leave_write_window`). The base merge and
the stale-plan note no longer need it: since T-155 (ADR 339) every cycle
merges the base before its first implementador. ADR number blocks need
nothing from the scheduler: they are reserved per task
(`_adr_block_note`, ADR 81).

`dispatcher/queue.py` `WriteScheduler` is the one implementation. The queue
builds it with its confirmed order; the worker's start group (ADR 306) builds
it with none and calls `enlist(task_id)` as each start joins.

## Rule

Build a `WriteScheduler` (project, log label, condition, optional order) and
pass it as `scheduler=`; hold the project's `ProjectLock` once for the group
and pass it as `project_lock=` so the cycle does not take or release it.

## Evidence

`dispatcher/queue.py` `_run_parallel` and `dispatcher/worker.py`
`Worker._join_project` / `_held_scheduler` at the end of T-140;
`docs/decisions.md` ADR 83 and ADR 306.
