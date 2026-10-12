# A property added to a role's `_ROLE_EXTRAS` is required of that role, with no second edit

**When it applies:** you are adding a field to one role's handoff schema in
`dispatcher/handoff.py`, or wondering why a role must answer a field it has
nothing to say about.

**Status:** unconfirmed — proposed by T-031's implementador, read against
the code by the auditor.

## What is true

`handoff.schema_for` builds a role's schema from `_PROPERTIES` plus
`_ROLE_EXTRAS[role]` and sets `"required": list(properties)`. There is no
optional property: the line `_ROLE_EXTRAS["arquitecto"] = _SPLIT_PROPERTY`
was the whole of making `split` both offered and required.
`test_schema_requires_every_field_it_declares` in
`tests/dispatcher/test_handoff.py` asserts `required` equals `properties`
for every role.

## What to do

Give a new property an empty answer that means "nothing" (`split: []`) and
say so in the role's duty text in `dispatcher/project_docs.py`, as the
`_ARQUITECTO` paragraph ends "With nothing to split, `split` is an empty
list". The reader must treat empty as absent (`handoff.split_of`). Then check
the size: `test_schema_stays_small_enough_to_send_on_every_call` caps each
role's schema
([offering-split-to-a-role-is-one-red-test-and-a-measured-fit](offering-split-to-a-role-is-one-red-test-and-a-measured-fit.md)).

## Evidence

`dispatcher/handoff.py`: `schema_for`, `_ROLE_EXTRAS`.
`tests/dispatcher/test_handoff.py`:
`test_the_arquitecto_is_offered_a_split_and_has_to_answer_it`,
`test_no_other_role_is_offered_a_split`.
