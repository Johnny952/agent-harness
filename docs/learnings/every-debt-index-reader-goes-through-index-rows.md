# Every debt index reader goes through `index_rows`

**When it applies:** you change the layout of `docs/debt/README.md` (its
columns, its row shapes, how a resolution is written) and are about to edit
the CLI, the dispatcher, the read API or the prompts to match.

**Status:** unconfirmed. Proposed by T-137's arquitecto, implementador and
revisor (one source, ADR 297); checked by its auditor with `git grep`.

## Symptom

None: a layout change that edits each caller instead of the parser keeps
working until the callers drift apart.

## Why

`dispatcher/debt.py` `index_rows` is the one parser of the index's table.
`cli.py`'s `import-debt`, `dispatcher.py`'s accepted-debt filing and
mirroring, `debt.open_rows` and `known_fingerprints`, `tasks.file_debt` and
`observability/api/app.py`'s debt route all take its rows. Since ADR 297 it
folds a resolution row into its entry, so no caller sees two rows for one id.

## Rule

Change the layout in `index_rows` and its helpers (`_raw_rows`,
`_is_resolution`, `_fold`, `duplicate_ids`); the callers need no edit while
the row dict keeps its keys.

## Evidence

`git grep -n "index_rows\|DEBT_INDEX" -- dispatcher board_service observability`
in the T-137 worktree: every row consumer calls `debt.index_rows`; the only
other reads are `gates.debt_index` and `debt.read_index`, which `cat` the file.
