# A dependency on a split task never clears

**When it applies:** you are making anything run a task whose `depends_on`
names a task that was split into an epic.

**Status:** unconfirmed — reported by T-188's arquitecto from reading the
code; checked by its auditor against `dispatcher/tasks.py`.

## Symptom

```
task T-X cannot start: T-P is not merged
```

(`open_cycle` in `dispatcher/dispatcher.py`, for a task depending on an epic
parent T-P.)

## Why

`dispatcher/tasks.py` `unmet_dependencies` counts a dependency met only when
its file has a `merged` block. A split parent becomes `status: epic`;
`close_epic` later sets it `done` or `dropped`, but no path ever writes
`merged` on it. So a dependency on a split task never clears, whether every
sub-task merged or not.

## Rule

Re-point the dependent's `depends_on` to the epic's sub-task ids (or change
the gate in `tasks.unmet_dependencies`) — never wait on the parent's merge.
The queue does the re-pointing since T-188 (`docs/decisions.md` ADR 495); a
task outside a queue is still stuck (`docs/debt/T-188-D1.md`).

## Evidence

T-188, 2026-10-26: read `dispatcher/tasks.py` `unmet_dependencies`,
`close_epic`, `file_split` and `dispatcher/dispatcher.py` `open_cycle`.
