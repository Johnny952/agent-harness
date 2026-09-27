# Debt

Work a task decided not to do, declared by the implementador, ruled on by the
revisor, filed here by the auditor. This index is the source of truth; a board
card, where there is one, mirrors a row and never replaces it.

Read the whole table and open only the entries whose **where** matches the task
in front of you: the trigger is a condition to check against your own work, not
a topic. An entry that gets resolved is marked resolved in place, with the task
that resolved it — never deleted, because a fix can be reverted and the row
should outlive that.

Declare `found` debt only for files your task touched, and only if it is not
already a row here.

| id | what | where | fix | card |
|---|---|---|---|---|
| `T-008-D1` | No lock around `LocalBoardClient.set_status`'s read-modify-write, and no racing-writers test | Phase 4's action queue, or two `dispatch` processes over one `local_board.dir` expecting both status moves to stick | Make the Phase 1 API the only writer, per `docs/plans/board.md` "Actions go through a queue, not a socket"; or an `O_EXCL` lock per card. Detail: [T-008-D1](T-008-D1.md) | none |
| `T-008-D2` | **Resolved 2026-09-26 by T-009.** A card that will not parse is skipped with a warning; nothing quarantines it or tells a caller the board is short | Phase 1's read API, or reconciling a task file's `kanban_issue_id` against a board that no longer lists it | Taken by the second branch of the fix, not the first: `LocalBoardClient.unreadable()` reports the card files `list_issues` skipped and `observability/api/app.py:_read_cards` names each one in `warnings`. No quarantine. Detail: [T-008-D2](T-008-D2.md) | none |
| `T-008-D3` | **Resolved 2026-09-26.** `docs/plans/board.md`'s Status paragraph denied Phase 0 existed — first by calling it queued, then by calling its last round uncommitted | Reading that plan's Status paragraph to decide whether Phase 0 exists — first task allowed to edit the plan owns it | Rewritten out of cycle rather than by Phase 1: the paragraph now states that Phase 0 is built and merged and cites the two debts left open. Detail: [T-008-D3](T-008-D3.md) | none |
| `T-009-D2` | Nothing caps `/api/events?limit=`, or the length of `warnings`; the range check added in round 3 rejects only an integer SQLite cannot bind | A caller or Phase 3's tail sending a large-but-bindable `limit` at a long-lived events database, or a `local_board.dir` holding many documents that will not parse | A module constant clamping `limit`, the clamp reported in `warnings`, with the number decided alongside the tail window in `docs/plans/board.md` "Phase 3". Detail: [T-009-D2](T-009-D2.md) | none |
| `T-009-D3` | **Resolved 2026-09-27 by T-010.** `dispatcher/debt.py:_rows` strips one backtick per cell end rather than a matched pair, so a cell ending in inline code loses its closing backtick | Phase 2 rendering `/api/debt`, or any task changing the cell stripping in `_rows` — the same stripping unwraps the ids `index_fingerprints` compares | Done as declared: `dispatcher/debt.py:_unwrap_code` takes one matched pair and nothing else, and no fingerprint moved — `tests/dispatcher/test_debt.py:test_the_fingerprints_of_the_projects_own_index_did_not_move` parses this index under both rules and asserts the two agree. `resolved()`'s own unwrap is untouched and declared as T-010's debt. Detail: [T-009-D3](T-009-D3.md) | none |
| `T-009-D4` | **Resolved 2026-09-26.** `/api/events` cannot open the collector's WAL database through the `:ro` mount, so it answers `[]` plus a warning that reads like a missing file | Phase 3's tail, comparing `/api/events` with the dashboard over the same volume, or handing any later service `observability_data:/…:ro` | Done as declared: confirmed by hand against the running stack, then `observability_data` mounted at `/events` with no options in both compose files. `observability/api/app.py` untouched — still `mode=ro`, still no `init_db`. The invariant survives as one named exception, `API_WRITABLE_MOUNTS`. Detail: [T-009-D4](T-009-D4.md) | none |

`card` is `none` on every row because no card exists to point at: the T-008 and
T-009 task files carry no `kanban_issue_id` and neither auditor was handed card
ids, which is what a run with no board configured looks like — the gap Phase 0
exists to close. A later task filing debt on a harness with `local_board` or
`vibe_kanban` set is handed the card's id with the entry's id and puts it here,
in backticks, verbatim.
