# A new `Config` field needs a `CONFIG_KEYS` row, and the table test counts it

**When it applies:** you added, renamed or removed a key `load_config` reads in
`dispatcher/config.py`, or a field on its `Config`.

**Status:** unconfirmed. T-064's arquitecto and implementador both reported it
(one source, one task). The auditor checked it against the test.

## What fails

`tests/dispatcher/test_config.py`
`test_config_keys_are_exactly_the_keys_load_config_reads` asserts three
things a new key breaks:

1. `set(CONFIG_KEYS)` equals `Config`'s field names plus `REMOVED_BOARD_KEYS`.
   A field with no row fails here.
2. The test's own `values` dict, plus the board keys, equals
   `set(CONFIG_KEYS)`. The new key needs a valid value in that dict.
3. The class counts are a literal tuple, 22 editable, 15 read-only and
   2 credential when T-064 merged. The new row moves one of them.

The row needs a class and the one-line reason (`docs/decisions.md` ADR 75
part 5, ADR 106). An `editable` row also needs a `kind` that
`_check_override_value` knows, and the console's writer (plan task 2) offers
it for editing. The class decision is a design call, so name it in an ADR.

## What it does not catch

The test compares against `Config`'s fields, not against the raw keys the
loader reads. A raw key folded into another field, or a field computed from
no key, gets past it. Grep `raw.get("` and `raw["` in `dispatcher/config.py`
too.

This adds a fourth place to
[a-config-key-has-three-homes](a-config-key-has-three-homes.md): that one is
docs no test guards; this one is code the suite does guard.

## Evidence

T-064, `docs/implementations/T-064.md` and `docs/decisions.md` ADR 106. ADR
106 exists because ADR 75 part 5 was written before ADR 77 added
`auto_approve` and `protected_paths`, so the table was missing them.
