# A writer moved to `dispatcher/tasks.py` cannot be re-exported from `context_transfer`

**When it applies:** you are moving a function from
`dispatcher/context_transfer.py` to `dispatcher/tasks.py` and planning to keep
the old name importable so callers do not change.

**Status:** unconfirmed — reported by T-027's arquitecto and checked by the
auditor against the imports.

## What is true

`dispatcher/tasks.py` imports `context_transfer`. A re-export
(`from dispatcher.tasks import handoff` in `context_transfer`) closes an
import cycle, so the old name cannot be kept as an alias. Every caller has to
change to `tasks.<name>`, and the callers are not only in the phase loop:
`dispatcher/operator.py` (`release_account`) called `release_stale_lock`.

## What to do

Find every caller before moving (`codegraph_callers`, or grep the name over
`dispatcher/`, `observability/` and `tests/`) and change them all in the same
task. Grep `docs/` for `context_transfer.<name>` too.

## Evidence

T-027: the six writers moved (`acquire_lock`, `refresh_heartbeat`,
`release_stale_lock`, `handoff`, `set_kanban_issue_id`, `set_resolved_debt`);
`dispatcher/operator.py` now imports `tasks`.
