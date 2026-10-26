# A refused create makes no actions directory

**When it applies:** a test in `tests/dispatcher/test_actions.py` expects an
`ActionRefused(400)` from `actions.create` and then asserts on
`names(tmp_path, "pending")` (or any other queue subdirectory).

**Status:** unconfirmed — proposed by T-180's implementador after two of its
tests failed this way.

## Symptom

The implementador did not quote its error; `Path.iterdir` on a missing
directory raises this shape:

```
FileNotFoundError: [Errno 2] No such file or directory: '.../actions/pending'
```

## Why

`create` refuses a malformed record in `_new_record`, before `_write_all`
calls `ensure_dirs`, so a 400 leaves `<tmp_path>/actions/` unmade; the test
helper `names` calls `iterdir()` on the directory and raises. A 409 is
refused inside `_write_all`, after `ensure_dirs`, so it does not hit this.

## Rule

Call `actions.ensure_dirs(tmp_path)` first in a test that asserts a queue
directory is empty after a 400.

## Evidence

`python3 -m pytest tests/dispatcher/test_actions.py` in the T-180 worktree at
the implementador phase; see `test_a_bad_git_token_is_refused` and
`test_the_secret_rules_at_create`.
