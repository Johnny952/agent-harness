# `task-split.md` still sketches sub-task files with a `description:` key; built as written they are unmigrated files

**When it applies:** you are building `file_split` or `_file_split` (step 2
onward of `docs/plans/task-split.md` section 8), or any other code that
creates a task file together with its description.

**Status:** unconfirmed — read off the plan by T-026's implementador and
revisor; the code it warns about is not written yet.

## What is true

`docs/plans/task-split.md` decision 2, *Sub-task files*, shows each filed
sub-task as a task file whose frontmatter ends in a `description:` block,
written by a new `context_transfer.file_split` through `write_task_file`.
The plan predates T-026 and was left as written, being a plan for code that
does not exist.

Since T-026 that sketch cannot be built literally, and since T-027 not for a
third reason either:

- `write_task_file` no longer writes a `description` key, and `TaskFile` has
  no such field, so the text would be dropped;
- a file that did carry the key is what `dispatcher/tasks.py` calls
  unmigrated: `open_cycle` and three CLI verbs refuse the whole hive until
  `dispatch migrate` has run;
- since T-027 no module under `dispatcher/` but `tasks.py` may call
  `write_task_file` (a test in `tests/dispatcher/test_tasks.py` walks them),
  so a `context_transfer.file_split` fails the suite: the filer belongs in
  `dispatcher/tasks.py` and writes through its `_write_task_file`.

## What to do

Write the task file without the key, then store the text with
`tasks.write_description`. The text must have `## Goal` and `## Done when`
or the write raises `DescriptionError`: the sketch's "the entry's
description, verbatim" plus an epic line has neither, so the split's handoff
shape or `file_split` has to supply the sections. Decide which in that task
and say so in the plan's Status paragraph; do not migrate-wrap model text
under `## Goal` silently.

Order matters for a crash: `migrate` writes `description.md` first and the
task file second so a re-run repairs it, and a filer should pick its order as
deliberately.

## Evidence

T-026: `docs/plans/task-split.md` decision 2 *Sub-task files*,
`dispatcher/tasks.py` `write_description` and `migrate`,
`docs/implementations/T-026.md` *Outside the card's file list*, closing
paragraph. T-027: `docs/implementations/T-027.md` *Decisions* items 1 and 2.
