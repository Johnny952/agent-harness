# Flask raises routing 404 and method 405 itself, in HTML, outside any envelope

**When it applies:** you are writing or consuming a client for
`observability/api/` — Phase 2 of `docs/plans/board.md` — or documenting "every
non-200 is JSON" for any Flask app in this repo.

**Status:** unconfirmed — reported once, by T-009's revisor; pinned by a test
since.

## Symptom

`POST /api/tasks` and `GET /api/nope`, against a module whose docstring promised
a JSON error body for every non-200:

```
405 METHOD NOT ALLOWED  Content-Type: text/html; charset=utf-8
<!doctype html>
<html lang=en>
<title>405 Method Not Allowed</title>
```

A client calling `resp.json()` on any non-200 gets a parse error, not
`{"error": …}`.

## Why

Flask raises routing 404 and method 405 before any view function runs, so
nothing in `app.py` constructs them: `_error(...)` is never reached and Werkzeug's
default HTML page is returned. Every non-200 the module *returns* — the 400s, the
404 for a task id with no file, the 404 for an unknown project slug — is JSON.
The two shapes of mistake a client makes most often, a typo'd path and a wrong
verb, are exactly the two that are not.

## What to do

Guard on `Content-Type` before `.json()` on a non-200 from `/api/*`. The JSON
error body is promised only for the non-200s the module returns, which is what
`docs/decisions.md` ADR 5's first bullet says. The behaviour is pinned by
`tests/observability/test_api.py::test_flasks_own_404_and_405_are_html_not_this_envelope`,
so a later task that adds `errorhandler`s fails there by design and updates ADR 5
with it.

## Evidence

The test above, measured rather than assumed: `get_json(silent=True)` is `None`
on both. Found as T-009's F5; inbox entry
`T-009-flask-answers-its-own-404-and-405-in-html.md`.
