# `Worker.run()` calls `recover()` first

**When it applies:** a test in `tests/dispatcher/test_worker.py` drives
`Worker.run()` (not `tick()`) with an action it has already claimed itself.

**Status:** unconfirmed. T-110's implementer hit it once.

## Symptom

There is no error. Before the loop runs, the claimed action is either ended
`interrupted` or put back in `pending/`, so the test's handler never sees it
the way the test set it up.

## Why

`run()` starts with `recover()`, which applies each kind's recovery rule
from `actions.recover` / `_recover_claimed` to every claimed action. For a
`turn` (recovery `interrupt`), that rule ends the action `interrupted`. The
same applies to the one the test set up.

## Rule

Stub `worker.recover` in a test that runs `run()` over its own claimed
action, or claim the action through `tick()` after `run()` has started.

## Evidence

T-110 implementer, the shutdown test in the turn section of
`tests/dispatcher/test_worker.py`.
