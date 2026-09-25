# Plan: a board for this harness

Status: Phase 0 is specified below and queued as T-008, to be built by
this harness's own role cycle. Phases 1–6 are unstarted.

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
