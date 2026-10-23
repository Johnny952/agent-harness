# A field inside a handoff list item is required too, and the revisor pays for it

**When it applies:** you are adding a field to an object inside a handoff list
in `dispatcher/handoff.py` `_PROPERTIES` (a `debt` declaration, say) and want
it optional, or are budgeting its bytes against the schema-size test.

**Status:** unconfirmed — proposed by T-164-S2's arquitecto and implementador,
read against the tests by the auditor.

## What is true

`test_schema_is_closed_at_every_level` in `tests/dispatcher/test_handoff.py`
asserts, for every list of objects, that the item's `required` equals its
`properties`. So there is no optional item field either: T-164-S2's
`blocked_by` is required and `""` means "not gated" (`docs/decisions.md`
ADR 396). This is the item-level twin of
[a-property-in-role-extras-is-required-of-that-role](a-property-in-role-extras-is-required-of-that-role.md).

`test_schema_stays_small_enough_to_send_on_every_call` caps every role's
schema. The revisor's is the widest because it restates the debt items with a
ruling on top, so a debt field costs there first: T-164-S2 measured the
revisor at 4205 bytes with `blocked_by` and moved the cap from 4096 to 4352.

## What to do

Give the field an empty answer that means "none", make the reader treat empty
as absent, keep its description short, and measure the revisor's schema before
touching the cap; if the cap moves, say why in the test's docstring as
T-164-S2 did.

## Evidence

`tests/dispatcher/test_handoff.py`: `test_schema_is_closed_at_every_level`,
`test_schema_stays_small_enough_to_send_on_every_call`,
`test_a_debt_declaration_carries_blocked_by_as_a_required_string`.
