# `task_lock` is the hive write lock, and it takes any single-path-component name

**When it applies:** a task description says "beside `task_store`" or "under
the hive's existing write lock", and you are writing a hive record that is not
a task file (a thread, say).

**Status:** unconfirmed. T-109's arquitecto and implementador both reported it
(one source, one task); the auditor checked it against `dispatcher/tasks.py`.

## What it is

`task_store` is `dispatcher/tasks.py`, imported under that alias wherever a
view or local is named `tasks`
([the-apis-views-shadow-a-module-imported-as-tasks](the-apis-views-shadow-a-module-imported-as-tasks.md)).
Its `task_lock(hive_dir, name)` is the hive's write lock (ADR 127): an
exclusive `flock` on `<hive root>/locks/<name>.lock`, re-entrant per thread.
`task_lock_path` only checks that `name` is one path component
(`context_transfer._is_single_path_component`), so a name no task id can take
works as a lock for another record: `dispatcher/threads.py` locks
`_thread-<id>`.

Nothing lists `locks/` (only `task_lock_path` builds paths in it), so a new
lock name shows up in no task listing. The lock files are never removed.

## Evidence

T-109, `dispatcher/threads.py` `_lock` and `docs/decisions.md` ADR 170 part 1;
`dispatcher/tasks.py` `task_lock_path`, `locks_root`.
