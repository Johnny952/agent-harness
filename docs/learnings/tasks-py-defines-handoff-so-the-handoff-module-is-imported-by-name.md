# `tasks.py` defines `handoff`, so the `handoff` module is imported there by name

**When it applies:** you are in `dispatcher/tasks.py` and need something from
`dispatcher/handoff.py` (`SubTask`, `split_problems`, `split_of`), or you are
tidying that file's imports.

**Status:** unconfirmed — proposed by T-029's arquitecto and implementador,
and checked by the auditor against the import and the function.

## What is true

`dispatcher/tasks.py` has a function named `handoff`, the task-file writer
`dispatcher/dispatcher.py` calls at the end of a phase. `from dispatcher
import handoff` in that module would bind the same name twice, and the `def`
below the import wins: every `handoff.split_problems(...)` then fails with an
`AttributeError` on a function, at call time and not at import.

The import itself is safe. `dispatcher/handoff.py` does not import `tasks`,
so `from dispatcher.handoff import SubTask, split_problems` does not cycle;
the suite collects with it.

## What to do

Import the names you need, as `tasks.py` does, and keep the comment above
that line. Do not rename the writer to free the module name: its callers and
tests use `tasks.handoff`. The same shape bites from the other side in
`observability/api/app.py`
(`docs/learnings/the-apis-views-shadow-a-module-imported-as-tasks.md`).

## Evidence

T-029: `dispatcher/tasks.py`, the import of `SubTask` and `split_problems`
and the function `handoff`; `python3 -m pytest -q` printing
`1502 passed, 10 skipped`.
