# Plan: wiring the console in `front/`

Status: **tier 1 is built on both sides and closed, and the Jinja board is
retired; tier 2's two routes are built on both sides and its *Done when* is
reached, with the configuration read behind Role models the one tier-2 piece
left, the Role models task's to answer.** The api's tier-1 share landed with
T-011; the console's —
the five served reads in `front/src/lib/api/client.ts`, the server-side bearer
forward, and the warning banner on every screen that lists rows — landed with
T-012, whose decisions are [`decisions.md`](../decisions.md) ADR 22–26. T-013
built `/api/phases` and wired it: the task detail's phase timeline renders real
handoff records, and *What a phase row is* below is answered by ADR 27. T-016
built `/api/learnings` and wired it: the Learnings screen and the task detail's
learnings region both read the hive's trap entries, and *What a learning row is*
below is answered by ADR 41, with the console's half in ADR 42.

Parity, as *Parity, and what it retires* defines it below, **is** reached: the
four screens the Jinja board had — tasks, a task's detail, debt and the events
tail — all read the real api, so on 2026-10-04 `observability/board/` was
deleted together with its compose service and its tests, which is what
[`docs/charter.md`](../charter.md) C-8 made it stay for
(`docs/decisions.md` ADR 32). The detail screen's learnings region wanted
`/api/learnings`, which was tier 2 and unbuilt, and named that route on screen
rather than showing a fixture — but the board never had a learnings region, so
that was tier 2 arriving late, not tier 1 left open. ADR 32 is where that
reading is written down, and T-016 discharged it.

One thing tier 2 was expected to settle and did not: the Board's five role lanes.
ADR 26 bound them to `/api/phases`, and ADR 28 found the route cannot answer them
— a handoff is written when a phase **ends**, so the newest file names the role
that finished and a lane fed from it would be one phase behind. In progress stays
one column and the Board's banner names the missing *record*, not a route. The
lanes are a dispatcher task now, not a console one.

`front/` is in this repo, whole, since 2026-10-01 — C-9, which also keeps the
Lovable round-trip live — having been generated against the specifications in
[`board.md`](board.md) and named the console by C-8 on 2026-09-28. Seven of its
twelve screens still resolve from a fixture. This plan says in
what order that stops being true, what each step may decide for itself, and what
it must stop and ask. It renumbers nothing: [`board.md`](board.md) owns Phases
0–6 and stays the specification of what the phases *are*, as its own C-8
paragraph says. The tiers below are an ordering of the wiring work, and each one
names which board phases it discharges.

One fact about `front/` has changed since the C-8 documentation sweep and it is
the reason this plan is being written now rather than then. ADRs 15 and 19 count
twelve functions in `front/src/lib/api/client.ts` and name `mock/fixtures.ts` as
the only fixture module. The file now exports 25 and imports a second fixture
module, `mock/ops-fixtures.ts`, behind six screens that did not exist on
2026-09-29: Approvals, Pool, Tokens, Session logs, Backlog and Role models. The
*rules* those ADRs set are about shape and still hold exactly. Their
*enumerations* are of the file as it stood that day, and this plan does the
sorting again over the part that arrived afterwards.

## What this plan owes

Three documents point here and expect an answer.

[`board.md`](board.md) line 78 and [`README.md`](../../README.md) both defer the
integration itself: who builds the screens after Phase 3, in what order the
console replaces the Jinja board, and what has to be true before
`observability/board/` can be deleted. That is the tiers — answered, and
spent: the condition fired and the deletion happened on 2026-10-04
(`docs/decisions.md` ADR 32).

[`board.md`](board.md)'s *Where a UI decision lives* defers something harder. It
set a condition — "there is no separate `docs/ui.md` and there should not be one
until a second surface exists" — recorded that the condition is now met, and
handed this file three questions: where a UI definition lives, what it is
allowed to decide, and whether the arquitecto may block a phase for a missing UI
definition the way it may block one for a missing ruling. Those are answered
below, before the tiers, because the first tier-1 task runs through that loop.

## What is already settled

Nothing in this section is reopened by a task working from this plan. A task
that believes one of these is wrong supersedes it the way the convention in each
file says to, and says so in its handoff; it does not quietly build the other
thing.

**Rulings, in [`docs/charter.md`](../charter.md).** C-8 makes `front/` the
console and accepts the Node runtime that implies. The console is an HTTP client
of the api and nothing else: no volumes, no `.hive/`, no events database, no
Docker socket. Actions reach the dispatcher through a queue drained by a worker,
never a mounted socket. `observability/board/` stayed until the console served
tasks, task detail, debt and the tail against the real api, and was the
tie-breaking reference until then; all four landed, so it was deleted on
2026-10-04 and `git log` holds the reference (`docs/decisions.md` ADR 32).
C-1 makes this conversation the only
interactive one, and C-4 keeps a human in front of every run that spends quota.

**Decisions, in [`docs/decisions.md`](../decisions.md).** ADR 14 keeps the
Loading row in `front/` and out of the Jinja board. ADR 15 makes the console a
service that presents a bearer token from its server half, never from the
browser, with `@requires_auth` untouched on all five routes. ADR 16 makes
`client.ts` unwrap `data` and carry `warnings` out with it, with a partial
region rather than a fourth state. ADR 17 sorts every field the front asks for
and the api does not serve, into renames the console does, fields the api grows,
joins and splits the console does for itself, and fields the console drops.
ADR 18 moves three thresholds onto `/api/accounts`, deletes `HEARTBEAT_STALE_S`
in favour of `lock_expired`, and keeps `usage_pct` unserved until something
persists it. ADR 19 keeps any screen whose queries are not all backed by a route
out of the parity set, and makes it say so on itself instead of showing a
fixture.

Four things this plan would otherwise have argued are therefore already decided
and are only cited below: the envelope, the duplicated threshold constants,
`front/AGENTS.md`'s "no authentication of any kind", and whether a screen may
ship against a fixture.

## Where a UI definition lives

[`board.md`](board.md) warned about the failure mode before it created the
opportunity for it: "a third file whose border with those two is 'it is about
pixels' is a file every role has to guess about". So the border is not about
pixels.

### The test that sorts them

Ask what has to agree with the answer.

- A **ruling** — something a human wants settled and not re-argued, or a
  question whose answer is "what does the operator want" rather than "what is
  consistent" — goes in [`docs/charter.md`](../charter.md), which no role may
  edit.
- A decision with a consequence **outside the console** — a route, a field, a
  credential, a parser's home — is an ADR in
  [`docs/decisions.md`](../decisions.md): numbered, appended, supersedable.
- A decision **more than one screen has to agree about** — a tone for a state,
  a date format, what an empty state must name, how a partial region is drawn,
  the nav and its hotkeys — goes in `docs/ui.md`.
- A decision **true of one screen only** goes in the phase or task that builds
  that screen, as [`board.md`](board.md) already does for the four it specifies.

The third bullet is the new one and the test is mechanical: *does a second
screen have to match this to be right?* If yes it is `docs/ui.md`'s; if no it is
the screen's own. Twelve screens is what makes the question real — with one
surface every cross-screen decision was trivially also a one-screen decision,
which is precisely the condition [`board.md`](board.md) said had to lapse first.

### What `docs/ui.md` may decide, and what it may not

It may decide anything the test above sorts into it, and it is binding: a screen
that contradicts it is wrong, not different.

It may not decide anything with a consequence outside the console. That the
Tokens screen shows a device code at all is not a styling question — it is about
a secret leaving a container, and it belongs to the charter. That `/api/debt`
should carry a lifecycle state is not a layout question — ADR 17 already refused
it. A `docs/ui.md` entry that can only be honoured by changing a route is an
entry filed in the wrong place, and the arquitecto that wrote it owes an ADR
instead.

It exists, created out of cycle on 2026-10-01 alongside the commit that put
`front/` in the repo, and seeded only from what the adopted console already
does — every entry cites the file it was read out of and is attributed to *the
`front/` import* rather than to a task. That is the same bar as the one this
paragraph used to set for the first tier-1 task: speculative vocabulary for
screens nobody is building is how a style guide stops describing the product,
and description of code that shipped is not speculation. The next task to
change one of those entries puts its own id there.

### How it changes

Unlike `docs/decisions.md`, `docs/ui.md` is edited in place. A vocabulary whose
current state has to be reconstructed by reading nineteen superseded entries in
order is not a vocabulary. Git holds the history, and each entry names the task
that set it.

An ADR is owed for a change to it in exactly one case: when the change makes a
screen that has already shipped wrong. That is not a new rule — it is the ADR
convention's own trigger, "a decision a later task could undo without knowing it
was a decision", applied here. Changing an entry no shipped screen relies on
needs the edit and nothing more.

### The arquitecto may block, and on what

Yes, and the bound matters more than the permission.

The arquitecto may refuse to release the implementador when a front task's spec
needs a state, a tone or an affordance that `docs/ui.md` does not define **and**
that a second screen will have to match. It may not block on a one-screen
choice: that is the implementador's to make and to record in the task, and an
arquitecto that blocks on it has converted a review into a veto.

A block must be actionable, which means the arquitecto names the missing
entries, not the deficiency. "UI definitions are incomplete" is not a block; "no
entry says what a partial region looks like, and Board, Debt and Pool all render
one" is.

There are two exits and the arquitecto chooses between them by the test above.
If the gap is a decision — something the console must be consistent about, and
any consistent answer will do — the arquitecto writes the entries itself, in its
own phase, and releases. If the gap is a ruling, it writes the question into its
handoff and stops the cycle, because C-1 says the human decides that one and no
role may edit the charter. The second exit is the expensive one and is meant to
be rare.

### The loop, role by role

1. A front task is opened naming the screens it touches.
2. The **arquitecto** reads `docs/ui.md` against those screens and either writes
   the missing cross-screen entries or stops with a named ruling question.
3. The **implementador** builds against the entries and may not invent one. If
   it finds a gap mid-build it uses the nearest existing entry and declares the
   gap in its handoff, the way it declares debt — a found condition with the
   entry it needed, not a request to stop.
4. The **revisor** checks the screens against `docs/ui.md`. A screen that
   contradicts an entry with no ADR for the change is a blocking finding; a gap
   the implementador declared is not.
5. The **auditor** files the declared gaps, which is how the next task's
   arquitecto learns what to write before it starts.

This is the iterative loop that declares UI and UX decisions before
implementation rather than during it, and it is deliberately built out of the
roles and the handoff shape the dispatcher already has, so that none of it needs
new machinery to be true.

## What `front/` is today

**Twelve screens.** `front/src/components/console/app-shell.tsx` lists eleven in
its nav — Board, Approvals, Pool, Tokens, Session logs, Live tail, Debt,
Learnings, Queue, Backlog, Role models — each with a single-key hotkey, plus the
drill-down `/tasks/$taskId` that is not in the nav. Four of them are
[`board.md`](board.md) Phase 2 and Phase 3 redrawn; the rest are new surface
this repo has never specified anywhere.

**Twenty-five exported functions in `client.ts`.** Sixteen reads, five writes,
one probe (`pingActionBackend`) and three local helpers (`isActionBackendDown`,
`setActionBackendDown`, `commandLine`). The five writes are `enqueueAction`,
`reauthContainer`, `decideApproval`, `setRoleModel` and `setBacklogCompleted`,
and each opens with the same `if (actionBackendDown) throw new ApiError(…, 503)`
— the mock's rehearsal of a backend that is not there, which is a fair sketch of
the only thing the write surface is certain to need.

Four module-level mutables hold what would be server state on a real backend:
`tokenState`, `approvalState`, `modelState` and `backlogState`, each lazily
seeded from `ops-fixtures`. They are the clearest measure of how much of tier 3
is missing: every one of them is a store nobody has built.

**Five of the sixteen reads already have a route.** `observability/api/app.py`
serves exactly `/api/tasks`, `/api/tasks/<task_id>`, `/api/accounts`,
`/api/events` and `/api/debt`, which is `listTasks`, `getTask`, `listAccounts`,
`listEvents` and `listDebt`. The other eleven have nothing behind them.

## The three tiers

| # | What | Discharges | Risk |
|---|---|---|---|
| 1 | The five served reads, wired, to board parity | Phases 2 and 3, in TypeScript | low — reads only |
| 2 | The reads the api can legitimately grow | none; new ground | low — reads only |
| 3 | A write surface: queue, worker, approvals | Phase 4 | privilege |

The order is not negotiable in one place only: tier 1 comes first because C-8
keeps the Jinja board as the tie-breaking reference until parity, and a
reference that is still being consulted while new screens are built around it
stops being able to break ties — ADR 14 makes that argument already.

## Tier 1 — the five reads that already exist

Five of the twelve screens are rendered entirely by the five routes that are
already up: Board (`/api/tasks`, `/api/accounts`), task detail
(`/api/tasks/<id>` and a filter over `/api/debt`), Debt (`/api/debt`), Live tail
(`/api/events`) and Pool (`/api/accounts`). Four of those five are the Jinja
board's four screens; Pool is the accounts strip promoted to a page, and it
rides along because it needs no route the other four do not.

### What changes, in the console

The bodies of five functions in `client.ts`, and the one thing that must not be
done while changing them: ADR 16 requires that they return the rows *and* the
warnings, not `(await res.json()).data`. The guards are ADR 7's, inherited —
`application/json` before `.json()`, because Flask answers its own 404 and 405
in HTML, then a shallow check that `data` is present and `warnings` is a list of
strings.

The bearer forward of ADR 15 is new code and has no file today: browser code
calls the console's own origin and the console's server half forwards with the
header, reading the token without a `VITE_` prefix so Vite cannot inline it into
the bundle, and refusing to start when no token is configured.

Three small deletions and one rename follow from ADR 17 and ADR 18:
`current_task` becomes `current_task_id` in `client.ts`, `rank` goes, and
`HEARTBEAT_STALE_S` goes in favour of the `lock_expired` the api already
computes. `pool.tsx`'s usage gauge loses its number until something persists it,
which ADR 18 calls a deliberate visible regression against the Lovable mock.

### The api's share

Tier 1 is not purely front work. ADR 17 admits three fields — `is_primary` on
`/api/accounts`, `depends_on` and the task body on `/api/tasks` — and ADR 18
puts three thresholds on `/api/accounts` beside the pool they describe. All six
are lines in routes that already iterate the right objects; none needs a new
source and none makes the api write anything.

T-011 served all six, and closed the two shapes these paragraphs left open.
`/api/accounts` answers nine keys per row — the five it had, plus `is_primary`
and the three thresholds as columns on every row, because the envelope has no
slot beside `data` for a pool-wide fact
([`decisions.md`](../decisions.md) ADR 20, narrowing ADR 18). `/api/tasks`
answers ten, growing `depends_on`, and `/api/tasks/<task_id>` answers those ten
plus `body`: the running record is the detail route's alone, so the list a
screen polls does not carry every card's whole history
([`decisions.md`](../decisions.md) ADR 21, narrowing ADR 17). The console must
therefore not treat the two task routes as interchangeable. `usage_pct`, `rank`
and the account `heartbeat` are still unserved, as ADR 17 and ADR 18 leave
them. What remains of this tier is the console half, in
[`docs/implementations/T-011.md`](../implementations/T-011.md) *What was not
done, and is not debt*.

### Parity, and what it retires

The parity C-8 measured `observability/board/`'s retirement against was
nameable, and ADR 19 said so — and it was reached on 2026-10-04, which is when
the directory was deleted (`docs/decisions.md` ADR 32). It was: the four Jinja
screens — index, task detail, debt, tail — rendered by the console against the
five real routes, with warnings shown
on every screen that lists rows, with no fixture behind any query those four
screens make, and with the bearer forward in place. The usage gauge is not in
it. Pool is not in it either, being a screen the Jinja board never had, though
it is expected to land in the same tier.

Parity is also where `front/src/lib/api/mock/fixtures.ts` stops being
load-bearing for those screens, and where `setActionBackendDown` — ADR 19's "a
control that fakes a failure is a fixture wearing a button" — is deleted rather
than wired.

### Decisions this tier's tasks make

Two were bound to the **first tier-1 task**, whichever screen it took. T-011
was that task and it took no screen — it built the api's share and nothing in
`front/` — so neither fired, which
[`docs/debt/T-011-D2.md`](../debt/T-011-D2.md) recorded. Both were then
discharged out of cycle on 2026-10-01, by the operator rather than by a task,
and they are kept here as settled rather than deleted because the reasoning is
what a later screen inherits:

- **How `front/` enters the repo.** ~~Bound to the first tier-1 task.~~ The
  operator ruled it in: `front/` is tracked, in one commit, with its `bun.lock`
  and its `.lovable/project.json`. It was a **ruling** and not a decision for
  exactly the reason stated here — `front/AGENTS.md` warns that rewriting
  published history breaks the Lovable sync, so the choice is about what the
  operator wants from a tool. The half C-8 deferred, whether `front/` keeps
  that round-trip, is still open; until it is closed the sync is treated as
  live, which makes "no force-push, no rebase or amend of a pushed commit" a
  constraint on `main` and not just on a side branch.
- **`docs/ui.md` is created.** ~~Bound to the first tier-1 task.~~ Done, with
  the entries the import already implies and no others — see *What
  `docs/ui.md` may decide* above for why reading them out of shipped code is
  not the speculative vocabulary this plan refuses. A screen's task still owns
  its own entries; the three cross-screen questions this tier left open were
  listed in that file under *Not decided here*, pointing back at the bullets
  below. All three are answered now, and that section records where each
  answer went.

T-012 took the Board screen, the Pool screen and the live tail in one task, so
all three bullets below were its to settle. It settled the third and left the
first two, which is what the by-hand review of T-012 found: a pointer bound to
a task that has already shipped is worse than an open question, because
`docs/ui.md` goes on naming an owner that will never come back to it. The
review closed both. They are kept here as settled rather than deleted because
the reasoning is what a later screen inherits.

Bound to the **Board screen's task**:

- **ADR 14's first open edge.** ~~Bound to the Board screen's task.~~ The state
  table makes the *region* the unit and `pool.tsx` answers `accounts.isLoading`
  with one line of text for the whole page. Whether Loading is per region or
  per page is a `docs/ui.md` entry by the test above — every screen has the
  state. Settled by the by-hand review of T-012, per page, as *Loading is per
  page, not per region* in that file: it was not a choice between two designs
  so much as every screen already agreeing and nobody having written it down.

Bound to the **Pool screen's task**:

- **The inert `Release` button.** ~~Bound to the Pool screen's task.~~ It had
  `disabled`, `title` and classes and no `onClick`. It is a tier-3 affordance
  on a tier-1 screen, so the choice was to remove it now and restore it with
  the write surface, or to render it as the named empty affordance ADR 19
  requires of an unbacked screen. Settled by the by-hand review of T-012:
  removed, because T-012 itself shipped the `docs/ui.md` entry *A region with
  no route says which route, and when*, which says the control goes rather than
  being disabled, and a rule a screen breaks on the same branch that wrote it
  is not a second defensible option. A `Banner` with tone `info` names tier 3
  and `dispatcher release-account --name <account>`, which is how an operator
  does it today.

Bound to the **Live tail's task**:

- **Whether the console's tail re-terminates the stream or consumes the board's
  frames.** ~~Bound to the live tail's task.~~ ADR 12 terminates the live tail
  on the board and makes its frames carry rendered HTML, which is a Jinja
  answer. A console that renders React cannot consume rendered HTML, and
  `/api/events` is a polled `GET` by `id > last`. This is an ADR — it has a
  consequence outside the console — and it is the one tier-1 decision with real
  design in it. Settled by T-012 as `docs/decisions.md` ADR 22: the console
  consumes `/api/events` and terminates nothing.

### Done when

The four parity screens read the real api, warnings render, the bearer never
reaches a browser, and `observability/board/` is deleted together with its
compose service and the tests that pin it. Not before: C-8 is explicit that the
board is the tie-breaking reference until exactly this.

**Met on 2026-10-04.** V0.6c walked the console's five reads and its bearer
forward against the running api, V0.6d the phase timeline and the Board's
banner, and the deletion followed in the same stroke — the directory, the
compose service in both files, and `tests/observability/test_board.py`
(`docs/decisions.md` ADR 32). What the tier does not own, and did not wait for,
is the detail screen's learnings region: the board had no such region, so
`/api/learnings` is tier 2's to grow.

## Tier 2 — the reads the api must grow

ADR 19 sorted five of the then-unbacked reads. `/api/phases` and
`/api/learnings` are routes this api may grow — both are `GET`s over files on a
`:ro` mount with the parser already in `dispatcher/`. Both are built: T-013 and
T-016. `/api/actions` and
`enqueueAction` never land there. `/api/threads` stays undecided as C-8 left it.

Seven reads arrived after that sweep and are sorted here the same way.

### The seven ADR 19 did not see

**`listBacklog`** has no source in this harness at all. The backlog that exists
is [`docs/ROADMAP.md`](../ROADMAP.md) and the task files, and neither is the
flat prioritised list with categories and a completed flag that
`ops-fixtures.ts` invents. Either the screen is dropped, or the ROADMAP becomes
a parsed source the way `docs/debt/README.md` already is, or something persists
a backlog. That choice is the Backlog task's, and until it is made the screen is
outside the parity set and says so.

**`listRoleModels`** and **`listModelOptions`** read configuration, which is a
legitimate `GET`. The fixture is not: `MODEL_OPTIONS` lists six models of which
four are not Claude and this harness cannot reach any of them, and the two it
can name use ids the dispatcher does not use. The real source is `config.yaml`'s
`default_model` and the per-role model the dispatcher passes with `--model`.

**`listSessions`** and **`listSessionLines`** are [`board.md`](board.md)
Phase 5's surface wearing a different name — a live execution timeline over
`--output-format stream-json`, the row that plan prices highest because it
touches the dispatcher's critical path. Nothing is served today and nothing is
written to a file a `:ro` reader could tail. These two are not tier 2 work; they
wait for Phase 5, and the screen says what is missing meanwhile. ADR 19's rule
applies unchanged: an operator must never be unable to tell a quiet harness from
an unwired console.

**`listTokens`** is a read the write-free api cannot serve. Credential state
lives inside each agent container, and reading it means reaching into another
container — a docker socket, which C-8 forbids this service and
`observability/api/app.py` is built without. It is privileged on the read path
alone, so it sits with tier 3 and not here.

**`listApprovals`** is tier 3's by the same logic: an approval has a lifecycle,
and a lifecycle has a store, and no store exists.

That leaves tier 2 as a short list: `/api/phases`, `/api/learnings`, and a
configuration read behind Role models. Everything else the fixtures imply is
either Phase 5's or tier 3's. **The first two are built** — T-013 and T-016 —
so the configuration read is all of tier 2 that is left, and it is the next
region in the console wired against a route that is not there yet.

### Decisions this tier's tasks make

Bound to the **Role models task**:

- **`run-phase` has no `--model` flag.** `dispatch run-task` passes `--model`
  and overrides the container default; `run-phase` does not, so a per-role model
  chosen in the console cannot reach a phase started that way. Either
  `run-phase` grows the flag or the screen is honest that it describes what
  `run-task` will do. The flag is dispatcher work, which makes this an ADR and
  possibly a dispatcher task before the screen is worth wiring.
- **Where the model list comes from**, given that the fixture's is foreign to
  this harness.

Bound to the **Learnings task** — T-016, and it found three open where this plan
saw none. All three are **decided**, in ADR 41 and ADR 42:

- **What a learning row is.** `dispatcher/learnings.py` parses the tree, but
  `Entry` is frontmatter-as-read plus four body sections, and nothing said which
  of that a route serves. ADR 41 part 5 sorts it field by field under ADR 17's
  rule: ten keys, with `body`, `path`, `project`, `reviewed`, `harness`,
  `refutes`, `refuted_by`, `fingerprint` and `orphaned_from` left out and the
  reason written on each. `LearningEntry`'s `id`, `trigger`, `body` and `retired`
  went with it (ADR 42).
- **Whether `docs/learnings/` is in this read.** It is not: the filed index in a
  project's checkout is a different object from the hive inbox — permanent,
  committed, per-project — and serving them as one list would merge two
  lifecycles into one table and need a second parser (ADR 41 part 3).
- **`LEARNING_TABLE_CAP` was not the console's layout decision.** ADR 18 kept it
  as one and that was true of a console with no route. It is
  `dispatcher/learnings.py:MAX_ROWS`, the cap on the table a phase's prompt
  carries, and this screen's subject is which rows get there — so it is deleted
  and arrives per row as `phase_table_cap` (ADR 42, narrowing ADR 18).

Bound to the **Phases task**:

- **What a phase row is.** `dispatcher/context_transfer.py` reads a structured
  handoff per task and role; `Phase` in `types.ts` is a guess at the same thing.
  ADR 17's sort applies field by field and the task does it.

### Done when

Task detail shows real phases, Learnings reads the real tree, and every screen
with no route behind it names what it is waiting for instead of drawing a
fixture.

**Reached**, for the two routes: T-013 for the phases, T-016 for the learnings
tree. The Role models configuration read is the one piece of this tier still
open, and it is a screen with its own undecided questions above rather than a
route waiting to be written. One caveat on the third clause, and it is a finding
rather than a gap: the detail's learnings region could not be made to show
"learnings handed to these phases" at all — that table is recomputed at every
dispatch and recorded nowhere — so it names the missing *record* and shows what
the task filed instead, which is `docs/ui.md` *A region with no route says which
route, and when*'s third sentence (ADR 42).

## Tier 3 — the write surface

### Why it is a different service

`observability/api/app.py` writes nothing, anywhere: every route is a `GET`,
every mount is `:ro`, and there is no docker socket —
`dispatcher/docker_exec.py` arrives as an import and is never called. That is
not an oversight to be corrected when the console needs a button. It is the
property that lets the api be reachable at all.

So every one of the five writes needs a different service, with its own
credential and its own audit trail, and [`board.md`](board.md)'s architecture
section states the shape: actions go through a queue, not a socket, and Phase 4
must not mount `/var/run/docker.sock` into a web process. A queue the console
writes to, a worker that drains it, and the dispatcher on the far side.

### Approvals is its first consumer, and it is also a charter question

Approvals is the screen most worth building and the one that cannot be built
quietly. C-4 says nothing spends an account's quota without a human authorizing
that run, and today the only place a human does that is this conversation, which
C-1 makes the only interactive one. A console that can approve a push is a
second place the human drives from, which is the same collision C-8 named for
the chat dock and declined to settle — "reconciling them is a later ruling, not
an implementation detail".

The ruling has to come before the screen, not after it. This plan's position is
only that the question is unavoidable here, and that the tier-3 opening task is
where it gets asked.

### Decisions this tier's tasks make

Bound to the **tier-3 opening task**:

- **Where the write service lives.** `observability/` stays Python and the api
  stays write-free; a Node app lives in its own top-level directory. Neither
  rule says where a Python queue-and-worker goes. An ADR.
- **The approvals ruling above.** A charter question; the arquitecto stops.

Bound to the **Approvals task**:

- **Where the blocking gate is enforced.** `approvals.tsx` disables Approve when
  `a.push?.gate_worst === "blocking"`, which is C-4 implemented in a browser.
  A client-side rule is a hint, not a gate.
- **Who parses the diff.** The screen splits a raw unified diff in the browser
  with `diff.split(/^(?=diff --git )/m)`. ADR 17's principle — a parser this
  service needs and does not have is one to make reachable in `dispatcher/` —
  points at the server, but a diff is not one of the dispatcher's parsers, so
  the task decides rather than inherits.

Bound to the **Tokens task**:

- **Whether a device code is ever rendered in the console.** `reauthContainer`
  mints a `user_code` and a `verification_url` and the screen shows them. A
  login code on a web page is a different object from a login code in a
  terminal, and this repo already treats login codes as things that do not get
  pasted around. A ruling, not an ADR.

Bound to the **Role models write**, if it happens here:

- **Whether the console may edit `config.yaml` at all**, which is a different
  privilege from enqueueing a dispatcher verb and should not be smuggled in
  behind the same queue by accident.

Bound to **no task yet**:

- **The chat dock.** C-8 left it open against C-1 and ADR 19 keeps it outside
  the parity set until a charter ruling puts it in. Building half of it first is
  how the ruling gets pre-empted, so nothing in `listThreads`' direction is
  tier 3's to start.

### Done when

There is no "done" to state here yet, because the ruling that opens the tier has
not been made. What can be said is the floor: no write reaches the dispatcher
through a socket mounted into a web process, and no screen offers a control that
is not wired to one.

## Deferred decisions, in one table

Every row is open on purpose, except the two struck through, which were
discharged out of cycle on 2026-10-01 and are kept so a reader of this table
does not go looking for them. The column that matters is the third: a decision
is made by the task that needs it, by the role named, at the time that task is
taken — not now.

| Question | Decided by | Kind |
|---|---|---|
| ~~How `front/` enters the repo~~ — ruled in, 2026-10-01; the Lovable round-trip is still open | the operator | charter |
| ~~First entries of `docs/ui.md`~~ — written 2026-10-01 from the import | the operator | `docs/ui.md` |
| Loading per region or per page (ADR 14) | Board screen task | `docs/ui.md` |
| The inert `Release` button | Pool screen task | task-local |
| Tail: re-terminate the stream, or consume ADR 12's frames | Live tail task | ADR |
| UI copy language, and `backlog.tsx`'s Spanish | first task touching copy | `docs/ui.md` |
| `run-phase` has no `--model` | Role models task | ADR + dispatcher |
| Where the model list comes from | Role models task | ADR |
| ~~What a phase row is~~ — answered 2026-10-03 by ADR 27, which sorts all twenty-two fields; ADR 28 takes the lanes it cost | Phases task | ADR |
| Whether a backlog has a source at all | Backlog task | ADR |
| Session logs: file on a `:ro` mount, or a tee | board Phase 5 | ADR |
| Persisting `usage_pct` with a stamp (ADR 18) | a dispatcher task | ADR |
| Approving from the console, against C-1 and C-4 | tier-3 opening task | charter |
| Where the write service lives | tier-3 opening task | ADR |
| Where the blocking gate is enforced | Approvals task | ADR |
| Who parses the diff | Approvals task | ADR |
| Whether a device code is rendered in a browser | Tokens task | charter |
| Whether the console may edit `config.yaml` | Role models write | ADR |
| The chat dock, against C-1 | no task yet | charter |

Two things that look like rows here are not decisions and are listed so nobody
defers them. `ops-fixtures.ts` assumes five accounts across `agent-cuenta1`
through `agent-cuenta5` and this harness has two containers; that is a fixture
that dies with ADR 19 and needs no ruling. And `sessions.tsx` polls with its own
`setInterval` instead of a query option, against `front/AGENTS.md`'s second
project rule; that is a defect, fixed by whichever task next touches the screen.

## What this plan does not decide

It does not renumber or reopen [`board.md`](board.md)'s phases, and where the
two disagree about what a screen should say before parity,
[`board.md`](board.md) and the Jinja board are right, because the board has run.

It does not write `docs/ui.md`. It establishes that the file exists, what sorts
into it, how it changes and who may block on it, and leaves every entry to the
task that needs one.

It does not price the tiers in days. [`board.md`](board.md)'s effort column was
written for Jinja and its own C-8 paragraph says to read those rows as sizing
the work rather than the hours; the same caution applies harder here, where a
draft of each screen already exists and the remaining work is almost entirely
the part a draft cannot do.
