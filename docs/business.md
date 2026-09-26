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
