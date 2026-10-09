# A `dispatcher/` reader that silently drops what it cannot parse needs an `unreadable()` sibling before a route can report the damage

**When it applies:** you are serving a `dispatcher/` reader over a route in
`observability/api/app.py` whose contract is "report the damage, never crash",
and that reader returns a list it has already filtered — a file it could not
parse is simply absent from what it hands back.

**Status:** confirmed — the shape exists twice:
`dispatcher/vibe_kanban_client.py:LocalBoardClient.unreadable` (built for
[`T-008-D2`](../debt/T-008-D2.md), served by `_read_cards`) and
`dispatcher/learnings.py:unreadable` (built by T-016, served by
`learnings_index`).

## Symptom

No error, and the response looks healthy. A route promises
[`docs/decisions.md`](../decisions.md) **ADR 5**'s contract — one unreadable
file costs a warning naming it and the rest of the list still comes back — but
the view has nothing to warn *about*: the reader returned the files it could
read, and the ones it could not are indistinguishable from files that do not
exist.

## Why

The two contracts are different and the difference is easy to miss. A
`dispatcher/` reader's job is to hand its four in-process callers a usable list,
so it drops a bad file and moves on, and
[`a-never-500-read-wraps-the-use-not-the-parse`](a-never-500-read-wraps-the-use-not-the-parse.md)
says not to widen the reader for the route's sake — `read_dir` keeps raising
because its own callers need the raise, and the per-entry parse keeps returning
`None` because its callers need the drop.

So the information the route owes an operator is not in the reader's return
value at all. It has to be recovered by a second pass that answers the opposite
question: of the files in this directory, which ones did the reader *not*
account for?

## What to do

Write a sibling named `unreadable(path) -> list[str]` next to the reader, in the
same module, that re-lists the directory and names the files the reader skipped.
Keep the reader untouched. Then let the view call both and put the sibling's
answer in `warnings`, per
[`a-per-item-listing-in-a-never-500-read-needs-its-own-guard`](a-per-item-listing-in-a-never-500-read-needs-its-own-guard.md)
for the listing call itself.

Two things come with it. The sibling re-reads the directory, so a file deleted
between the two passes is absent from both and nothing guards that race — say so
in the ADR rather than pretending the pair is atomic. And a reader that can pick
up a half-written temp file will report it as an entry *or* as a warning
depending on which pass wins; that is the reader's pre-existing behaviour, not
the sibling's.

## Evidence

`dispatcher/learnings.py:unreadable` and `read_dir` above it, served by
`observability/api/app.py:learnings_index`, with the three warnings the route
answers pinned in `tests/observability/test_api.py`; the reasoning is
`docs/decisions.md` **ADR 41** and
[`docs/implementations/T-016.md`](../implementations/T-016.md). The earlier
instance is `LocalBoardClient.unreadable`, which is how
[`T-008-D2`](../debt/T-008-D2.md) was resolved — the same shape, arrived at
independently, which is what makes this a convention rather than one route's
trick.
