# The push route tests run without `cryptography`, and since T-119 only the key tests skip

**When it applies:** a full `python3 -m pytest` in an agent container stops at
collection on `tests/board_service/test_push_routes.py`, or shows three skips
there, on a diff that never touched push.

**Status:** unconfirmed — reported by T-119's arquitecto (shared inbox
`T-119-push-route-tests-need-cryptography`), fixed by T-119's implementador.

## Symptom

```
ERROR collecting tests/board_service/test_push_routes.py
tests/board_service/test_push_routes.py:12: in <module>
    from cryptography.hazmat.primitives import serialization
E   ModuleNotFoundError: No module named 'cryptography'
```

## Why

The agent image has no `cryptography` (pywebpush's dependency, ADR 229, is
declared in `pyproject.toml` but the agent image was not rebuilt with it), and
the test module imported it at module level to make a VAPID key, so the whole
file, and the pytest run, errored at collection on base 8cfcf70.

## Rule

Import `cryptography` inside the key fixture through `pytest.importorskip`, so
only the key tests skip and the rest of the file runs. Since T-119 the
`key_path` fixture writes a placeholder when the import fails and the three
tests that read the key's content (`test_the_public_key_is_served_open`,
`test_no_key_is_a_404`, `test_a_key_that_is_not_one_is_a_404`) `importorskip`
it: three skips there are expected; a collection error means the tree predates
T-119 or a new test imports it at module level again.

## Evidence

`python3 -m pytest -q tests/board_service/test_push_routes.py` in the T-119
worktree at base 8cfcf70: exit 2, the error above. After T-119's round 1:
`python3 -m pytest -q`, 3981 passed, 15 skipped.
