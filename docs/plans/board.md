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
Phase 1 is specified below and unstarted; phases 2–6 have a row in the
table and no spec.

The dispatcher has no board. `NullKanbanClient` is what every run to date has
used, and the only surface a human gets is `observability/dashboard/`: 67 lines
of Flask rendering the last 200 hook events as one table. This plan says what
replaces it, in what order, and what each phase is allowed to assume.

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
1–3 in about a day with no new toolchain.

## Phases

| # | What | Effort | Risk |
|---|---|---|---|
| 0 | `LocalBoardClient` behind the existing seam | ~½ day | none — new code behind an unchanged interface |
| 1 | Read API in Python: `/api/tasks`, `/api/accounts`, `/api/events`, `/api/debt` | ~½ day | none — reads only |
| 2 | Next.js read-only board (App Router, Server Components) | 1.5–2 days | new toolchain |
| 3 | Live tail: SSE over `events` by `id > last` | ½–1 day | low |
| 4 | Actions from the UI: `run-task`, `merge-task`, `cleanup-task` | 2–3 days | privilege — queue + worker, never a mounted socket |
| 5 | Live execution timeline: `--output-format stream-json`, incremental `Popen` | 3–5 days | **highest** — touches the dispatcher's critical path |
| 6 | Per-worktree diff and merge review | 2–3 days | medium |

Phases 0–3 are the useful subtotal: 3–4 days for a board that shows the truth.
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
mount is `:ro`, and the phase is done when a Next.js page could be written
against it without a mock.

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
`/api/events?since=` — no Next.js, no change to what the dashboard renders, no
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
