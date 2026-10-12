# Nothing restores a task status a later writer overwrote: `tasks.handoff` writes over any status, and neither a re-file nor `close_cycle` puts `epic` back

**When it applies:** you are adding a task status, a `CycleOutcome` or a verb
that runs a phase, and are deciding where to refuse a task that is already
`epic` or `done`; or you found a task with `split_into` set whose status is
not `epic` and expect `dispatch file-split` to repair it.

**Status:** unconfirmed — proposed by T-031's arquitecto, implementador and
revisor, and read against the code by the auditor. No run has produced the
overwritten epic.

## What is true

Three facts about `dispatcher/tasks.py` and `dispatcher/dispatcher.py`, each
harmless alone:

- `tasks.handoff` sets `task.status = new_status` and clears the owner and
  the heartbeat, whatever the status was. Every phase ends in it, so a phase
  that runs on an `epic` leaves it `pending`.
- `tasks.file_split` writes the parent only while its `split_into` is not
  this split's already. Filing the same split again writes nothing, so a
  parent whose `epic` was overwritten after the filing stays overwritten.
- `close_cycle` writes nothing to the task file: it drops the review
  worktrees and stamps or orphans learnings. A cycle outcome that is not a
  block keeps whatever status its last writer left. That is why a SPLIT ends
  with the `epic` that `file_split` wrote, and why nothing tidies a status
  on the way out.

Together: once `epic` is on a task file, the only protection is that no
writer reaches it.

## What to do

Refuse in `open_cycle`, not in `run_task_cycle` alone: `run-task` and
`run-phase` both pass through it, and it runs before the supplied
description is written and before any lock. T-031's epic refusal is there for
this reason (`docs/decisions.md` ADR 60). `_file_split` refuses a parent a
live run still holds for the same reason: that run's next handoff would write
`pending` over the `epic`.

A verb that opens no cycle (`file_saved_split`) has to make its own checks.
To split an epic again, or to repair an overwritten one, edit the status by
hand (`docs/implementations/T-031.md`, *What a later task should know*).

## Evidence

`dispatcher/tasks.py`: `handoff`, and the closing `if parent.split_into !=
ids` of `file_split`. `dispatcher/dispatcher.py`: `close_cycle`, the epic
branch of `open_cycle`, `_file_split`'s docstring.
`tests/dispatcher/test_dispatcher.py`:
`test_a_single_phase_on_an_epic_is_refused_and_keeps_it_an_epic`,
`test_file_split_refuses_a_parent_a_live_run_still_holds`.
