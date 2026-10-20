# `open_cycle` and `close_cycle` are the seam every cycle passes through

**When it applies:** you add a rule that must hold for every cycle, whichever
entry point started it — `run-task`, `run-phase`, the verdict commands, a
queue or the worker.

**Status:** unconfirmed. T-116's architect and implementer both reported it,
but that is one source; the auditor checked the callers below.

## Why

`dispatcher/dispatcher.py` `open_cycle` is called by `run_task_cycle` and
`run_single_phase`, and by `dispatcher/command_run.py`
`run_command_request` and `resume_command_verdict`. Those four are every way
a cycle starts: the CLI verbs, `dispatcher/queue.py` `run` and the worker's
`start` and `command_verdict` handlers all go through one of them. Each pairs
`open_cycle` with `close_cycle`, which runs on every way out (done, blocked,
crashed).

## Rule

Put a per-cycle rule in `open_cycle` (taken last, after every refusal, so a
refused start writes nothing) and its undoing in `close_cycle`'s `finally`, as
ADR 200 did with the project lock; do not add it to each entry point.

## Evidence

`grep -n "open_cycle(" dispatcher/` at the end of T-116: four callers, the
four functions above. `docs/decisions.md` ADR 200.
