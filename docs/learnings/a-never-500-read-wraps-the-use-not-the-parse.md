# A never-500 read over a file catches the shape too, and guards the *use* of what it parsed

**When it applies:** you are writing or widening a reader whose contract is
"report the damage, never crash" over `.hive/tasks/*.md`,
`dispatcher_state/*.json` or a board card — `observability/api/app.py`'s views,
or anything a later phase adds beside them.

**Status:** unconfirmed — reported by T-009's arquitecto, revisor and
implementador across four rounds; each case below is pinned by a test in
`tests/observability/test_api.py`.

## Symptom

Four 500s out of one endpoint, each past an `except` tuple that looked
exhaustive. Frontmatter that does not scan:

```
yaml.parser.ParserError: while parsing a flow sequence
  in "<unicode string>", line 2, column 9:
    status: [unclosed
            ^
expected ',' or ']', but got '<stream end>'
```

Frontmatter that scans but is not a mapping — `---\nTODO write this up\n---`,
or a list of bullets:

```
TypeError: string indices must be integers, not 'str'
TypeError: list indices must be integers or slices, not str
```

A parsed value of the wrong *type*, which raises where it is used and not where
it was read:

```
TypeError: unhashable type: 'dict'
  File "observability/api/app.py", line 325, in _task
    "card": cards.get(task.kanban_issue_id) if task.kanban_issue_id else None,
```
```
TypeError: Object of type set is not JSON serializable
```

And a configured directory that is not one:

```
NotADirectoryError: [Errno 20] Not a directory: '.../board'
  names = sorted(os.listdir(self.config.dir))
```

## Why

Three separate facts, one direction.

`yaml.YAMLError` is not a `ValueError`: `read_task_file` splits on `---`
(`ValueError`) and then calls `yaml.safe_load` (`yaml.YAMLError`), so one file
has two failure modes needing two entries. A successful scan is not a shape:
`yaml.safe_load("TODO")` returns a `str` and `json.loads("[...]")` a `list`, so
`fm["task_id"]` and `data["state"]` subscript the wrong type one line later —
`TypeError`, and `AttributeError` for `.get` on a list. And a wrong-typed value
that parses fine raises at its *use*: as a dict key in `_task`, as `_scan`'s
sort key, or inside `jsonify`, which sits outside every `except` a view wrote.
Only the field a reader validates on the way in is safe — `LocalBoardClient._read_path`
checks `issue_id` is a `str` and leaves `created_at` whatever was on disk.

For directories, `_scan` catches `FileNotFoundError` only, and deliberately: for
the dispatcher a missing board is a board with no issues, while a
`local_board.dir` that is a regular file must raise, or `create_issue` mints
cards nobody can list. "Missing is empty" is not "unreadable is empty".

## What to do

Catch `(OSError, TypeError, ValueError, KeyError, yaml.YAMLError)` — that is
`observability/api/app.py:_UNREADABLE` — and add `AttributeError` where a
`.get` runs on the parsed document, as the accounts view does. End the `try`
after the last line that *touches* a parsed value: the row-building, the dict
lookup, the sort, and `app.json.dumps(row)` for the serialisation `jsonify`
would do outside your guard. Narrowing it back to the parse call reopens every
500 above.

Widen it at the caller whose contract is never-crash, never in the `dispatcher/`
reader it calls: that reader's narrow catch is load-bearing for the writer
sharing it — see
[a-lookup-that-never-raises-catches-valueerror](a-lookup-that-never-raises-catches-valueerror.md)
for the other side of the same seam. What the guard does and does not cover is written down in
`docs/decisions.md` ADR 5.

## Evidence

`tests/observability/test_api.py`, the cases named `does_not_scan`,
`board_directory_that_is_a_file`, and the six round-4 cases over a mapping and a
sequence `kanban_issue_id`, `owner: !!set`, and two cards whose `created_at`
will not sort. Five of the six were run against `git show 73bc59b:observability/api/app.py`
and failed with the `TypeError`s above. Inbox entries:
`T-009-a-task-file-fails-with-yamlerror-not-valueerror.md`,
`T-009-frontmatter-that-scans-is-not-yet-a-mapping.md`,
`T-009-a-never-500-guard-must-wrap-the-use-not-the-parse.md`,
`T-009-a-missing-dir-is-empty-but-a-file-where-a-dir-goes-raises.md`.
`dispatcher/operator.py:_read_task` still catches only `(OSError, ValueError,
KeyError)`.
