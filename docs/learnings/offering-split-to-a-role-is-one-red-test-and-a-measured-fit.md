# Offering `split` to a role turns one test red on purpose, and the schema still fits the cap

**When it applies:** you are adding `_SPLIT_PROPERTY` to `_ROLE_EXTRAS` in
`dispatcher/handoff.py` — step 4 of `docs/plans/task-split.md` section 8 — or
adding any other property to a role's handoff schema and budgeting it against
the size cap.

**Status:** unconfirmed — proposed by T-025's implementador and revisor; the
number was re-measured by T-025's auditor, the red test is read off the code
and has not been hit by any task yet.

## Symptom

No error today. The moment `split` joins a role's extras, this fails for that
role:

```
tests/dispatcher/test_handoff.py::test_no_role_is_offered_a_split_yet
```

It is parametrized over every role and asserts
`"split" not in handoff.schema_for(role)["properties"]`.

## Why

T-025 built the split shape inert: `_SPLIT_PROPERTY` is defined and in no
role's extras, because offering the field is the switch — a role that can
return a split will, and until the dispatcher branch that files one exists the
cycle would run the implementador on it anyway. The test is the guard on that,
so it is meant to go red in the task that flips the switch. Reading it as a
regression and loosening the schema to get green is the trap.

The size half: `test_schema_stays_small_enough_to_send_on_every_call` caps
every role's compact-JSON schema at 4096 bytes. Measured on T-025's tree by a
throwaway test:

| Schema | Bytes |
|---|---|
| arquitecto today | 2866 |
| arquitecto with `_SPLIT_PROPERTY` merged and required | 3682 |
| the widest role today | 3713 |

So step 4 fits with 414 bytes left, and the arquitecto stays narrower than the
role that is already widest.

## Rule

In the task that adds `split` to `_ROLE_EXTRAS["arquitecto"]`, narrow
`test_no_role_is_offered_a_split_yet` to the other roles (or delete it) in the
same commit, write section 7's two `schema_for("arquitecto")` tests, and drop
`test_the_split_property_is_closed_like_the_other_object_lists` once
`test_schema_is_closed_at_every_level` can see the property. Re-measure rather
than quote 3682 if any description in `_PROPERTIES` or `_SPLIT_PROPERTY`
changed first.

## Evidence

`dispatcher/handoff.py`, `_SPLIT_PROPERTY` and `_ROLE_EXTRAS`;
`tests/dispatcher/test_handoff.py`, the three tests named above;
`docs/implementations/T-025.md` *One number for step 4*. The auditor's
throwaway test asserted `(base, merged, widest)` and pytest printed
`(2866, 3682, 3713)`.
