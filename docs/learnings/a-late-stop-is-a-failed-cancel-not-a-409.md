# A stop that comes too late is a failed cancel, not a 409

**When it applies:** you write a screen or test that stops a running or queued
action through `POST /api/actions/<id>/cancel` and expect the route to refuse a
target that already finished.

**Status:** unconfirmed — proposed by T-114's architect and implementador; the
auditor corrected "ends with `target_status`" against the worker.

## Symptom

The cancel route answers 201 for a target that is already `done`. Later the
cancel record itself ends `failed` with
`target <id> already finished as <status>`; only a cancel that took effect ends
`done` with `target_status` (`cancelled` or `running`).

## Why

`dispatcher/actions.py` `_validate_cancel` checks only that `params.target` is
an action id; whether the target can still be stopped is decided by
`dispatcher/worker.py` `_cancel` on its claim.

## Rule

Never show a 409 for a late stop; read the outcome from the cancel record on
the next queue poll (see `a-cancel-answers-with-a-new-cancel-action`).

## Evidence

`dispatcher/worker.py` `_cancel` (its `already finished as` branch);
`_validate_cancel` in `dispatcher/actions.py`; ADR 190, 4.
