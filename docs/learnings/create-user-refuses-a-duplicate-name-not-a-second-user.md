# `signin.create_user` refuses a duplicate name, not a second user

**When it applies:** you write code that must create a user only when no user
exists yet (the first-user command, a restore of the auth dir).

**Status:** unconfirmed — read from the code by T-193-S1's arquitecto,
implementador, revisor and auditor.

## Symptom

None loud: a "no users yet" check made before calling `create_user` passes,
and a concurrent create lands a second user anyway.

## Why

`board_service/signin.py` `create_user` checks only `name in users` under
`_locked`. ADR 519 added the keyword `first=True`, which raises
`signin.UserExists` (a `ValueError`) inside the same locked section when any
user exists.

## Rule

Call `create_user(..., first=True)` and catch `UserExists`; treat any check
made outside `signin._locked` as a courtesy, not the guard.

## Evidence

T-193-S1, 2026-10-27: `git diff a571ee0 HEAD -- board_service/signin.py`
shows the `first and users` check inside `with _locked(auth_dir)`;
`tests/board_service/test_first_user.py` `test_create_user_first_flag` covers it.
