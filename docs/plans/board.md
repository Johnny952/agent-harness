# Plan: a board for this harness

Status: Phase 0 is built and merged. It was specified below, dispatched as
T-008 on 2026-09-25, and landed as `LocalBoardClient` in
`dispatcher/vibe_kanban_client.py` behind a `local_board` block in
`config.yaml` — `docs/decisions.md` ADR 1 and
[`docs/implementations/T-008.md`](../implementations/T-008.md) are the record.
The run was cut off by a host reboot partway through the revisor's second
round and was resumed a phase at a time with `dispatch run-phase`: round 2
was committed as `1d4ca62` and APPROVED, the auditor closed the task, and
`agent/task/T-008` is merged. Two Phase 0 items stay open as debt —
[`T-008-D1`](../debt/T-008-D1.md), no lock around `set_status`'s
read-modify-write, and [`T-008-D2`](../debt/T-008-D2.md), a card that will
not parse is skipped without telling a caller the board is short. Phase 1
owns the second of those. See the T-008 rows in `docs/ROADMAP.md`.
Phase 1 is built and merged too. It was dispatched as T-009 on 2026-09-26 and
landed as `observability/api/`, five read-only `GET` endpoints behind the same
basic auth the dashboard uses — ADRs 2–5 and
[`docs/implementations/T-009.md`](../implementations/T-009.md) are the record.
Three Phase 1 items stay open as debt: [`T-009-D2`](../debt/T-009-D2.md), no
clamp on `/api/events?limit=`; [`T-009-D3`](../debt/T-009-D3.md), a debt cell
ending in inline code loses its closing backtick; and
[`T-009-D4`](../debt/T-009-D4.md), the events volume had to be mounted
read-write for a `mode=ro` reader. Phase 2 owns the second of those — it is the
phase that renders the cell.
Phase 2 is built and merged as well. It was dispatched as T-010 on 2026-09-27
and landed as `observability/board/`, four server-rendered screens over those
five endpoints — Flask and Jinja per [`docs/charter.md`](../charter.md) C-7, with
ADRs 6–10 and [`docs/implementations/T-010.md`](../implementations/T-010.md) as
the record. It closed `T-009-D3` and deleted `observability/dashboard/` and its
compose service. `T-008-D1` and `T-009-D2` stay open. Two sentences in the Phase
2 section below are now records of what was true when it was written rather than
of the present, and are deliberately not rewritten: its state table carries a
Loading row a server-rendered board cannot enter (ADR 6 argues that), and *What
this closes* says the dashboard's auth tests had already moved to
`observability/auth.py` in Phase 1 — they had not, and T-010 rehomed them by
hand into `tests/observability/test_auth.py`. Phases 3–6 have a row in the table
and no spec.

The dispatcher has no board. `NullKanbanClient` is what every run to date has
used, and the surface a human gets is the one this plan built: `/`,
`/tasks/<id>`, `/debt` and `/events` on `127.0.0.1:8790`, over the read API on
8789. Before Phase 2 it was `observability/dashboard/` — 43 lines of Flask
rendering the last 200 hook events as one table — which is what the sections
below mean whenever they speak of the dashboard in the present tense. This plan
says what replaced it, in what order, and what each phase is allowed to assume.

## Why not adopt one

The harness already carries a `KanbanClient` seam built for Vibe Kanban. That
dependency is gone — `docs/ROADMAP.md` records the 2026-09-25 re-run: the
remote service answers the SPA shell to every API path, `create_issue` fails
with `project_id is required`, and the one build said to restore local projects
was unpublished from npm two hours after release. The seam survives; the board
behind it does not.

Reaching for another generic board replaces one dependency with the same
mistake. `config.example.yaml` states the reason in one line, about the board
that is now dead:

> The dispatcher's `in_progress:<role>` maps through `in_progress`; the role
> does not reach the board.

A generic kanban has one *In Progress* column, so arquitecto, implementador,
revisor and auditor all flatten into it. The role is the only dimension this
harness has that a task tracker does not, and it is the one an operator watches.
Four more things no generic board models:

- **Quota is the scarce resource.** `IDLE`/`BUSY`/`PRE_COOLDOWN`/`COOLING_DOWN`
  and `rate_limited_at` decide which account may run next. A board that shows
  tasks and not accounts hides the thing that actually stops work.
- **`depends_on` is a graph**, not a label. A card that cannot start is
  different from a card nobody picked up.
- **Locks are heartbeats with a TTL.** "Assigned to" is wrong; the question is
  whether the holder is still alive.
- **The debt index and the learnings inbox are first-class.** `dispatcher/debt.py`
  keeps a `card` column per row; `dispatcher/learnings.py` keeps what a run
  discovered and what a later run disproved. Both are already written; neither
  has anywhere to be seen.

What to take from the products worth taking from:

| Source | Take | Leave |
|---|---|---|
| Vibe Kanban | a stable issue id carried on the task file (`kanban_issue_id`, already there) | the cloud, the project model, the MCP round-trip |
| conductor.build | worktree per agent, review-then-merge | nothing — `docker_exec.py` and `merge-task` already do both |
| Munder Difflin | *show the process, not just the state* | the byte-for-byte xterm.js pty: these agents are non-interactive `claude -p`, so `stream-json` gives semantic events that render better as a timeline than a terminal replay |

## Architecture decisions

**The source of truth does not move.** `.hive/tasks/*.md` are versioned beside
the code and read by `dispatcher/context_transfer.py`. Anything the board keeps
is an index, a cache or a history — events, executions, streams. A board that
owns dispatch state is a board whose outage stops the harness, and every call
site in `dispatcher.py` is written the other way round: a board that cannot be
reached costs one warning and nothing else.

**The frontmatter parser stays in Python.** A TypeScript re-implementation of
`TaskFile` forks the source of truth, and the fork is silent until the two
disagree. The UI reads the parser's output over HTTP; it does not re-parse.

**SQLite is enough until it is not.** `observability/collector/schema.sql` is
already one table and one mounted volume. Postgres buys concurrent writers and
analytics, and this has one writer.

**Actions go through a queue, not a socket.** Phase 4 must not mount
`/var/run/docker.sock` into a web process: that is root on the host for anyone
who reaches the page. A row in a table and a worker that runs `dispatch` is the
same feature with a blast radius.

**Node is a real cost.** Next.js adds a toolchain, a lockfile and a CVE surface
to a repo that is Python and Docker. It pays for itself at Phase 4 and not
before. If phases 4–6 are not going to happen, Flask plus htmx delivers phases
1–3 in about a day with no new toolchain. Ruled in
[`docs/charter.md`](../charter.md) C-7, which keeps the reasoning and moves the
trigger from Phase 4 to Phase 5: Phase 4's actions are a form post and a queue,
which Jinja serves.

## Phases

| # | What | Effort | Risk |
|---|---|---|---|
| 0 | `LocalBoardClient` behind the existing seam | ~½ day | none — new code behind an unchanged interface |
| 1 | Read API in Python: `/api/tasks`, `/api/accounts`, `/api/events`, `/api/debt` | ~½ day | none — reads only |
| 2 | Read-only board over those endpoints | ½ day | none — C-7 rules it Flask and Jinja |
| 3 | Live tail: SSE over `events` by `id > last` | ½–1 day | low |
| 4 | Actions from the UI: `run-task`, `merge-task`, `cleanup-task` | 2–3 days | privilege — queue + worker, never a mounted socket |
| 5 | Live execution timeline: `--output-format stream-json`, incremental `Popen` | 3–5 days | **highest** — touches the dispatcher's critical path |
| 6 | Per-worktree diff and merge review | 2–3 days | medium |

Phases 0–3 are the useful subtotal: 2–2½ days for a board that shows the truth.
Phases 4–6 are another 7–11 days and turn it into a control surface. Nothing
after Phase 3 should start before Phase 3 has run against a real dispatch.

## Phase 0 — `LocalBoardClient`

The whole phase is one class beside `NullKanbanClient` and `VibeKanbanClient`
in `dispatcher/vibe_kanban_client.py`, plus its tests. Nothing else changes
shape: `dispatcher/dispatcher.py` calls the seam unconditionally and must not
learn which implementation it got.

### Why it comes first

Two prioritized items in `docs/ROADMAP.md` wait on a working `create_issue` and
on nothing else — project memory (debt cards) and task profiles (epic
decomposition). Neither needs a UI. A board that persists issues to local
storage unblocks both today, and gives phases 1–3 something to read that is not
a mock.

### The interface, verbatim

```python
class LocalBoardClient:
    enabled = True
    def list_issues(self, **filters: object) -> list[KanbanIssue]: ...
    def get_issue(self, issue_id: str) -> KanbanIssue | None: ...
    def create_issue(self, title: str, description: str | None = None) -> str: ...
    def set_status(self, issue_id: str, status: str) -> None: ...
```

`KanbanIssue` is unchanged: `issue_id, title, status=None, simple_id=None`.

### Required behaviour

- **One JSON document per issue**, written atomically the way
  `dispatcher/state_machine.py` writes account state — `tempfile` in the target
  directory, then `os.replace`. A half-written card is not a state this harness
  has to reason about.
- **`issue_id` is a `uuid4`.** Not cosmetic: `dispatcher/cli.py` rejects a
  `--kanban-issue-id` that does not parse as a uuid, on the grounds that every
  id in the MCP schema was one. Minting short ids would mean relaxing that
  check, which is a second change disguised as a first. `simple_id` stays
  `None` in this phase; a display handle is the UI's problem and it can number
  by creation time.
- **`create_issue` returns the id and stores `title`, `description`, `status`
  and a creation timestamp.** `status` starts as the dispatcher's own
  vocabulary, not a board's column name — there is no board to rename them.
  Storing the description is what makes the round-trip V2.5 could never verify
  verifiable here.
- **`set_status` on an unknown id raises.** The Vibe Kanban client warns and
  carries on because the board was remote and could legitimately be out of
  sync. Local storage that has forgotten a card the task file still points at
  is a bug, and every call site already wraps this in `try/except` and logs —
  so raising surfaces it without threatening a run.
- **`list_issues(**filters)` matches stored fields by equality, and rejects an
  unknown filter key.** Returning every issue when asked to filter by something
  that does not exist is the failure mode that costs the caller silently.
- **`get_issue` returns `None` for an id with no file**, never raises. It is a
  lookup.
- The directory is created on first write. An empty or missing directory means
  a board with no issues, not an error.

### Configuration

A new optional top-level block in `config.yaml`, documented in
`config.example.yaml` alongside the existing commented `vibe_kanban` block:

```yaml
local_board:
  dir: /state/board
```

`dispatcher/cli.py:_kanban()` becomes a three-way choice, in this order:
`vibe_kanban` → `VibeKanbanClient`; `local_board` → `LocalBoardClient`;
neither → `NullKanbanClient`. Configuring both is a config error with a
message that says which one to drop, not a silent precedence rule.

Opt-in in this phase. A harness that has been running without cards should not
start writing them because it was upgraded; Phase 2 is where the default flips,
because that is when there is something to look at.

### Out of scope for Phase 0

No HTTP, no schema migration, no change to `dispatcher.py`, no change to the
dashboard, no deletion of `VibeKanbanClient`. The seam is what made this phase
half a day; removing the dead implementation would be a refactor with no user.

### Done when

`python3 -m pytest` is green, including new tests that cover: the round-trip of
a created issue through `get_issue`; `set_status` moving a stored status;
`set_status` on an unknown id raising; `list_issues` filtering and rejecting an
unknown key; two clients over the same directory seeing each other's issues;
and the interface-parity test in `tests/dispatcher/test_vibe_kanban_client.py`
extended so `LocalBoardClient` is held to the same public surface as the other
two.

## Phase 1 — a read API in Python

Phase 0 gave the harness somewhere to keep cards. This phase gives a reader
somewhere to ask what is true, over HTTP, in the language the parsers are
already written in. Nothing here writes: every endpoint is a `GET`, every
mount is `:ro`, and the phase is done when a board could be written against it
without a mock.

### Why it comes next

Phase 2 is a rendering problem only if the data arrives shaped. The
architecture decision above — the frontmatter parser stays in Python — is what
this phase implements. Four readers already exist and not one is reachable
from outside the container that holds it: `dispatcher/context_transfer.py`
turns a task file into a `TaskFile`, `dispatcher/state_machine.py` answers what
an account is doing, `observability/collector/db.py` lists events, and
`dispatcher/debt.py` parses the debt index's table. The phase is an HTTP
surface over code that is written and tested, which is why it is half a day
and no risk.

It pays before Phase 2 exists, too: `curl` against a running harness answers
"which account is on which task, and since when" without a container shell and
without reading root-owned JSON through a throwaway container.

### Where it runs

A new service, `observability/api/`, beside the collector and the dashboard —
not inside the dashboard. The dashboard is what Phase 2 deletes, so endpoints
put there would move twice. It also mounts nothing but the events volume
today, and this phase needs the task files, the dispatcher's state directory
and a project checkout: hanging those off the page a password protects widens
what one auth bug reaches, for no gain.

Published on `127.0.0.1:8789` like every other port in this compose file, and
on `ia_harness_net` so Phase 2 reaches it by service name.

### The endpoints, verbatim

```
GET /api/tasks                                list of Task
GET /api/tasks/<task_id>                      one Task, or 404
GET /api/accounts                             list of Account
GET /api/events?limit=&source_app=&since=     list of Event
GET /api/debt?project=<slug>                  list of DebtRow
```

Every one answers with the same envelope, and that envelope is this phase's
one invention:

```json
{"data": [], "warnings": []}
```

`warnings` names what the endpoint could not read — a task file that will not
parse, a card whose JSON is truncated, a debt index that is not there. See
*What this closes*.

The shapes:

- **Task** — `task_id`, `status`, `owner`, `heartbeat`, `description`,
  `kanban_issue_id`, `resolved_debt`, and `card`: the board document that
  `kanban_issue_id` points at, or `null` when there is no board or no such
  card. The fields are `TaskFile`'s. The endpoint invents none.
- **Account** — `name` and `container` from the config; `state`,
  `current_task` and `rate_limited_at` from `state_dir`.
- **Event** — what `db.list_events` already returns: `id`, `source_app`,
  `event_type`, `payload` parsed rather than a string, `created_at`.
- **DebtRow** — `id`, `what`, `where`, `fix`, `card`, and `resolved`. The last
  is best-effort: the index marks a resolved entry in place by opening its
  **what** cell with a bolded `Resolved`, which is a convention in prose and
  not a schema, so the flag is offered and the row is never hidden by it.

### Required behaviour

- **Nothing is dropped silently.** One unreadable file answers with the others
  and a warning naming it; it never empties the list and never 500s. This is
  the rule the rest of the phase follows from, and the reason the envelope is
  not a bare array.
- **`since` is an id, not a time.** `db.list_events` orders by `id DESC` under
  a limit; Phase 3 tails by `id > last`. Building that read here means the SSE
  phase is a loop around an endpoint that already exists. `since` and `limit`
  compose: the newest `limit` events with an id above `since`.
- **The events database is opened read-only and its schema is never created.**
  `db.init_db` runs `CREATE TABLE` and the volume is mounted `:ro`, so a
  schema call is a 500 on the first request of a cold start. Connect through
  `file:<path>?mode=ro`, and answer a database that is not there with an empty
  list and a warning — a harness that has never run has no events, and that is
  not an error.
- **An unknown query parameter is a 400**, on Phase 0's reasoning about
  `list_issues`: a filter that silently does nothing costs the caller more
  than no filter at all.
- **`project` is optional where `projects_root` holds exactly one checkout**
  and required where it holds several; naming one that is not there is a 404,
  not an empty list.
- **The config is the dispatcher's own**, loaded with `dispatcher/config.py`
  from a read-only mount, and this phase adds no keys to it. That works only
  because the mounts reproduce the paths the dispatcher already uses — see
  *Configuration*.
- **One parser per fact.** The API imports the dispatcher's readers; it does
  not re-read a file format. Two of them need a path-based entry point they do
  not have today: `debt.read_index` reads through `docker exec`, and the row
  parsing behind it is private. Add `debt.index_rows(text) -> list[dict]` and
  put both callers through it.
- **Auth is the dashboard's, factored out.** `check_auth` and `requires_auth`
  move from `observability/dashboard/app.py` to `observability/auth.py` and
  both apps import them: the timing-safe comparison and the
  `auth.type != "basic"` guard are worth having once rather than twice. The
  dashboard's behaviour does not change, which is what its existing tests are
  there to hold.

### Configuration

No new keys. The service mounts the same host directories the dispatcher and
the agents mount, at the same container paths, so `state_dir: /state`,
`hive_tasks_dir: /data/.hive/tasks` and `projects_root: /data/projects` are
true in this container too:

```yaml
  api:
    build:
      context: ../..
      dockerfile: observability/api/Dockerfile
    restart: unless-stopped
    ports:
      - "127.0.0.1:8789:8789"
    volumes:
      - observability_data:/events
      - ../../dispatcher_state:/state:ro
      - ../../.hive:/data/.hive:ro
      - ../../.data/projects:/data/projects:ro
      - ../../config.yaml:/app/config.yaml:ro
    environment:
      - COLLECTOR_DB_PATH=/events/events.db
      - DASHBOARD_USERNAME=${DASHBOARD_USERNAME}
      - DASHBOARD_PASSWORD_HASH=${DASHBOARD_PASSWORD_HASH}
    depends_on:
      - collector
    networks:
      - ia_harness_net
```

The events volume lands somewhere other than `/data` on purpose: `/data` is
where the two bind mounts live in the dispatcher's and the agents' layout, and
nesting the volume under them to keep `COLLECTOR_DB_PATH` byte-identical would
trade a legible mount list for an environment variable.

It is also the one mount here without `:ro`, which this block specified until
2026-09-26 and Phase 1 implemented. It could not work: the collector keeps
`events.db` in WAL mode and a read-only sqlite open still creates the `-shm`
sidecar beside the file, so `/api/events` answered `[]` plus a warning on every
request. Phase 1 declared it rather than overriding its own plan — debt
[T-009-D4](../debt/T-009-D4.md), fixed out of cycle once the by-hand check
against the running stack confirmed the mount was the cause. Everything else
here stays `:ro`, and `tests/integration/test_compose_invariants.py` holds that
as `API_WRITABLE_MOUNTS = {"/events"}`: a second writable mount fails the suite.
The API itself still opens with `mode=ro` and never calls `init_db`, so what the
mount grants is the sidecar, not the rows.

The image copies `dispatcher/` as well as `observability/`, which is the price
of one parser per fact: this service rebuilds when the dispatcher's readers
change. It gets no docker socket — `dispatcher/docker_exec.py` comes along as
an import and is never called.

The container runs as root, like the collector and the dashboard, and here
that is load-bearing rather than unexamined: `dispatcher_state/*.json` are
written root-owned mode 600, so a non-root `USER` in this image is a change to
who owns that directory first.

### What this closes

[`T-008-D2`](../debt/T-008-D2.md) — a card that will not parse is skipped with
a warning nobody reads, and `list_issues` hands back a board that is quietly
short. The envelope is the fix: `LocalBoardClient` gains
`unreadable() -> list[str]`, a scan that names the files that do not parse,
and `/api/tasks` puts them in `warnings`. The shared interface does not move —
the API builds its own client from the config and knows which one it got — and
nothing is renamed or quarantined, because that is a write and this phase has
none. The entry is then marked resolved in place in `docs/debt/README.md`, per
that index's own rule.

[`T-008-D1`](../debt/T-008-D1.md) is not closed here and is not made worse:
this phase adds a reader, and the race it describes is between writers.

### Out of scope for Phase 1

No writes of any kind, no SSE — that is Phase 3, and it is a loop around
`/api/events?since=` — no board, no change to what the dashboard renders, no
schema migration, and no deletion of the dashboard. Phase 2 replaces it, and
gets to remove it once there is something better to look at.

### Done when

`python3 -m pytest` is green, including new tests that cover: every endpoint
against a temporary-directory fixture rather than a running harness; a task
file that does not parse appearing in `warnings` while the other tasks still
come back; a card that does not parse doing the same; `/api/events?since=`
returning only ids above it, under `limit`; an events database that does not
exist answering empty rather than 500; an unknown query parameter answering
400; every endpoint answering 401 without credentials; and the dashboard's
existing auth tests passing unchanged against the extracted module.

And, by hand once, against a harness that has run a task with `local_board`
configured: `curl -u` on `/api/tasks` returns that task with `card` populated.

## Phase 2 — a read-only board

### Why it comes next

Phase 1 made the harness readable over HTTP and nothing reads it. Every fact
an operator chases today is behind a `docker exec`, a root-owned
`dispatcher_state/*.json` or a `.hive/` file that needs a container to open;
the api answers all of it on one port and nobody has opened it. This phase is
the first thing a human looks at instead of a shell prompt.

It is read-only on purpose. Every screen is a `GET` of an endpoint that
already exists, and the phase adds two fields to the api and no new source of
truth. That keeps the parse count at one: the dispatcher's modules parse the
files, the api serves the rows, the board renders them. A board with its own
frontmatter parser is a second answer to the same question, and the first time
the two disagree the operator has to work out which one is lying.

What it replaces is `observability/dashboard/`: 43 lines rendering the last
200 hook events as a three-column table. That is the harness's exhaust and not
its state — it cannot say which accounts exist, which task holds a lock, or
whether a run died three hours ago.

### The toolchain, and why it is not this spec's to choose

This spec was written with the toolchain left open, because what was being
committed to was maintenance — a lockfile, a build step and a CVE surface in a
repo that is otherwise Python and Docker — and that is a human's call, not a
phase's. It was exactly the shape of block the arquitecto was given in
[`docs/charter.md`](../charter.md) and in its role prompt: a decision nobody
has made, in front of a task that cannot be implemented two ways at once.
Dispatched before it was answered, this phase would have blocked on its first
phase.

**[C-7](../charter.md) answers it: Flask and Jinja.** Node is revisited at
Phase 5, where a live execution timeline is what a client framework buys;
Phase 4's actions are a form post and a queue. Either way the trigger is after
Phase 3, and nothing after Phase 3 starts before Phase 3 has run against a real
dispatch.

The ruling reaches this spec in two paragraphs and nowhere else. The screens,
the states, the warnings and the exclusions never depended on it; only *Where
it runs* and *Configuration* name a toolchain at all. That is the property that
makes C-7 cheap to reverse, and it is why the section is still here instead of
deleted — a ruling is reversible, and the reasoning has to outlive it.

### Where it runs

`observability/board/`, beside `observability/api/`, on the same
`python:3.11-slim` base: Jinja templates, no `package.json` and no lockfile.

Its own service in `docker/compose/docker-compose.yml`, published on
`127.0.0.1:8790` and joined to `ia_harness_net`, where it reaches Phase 1 as
`http://api:8789`. The browser never talks to the api: every fetch is
server-side, which is the property that keeps the api's credential inside a
container instead of in a page the operator can view-source. The human
authenticates to the board; the board authenticates to the api.

The board is an HTTP client of the api and nothing else — no volumes, no
`.hive/`, no events database, no socket. If C-7 is ever reversed, what moves is
this paragraph: a Node app goes in a new top-level `board/` rather than under
`observability/`, which is Python, and a `package.json` buried inside it would
muddle that border for every tool and every role that walks the repo.

### The screens, verbatim

```
GET /                  the pool and the work: an accounts strip over a tasks table
GET /tasks/<task_id>   one task: its fields, its card, and the events around it
GET /debt              the debt index for the configured project
GET /events            the event log, newest first, filterable by source_app
```

Four screens over five endpoints. `/` calls `/api/accounts` and `/api/tasks`;
`/tasks/<id>` calls `/api/tasks/<id>`, and `/api/accounts` and `/api/events`
for the events half. No other route exists — no settings, no login page beyond
the basic-auth prompt, no "about".

`/` is the screen a human leaves open. It answers, without scrolling: which
accounts exist and what state each is in, which tasks are in flight and who
owns them, and whether any lock is stale. Anything that answers none of those
three does not go above the fold.

**`/tasks/<id>` cannot show a task's events, and says so.** The collector
records per agent, not per task: an event carries `source_app`, which is the
account's *container* (`agent-cuenta1`), while a task's `owner` is the account
*name* (`cuenta1`). The board maps one to the other through `/api/accounts`
and labels the list for what it is — the events from the account that owns
this task, in the window it has held it, not the events of this task. A
per-task timeline is Phase 5's, and inventing one here out of a field that
does not exist is the kind of thing a screen spec is written to prevent.

### The four states, verbatim

Every region of every screen is in exactly one of these. The region is the
unit and not the page: `/` calls two endpoints and they fail independently.

| State | The region is in it when | What it renders |
|---|---|---|
| **Loading** | its fetch has not resolved | Its heading, and a skeleton the height of the table that is coming. Never a spinner over the whole page: one slow endpoint must not blank the regions that already answered. |
| **Empty** | a 200 whose `data` is `[]` | A sentence naming what would put a row there — "no tasks yet; `dispatch run-task` creates one" — in the region's normal type. Empty is never styled as a failure. Phase 1 deliberately answers a harness that has never run with `[]`, and a board that paints that red teaches the operator to distrust the colour for the case that matters. |
| **Stale** | always, one second after it rendered | Two different facts, both shown. See below. |
| **Error** | the fetch raised, the status is not 200, or the body is not the envelope | Which endpoint failed and with what status — `GET /api/accounts → 503` — plus the api's own `{"error": …}` string when there is one, and a retry that re-navigates. Every other region stays. A page that fails because one of its calls did is a page that hides three working answers. |

A fifth case that is not a state: a **partial** region, a 200 whose `data` has
rows and whose `warnings` is not empty. It renders as data, with the warnings
above it — see *Where warnings go*.

### Two kinds of stale

The board must not conflate them: on one screen they look alike and mean
opposite things.

**The page is stale** the moment it renders. Phase 2 has no push and no poll,
so every screen is a snapshot. Every page therefore carries `as of <HH:MM:SS>`
in a fixed place, next to a control that re-navigates. Without it a
five-minute-old page and a dead harness are the same picture.

**A lock is stale** when a task is `in_progress` and its `heartbeat` is older
than the dispatcher's `heartbeat_ttl_seconds` — a run that died holding the
file. That is the most useful single thing this board can show, and it is the
one derived fact the board must not derive:
`dispatcher/context_transfer.py:is_lock_expired` already computes it, tested,
and the TTL is config the api holds and the browser does not. **The api gains
`lock_expired: bool | null` on the task row** — null when `heartbeat` is null —
and the board renders the flag. One parser per fact applies to derived facts
too.

A page stamped 14:02 showing a heartbeat from 13:57 is a healthy harness. The
same screen with no page stamp is indistinguishable from a crashed one, which
is why the first line of this section is a requirement and not a nicety.

### Where warnings go

**Once per region, above its rows, never per row and never as a toast.** A
Phase 1 warning names a *file*, and the file it names is precisely the row
that is missing — an unreadable task file, a card whose JSON is truncated, an
events database that is not there. There is by construction no row to hang it
on.

They render verbatim. Phase 1's strings are already operator-grade
(`<path>: unreadable task file: <exc>`), and a board that prettifies a path it
cannot verify makes the one artifact the operator needs to paste into a shell
harder to paste.

`warnings: []` renders nothing at all: no empty container, no green tick, no
reserved space that makes the table jump when one appears. The absence of a
warning is not worth a pixel. Its arrival should be a change in the page, not
a change in a badge.

A screen calling three endpoints has three possible places for one, each
attached to the region whose data it qualifies. `/tasks/<id>` puts the events
warning over the events list and the task warning over the task fields, never
merged into one list at the top — merging loses which call came back short.

### Where a UI decision lives

This spec answers the questions the arquitecto would otherwise block this
phase on. The ones it does not answer need somewhere to land, or the block has
no exit:

- A **ruling** — the toolchain question C-7 answered, or anything a human wants
  settled and not re-argued — goes in [`docs/charter.md`](../charter.md),
  which no role may edit.
- A **decision an agent makes while building** — a component boundary, a date
  format, whether the accounts strip wraps — is an ADR in
  [`docs/decisions.md`](../decisions.md): numbered, appended, supersedable.
- A **screen and its states** go in the phase that builds it, here.

There is no separate `docs/ui.md` and there should not be one until a second
surface exists. A third file whose border with those two is "it is about
pixels" is a file every role has to guess about.

### Required behaviour

- **Every fetch is server-side.** The api credential lives in the board's
  environment and never reaches a browser. The page ships no `fetch` of the
  api: a `<script>` that dials it from the browser is a review finding, not a
  style preference.
- **Guard the content type before parsing.** ADR 5 pins that Flask's own 404
  and 405 are HTML and bypass the envelope. A client that assumes JSON on
  every response crashes on a mistyped path instead of showing its Error
  state. Anything that is not `application/json` is the Error state, with the
  status.
- **Validate the envelope shallowly, once, at the boundary.** `data` and
  `warnings` present, `warnings` a list of strings, each row carrying the keys
  the screen reads. A shape that fails is that region's Error state and not a
  `jinja2.UndefinedError` halfway down a half-rendered page.
- **Render, do not re-derive.** No frontmatter parser, no markdown-table
  parser, no status vocabulary restated in the template. There are two
  vocabularies and the api owns both: a card is `pending`/`done`/`blocked`, a
  task file is `pending`/`in_progress`. The board maps them to labels and
  stops.
- **A null `card` is three facts and the board separates two of them.** No
  `kanban_issue_id` means the task was dispatched without a board — say that
  plainly. A `kanban_issue_id` with a null `card` means the board does not
  hold it, which is an inconsistency worth a visible mark: it is the symptom
  [`T-008-D2`](../debt/T-008-D2.md) describes.
- **Timestamps are relative in the cell, absolute in the `title`.** `4m ago`
  answers the question the operator has; the ISO string answers the one they
  have next, in a log.
- **Nothing on the page writes.** No control that does not navigate, no
  disabled button previewing Phase 4. A greyed-out "retry" is a lie about what
  exists.
- **It is a table, so use a table.** Real `<table>`, real headers, focus order
  following reading order, `prefers-color-scheme` respected and nothing else
  themed. This gets opened at 2am on a laptop: the floor is legible,
  keyboard-navigable, and readable at 320px with no horizontal scrollbar.
- **Every events call sets `limit` explicitly.** The board asks for what it
  renders rather than relying on `DEFAULT_EVENT_LIMIT` staying 100.
  [`T-009-D2`](../debt/T-009-D2.md) stays open: a hand-typed `?limit=` against
  the api is still unbounded, and Phase 3 picks the clamp alongside the tail
  window.

### Configuration

One service, added to `docker/compose/docker-compose.yml`:

```yaml
  board:
    build:
      context: ../..
      dockerfile: observability/board/Dockerfile
    ports:
      - "127.0.0.1:8790:8790"
    environment:
      API_BASE_URL: http://api:8789
      API_TOKEN: ${API_TOKEN}
      BOARD_USERNAME: ${DASHBOARD_USERNAME}
      BOARD_PASSWORD_HASH: ${DASHBOARD_PASSWORD_HASH}
    networks:
      - ia_harness_net
```

**No volumes.** The board reads nothing from disk: no `.hive/`, no
`dispatcher_state/`, no events database, no docker socket. That is the whole
reason this phase is cheap to reason about, so it is what the
compose-invariants test pins.

`API_TOKEN` is the one thing this phase adds to Phase 1's auth surface, and it
exists because every alternative is worse. The api checks
`DASHBOARD_PASSWORD_HASH`; a service authenticating with basic auth needs the
plaintext, and putting the plaintext in `.env` beside the hash makes the hash
decorative. So `observability/auth.py` gains a second accepted credential — a
token read from the environment and compared with `secrets.compare_digest`,
alongside the existing basic-auth path. The token is generated by
`scripts/configure.sh` into `docker/compose/.env`, beside
`DASHBOARD_PASSWORD_HASH` and written by the same block, because that file is
already the one thing compose reads and a second copy elsewhere is a second
thing to rotate. The human's path is unchanged: basic auth, same username,
same hash.

The two environment variables keep their `DASHBOARD_` names even though the
dashboard is what this phase deletes. Renaming them means editing a `.env`
that exists on a running host, `scripts/configure.sh`, the api, the coolify
compose file and every doc that names them, to buy a tidier spelling.

### What this closes

[`T-009-D3`](../debt/T-009-D3.md), whose stated trigger is "Phase 2 rendering
`/api/debt`". `dispatcher/debt.py:_rows` strips one backtick from each end of
a cell rather than a matched pair, so a cell that ends in inline code renders
with its closing backtick eaten. `/debt` is the first screen that shows those
cells to a human. Fix it here — matched pair only — and re-run
`tests/dispatcher/test_debt.py` to confirm no fingerprint moved.

It also deletes `observability/dashboard/` and its compose service: `/events`
supersedes it, and Phase 1 already said Phase 2 gets to remove it once there
is something better to look at. Its auth tests do not go with it — they moved
to the shared `observability/auth.py` in Phase 1, and now cover the token path
as well.

It closes neither [`T-008-D1`](../debt/T-008-D1.md) — a reader cannot fix a
writer's race — nor [`T-009-D2`](../debt/T-009-D2.md) nor
[`T-009-D4`](../debt/T-009-D4.md).

### Out of scope for Phase 2

No push of any kind: no SSE, no websocket, no polling interval. A refresh is a
navigation a human asks for, and Phase 3 is what makes the page move on its
own — a poll built here is a poll deleted there. No write, no action, no form:
Phase 4. No execution timeline and no diff view: Phases 5 and 6. No chart — a
harness with two accounts and one task at a time has nothing to plot. No
state-management library, no component library, no design system, no
dark-mode toggle. No change to what the collector stores and no schema
migration. No auth change beyond `API_TOKEN`.

### Done when

`python3 -m pytest` is green, including new tests that cover: the board
service mounting nothing and getting no docker socket, in
`tests/integration/test_compose_invariants.py`; `lock_expired` on
`/api/tasks` and `/api/tasks/<id>`, with a live heartbeat, an expired one and
a null one; the token path in `observability/auth.py`, accepted, rejected, and
not accepted in place of the password; and `tests/dispatcher/test_debt.py`
unchanged in its fingerprints after the T-009-D3 fix.

The board's own tests run in the same `python3 -m pytest` run, against
recorded fixtures of each endpoint rather than a live api, and cover the four
states per screen: a 200 with rows, a 200 with `[]`, a 200 with rows and a
warning, and a non-200 — plus one HTML 404 body, which is the case the
content-type guard exists for.

The image builds from a clean checkout and serves `/` inside it. There is no
lockfile to commit and no type check to pass: `flask` and `requests` are
already `pyproject.toml` dependencies and Jinja arrives with Flask, so this
phase adds no dependency to pin. That is C-7's saving, and it is the thing to
check has actually been taken — a `package.json` anywhere under
`observability/board/` means the phase was built against the wrong ruling.

And, by hand once against the running stack: open `http://127.0.0.1:8790`, see
both accounts with their state and T-009 in the tasks table with its card;
then stop the api container and reload, and see the Error state name the
endpoint while the page keeps everything else.
