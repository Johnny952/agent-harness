# `python3 -m pytest` is the whole gate, and the only interpreter you get

**When it applies:** you are deciding what "green" means before handing work
on, or you want to observe a value, an exception or a shell script's syntax and
are reaching for something other than the declared test command.

**Status:** confirmed — every phase of T-008 ran into one half of this.

## The gate

`pyproject.toml` configures nothing but pytest (`[tool.pytest.ini_options]`,
`testpaths = ["tests"]`). There is no linter, no formatter, no type checker and
no `build:` key in `docs/README.md`'s frontmatter. So the suite is the entire
automated opinion this project has about a change: nothing else will catch a
style drift or an unused import for you, and nothing else has to be run.

## The interpreter

`python3 -m pytest` being allowed does not make `python3` allowed. `python3 -c
"..."`, `python3 some_script.py` and `bash -n scripts/configure.sh` are each
refused in an agent phase, so a value you want to see has to come out of the
suite: add a throwaway test under `tests/`, assert what you expect, and read
the failure — or run it with `-s` and print. Delete the file before the phase
ends. Shell edits are read line by line instead; no test in the suite covers
`scripts/`.

## Evidence

T-008, all four phases, from
`/data/projects/ia-harness/worktrees/T-008/work`. The verbatim refusals are in
the shared inbox, harness-scoped:
`T-008-python3-only-runs-the-declared-test-command.md` and
`T-008-bash-n-is-refused-like-python3-c.md`.
