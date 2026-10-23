# `command_run` imports the cycle tails by name

**When it applies:** you change the signature of `_cycle_from_arquitecto` or
`_cycle_rounds` in `dispatcher/dispatcher.py`.

**Status:** unconfirmed — proposed by T-154-S1's arquitecto and implementador, one source.

## What happens

`dispatcher/command_run.py` imports both private functions by name and calls
them on a verdict's resume: `_cycle_from_arquitecto(ctx, True, None)` and
`_cycle_rounds(ctx, True, None, resumed=(round_num, implemented))`. A
renamed, reordered or newly required parameter breaks the verdict's resume
path, not the cycle that `run_task_cycle` runs.

## What to do

Add new parameters as keywords with defaults (as T-154-S1 did with
`_cycle_rounds`' `start_round`), and grep `command_run.py` before renaming.

## Evidence

`grep -n "_cycle_from_arquitecto\|_cycle_rounds" dispatcher/command_run.py`
on T-154-S1's branch: the import and the two calls.
