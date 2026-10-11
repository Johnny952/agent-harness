# A removed `context_transfer` symbol or frontmatter key is also named in the root `README.md` and in `docs/plans/`

**When it applies:** your task removes or renames a function in
`dispatcher/context_transfer.py` or a key of the task file's frontmatter, and
you have updated `docs/README.md` and are about to call the docs done.

**Status:** unconfirmed — it cost T-026 a revision round (the revisor's F1 to
F3); one task, one occurrence.

## What is true

Three places outside `docs/README.md` state the task file's shape in the
present tense, and none is tested against the code:

- the root `README.md`, bullet *Context handoff*, which lists the frontmatter
  keys;
- `docs/plans/task-split.md`, bullet *Task files*, which lists them again;
- `docs/plans/balancer.md`, paragraph *What the loop still cannot do: open a
  task*, which names the helper that stores a description.

T-026's first round fixed `docs/README.md` and the root README's `run-task`
example and left all three saying `description` was a frontmatter key and
`set_description` existed.

## What to do

Grep the removed name over `README.md` and `docs/plans/` as well as
`docs/README.md`, which is the general rule of
[correcting-a-false-mechanism-greps-the-identifier](correcting-a-false-mechanism-greps-the-identifier.md)
applied to this module. A present-tense sentence about code that exists is
yours to fix under `docs/charter.md` C-6 even when the card did not list the
file; record it in the implementation note. A plan's *sketch of code not yet
built* is not: leave it and say so, as T-026 did for `task-split.md`
decision 2
([file-split-must-write-descriptions-through-the-task-module](file-split-must-write-descriptions-through-the-task-module.md)).

## Evidence

T-026: `docs/implementations/T-026.md` *Outside the card's file list*, and the
revisor's round 1 findings F1 to F3 in that task's handoff.
