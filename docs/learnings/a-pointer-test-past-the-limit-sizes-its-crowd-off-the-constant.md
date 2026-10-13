# A pointer test that overflows the limit sizes its crowd off the constant

**When it applies:** you are writing a test in
`tests/dispatcher/test_gates.py` that needs more present-doc citations than
the pointers gate's limit, or one that asserts which paths the gate asked
`ls` about, and are about to monkeypatch `gates._POINTER_LIMIT` down to a
small number.

**Status:** unconfirmed — written by T-036's implementador and read by its
revisor; no second task has used the helpers.

## Symptom

None: a design choice with a helper behind it.

## Why

A test run against a patched limit of 5 passes or fails without regard to
the real number, and says nothing about what the limit bounds. Two helpers
at the end of the Gate 4 section make the real number cheap:

- `_crowd(count)` returns `count` present-doc citations that all resolve, as
  the pair the `_Worktree` fake takes: grep's lines for `pointers`, and the
  paths for `present`;
- `_asked(worktree)` is every path handed to `ls`, over however many calls.

## Rule

Size the crowd off the constant, `_crowd(gates._POINTER_LIMIT + 5)`, and
assert on `_asked`. Do not patch `_POINTER_LIMIT`. A crowd of 1005 runs in
milliseconds, and the test still holds when the number moves.

A present-doc pair that is also one of the task's own is counted once, as
the task's: add the task's own paths to `present` so they resolve, as
`test_the_log_names_how_many_present_citations_went_unchecked` does.

## Evidence

The four tests after `_crowd` in `tests/dispatcher/test_gates.py`;
`python3 -m pytest tests/dispatcher/test_gates.py -q` printed `104 passed in
0.13s` in the T-036 worktree. `docs/implementations/T-036.md`, *Tests* and
*Ruled out*.
