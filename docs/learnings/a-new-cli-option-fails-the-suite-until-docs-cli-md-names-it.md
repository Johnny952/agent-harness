# A new CLI option fails the suite until `docs/cli.md` names it

**When it applies:** you add, rename or remove a subcommand, an option, a
positional or an `EXIT_*` code in `dispatcher/cli.py`, or you edit a table in
`docs/cli.md`.

**Status:** unconfirmed — proposed by T-151's arquitecto and implementador; no
second task has hit it.

## Symptom

`tests/dispatcher/test_cli_reference.py` `test_the_reference_matches_the_parser`
fails with a line such as `run-queue --bogus: in the parser, not in cli.md`.

## Why

The parser is built by `cli.build_parser()` (ADR 334), and the test compares
it both ways with `docs/cli.md`: the `### \`<subcommand>\`` headings under
`## Commands`, the first word of the first backticked span in each table row's
first cell, the `## Global options` table, and the `## Exit codes` table
against a fixed set (0, 2 and the `EXIT_*` constants named in the test). A
row whose first cell has no backticks is not read at all.

## Rule

Change `docs/cli.md` in the same diff as the parser, keep the argument name as
the first backticked span of the row's first cell, and add a new `EXIT_*`
constant to the test's `codes` set by hand — it is not discovered.

## Evidence

`dispatcher/cli.py` `build_parser`; `tests/dispatcher/test_cli_reference.py`
`_table_names`, `_differences`; `python3 -m pytest -q` printed
`4439 passed, 15 skipped` in the T-151 worktree.
