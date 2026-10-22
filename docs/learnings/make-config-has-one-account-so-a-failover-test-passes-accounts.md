# `_make_config` has one account, so a failover test must pass `accounts=`

**When it applies:** you are writing a `tests/dispatcher/test_dispatcher.py`
case in which a phase is handed to a second account (a rate limit, a login
failure) and build the config with `_make_config(tmp_path, ...)`.

**Status:** unconfirmed — reported by T-148's implementador; the default was
checked against the helper by T-148's auditor.

## Symptom

No exception: the handed-on phase ends `no_account` instead of running on the
next account, and the assertion on the second call fails.

## Why

`_make_config`'s defaults hold a single `AccountConfig(name="cuenta1", ...)`.
Once that account is parked `COOLING_DOWN` (or tried) there is no idle account
left for the failover to pick.

## Rule

Pass `accounts=[AccountConfig(...), AccountConfig(...)]` to `_make_config` in
any test whose phase must land on a second account.

## Evidence

`tests/dispatcher/test_dispatcher.py` `_make_config`: the `accounts=` default.
The T-148 tests using `_scripted_phase` pass two accounts explicitly.
