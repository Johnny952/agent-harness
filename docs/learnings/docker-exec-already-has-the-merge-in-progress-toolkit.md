# `docker_exec` already has the merge-in-progress toolkit

**When it applies:** you are about to write git code that starts, checks,
finishes or aborts a merge in a task's writers' checkout.

**Status:** unconfirmed — proposed by T-155's arquitecto, used by its
implementador; the auditor checked the names against the code.

## What happens

`dispatcher/docker_exec.py` already holds every piece: `merge_base_into_writer`
starts the base merge in the writers' checkout and leaves a conflict in
progress (ADR 277; it refuses on uncommitted work or a checkout off the tip),
`merge_in_progress` and `unmerged_paths` read the state, `_MARKER_RE` is the
marker test, `conflicted_with_markers` is the commit guard and
`abort_writer_merge` the abort (both T-155, ADR 339). `finish_merge_commit` is
the marker-checked merge commit `resolve-conflict` uses. A round commit through
`commit_worktree` (`git add -A` then `git commit`) with `MERGE_HEAD` set is
already a merge commit with both parents.

## What to do

Call these rather than shelling out to git again; add a function next to them
only for a state they do not read.

## Evidence

T-155 built ADR 339 on `merge_base_into_writer` and `commit_worktree`;
`tests/dispatcher/test_premerge.py` asserts the round commit's parents are
`[tip, base]`.
