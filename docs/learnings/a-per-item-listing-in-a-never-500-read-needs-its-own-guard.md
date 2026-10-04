# A never-500 route that lists one directory *per item* guards the listing too, not only the rows

**When it applies:** you are adding a view to `observability/api/app.py` that
calls a `dispatcher/` reader which does an `os.listdir` once **per item** —
once per task, per account, per project — rather than once for the whole
collection. `list_handoff_roles` is the first; any later per-item enumerator is
the same shape.

**Status:** unconfirmed — found by T-013's implementador while building
`/api/phases`, after the route was already written to the module's own rule. It
was a real 500 before the guard and a 200 with one warning after.

## Why the module's own rule is not enough here

[a-never-500-read-wraps-the-use-not-the-parse](a-never-500-read-wraps-the-use-not-the-parse.md)
says to wrap the *use* of what you parsed, and every row of a per-row `try` is
covered by it. A per-item **listing** sits one level up, outside that `try`, in
the loop header — so it is exactly the line the rule does not reach.

And it raises. `dispatcher/context_transfer.py:list_handoff_roles` guards with
`os.path.isdir` and then calls `os.listdir`, which answers `PermissionError`
for a directory that exists and cannot be read. `.hive/` is written by the
dispatcher as root and read by the api over a `:ro` mount, which is the
mixed-ownership case `_write_atomic`'s `chmod` already exists for — so this is a
live shape rather than a hypothetical.

The blast radius is what makes it worth its own entry: the listing is inside the
loop over tasks, so **one** unreadable directory costs every *other* task its
rows. A per-row failure costs one row. That inverts this service's one rule —
one unreadable file costs a warning naming it and the rest of the list still
comes back.

## What to do

Wrap the per-item call in its own `try`, warn with the *directory's* path rather
than a file's, and `continue` to the next item:

```python
try:
    roles = context_transfer.list_handoff_roles(cfg.hive_tasks_dir, tid)
except OSError as exc:
    warnings.append(f"{context_transfer.scratch_dir(cfg.hive_tasks_dir, tid)}: "
                    f"unreadable handoff directory: {exc}")
    continue
```

Do not widen the `dispatcher/` reader instead. Its `os.path.isdir` guard is
load-bearing and means something different — *missing is empty, unreadable
raises* — which is the distinction
[a-never-500-read-wraps-the-use-not-the-parse](a-never-500-read-wraps-the-use-not-the-parse.md)
already draws for `_scan`, and the dispatcher's own callers need the raise.

`/api/tasks` has the same exposure on the **one** top-level listing it does, and
was deliberately left as it was: one directory, and if it will not list there
are no tasks to serve anyway. Per-item is the case that needs the guard.

## Evidence

T-013: `observability/api/app.py`, the `phases` view's loop over `task_ids`;
`dispatcher/context_transfer.py:list_handoff_roles`;
`tests/observability/test_api.py:test_a_handoffs_directory_that_will_not_list_is_a_warning_and_not_a_500`.
`docs/implementations/T-013.md` *What was built — the route* records it as
the fifth edge, the one `docs/decisions.md` ADR 27 does not contain because it
was found while building rather than while deciding.
