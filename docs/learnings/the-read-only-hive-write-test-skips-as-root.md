# The read-only hive write test fails as root, and since T-098 it skips

**When it applies:** the tests-run gate or a full `python3 -m pytest` reports
`tests/board_service/test_writes.py::test_a_write_to_a_read_only_hive_is_a_tool_error`
as failed or skipped in a phase where `id -u` prints 0.

**Status:** unconfirmed — reported by T-097's implementador (shared inbox),
carried by T-099, T-100 and T-102.

## Symptom

```
tests/board_service/test_writes.py:26: AssertionError
E       AssertionError: {'content': [{'text': '{"id":"T-001","comment":{"at":"2026-10-19T12:44:12.832052+00:00","author":"operator","text":"x"...
FAILED tests/board_service/test_writes.py::test_a_write_to_a_read_only_hive_is_a_tool_error
```

## Why

The test makes the hive read-only with `chmod 0555` and expects the write to
fail. Root ignores the mode bits, so the write succeeds and the test asserts
on a success. It was not caused by T-097's diff: it failed on the base too.
T-098 added a `skipif` on `os.geteuid() == 0` to the test, so on a tree at or
after T-098 it shows as one of the skips, not a failure.

## Rule

Do not chase it: as root a skip is expected; a failure means your tree predates
T-098 or the `skipif` was lost.

## Evidence

T-097 (2026-10-19): `python3 -m pytest` from its worktree as uid 0 — 1 failed
(this test), the rest passed; `/data/.hive/tasks/T-097/gates-round-2.log`.
The fix is commit `a22c85a` (T-098).
