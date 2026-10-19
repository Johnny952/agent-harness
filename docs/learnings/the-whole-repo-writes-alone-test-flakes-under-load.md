# The whole-repo-writes-alone queue test can fail a full suite run on a tree that did not touch `dispatcher/`

**When it applies:** a full `python3 -m pytest` fails on
`tests/dispatcher/test_parallel_queue.py::test_a_whole_repo_task_writes_alone`
and your diff touches nothing under `dispatcher/` or that test file.

**Status:** retired 2026-10-19: debt `T-065-D1` is resolved. The cause was
found and it was the test's: it asserted which task wrote first, and the
scheduler orders only tasks already waiting, so T-2 could legally write first
— exactly the `assert 5 < 2` log. The test now orders the two windows with an
event and a poll on the scheduler's waiters, in both directions. If it fails
now, it is a real failure of the write window; do not treat it as this flake.
Earlier: seen by T-061's and T-062's auditors and by T-065's implementador.

## Symptom

```
>       assert windows["T-1"][1] < windows["T-2"][0]
E       assert 5 < 2

tests/dispatcher/test_parallel_queue.py:158: AssertionError
FAILED tests/dispatcher/test_parallel_queue.py::test_a_whole_repo_task_writes_alone
```

## Why

The test ran two fake cycles through `ScheduledCycle(cfg, hold=0.1)` and
asserted that T-1's window came first. `_Scheduler.enter_write_window` lets the
queue order decide only between tasks already waiting, so when T-2's thread
asked first it wrote first, and T-1 after it: no overlap, but the six-event log
`T-1 arq, T-2 arq, T-2 enter, T-2 leave, T-1 enter, T-1 leave` gives
`assert 5 < 2`. Proved on 2026-10-19 (see `docs/debt/T-065-D1.md`).

## Rule

Retired. Before 2026-10-19 the rule was to re-run the file and the suite and
report a flake. Now a failure of this test is a real one. To order threads in
a queue test, use an event and a poll with a generous deadline, not `hold`.

## Evidence

T-062 auditor, 2026-10-17: the full suite failed once (1 failed, 2107 passed,
10 skipped), then `python3 -m pytest -q tests/dispatcher/test_parallel_queue.py`
printed 14 passed and the full suite 2108 passed, 10 skipped. T-061's auditor
reported the same sequence the same day.

T-105-S2 auditor, 2026-10-19, on a diff touching only `front/` and `docs/`:
the full suite failed on it twice running, and so did the file run alone
(`1 failed, 13 passed`); only `-k whole_repo` passed. So "re-run the file"
does not always clear it. The assertion also fixes the order (T-1's window
first): with six events, `assert 5 < 2` fits T-2 writing first and T-1 after
it, which is still "writes alone" — suspected, not proved.
