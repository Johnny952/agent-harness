# `place` is derived from status, not stored

**When it applies:** you need a task's `place` (`board` or `backlog`) and are
about to read it from the task file's frontmatter.

**Status:** unconfirmed. Proposed by the T-178-S2 arquitecto and
implementador, checked by its auditor against the code.

## What is true

`TaskFile` has no `place` field and no task file writes one. `place` is
`board_service/app.py` `place_of(status)`: `backlog` when the status is in
`dispatcher/tasks.py` `BACKLOG_STATUSES` (proposed, deferred, dropped), else
`board` (ADR 125 part 5). `observability/api/app.py` keeps its own copy of the
list as `_BACKLOG_STATES` for the roadmap's epic rows.

## What to do

Derive it from `status` with `place_of`, or the same tuple; never look for a
`place` key. If the backlog statuses change, change both copies.

## Evidence

T-178-S2, `grep -n "BACKLOG_STATES" board_service/app.py
observability/api/app.py`.
