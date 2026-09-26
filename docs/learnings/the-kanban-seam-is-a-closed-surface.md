# The `KanbanClient` seam is a closed surface: five public names, no more

**When it applies:** you are adding a method to any client in
`dispatcher/vibe_kanban_client.py`, adding a third implementation of the seam,
or changing `KanbanIssue`.

**Status:** confirmed — T-008 added the second implementation and the parity
tests decided the shape.

## What the tests pin

`tests/dispatcher/test_vibe_kanban_client.py` holds every client to the same
public surface twice over:

- `test_every_board_offers_what_the_real_one_does` asserts each client has
  everything `VibeKanbanClient` exposes — `enabled`, `list_issues`,
  `get_issue`, `create_issue`, `set_status`;
- `test_the_local_board_adds_nothing_to_the_shared_surface` asserts
  `LocalBoardClient` exposes *exactly* that set, not a superset.

So a public method on one client only — a Phase 1 reader method, a `delete`,
a `count` — fails the second test. That is deliberate:
`dispatcher/dispatcher.py` does not know which implementation it got, so a
method only one board has is a method nothing can call. Widening the seam means
widening it for every client, in one change, with the ADR to say why.

## What needs no edit

`dispatcher/dispatcher.py` annotates the `KanbanClient` union alias and nothing
else, so adding an implementation does not touch that file. The wiring is
`dispatcher/cli.py:_kanban()` alone.

## What is shared and easy to leave lying

`KanbanIssue`'s docstring is a contract read through both implementations. It
claimed the `issue_id` was server-assigned and that `simple_id` was searchable;
both went false the moment a second client minted its own ids and stored no
`simple_id`. A shared type's docstring is part of the diff.

## Evidence

T-008, revisor finding 2 and the two parity tests. `docs/decisions.md` ADR 1
carries the field list and the status vocabulary.
