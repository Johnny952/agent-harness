# A gates test that wants no ask needs a test file in its diff

**When it applies:** you are writing a test in
`tests/dispatcher/test_gates.py` whose `_Worktree` diff touches a code file,
and you assert `not report.needs_answer` (or that only your own gate spoke).

**Status:** unconfirmed — proposed by T-141's implementador; no second task
has hit it.

## Symptom

`assert not report.needs_answer` fails although the gate under test only
emits a NOTE.

## Why

Gate 1, `_tests_in_diff` in `dispatcher/gates.py`, runs on every diff and
answers ASK whenever a non-test code file changed with no test of the same
language group beside it. A fake diff of `("pkg/mod.py",)` alone therefore
always carries a `tests-in-diff` ASK.

## Rule

Put a test file of the same language in the fake diff
(`diff=("pkg/mod.py", "tests/test_mod.py")`) whenever the test asserts there
is nothing to answer; filter by gate name with `_findings` otherwise.

## Evidence

`test_a_touched_file_past_the_threshold_is_a_note_that_never_blocks` in
`tests/dispatcher/test_gates.py`; `python3 -m pytest -q` printed
`4333 passed, 15 skipped` in the T-141 worktree.
