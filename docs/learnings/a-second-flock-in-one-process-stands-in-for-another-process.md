# A second `flock` in one process stands in for another process

**When it applies:** you test a `flock`-based lock (`dispatcher/project_lock.py`
`ProjectLock`, the task lock in `dispatcher/tasks.py`) and are about to start a
subprocess only to have "someone else" hold it.

**Status:** unconfirmed. T-116's architect and implementer both reported it,
but that is one source.

## Why

`flock` locks belong to the open file description, not the process. Two
separate `os.open` calls on one lock file in the same process get two
descriptions, and an exclusive non-blocking `flock` on the second fails while
the first holds it, exactly as it would across processes. (A `dup` of one
descriptor shares the description and would not exclude.)

## Rule

Take the lock twice in-process to test "held by another"; spawn a real process
only for what needs one — the kernel giving the lock back when its holder dies.

## Evidence

`tests/dispatcher/test_project_lock.py`
`test_a_second_take_is_busy_and_names_the_holder` (in-process) and
`test_a_process_that_dies_gives_the_lock_back` (a killed subprocess), both
green in `python3 -m pytest -q` at the end of T-116.
