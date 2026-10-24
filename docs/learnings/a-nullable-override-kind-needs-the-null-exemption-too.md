# A nullable override kind needs the null exemption in `_read_overrides` too

**When it applies:** you add an override kind to `dispatcher/config.py`
`_check_override_value` that should accept `null`.

**Status:** unconfirmed. Read off the T-177-S1 diff by its auditor; no phase
hit it as a failure.

## What is true

`_read_overrides` refuses a `None` value before the kind check ever runs
(`null is not a value, a reset is deleting the key`, ADR 75). A kind whose
check accepts `None` never sees one unless the `elif value is None` branch
also exempts that kind, as it does for `nullable_model` (ADR 441). The
check alone passes its own unit tests and still refuses the override at load.

## What to do

Edit both places: the kind's branch in `_check_override_value` and the
`value is None` exemption in `_read_overrides`. Test through `load_config`
with a `key: null` overrides file, not through the check, and keep a test
that a null override of any other key is still refused.

## Evidence

T-177-S1, `git diff 175322e HEAD -- dispatcher/config.py`;
`tests/dispatcher/test_config.py`
`test_a_null_override_of_any_other_key_is_still_refused`.
