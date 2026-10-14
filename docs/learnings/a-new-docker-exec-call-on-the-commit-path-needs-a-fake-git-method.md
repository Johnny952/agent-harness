# A new `docker_exec` call on the commit path needs a `_FakeGit` method

**When it applies:** you are adding a call to a `dispatcher/docker_exec.py`
function inside `dispatch_phase` (around `commit_worktree`, or anywhere a
dispatcher test reaches without monkeypatching it itself).

**Status:** unconfirmed — proposed by T-050's arquitecto and implementador,
one source.

## What happens

Every test in `tests/dispatcher/test_dispatcher.py` runs through the autouse
fixture `fake_git`, which monkeypatches the `docker_exec` functions the commit
path calls (`commit_worktree`, `dirty_paths`, `read_owner`, `restore_owner`,
and since T-050 `left_behind` and `diff_of`) onto a `_FakeGit` instance. A new
call that is not patched there reaches for a real container: `docker exec`
fails or hangs in every dispatch test, far from the line you changed.

## What to do

Add a method to `_FakeGit` (with a class attribute holding its default, empty
being the ordinary case) and a `monkeypatch.setattr` line for it in
`fake_git`. Tests that need a non-default answer set the class attribute with
`monkeypatch.setattr(_FakeGit, ...)`, as the T-050 left-behind tests do.

## Evidence

T-050 added `left_behind` and `diff_of` to the commit path; both were faked in
`_FakeGit` before the dispatcher tests could run.
