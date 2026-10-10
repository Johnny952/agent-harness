# A year-less date parser appends the year to the text; it never replaces it after the fact

**When it applies:** you are turning a date with no year out of CLI free text
into a datetime — a `/usage` reset clause, a log line, anything a provider
prints for a human — and deciding which year it belongs to.

**Status:** unconfirmed — one task. Found by T-020's implementador while
writing `dispatcher/quota.py:parse_reset`; no test ever failed over it,
because the shape that loses data is the one nobody writes a test for.

## Symptom

None, and that is the point: the obvious shape fails one day in 1461 and is
green every other day.

```
>>> datetime.strptime("Feb 29, 4:59pm", "%b %d, %I:%M%p")
ValueError: day is out of range for month
```

`strptime` defaults the year to **1900**, which is not a leap year. So
parse-first-then-`.replace(year=...)` never reaches the replace: the clause is
already gone with a `ValueError` the caller reads as "unparseable", and the
fallback fires on a date that was perfectly well formed.

## Why

The year is not missing from the problem, only from the text. A parse that
defaults it has already committed to a calendar — 1900's — before the code
that knows the right year gets a turn. Putting the year into the string the
parser sees means the only calendar ever consulted is the right one.

## What to do

Append the year to the text and parse it, one candidate year per attempt:

```python
for year in (now.year, now.year + 1):
    for fmt in _RESET_FORMATS:
        try:
            naive = dt.datetime.strptime(f"{when} {year}", f"{fmt} %Y")
        except ValueError:
            continue
```

Two candidates and not three. A **forward-only** validity window — "the reset
is inside the next seven days of `now`" — makes a `now.year - 1` candidate dead
code: a last-year date is always behind `now`, so it can never satisfy the
window and the loop can never choose it. Say so in a comment, because the
missing third candidate reads like an oversight.

Take `now` as a parameter rather than reading a clock, so no test depends on
the day it runs on — see
[one-clock-seam-per-module-and-a-now-parameter-is-the-other-half](one-clock-seam-per-module-and-a-now-parameter-is-the-other-half.md)
for which of the two a module should be.

## Evidence

`dispatcher/quota.py` `_RESET_FORMATS` and `parse_reset`, whose comments carry
both halves, and `tests/dispatcher/test_quota.py`'s `Jan 2` clause probed on
`Dec 30`, which is the case the `now.year + 1` candidate exists for.
`docs/decisions.md` **ADR 48** *The fallback* is the decision the parser
implements.
