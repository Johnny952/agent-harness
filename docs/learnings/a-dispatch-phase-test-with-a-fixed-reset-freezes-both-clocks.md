# A `dispatch_phase` test with a fixed reset time freezes both of the dispatcher's clocks

**When it applies:** you are writing or reading a test in
`tests/dispatcher/test_dispatcher.py` that feeds `dispatch_phase` a refusal or
`rate_limit_event` with a hard-coded `resetsAt`, and asserts the retry time
or hold it records — or such a test fails only after a certain wall-clock
date.

**Status:** unconfirmed — one task. T-152's tests-run gate failed on it in
round 1 and its round 2 fixed the test.

## Symptom

```
>       assert get_retry_at(cfg.state_dir, "cuenta1") == 1792709400.0
E       AssertionError: assert None == 1792709400.0
```

in `test_a_streamed_refusal_records_its_reset_as_the_retry_time`, from
2026-10-22 ~23:10 UTC on, with no change to the code it exercises.

## Why

`dispatcher/dispatcher.py` reads the clock in two ways: `_utc_now()` (the seam
`one-clock-seam-per-module-and-a-now-parameter-is-the-other-half.md`
describes) and bare `time.time()`, which the cooling-account recheck uses. The
recheck drops a hold whose `retry_at` has passed, so once real time passes
the fixture's `resetsAt` the recorded retry time is gone before the assert.

## Rule

Patch both `dispatcher_mod._utc_now` and `dispatcher_mod.time.time` to a fixed
moment before the fixture's reset (`_D2_NOW` in that file); a failure of this
shape on an untouched test is the calendar, not your diff.

## Evidence

`python3 -m pytest tests/dispatcher/test_dispatcher.py -q -k
streamed_refusal_records_its_reset` in the T-152 worktree on 2026-10-22 (the
round-1 gate log); the fix is in that test, committed in T-152 round 2.
