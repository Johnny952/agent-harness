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
| `T-008-D2` | A card that will not parse is skipped with a warning; nothing quarantines it or tells a caller the board is short | Phase 1's read API, or reconciling a task file's `kanban_issue_id` against a board that no longer lists it | Rename it aside (`.corrupt`) and surface it in Phase 1's read API, or have `list_issues` report a skip count. Detail: [T-008-D2](T-008-D2.md) | none |
| `T-008-D3` | `docs/plans/board.md`'s Status paragraph still calls Phase 0 specified and queued as T-008 | Reading that plan's Status paragraph to decide whether Phase 0 exists — first task allowed to edit the plan owns it | One line there: Phase 0 landed in T-008, citing `docs/decisions.md` ADR 1. Detail: [T-008-D3](T-008-D3.md) | none |

`card` is `none` on all three because no card exists to point at: T-008's own
task file carries no `kanban_issue_id` and the auditor was handed no card ids,
which is what a run with no board configured looks like — the gap Phase 0 exists
to close. A later task filing debt on a harness with `local_board` or
`vibe_kanban` set is handed the card's id with the entry's id and puts it here,
in backticks, verbatim.
