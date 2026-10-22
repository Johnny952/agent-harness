# An inbox writer locks per entry project, not per caller

**When it applies:** you add a writer of inbox entries in
`dispatcher/learnings.py`, or change `mark_orphaned` or `drop_promoted`, and
are about to take `_carry_lock` once for the caller's project.

**Status:** unconfirmed. Proposed by T-138's arquitecto and implementador
(one source).

## Why

`carry` is called with a project and locks that project. `mark_orphaned`
and `drop_promoted` are called with a task id only, and the entries one
task wrote or carries can each name a different `project`. There is no
single project to lock, so ADR 300 groups the entries by `entry.project`,
takes each project's `_carry_lock` in turn, one at a time, and re-reads the
inbox under each lock. An entry whose project is not one path component
(`_lockable`) cannot be locked and no `carry` can reach it, so it is
written unlocked.

## Rule

Lock per entry project, one lock at a time, on a fresh read under the lock;
never one lock for the caller.

## Evidence

`tests/dispatcher/test_learnings.py`
`test_another_projects_lock_does_not_block_the_release` and
`test_mark_orphaned_and_carry_lose_neither_write`, green in
`python3 -m pytest tests/dispatcher/test_learnings.py tests/dispatcher/test_dispatcher.py -q`
(587 passed) at T-138's audit.
