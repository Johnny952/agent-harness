# The size note fires on most dispatcher modules

**When it applies:** you are reading a `size` NOTE from the gates on a diff
under `dispatcher/`, or weighing `SIZE_NOTE_LINES` in `dispatcher/gates.py`
on the strength of `docs/plans/backlog.md` item 1 saying that at 800 lines
only `dispatcher/dispatcher.py` would be noted.

**Status:** unconfirmed — proposed by T-141's arquitecto, recounted by its
auditor; no later task has read the gate's output yet.

## Symptom

None: a stale premise, not an error.

## Why

The backlog item was written when `dispatcher.py` was the one outlier. By
T-141, `wc -l dispatcher/*.py` put 12 of the 35 modules directly under
`dispatcher/` past 800 lines — `dispatcher.py` (5041), `tasks.py` (3186),
`docker_exec.py` (2189), `cli.py`, `worker.py`, `config.py`, `actions.py`,
`learnings.py`, `gates.py` itself, `handoff.py`, `context_transfer.py` and
`queue.py`. (The arquitecto's handoff said 11 of 20; the auditor's count is
the one above.)

## Rule

Expect the note on most dispatcher edits and do not treat it as a finding to
answer; the threshold is provisional, and `docs/decisions.md` ADR 309 says to
measure how often it fires before moving it.

## Evidence

`wc -l dispatcher/*.py` in the T-141 worktree at the auditor phase.
