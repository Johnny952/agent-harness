# Jinja autoescapes, so a literal assertion against rendered HTML fails on an apostrophe — and the negative form passes vacuously

**When it applies:** you are asserting that a sentence fixed verbatim by a spec
appears in (or is absent from) HTML rendered by `observability/board/` or
`observability/collector/`, and the sentence contains an apostrophe, a quote or
an angle bracket.

**Status:** unconfirmed — reported once, by T-010's implementador, while pinning
the board's empty states.

## Symptom

```
assert "no events yet; an agent's hook posts the first one" in html
E  assert ... in '...<p>no events yet; an agent&#39;s hook posts the first one</p>...'
```

The same shape hit `no task 'T-1' in /data/.hive/tasks` (the api's own error
sentence, rendered into a board region's Error state) and `not this task's
events` (a heading the spec fixes verbatim).

## Why

Flask turns autoescaping on for `.html` templates, so `'` reaches the page as
`&#39;`. A test that compares the Python constant against the page is therefore
asserting the page was *not* escaped, which is the opposite of what it should
pin. The negative form is worse than a failure: `assert CONSTANT not in html`
passes for any constant containing an apostrophe no matter what the page says,
so a test written that way never fails and never means anything.

## What to do

Escape the expectation, not the page. A one-line helper —
`_says(html, text) -> str(markupsafe.escape(text)) in html`, which is
`tests/observability/test_board.py:_says` — keeps the constant readable in the
test and keeps the autoescaping itself under test, because a template that
marked the string safe would now fail. Never reach for `| safe` to make the
assertion pass: these strings carry text the api sent, and the api's text comes
off task files an operator wrote.

## Evidence

T-010, `python3 -m pytest tests/observability/test_board.py -q`; the constants
are `EMPTY_ACCOUNTS`, `EMPTY_TASKS`, `EMPTY_EVENTS`, `EMPTY_DEBT` and
`TASK_EVENTS_NOTE` in `observability/board/app.py`. Carried from
`/data/.hive/learnings/inbox/T-010-autoescaped-sentences-break-literal-assertions.md`.
