# `test_premerge.py` has a real-git repo fixture

**When it applies:** you want a test that runs real git merges (a clean
merge, a conflict kept in progress, a merge commit's parents) through
`dispatcher/docker_exec.py` without docker.

**Status:** unconfirmed — proposed by T-155's arquitecto and implementador,
one source.

## What happens

`tests/dispatcher/test_premerge.py` has the `repo` fixture, which
monkeypatches `docker_exec.run_docker_exec` to `_local_exec` (runs the argv
locally in the workdir) and lays out a project clone with a task's writers'
checkout, and `_diverge(repo, conflict=...)`, which moves the base and the
task branch apart and returns `(tip, base)`. Every `docker_exec` git helper
then runs on a real repository in `tmp_path`, including a merge left in
progress in the writers' checkout.

## What to do

Add the test to `test_premerge.py` and use `repo` and `_diverge`, as the
T-155 tests under "the base merge before an implementador (ADR 339)" do,
rather than faking git output.

## Evidence

T-155's four real-git tests there (conflict kept and resolved, marker not
committed, dirty writer falls back, leftover merge aborted) pass with no
container.
