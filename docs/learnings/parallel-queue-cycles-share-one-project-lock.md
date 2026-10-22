# Parallel queue cycles share one project lock

**When it applies:** you need two cycles of the same project under
`run-queue --parallel N` not to do something at once, and are about to rely
on ADR 200's project `flock` for it.

**Status:** unconfirmed. Proposed by T-136's arquitecto.

## Why

ADR 200, decision 7: under `--parallel` above 1 the queue takes the project
lock once and its N cycles, threads of one process, share it. None of them
takes it, so it orders nothing between them; ADR 83's surface write windows
are the only ordering inside the queue.

## Rule

Take a lock of your own, per project, on a new descriptor per acquisition
(as `dispatcher/adr_numbers.py` `_locked` and `learnings._carry_lock`
do), so two threads of one process exclude each other.

## Evidence

`docs/decisions.md` ADR 200 decision 7 and ADR 294 *Ruled out*.
