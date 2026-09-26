# Decisions

One ADR per decision a later task could undo without knowing it was a
decision. Append; never rewrite an entry that is already here — to replace
one, strike its heading through and point at the number that supersedes it.

## ADR 1 — A local board is a directory of JSON cards, and stores dispatcher vocabulary

**Status:** accepted (T-008, 2026-09-25).

**Context.** `dispatcher/vibe_kanban_client.py` is a four-method seam
(`list_issues`, `get_issue`, `create_issue`, `set_status`) built for a board
that no longer exists: `docs/ROADMAP.md` records the 2026-09-25 re-run where
`create_issue` fails with `project_id is required` against a retired service.
Every run to date has used `NullKanbanClient`, so two prioritized items in the
README — debt cards and epic decomposition — wait on a `create_issue` that
works. `docs/plans/board.md` "Phase 0 — `LocalBoardClient`" is the spec this
implements.

**Decision.** `LocalBoardClient` stores one JSON document per issue under
`local_board.dir`, named `<issue_id>.json`, written with a temp file in that
directory and `os.replace` the way `dispatcher/state_machine.py` writes account
state. Within that:

- Ids are `uuid4`, minted by the client. `dispatcher/cli.py:_seed_kanban_issue_id`
  rejects a `--kanban-issue-id` that is not a uuid, and short ids would mean
  relaxing that check. `simple_id` stays `None`: a display handle is the UI's
  problem, and it can number by `created_at`.
- A card holds exactly the fields in `CARD_FIELDS` — `issue_id`, `title`,
  `description`, `status`, `created_at`.
- `status` is the dispatcher's own string, stored verbatim: `set_status` writes
  `in_progress:implementador` as it comes, with no `status_map`. There is no
  column here to rename, so the role — the one dimension a generic board
  flattens — survives on the card.
- `list_issues(**filters)` matches `CARD_FIELDS` by equality and raises
  `ValueError` on any other key. A filter whose value is `None` is dropped, as
  `VibeKanbanClient` drops it.
- `set_status` on an id with no card raises `LookupError`, where the remote
  client warns and carries on. A remote board could legitimately be out of
  sync; local storage that has forgotten a card the task file still points at
  is a bug. Every call site in `dispatcher/dispatcher.py`
  (`_update_task_status`, `_open_kanban_issue`) already catches `Exception` and
  logs, so this surfaces without threatening a run.
- `get_issue` is a lookup and returns `None` for anything it cannot answer. A
  missing file is silent: it is the ordinary "no such issue". The other three
  each log a warning, because each means someone wrote something this client
  did not — a document that will not parse, a document that parses but carries
  no string `issue_id`, and an id that is not a bare filename.
- A missing directory is a board with no issues. The first write creates it.

**Consequences.** Phase 1's read API and Phase 2's UI read these documents, so
a status they render is `in_progress:<role>` and not a column name; sorting a
board by age means `created_at`, the only order the random ids allow. Adding a
stored field widens what `list_issues` accepts and has to be added to
`CARD_FIELDS`; removing one breaks a caller that filters on it. The client
caches nothing, so two clients over one directory see each other's writes —
which is what lets `dispatch run-task` and a later `dispatch learnings` agree,
and what a Phase 1 reader relies on. Nothing here is dispatch state:
`.hive/tasks/*.md` remains the source of truth, per `docs/plans/board.md`
"Architecture decisions".

## ADR 2 — Two boards configured is a startup error, not a precedence rule

**Status:** accepted (T-008, 2026-09-25).

**Context.** `config.yaml` now has two optional board blocks, `vibe_kanban` and
`local_board`, and a run talks to one board. `dispatcher/cli.py:_kanban()`
picks in that order, so a silent rule was available.

**Decision.** `dispatcher/config.py:_load_boards` raises when both blocks are
present, with a message naming which one to drop. Both blocks remain opt-in:
with neither, a run gets `NullKanbanClient` and says nothing about a board, so
an upgraded harness does not start writing cards it was never asked for.
`--kanban-issue-id` is accepted with either block and refused with neither —
the check is "no board", not "no MCP".

**Consequences.** An operator who writes both gets a failed startup rather than
a board nothing moves. Phase 2 is where the local board's default is meant to
flip on (`docs/plans/board.md` "Configuration"); doing that means deciding what
an existing `vibe_kanban` block then means, and this ADR is why that cannot be
"the local one wins quietly".
