# In `dispatcher/`, "returns `None`, never raises" means catching `(OSError, ValueError)`

**When it applies:** you are writing a file-backed read in `dispatcher/` whose
contract is "returns `None`, never raises", keyed on an id that came off a task
file's frontmatter — `get_issue`, or anything a later phase adds beside it.

**Status:** unconfirmed — reported once, by T-008's implementador.

## Symptom

```
ValueError: embedded null byte
/usr/lib/python3.11/pathlib.py:1045: ValueError
```

Raised out of `LocalBoardClient.get_issue`, which is specified never to raise,
straight past an `except (OSError, json.JSONDecodeError)` that looked
exhaustive.

## Why

Two stdlib facts point the same way. `Path.read_text()` on a path containing a
NUL raises plain `ValueError`, not `OSError` — the byte is rejected before any
syscall. And `json.JSONDecodeError` is a *subclass* of `ValueError`, so naming
it buys nothing `ValueError` does not already cover, while hiding that the path
itself can be rejected.

The id is not the client's own: an `issue_id` reaching the board comes from
`kanban_issue_id` in `.hive/tasks/<task-id>.md`, a YAML scalar an earlier phase
wrote, and a double-quoted YAML scalar can carry `\0`. Treat it as untrusted
text, not as something this process minted.

## What to do

Catch `(OSError, ValueError)` — never `json.JSONDecodeError`. When one case has
to stay silent and the rest warn, catch `FileNotFoundError` first and warn in
the wider `except`: that is the order
`LocalBoardClient._read_path` uses, and it is what makes
`docs/decisions.md` ADR 1's `get_issue` bullet true of every case it names.

## Evidence

`python3 -m pytest tests/dispatcher/test_vibe_kanban_client.py -k no_path_can_hold`,
against `dispatcher/vibe_kanban_client.py:LocalBoardClient._read_path` before
the catch was widened. The regression test is
`test_local_board_get_issue_does_not_raise_on_an_id_no_path_can_hold`; the
inbox entry is `T-008-a-lookup-that-never-raises-must-catch-valueerror.md`.
