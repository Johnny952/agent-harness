# A grep invariant forbids its own word in the comment explaining why the code obeys it

**When it applies:** a doc, ADR or task hands you an invariant shaped "a grep
for `<word>` over `<dir>` must come back empty", and you are about to write code
— or a comment — in that directory.

**Status:** unconfirmed — reported once, by T-010's implementador, against
`docs/decisions.md` ADR 8.

## Symptom

The invariant's own test failed on the code that obeys it:

```
assert "ttl" not in path.read_text().lower(), path
E  AssertionError: observability/board/app.py
E   (the line: "never a judgement against a TTL (docs/decisions.md ADR 8)")
```

`observability/board/templates/index.html` failed the same way, on a comment
explaining that `lock_expired` is derived from a TTL the api holds.

## Why

The invariant is a text grep, so it cannot distinguish a TTL *comparison* from
the word TTL in a docstring explaining why there is none. This repo's comment
density makes the collision near-certain rather than unlucky: every non-obvious
line carries its reasoning beside it, and the reasoning names the thing being
avoided.

## What to do

Say the concept without the grepped token — "the expiry window the api holds",
not "the TTL" — and cite the ADR and the test by name so a reader can still find
the argument. Keep the grep mechanical rather than teaching it to skip comments
or carry an exception list: a reviewer can run a mechanical grep by hand, and an
exception list is the first thing to rot. The test is
`tests/observability/test_board.py:test_the_board_never_compares_a_timestamp_against_an_expiry_window`.

## Evidence

T-010, `python3 -m pytest tests/observability/test_board.py -q`, against
`docs/decisions.md` ADR 8, which specifies the check as
`grep -rni "ttl" observability/board/` returning nothing. Carried from
`/data/.hive/learnings/inbox/T-010-a-grep-invariant-forbids-the-word-in-its-own-comment.md`.
