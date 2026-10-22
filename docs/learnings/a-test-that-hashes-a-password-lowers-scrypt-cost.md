# A test that hashes a password lowers the scrypt cost first

**When it applies:** you are writing a test that creates a user or signs one in
through `board_service/signin.py` — anything that calls `hash_password`,
`verify_password` or `create_user`, or posts to `/api/session`.

**Status:** unconfirmed — reported by T-146's implementador.

## Symptom

No error: the suite just slows. Each hash at the production parameters
(`SCRYPT_N = 2**14`, `SCRYPT_P = 5`) takes about 0.3 s, and a sign-in test
hashes at least twice — `create_user`, then the check, plus `_dummy` once per
process for an unknown name.

## Why

The cost is the point of scrypt, and the parameters are module constants read
at call time.

## Rule

Monkeypatch `signin.SCRYPT_N` and `signin.SCRYPT_P` low in an autouse
fixture, as `tests/board_service/test_signin.py` `fast_scrypt` does, and keep
one test that calls `monkeypatch.undo()` to pin the production values and hash
once at them (`test_the_default_scrypt_parameters`).

## Evidence

`tests/board_service/test_signin.py`'s fixture sets `SCRYPT_N` to `2**4` and
`SCRYPT_P` to 1; with it the board service suite ran 559 tests in 8.3 s in
T-146's worktree.
