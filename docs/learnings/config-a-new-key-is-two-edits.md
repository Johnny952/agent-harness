# Adding a key to `Config` is two edits, and the second one is in a test you never touched

**When it applies:** you added a field to `dispatcher/config.py`'s `Config`
dataclass and the suite fails in a file this change has nothing to do with.

**Status:** unconfirmed — reported once, by T-008's arquitecto, and not hit
since.

## Symptom

```
E       TypeError: Config.__init__() missing 1 required positional argument: 'local_board'
tests/dispatcher/test_dispatcher.py:93: TypeError
```

Every dispatcher test fails at once, from a file the change never mentions.

## Why

`Config` has no defaulted fields, and `tests/dispatcher/test_dispatcher.py`'s
`_make_config` builds one by spelling out every key. So a new required field
breaks all of them, and because `Config`'s later fields have no defaults
either, a new field cannot be given `= None` unless it goes last in the
dataclass.

## What to do

Adding a key to `Config` is two edits: the dataclass in
`dispatcher/config.py`, and `_make_config`'s `defaults` dict in
`tests/dispatcher/test_dispatcher.py`. `load_config` is the only other
construction site. If the field is optional, put it last and default it to
`None` — that is what `Config.local_board` does.

Only a **required** field costs the second edit. A field that is defaulted
*and* last is one edit: T-020 added `pace_primary_week: bool = True` at the end
of the dataclass and touched no test helper, and the whole suite stayed green —
`_make_config` spells out every key it knows and simply does not mention this
one, so the default applies. A test that wants the other value passes it as an
override. Both halves have to hold: a defaulted field that is *not* last is a
`TypeError` at import on the fields after it.

## Evidence

`python3 -m pytest tests/dispatcher/test_dispatcher.py -q -x`, with
`local_board` added to `Config` and `_make_config` left alone. T-008,
arquitecto phase; the inbox entry is
`T-008-config-field-breaks-dispatcher-test-helper.md`.
