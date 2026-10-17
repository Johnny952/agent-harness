# A new keyword on `create_worktree` is passed only when it is set

**When it applies:** you are adding a parameter to
`docker_exec.create_worktree` (or another `docker_exec` call that
`dispatch_phase` makes) and want `dispatch_phase` to pass it on every call.

**Status:** unconfirmed — reported by T-060's implementador and revisor; the
fake signatures were checked by T-060's auditor with a grep.

## Symptom

Passing the new keyword unconditionally makes dozens of dispatcher tests fail
with a `TypeError` for an unexpected keyword argument, in tests that have
nothing to do with the change.

## Why

The tests fake `create_worktree` with a five-positional-argument lambda,
`lambda container, projects_root, slug, task_id, role: ...`: about twenty in
`tests/dispatcher/test_dispatcher.py`, plus `test_handoff_resume.py` and
`test_account_claim.py`. None takes `**kwargs`.

## What to do

Forward the keyword only when it differs from its default, as T-060 does for
`at_base` in `dispatch_phase`:
`**({"at_base": True} if at_base else {})`. A test of the new path uses its
own fake that takes `**kwargs` and records them.

## Evidence

`grep -rn "lambda container, projects_root, slug, task_id, role" tests`, from
the T-060 worktree.
