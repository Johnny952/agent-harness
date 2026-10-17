# A parametrized compose invariant that another test calls takes its argument with no default

**When it applies:** you are parametrizing a test in
`tests/integration/test_compose_invariants.py` that
`tests/scripts/test_configure.py` also calls as a plain function, and want to
give the new parameter a default so those calls keep working.

**Status:** unconfirmed — reported by T-059's implementador; the error below
was reproduced by T-059's auditor with a throwaway test outside the tree.

## Symptom

```
In .hive/tasks/T-059/test_default_param.py::test_defaulted: function already takes an argument 'compose_file' with a default value
```

A collection error: the whole run stops with `Interrupted: 1 error during
collection`.

## Why

`pytest` refuses `@pytest.mark.parametrize` on an argument that has a default
value. `test_configure.py` reuses the invariants by calling them directly
against a rendered file written under the agents file's name, so a default
looks like the way to keep those calls unchanged, and it is not allowed.

## What to do

Leave the parameter without a default and pass the file name explicitly at
every direct call site in `tests/scripts/test_configure.py` (T-059 passes
`"docker-compose.agents.yml"`, as for
`test_every_agent_service_isolates_its_config_home`). Grep that file for the
test's name before parametrizing it.

## Evidence

`python3 -m pytest <hive>/tasks/T-059/test_default_param.py -q`, from the
T-059 worktree: a two-value parametrize over `compose_file: str = "a.yml"`.
