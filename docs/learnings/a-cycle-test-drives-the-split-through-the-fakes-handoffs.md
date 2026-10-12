# A cycle test drives the split branch through `_recording_dispatch_phase`'s `handoffs=`

**When it applies:** you are writing a test in
`tests/dispatcher/test_dispatcher.py` for a cycle whose arquitecto splits,
for `_file_split`, or for a parent task in a given status.

**Status:** unconfirmed — proposed by T-031's implementador; the helper
names were checked against the file by the auditor.

## What is true

`_recording_dispatch_phase(calls, failing=(), handoffs=None)` takes a dict of
role to payload, and `handoffs={"arquitecto": payload}` is all the split
branch of `run_task_cycle` needs: no model, no container. Under the comment
`# --- the split, switched on` are four helpers built on it:

- `_split_payload(subtasks=_SPLIT, status="complete")`: an arquitecto
  handoff carrying a split.
- `_splitting_cycle(cfg, monkeypatch, payload, …)`: runs the cycle with that
  payload and returns the outcome and the roles dispatched.
- `_split_ctx(cfg, task_id)`: a `CycleContext` for calling `_file_split`
  directly.
- `_with_status(cfg, status, task_id, **fields)`: writes the parent's task
  file in a status, with an owner or heartbeat when the test needs a lock.

## What to do

Use them instead of building a payload by hand. The fake takes no lock, so
the caveat of
[a-faked-dispatch-phase-never-creates-the-task-file](a-faked-dispatch-phase-never-creates-the-task-file.md)
holds: the parent's file exists because the arquitecto's `tasks.handoff`
landed, or because `_with_status` wrote it. A sub-task description in a test
needs `## Goal` and `## Done when`, or the filing is refused
([file-split-must-write-descriptions-through-the-task-module](file-split-must-write-descriptions-through-the-task-module.md)).

## Evidence

`tests/dispatcher/test_dispatcher.py`, from
`test_a_cycle_whose_arquitecto_split_files_the_epic_and_stops` to
`test_file_saved_split_refuses_what_it_cannot_file`.
