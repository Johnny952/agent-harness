# A full `dispatch_phase` test lives in `tests/dispatcher/test_dispatcher.py`

**When it applies:** you add a test that drives `dispatch_phase` end to end and
are about to put it in a new or a different test module.

**Status:** unconfirmed — T-170's auditor, from the implementador's round 1.

## Symptom

Outside `tests/dispatcher/test_dispatcher.py` the phase fails for a missing
handoff, and the git calls are not faked.

## Why

That module's autouse fixtures `fake_git` and `_phases_may_end_in_prose` (which
stubs `dispatcher._resume_missing_handoff`) are what let a fake "phase done"
answer through. They are module-scoped autouse fixtures, not in
`tests/dispatcher/conftest.py`, so no other module gets them.

## Rule

Put a full `dispatch_phase` test in `tests/dispatcher/test_dispatcher.py`; keep
unit tests of the pieces in their own module.

## Evidence

T-170 implementador: the `dispatch_phase` test for ADR 429 was moved into
`tests/dispatcher/test_dispatcher.py`, while the probe and reaper tests stayed in
`tests/dispatcher/test_process_probe.py`.
