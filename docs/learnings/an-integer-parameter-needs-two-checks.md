# An integer bound into SQL needs two checks: `int()` parsed it, and SQLite can hold it

**When it applies:** you take an integer off a query string, a CLI flag or a
cursor and bind it into SQL through `observability/collector/db.py` (`limit`,
`since`, any id), in a reader whose contract is "never 500s".

**Status:** unconfirmed — reported once, by T-009's revisor; pinned by a test
since.

## Symptom

```
OverflowError: Python int too large to convert to SQLite INTEGER
```

Raised at `conn.execute(...)` in `db.list_events`, surfacing as a 500 from
`/api/events?since=9223372036854775808`. `9223372036854775807` is fine; `…808`
is not.

## Why

SQLite integers are signed 64-bit; a Python int is unbounded. `OverflowError`
is neither `sqlite3.Error` nor `OSError` nor `ValueError`, so the
"empty plus a warning" `except` around a database read never sees it, and a
validator built on `int(raw)` succeeding lets it straight through.

## What to do

Validate the range as well as the parse and answer the same 400 as a
non-numeric value: `observability/api/app.py:_int_parameter`, bounded by
`_SQLITE_INT_MAX` / `_SQLITE_INT_MIN`. Widening the `except` instead answers a
range error with a warning about the database, which is a lie about which side
was wrong. Pin *both* ends and the boundary value that must still pass, or a
range check passes its test by rejecting everything large. This is not a cap on
how much a caller may ask for — that is policy, and open debt
[T-009-D2](../debt/T-009-D2.md).

## Evidence

`tests/observability/test_api.py::test_an_integer_sqlite_cannot_bind_answers_400_and_not_a_500`,
over `limit` and `since` at both ends; `9223372036854775807` answers 200. Found
by throwaway tests in `/data/projects/ia-harness/worktrees/T-009/revisor`; inbox
entry `T-009-a-sqlite-bound-int-overflows-before-any-error-you-catch.md`;
`docs/decisions.md` ADR 5, the 400 list.
