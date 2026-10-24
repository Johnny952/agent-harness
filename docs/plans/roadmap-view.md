---
status: building
approved: 2026-10-11
built: null
supersedes: []
---

# Roadmap view — the project's path, newest to oldest, derived rather than written

**Status:** idea, accepted in outline by the user on 2026-10-11, not designed
in detail and not built. It records an idea the user raised that day ("a
historical roadmap, vertical, from the newest, the future, to the oldest,
already built, at the level of epics or so") and the main thread's analysis of
it, which the user agreed with. It is built after `docs/plans/task-split.md`
and steps 1 to 5 of `docs/plans/board-service.md`, or earlier in its cheap
form (*How it would be built*, 1).

## The goal

One screen that answers what the project is doing now, what comes next, and
what it has done and why. The person scrolls down from the future to the past.

## What exists today, and what is missing

The history is all on disk, spread over five places, none of which shows it
whole:

| Source | What it holds | Why it is not the view |
|---|---|---|
| `docs/ROADMAP.md` | the verification stages and the Results log, plus Stage 2's ordered list of future work | written by hand; its Stage 2 order already trails what is being built |
| `docs/plans/*.md` | the initiatives: board-service, review, token-economy, task-split… | each has a *Status* line, in prose |
| `docs/decisions.md`, `docs/implementations/` | why each thing is the way it is | ordered by number or task, not by initiative |
| git | 27 merge commits, `Merge T-0xx: …`, with their dates | only a reader of `git log` sees them |
| the board and backlog (`board-service.md`) | today's tasks and the proposed ones | the present, not the path |

The planned screens cover three heights: the board (tasks now), the backlog
(proposed), and the epic screen (one epic's tasks). Nothing sits above the
epic, so nothing answers the question across time. This view fills that gap;
it does not repeat any of the three.

## Decisions

### 1. Derived, never written

The view only reads what is already written. A roadmap kept by hand would be
a sixth source of truth and would drift, as Stage 2 of `ROADMAP.md` already
has. So the view adds a `GET` route and a screen: no write, no service, no
quota.

### 2. The unit is the plan, with epics under it

Epics do not exist yet, and no past task has one, but every initiative is
already a plan in `docs/plans/`. So there are three levels:

```
Plan (initiative)   a row of the roadmap          e.g. Review
  └─ Epic           expands under it              e.g. T-030: approve and push
       └─ Task      links to the task screen      e.g. T-030-S1
```

### 3. Three bands, top to bottom

- **Future.**
  - Plans that are an *idea*, a *design* or *approved, not built*.
  - Backlog epics, in `priority` order.
  - What has been decided but not started is told apart from what has not
    been decided.
- **Now.** What is in progress, in *Awaiting review* or in *Conflict*, with
  its phase.
- **Past.** Merged work, by month, dated by `merged.at`. Each entry links
  the ADRs and implementation notes it produced. That is what the board does
  not give: the why.

Dates are only real ones: approved, started, merged. There are no estimates
and no Gantt bars, because a date a model guessed would be noise.

### 4. For the person, not for the agents

No role is given the roadmap as context. It would ride in every phase that had
it (`docs/plans/token-economy.md`) for little benefit, since a phase already
gets its plan and its task.

### 5. A change is a new entry, never an edit

When the project changes course, both decisions stay on the roadmap: the new
one is added and the old one is not moved or removed, as `docs/decisions.md`
is append-only. A derived view could not rewrite the past anyway; the past is
git and the ADRs.

The repo already tells three kinds of change apart, and the view shows each its
own way:

| Relation | Example | How it shows |
|---|---|---|
| **Supersedes** | C-11 superseded C-10; an ADR with its heading struck through | The old entry keeps its date, dimmed, marked "superseded by X"; the new one says "supersedes Y"; each links the other |
| **Narrows** | "Narrows ADR 53 and does not supersede it" | The old entry stays current; the new one hangs from it as an event |
| **Amends** | C-5, amended 2026-10-11 for the task-branch push | One entry, with the amendment's date as one more milestone |

By when the change comes:

1. **A plan changes before it is built.** It is in the future band and shows
   its current state. Expanded, it lists its revisions, read from the plan
   file's `git log` (date and commit subject). Nothing extra is written.
2. **Built work is replaced by new work.** The old task stays in the past
   band, in its month; the new one is in the future or now band with
   "supersedes T-0xx". The past gains a link forward and nothing else.
3. **A decision changes with no new code** (a superseding ADR, a charter
   amendment). It is an event in the past band, at its date, under its plan.
4. **A plan is dropped.** Its status becomes `superseded` or `dropped`; it
   stays in the past with its reason, linked to what replaced it, if anything.

Every link carries the why: the ADR or ruling that justified the change. Today
that answer takes reading `docs/decisions.md` whole.

The screen has a filter: *full history*, the default, with superseded entries
dimmed, and *current only*, which hides them and shows the project as it
stands.

## What it needs underneath

1. **A small frontmatter on each plan.** It replaces the prose *Status* line:
   `status` (idea, design, approved, building, built, superseded), and the
   dates it was approved and built, and `supersedes`, the plans it replaces,
   when any. It is the one new thing written, once per plan, and kept by
   whoever changes the plan's status.

   **Note 2026-10-17** (T-071): the frontmatter is written, and it does not
   replace the prose *Status* line. Both stay: the frontmatter is the state
   a reader parses, the prose line says why. `tests/test_plan_frontmatter.py`
   keeps the frontmatter valid. This plan is `building` from here: the first
   piece of the cheap form is written, the backfill, route and screen are not.
2. **An optional `plan` field on tasks and epics.** It is written only when
   set, like `epic`, and the arquitecto fills it when it proposes. So is
   `supersedes`, the task or epic the new work replaces. Writable since
   T-178-S1 (`backlog_propose`, `task_edit`; ADR 450), as a path
   `docs/plans/<name>.md`; `/api/roadmap` still matches bare file names, so
   such values land in `no_plan` until the api task strips the prefix.
3. **A one-time backfill of the past.** A script reads the merge commits from
   git and the `docs/implementations/T-*.md` notes, and writes `plan` (and
   `merged`, where the task file lacks it) onto the old tasks. A task it
   cannot place stays under "no plan", which is honest.
4. **`GET /api/roadmap`.** It builds the tree from the plans' frontmatter, the
   task files and the ADR index. It is read-only, like every api route. The
   relations between ADRs and charter entries are read from the conventions
   they already follow: a struck-through heading, "supersedes", "narrows",
   and the charter's **Amended:** block. No historical record is edited for
   the view (*Decisions*, 5).

   **Note 2026-10-17** (T-072): built, in part. `GET /api/roadmap?project=`
   reads the plans from the project checkout's `docs/plans/` (the api image
   carries no harness docs) with the reader `tests/test_plan_frontmatter.py`
   now imports, `observability/api/plans.py`, and the tasks from the hive. It
   returns the three bands and `no_plan`, the tasks with no plan, split into
   not merged and merged by month. A plan with no frontmatter or an invalid
   one is listed in `future` with `status: null` and a warning. Tasks are per
   harness: no task file carries a project key or a `plan` today, so every task
   lands in `no_plan` until the backfill and item 2. ADRs and charter entries
   are not read yet; that is a later step.

   **Note 2026-10-24** (T-178-S2): epics are built (ADR 447). An epic, a
   task file with `split_into`, is a row in the same lists a task is, with
   `kind: "epic"`, `place`, `merged_tasks` and its sub-tasks nested in
   `tasks` in `split_into` order; they are no longer flat rows. A sub-task
   with no `plan` takes its epic's. A backlog epic is in the `future` band,
   as a second row for its plan file when that plan is `building` or past,
   or in `no_plan.now` with no plan. A `plan` is a plan file name or
   `docs/plans/` plus it. A plan file can so be in two bands.
5. **The screen.** A vertical list in the three bands, each plan expanding to
   its epics and each epic to its tasks, with the *full history* and *current
   only* filter.

   **Note 2026-10-18** (T-075): built, in its cheap form. `/roadmap` in the
   console reads `GET /api/roadmap` through the bearer forward, project-scoped
   as `/api/debt` is. It lists the future, now and past bands newest first,
   each with its count; a plan is a collapsed row (title, status, dates) that
   opens to its tasks, each linking to `/tasks/$taskId`. `no_plan.now` closes
   the now band and `no_plan.past` closes the past band, grouped by month. The
   filter is `?view=current` (*Current only*, which hides the past band and
   every merged or done task); no parameter is *Full history*. Plans expand
   straight to tasks: epics come with step 2 of *How it would be built*.

   **Note 2026-10-24** (T-178-S3, ADR 453): the epics are drawn. An epic is a
   collapsed row in its plan's task list (title, state, `merged/total
   merged`) that opens to its sub-tasks, each a task link; a backlog epic
   carries a `backlog` label, in the future band; *Current only* hides a
   merged epic and a kept epic's merged sub-tasks. A plan row's open state is
   keyed by band and file, since one file can be in two bands.

## How it would be built

1. **The cheap form, possible now.** The plans' frontmatter, the backfill,
   and the route and screen with plans and tasks only: the past band and the
   plans' place in the future band. It needs nothing from the other plans.
2. **Epics and the backlog.** Once `task-split.md`'s epics and
   `board-service.md`'s backlog with `priority` exist, the `plan` field, epics
   in all three bands, and backlog epics in the future band.

Each step is one surface at a time: the plan frontmatter and backfill, the api
route, the front screen (behind its own ask for a build).

## Open questions

- **Retire Stage 2 of `ROADMAP.md`?** The order of future work is written twice
  once the backlog exists: in Stage 2's table and in the backlog's
  `priority`. With this view, it could live only in `priority`, and
  `ROADMAP.md` would keep the verification stages and the Results log, which
  is what `docs/README.md` already says the file is for. The user's call,
  separate from this plan.
- **Which plans count.** `board.md` and `front.md` are older and broader than
  one initiative. Whether they are rows, or split into the phases they
  describe, is decided when the frontmatter is written.

  **Note 2026-10-17** (T-071): rows. Each of `board.md` and `front.md` has one
  frontmatter, not one per phase or tier. `board-service.md` carries
  `supersedes: [board.md]`, and `board.md` is `superseded`, not `building`:
  its own header says it is superseded in part, its later phases are
  re-planned in `board-service.md`, and nothing more is built from it. Its
  Phases 0–2 stay as the record of what was built. `front.md` is `building`:
  tiers 1 and 2 are built, tier 3 is not. `built` carries a date only when
  `status` is `built`, so a superseded or partly built plan has
  `built: null`.
- **A structured relation line on new ADRs.** Reading the prose conventions
  breaks the day one is written differently. New ADRs could carry a line such
  as `**Relation:** supersedes 30`, with old ones left as they are and still
  read from their prose. Decided when the route is built.

  **Note 2026-10-17** (T-072): deferred to the step that reads ADRs. The
  route as built reads plans and tasks only, no ADR and no charter entry, so
  there is no reader yet whose breakage the line would prevent, nor one to
  show what the line must hold. It stays open until that step.
- **Debt.** Whether accepted debt items show in the future band, or only on
  the board and the debt screen.

## What it is not

Not a planning tool: nothing is ordered or accepted here, which stays on the
backlog. Not a replacement for `docs/decisions.md` or the implementation notes:
it links them. Not a Gantt chart.
