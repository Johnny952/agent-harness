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

## What it needs underneath

1. **A small frontmatter on each plan.** It replaces the prose *Status* line:
   `status` (idea, design, approved, building, built, superseded), and the
   dates it was approved and built. It is the one new thing written, once per
   plan, and kept by whoever changes the plan's status.
2. **An optional `plan` field on tasks and epics.** It is written only when
   set, like `epic`, and the arquitecto fills it when it proposes.
3. **A one-time backfill of the past.** A script reads the merge commits from
   git and the `docs/implementations/T-*.md` notes, and writes `plan` (and
   `merged`, where the task file lacks it) onto the old tasks. A task it
   cannot place stays under "no plan", which is honest.
4. **`GET /api/roadmap`.** It builds the tree from the plans' frontmatter, the
   task files and the ADR index. It is read-only, like every api route.
5. **The screen.** A vertical list in the three bands, each plan expanding to
   its epics and each epic to its tasks.

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
- **Debt.** Whether accepted debt items show in the future band, or only on
  the board and the debt screen.

## What it is not

Not a planning tool: nothing is ordered or accepted here, which stays on the
backlog. Not a replacement for `docs/decisions.md` or the implementation notes:
it links them. Not a Gantt chart.
