# An auditor's edit outside `docs/` never lands, and the handoff may say it "kept" it

**When it applies:** an auditor's handoff says it fixed or kept something
outside `docs/` (a test comment, a source file), or a log shows "left
uncommitted, outside this phase's scope"; or you are about to merge a task and
want to know what its scoped phases left behind.

**Status:** unconfirmed — T-047 hit it once.

## What happens

The auditor's commit is scoped to `docs/` (`dispatcher/project_docs.py`
`_COMMIT_SCOPES`, `docs/decisions.md` ADR 70): the scope exists so the auditor
does not change the work after the review. `commit_worktree` in
`dispatcher/docker_exec.py` therefore leaves every other edit in the worktree,
and the worktree is removed at merge. The edit is gone from the merged branch
whatever the handoff says. A path in the excluded list is left the same way.

Before T-048 such a phase also crashed the cycle: with nothing in scope to
commit, `git commit` exited 1 with "no changes added to commit" and
`commit_worktree` raised `git commit failed`. `_nothing_to_commit` knows that
text now.

## How to tell

- The log warning "left uncommitted, outside this phase's scope (...)", or
  "NOT committed, and no role may change it" for an excluded path.
- Since T-050, a record per phase under `<hive_tasks_dir>/<task_id>/left-behind/`:
  `<role>.json` names the paths and `<role>.patch` is a `git apply`-able diff of
  exactly them. `run-task`, `run-phase` and `merge-task` print a line for each
  on stderr.

## What to do

Apply the `.patch` by hand on the branch (or drop it) before or after the
merge; nothing applies it for you. An auditor that wants a change to land must
put it in `docs/`, or hand it on as debt.

## Evidence

T-047 (2026-10-14): the auditor's "nine" to "ten" fix in
`tests/dispatcher/test_dispatcher.py` was not on merged `main`; fixed by T-050.
