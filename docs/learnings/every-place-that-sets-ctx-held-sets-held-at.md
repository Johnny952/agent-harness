# Every place that sets `ctx.held` sets `ctx.held_at`

**When it applies:** you add or move a place in `dispatcher/dispatcher.py`
that sets `CycleContext.held`, or change what `run_task_cycle`'s `on_held`
is told.

**Status:** unconfirmed — proposed by T-154-S1's arquitecto and implementador, one source.

## What happens

`run_phase` sets `ctx.held` in two places: the `may_start_phase` refusal and
a fatal phase that ended `no_account`. Since T-154-S1 (ADR 354) each also sets
`ctx.held_at = (role, round_num)`, and `_cycle_rounds` overrides it to
`("auditor", rounds_run)` when the auditor was held, since the auditor runs
with no round. `run_task_cycle` calls `on_held` only when the outcome is HELD
and `held_at` is set, so a new `held = True` without `held_at` makes a HELD
cycle silently skip its callback.

## What to do

Set `held_at` next to every `held = True`, and add an `on_held` test for the
new path in `tests/dispatcher/test_dispatcher.py`.

## Evidence

`grep -n "ctx.held = " dispatcher/dispatcher.py` on T-154-S1's branch: two
hits, both in `run_phase`, each followed by its `held_at`.
