# A cycle test stubs the base merge helpers

**When it applies:** you write a test that runs the real `run_task_cycle` or
`command_run._resume` with its own fakes, or one that counts a cycle's
docker, `read_owner` or `restore_owner` calls.

**Status:** unconfirmed — proposed by T-155's implementador, one source.

## What happens

Since T-155 (ADR 339) every cycle calls `dispatcher._abort_leftover_merge` at
open and `merge_base_at_write_window` before the first implementador, and a
verdict's resume of an implementador calls `command_run._merge_pending` and
`command_run.merge_base_at_write_window`. Each reaches for the container, so
an unstubbed test either calls docker or sees extra owner reads and restores
in its counts.

## What to do

Monkeypatch the four, as `fake_git` in `tests/dispatcher/test_dispatcher.py`
does (and the `cycle` fixtures in `test_parallel_queue.py` and
`test_base_merge_paths.py` for the first two), or use one of those fixtures.

## Evidence

T-155's implementador had to add these stubs to `fake_git` before the
existing `test_dispatcher.py` call-count tests passed again.
