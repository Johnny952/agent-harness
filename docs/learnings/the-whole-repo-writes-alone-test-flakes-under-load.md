# The whole-repo-writes-alone queue test can fail a full suite run on a tree that did not touch `dispatcher/`

**When it applies:** a full `python3 -m pytest` fails on
`tests/dispatcher/test_parallel_queue.py::test_a_whole_repo_task_writes_alone`
and your diff touches nothing under `dispatcher/` or that test file.

**Status:** unconfirmed — seen by T-061's auditor (shared inbox, not filed
then) and again by T-062's auditor, both on diffs with no `dispatcher/` file.
T-065's implementador saw it a third time (failed, then 14 passed on the
rerun), and it is now debt `T-065-D1` in `docs/debt/README.md`. Not diagnosed.

## Symptom

```
>       assert windows["T-1"][1] < windows["T-2"][0]
E       assert 5 < 2

tests/dispatcher/test_parallel_queue.py:158: AssertionError
FAILED tests/dispatcher/test_parallel_queue.py::test_a_whole_repo_task_writes_alone
```

## Why

The test runs two fake cycles through `ScheduledCycle(cfg, hold=0.1)` and
asserts their windows do not overlap, so it depends on wall-clock scheduling;
under the load of the rest of the suite the order has come out wrong. The
exact race is suspected, not proved.

## Rule

Re-run the file alone and then the whole suite once before treating the
failure as yours; if it passes and your diff has no `dispatcher/` change,
report it as a flake under `risks` rather than fixing around it.

## Evidence

T-062 auditor, 2026-10-17: the full suite failed once (1 failed, 2107 passed,
10 skipped), then `python3 -m pytest -q tests/dispatcher/test_parallel_queue.py`
printed 14 passed and the full suite 2108 passed, 10 skipped. T-061's auditor
reported the same sequence the same day.
