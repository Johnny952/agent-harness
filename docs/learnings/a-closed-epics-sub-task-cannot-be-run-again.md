# A closed epic's sub-task cannot be run again

**When it applies:** you want to run, re-run or rework a sub-task (a task
file with `epic:`) after its epic has closed, or you are changing
`tasks.epic_problems`, `tasks.close_epic` or the start check in
`dispatcher/dispatcher.py` that calls them.

**Status:** confirmed — the operator ran it on the running stack on
2026-10-14, with T-045, the first real epic.

## Symptom

`run-task --task-id T-045-S2` after both children merged exits 1 and prints:

```
task T-045-S2 cannot start: its epic T-045 has status done, not epic
```

No phase runs and no account is spent.

## Why

The last child's merge runs `tasks.close_epic` (both merge paths;
`a-merge-outcome-that-is-not-refused-is-not-always-merged`), which sets the
parent to `status: done` once every id in its `split_into` has a `merged`
block. Before any phase, the cycle's start check
(`dispatcher/dispatcher.py`, `unmet_dependencies` + `epic_problems`) refuses
a task whose epic is not `status: epic`. A closed parent is `done`, so every
one of its children is refused from then on. That is the rule
`docs/plans/task-split.md` section 3 and ADR 60 describe, built by T-030.

## What happened, end to end

T-045 was dispatched with a card that asked for a split into two sub-tasks.
The arquitecto returned the split (exit 3, "task T-045 is now an epic:
T-045-S1, T-045-S2"). Each child ran from its stored description, passed
its gates and was merged with `merge-task`. The second merge printed
"epic T-045: every sub-task is merged, so it is done". Re-running T-045-S2
then gave the refusal above.

## Rule

Rework of a merged sub-task is a new task, not a re-run of the child. Do
not reopen the parent by hand-editing its `status` back to `epic` to get
past the refusal.

## Evidence

`dispatcher/tasks.py` `epic_problems` (the `has status … not epic` line) and
`close_epic`; T-045, T-045-S1 and T-045-S2's task files under the hive's
tasks directory (all `status: done`); `docs/implementations/T-045-S1.md`,
`docs/implementations/T-045-S2.md`.
