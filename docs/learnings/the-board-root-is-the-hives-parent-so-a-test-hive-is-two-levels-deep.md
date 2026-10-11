# The board root is a sibling of the hive tasks dir, so a test hive has to sit two levels under `tmp_path`

**When it applies:** you are writing a test that stores or reads a task
description through `dispatcher/tasks.py`, or choosing the hive directory for
a new fixture.

**Status:** unconfirmed — reported by T-026's implementador; the auditor
checked the derivation and the two fixtures, not the no-collision claim.

## What is true

`tasks.board_root` is `context_transfer.hive_root(hive_dir)` plus `board`,
and `hive_root` is the *parent* of the hive tasks dir. A fixture whose hive
is `tmp_path / "hive"` therefore writes descriptions to `tmp_path / "board"`,
outside the directory the test thinks it owns, and beside whatever else the
test put in `tmp_path`.

`tests/dispatcher/test_tasks.py`'s `_hive` uses `tmp_path / "hive" / "tasks"`
so the board lands at `tmp_path / "hive" / "board"`, and one of its tests
asserts that the hive root holds only `tasks` after a refused write.

`tests/dispatcher/test_cli.py`'s `_write_config` keeps the older one-level
hive, and its `local_board` option puts the local kanban board at
`tmp_path / "board"` too: the same directory. The implementador reports they
do not collide, because the local board client lists only `.json` files and
descriptions sit in per-task subdirectories. The suite is green with both.

## What to do

Use the two-level layout in a new fixture. In `test_cli.py`, do not assert on
a listing of `tmp_path / "board"` in a test that also sets `local_board`, and
do not give a task an id ending in `.json`.

## Evidence

T-026: `dispatcher/tasks.py` `board_root`, `dispatcher/context_transfer.py`
`hive_root`, `tests/dispatcher/test_tasks.py` `_hive`,
`tests/dispatcher/test_cli.py` `_write_config`.
