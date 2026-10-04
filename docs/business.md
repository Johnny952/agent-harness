# Business rules

What this harness enforces, as rules rather than as code. **This file is
partial and starts here**: it was opened by T-009 (Phase 1 of
`docs/plans/board.md`) to hold the rules that phase inferred from the code
rather than read in a spec, because those are the ones a human has to confirm or
kill. It is not a map of the project — that is `docs/README.md` — and a later
task extends it in the same shape rather than rewriting it.

Each rule is **confirmed** (stated in a doc, an ADR or a comment, cited) or
**unconfirmed** (inferred from the code by reading it). Unconfirmed is not a
defect; it is the list somebody is being asked to rule on.

This file runs code → human: an agent writes what it found, a human rules on it.
The other direction is [`charter.md`](charter.md), which a human writes and no
role may edit. A rule here that the charter already settles is not unconfirmed —
cite the entry and move it up, or drop the row.

## Reading the harness's state

- **A task's `card` is the board document its `kanban_issue_id` points at, or
  `null`; a harness with no board configured is not an error.** Confirmed:
  `docs/plans/board.md`, "Phase 1 — a read API in Python", and
  `docs/decisions.md` ADR 3, which narrows it further — only a `local_board`
  harness gets cards, and a `vibe_kanban` one gets `null` plus one warning.
- **Events are tailed by id and never by time: `since` filters `id > since`.**
  Confirmed: the same spec section. uuid4 task ids admit no other stable order,
  so there is deliberately no time-based filter beside it.
- **Nothing is dropped silently: one unreadable file costs a warning naming it
  and the rest of the list still comes back.** Confirmed: the same spec section,
  bounded by `docs/decisions.md` ADR 5.
- **The quota ceiling an account is held to is `reserve_pct` when it is the
  primary and `quota_threshold_pct` otherwise, so an account row carrying all
  three facts has to be read as a pair and not as three independent numbers.**
  Confirmed: `dispatcher/dispatcher.py:_threshold_for`, whose docstring says why
  every reader of it has to agree — the gate that parks an account above the line
  and the recheck that un-parks it below one are the same decision seen twice, and
  disagreeing would loop. `docs/charter.md` C-2 is the ruling behind it (a reserve
  is a ceiling, not a partition) and `docs/decisions.md` ADR 20 is why
  `/api/accounts` serves both numbers on every row with `is_primary` beside them:
  the relation is not served, so the consumer applies it. Added by T-011, which
  served the three fields and had to state which one governs.

## Unconfirmed — inferred from the code by T-009

- **Every non-dotted subdirectory of `projects_root` is a project the harness
  will answer for.** `observability/api/app.py:_project_slugs` lists them and
  filters only on a leading dot, so a scratch directory, a backup or an
  unrelated checkout parked there counts as a project — which makes
  `/api/debt?project=` *required* where it would otherwise be optional, and
  offers the stray name in the 400 that says so. Nothing marks a directory as a
  harness checkout; `bootstrap-project`'s layout is the only convention.
  A human should say whether a marker file ought to be the test instead.
  Observed rather than only inferred on 2026-09-26, by hand against the running
  stack: `/api/debt` with no project answers `400 project is required:
  /data/projects holds ia-harness, scratch`, and `scratch` is the toy repo the
  V0 checks use — so the behaviour is real, not a misreading of `_project_slugs`
  (`.data/verify/t009-api-verification.txt`). It stays here because what is
  missing is the ruling, not the evidence: no doc, ADR or comment says a
  directory parked under `projects_root` is meant to be a project.
- **An account whose state file holds the JSON literal `null` is treated as
  `IDLE`.** `dispatcher/state_machine.get_state` reads it as the default rather
  than as damage, so such an account is eligible for dispatch and `/api/accounts`
  reports it as idle with no warning. Inherited behaviour, left alone by T-009
  deliberately; whether an unreadable-but-scannable state file should park an
  account instead is a policy decision no doc makes.
- **`/api/debt` answers 404 for a project slug with no checkout and treats a
  project with no `docs/debt/README.md` as a project with no debt** (`[]` plus a
  warning), rather than as an unbootstrapped one. The distinction between "never
  filed debt" and "docs contract not set up" is not represented anywhere.

## Unconfirmed — inferred from the code by T-013

- **A task with no handoff files is a task no phase has finished *and* a task
  that ran before the harness kept these records: `/api/phases` answers `[]` for
  both and nothing distinguishes them.** A phase record exists only because
  `dispatcher/context_transfer.py:save_handoff` wrote one, and `save_handoff` is
  newer than most of this harness's task ids — ten of the thirteen under
  `.hive/tasks/` have no `handoffs/` directory at all. The same screen shows the
  contradiction: `/api/tasks/<id>` serves the task file `body`, which carries one
  `## <role>` section per phase that ran (`dispatcher/handoff.py:body`), so the
  detail screen renders six role sections for `T-008` one region above a phase
  timeline that is empty, and the empty state has to cover both readings at once
  ("a task whose first phase is still running has none yet — and a task
  dispatched before the harness kept these records has none at all"). Nothing
  rules on whether the api owes a reader that difference, and the prose sections
  are the evidence it could serve: `docs/decisions.md` ADR 27 decides what a
  phase row *is* and ADR 28 decides that no file says which phase is *running*,
  but neither addresses a phase that ran and left no record. A human should say
  whether "never recorded" and "not yet" are the same answer.
