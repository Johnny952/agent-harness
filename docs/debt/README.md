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
| `T-008-D1` | **Resolved 2026-09-28.** No lock around `LocalBoardClient.set_status`'s read-modify-write, and no racing-writers test | Phase 4's action queue, or two `dispatch` processes over one `local_board.dir` expecting both status moves to stick | Taken by the narrow branch, not the architectural one: `_card_lock` holds an `flock` per card around the read, the merge and the write, and `tests/dispatcher/test_vibe_kanban_client.py` proves a second writer blocks and then reads the first's new status. `flock` and not the `O_EXCL` declared, because an `O_EXCL` lock outlives the process that took it and would wedge a card after the crash `run-phase` exists to repair. The Phase 1 API as sole writer stays the end state. Detail: [T-008-D1](T-008-D1.md) | none |
| `T-008-D2` | **Resolved 2026-09-26 by T-009.** A card that will not parse is skipped with a warning; nothing quarantines it or tells a caller the board is short | Phase 1's read API, or reconciling a task file's `kanban_issue_id` against a board that no longer lists it | Taken by the second branch of the fix, not the first: `LocalBoardClient.unreadable()` reports the card files `list_issues` skipped and `observability/api/app.py:_read_cards` names each one in `warnings`. No quarantine. Detail: [T-008-D2](T-008-D2.md) | none |
| `T-008-D3` | **Resolved 2026-09-26.** `docs/plans/board.md`'s Status paragraph denied Phase 0 existed — first by calling it queued, then by calling its last round uncommitted | Reading that plan's Status paragraph to decide whether Phase 0 exists — first task allowed to edit the plan owns it | Rewritten out of cycle rather than by Phase 1: the paragraph now states that Phase 0 is built and merged and cites the two debts left open. Detail: [T-008-D3](T-008-D3.md) | none |
| `T-009-D2` | **Resolved 2026-09-28.** Nothing caps `/api/events?limit=`, or the length of `warnings`; the range check added in round 3 rejects only an integer SQLite cannot bind | A caller or Phase 3's tail sending a large-but-bindable `limit` at a long-lived events database, or a `local_board.dir` holding many documents that will not parse | Both halves, with the number decided without waiting for Phase 3: `MAX_EVENT_LIMIT = 1000` clamps `limit` and names the clamp in `warnings`, `MAX_WARNINGS = 100` caps the list in `_envelope` with its last slot tallying the rest. A clamp and not a rejection, sitting after the round-3 range check so an unbindable integer stays a 400. 1000 is five times the board's `EVENTS_LIMIT`, not a tail window: Phase 3's tail is `since`-bounded. `docs/decisions.md` ADR 11. Detail: [T-009-D2](T-009-D2.md) | none |
| `T-009-D3` | **Resolved 2026-09-27 by T-010.** `dispatcher/debt.py:_rows` strips one backtick per cell end rather than a matched pair, so a cell ending in inline code loses its closing backtick | Phase 2 rendering `/api/debt`, or any task changing the cell stripping in `_rows` — the same stripping unwraps the ids `index_fingerprints` compares | Done as declared: `dispatcher/debt.py:_unwrap_code` takes one matched pair and nothing else, and no fingerprint moved — `tests/dispatcher/test_debt.py:test_the_fingerprints_of_the_projects_own_index_did_not_move` parses this index under both rules and asserts the two agree. `resolved()`'s own unwrap is untouched and filed as `T-010-D1`, and a narrower gap in the replacement as `T-010-D2`. Detail: [T-009-D3](T-009-D3.md) | none |
| `T-009-D4` | **Resolved 2026-09-26.** `/api/events` cannot open the collector's WAL database through the `:ro` mount, so it answers `[]` plus a warning that reads like a missing file | Phase 3's tail, comparing `/api/events` with the dashboard over the same volume, or handing any later service `observability_data:/…:ro` | Done as declared: confirmed by hand against the running stack, then `observability_data` mounted at `/events` with no options in both compose files. `observability/api/app.py` untouched — still `mode=ro`, still no `init_db`. The invariant survives as one named exception, `API_WRITABLE_MOUNTS`. Detail: [T-009-D4](T-009-D4.md) | none |
| `T-010-D1` | **Resolved 2026-10-01.** `dispatcher/debt.py:resolved` still unwraps a handoff's entry id with the one-backtick-per-end strip that `T-009-D3` replaced inside `_rows`, so the module now has two answers to what a backtick around an id means | A `resolved_debt` id written with doubled or unbalanced backticks in a handoff payload or a task file's frontmatter, or a later task changing how ids are unwrapped in `dispatcher/debt.py` — it now has to change two functions that disagree | Reuse `_unwrap_code` in `resolved`, add the double-backtick case to `tests/dispatcher/test_debt.py`, and confirm `test_the_fingerprints_of_the_projects_own_index_did_not_move` still holds. Detail: [T-010-D1](T-010-D1.md) | none |
| `T-010-D2` | **Resolved 2026-10-01.** `dispatcher/debt.py:_unwrap_code` takes the outer pair off a cell that opens with one code span and closes with another, so `` `cli.py` and `debt.py` `` renders unbalanced. Not a regression: the strip it replaced did the same and worse | An index row whose **where** or **fix** cell both begins and ends with inline code — the shape of this index's newest rows — rendered through `/api/debt` or the board's `/debt`; or a later task changing cell unwrapping in `dispatcher/debt.py` | Require no backtick in `cell[1:-1]` before unwrapping, and add the case to `tests/dispatcher/test_debt.py:test_a_cell_keeps_the_backtick_it_only_opens_or_only_closes_with`. Detail: [T-010-D2](T-010-D2.md) | none |
| `T-011-D1` | Nothing bounds the `body` served on `/api/tasks/<task_id>`: `handoff()` only ever appends to it, so the response carries the task id's whole history and grows for as long as that id lives — 55 KB for the largest card today, and the only thing this service answers with no cap | A card under `.hive/tasks/` past a few hundred KB, or a client polling the detail route rather than reading it once per screen view — the console's task-detail screen is tier 1 of `docs/plans/front.md`. Not the list route: ADR 21 keeps `body` off `/api/tasks` and a test pins it | `MAX_BODY_BYTES` truncating and naming itself in `warnings`, on `MAX_EVENT_LIMIT`'s model under ADR 11, or a `?body=` parameter — which is also a `_reject_unknown_parameters` contract change. Either is an ADR narrowing ADR 21, and it belongs with the console task that renders the detail screen. Detail: [T-011-D1](T-011-D1.md) | none |
| `T-011-D2` | `docs/plans/front.md` *Decisions this tier's tasks make* binds `front/`'s entry into git and `docs/ui.md`'s creation to "the first tier-1 task, whichever screen it takes". T-011 was it, took no screen, fired neither, and the section does not say so | A tier-1 task of `docs/plans/front.md` that takes a screen, reading that section to find whether those two items are discharged or whether its arquitecto must raise the `front/` ruling and stop | One clause naming T-011 as the tier-1 task that took no screen, passing both items to the first that does. Not T-011's to make: the section is bound to the console task, and only a plan's Status paragraph is editable out of cycle. Detail: [T-011-D2](T-011-D2.md) | none |

`card` is `none` on every row because no card exists to point at: the T-008 and
T-009 task files carry no `kanban_issue_id` and neither auditor was handed card
ids, which is what a run with no board configured looks like — the gap Phase 0
exists to close. A later task filing debt on a harness with `local_board` or
`vibe_kanban` set is handed the card's id with the entry's id and puts it here,
in backticks, verbatim.

The two `T-010` rows are `none` for a second reason as well, worth naming
because it is not the same gap. That cycle was recovered one phase at a time
with `run-phase` after the implementador hit `phase_timeout_seconds`, so
`dispatcher/dispatcher.py:_file_accepted_debt` — which assigns the ids and opens
the cards before the auditor runs — never ran at all. The ids were re-derived
by the auditor from `dispatcher/debt.py:entry_id`, in the order the declarations
were made: `T-010-D1` is the one the revisor accepted in round 1, `T-010-D2` is
the revisor's own proposal (its W1), ruled on by the auditor because the revisor
could not rule on itself. If cards for either turn out to exist, these two
`card` cells are the ones to correct.

The two `T-011` rows are `none` for the first reason above and, for `T-011-D2`,
for a third worth naming because it is structural rather than an accident of how
that cycle was driven. `T-011-D1` arrived with a filing note, which is the normal
path, and is `none` only because this harness has no board. `T-011-D2` is the
revisor's own proposal: `dispatcher/debt.py:accepted` reads the implementador's
declarations filtered by the revisor's rulings, so a debt the revisor raises about
its own round has no route to `_file_accepted_debt` and gets no card and no id
from the dispatcher, `--final` or not. The auditor ruled on it and gave it the
next id in the same sequence, exactly as `T-010-D2` was handled.
