# `python3 -m pytest` is the whole gate, and the only interpreter you get

**When it applies:** you are deciding what "green" means before handing work
on, or you want to observe a value, an exception or a shell script's syntax and
are reaching for something other than the declared test command.

**Status:** confirmed — every phase of T-008 ran into one half of this,
corrected 2026-10-10 by T-019 on the gate half.

## The gate

`pyproject.toml` configures nothing but pytest (`[tool.pytest.ini_options]`,
`testpaths = ["tests"]`), and there is no `build:` key in `docs/README.md`'s
frontmatter. For Python that is still the whole automated opinion this project
has: no linter, no formatter, no type checker, and nothing else to run.

**It is not the whole gate any more.** Corrected 2026-10-10 by T-019: since
2026-10-08 the frontmatter also carries `install:`, two `cd front && bun run …`
`test:` entries and a `lint:`, and the test gate runs them in the order
install, test, lint (`docs/decisions.md` ADR 39) — so a `front/` change is
executed in the loop, a red typecheck or test blocks and a red lint is a note
(`docs/charter.md` C-10). A phase whose card grants them can run the three
itself, after `cd front && bun install --frozen-lockfile`
(`docs/decisions.md` ADR 46).

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
