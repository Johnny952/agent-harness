---
status: building
approved: 2026-10-11
built: null
supersedes: [board.md]
---

# Board service — the harness's own board, kept by agents over MCP

**Status:** design, approved by the user on 2026-10-11. Step 1 of *How it
would be built* is built, by T-026 (`docs/implementations/T-026.md`) and
T-027 (`docs/implementations/T-027.md`): the task module
`dispatcher/tasks.py`, the description in its own `description.md`,
`dispatch migrate`, the phase prompt on *Goal* and *Done when* plus the
file's path, and the dispatcher's task-file writes on the module, whose
writer refuses an unmigrated file. Step 2 is built by T-028
(`docs/implementations/T-028.md`, `docs/decisions.md` ADR 59): the board
seam is removed. The first half of step 3 is built by T-029
(`docs/implementations/T-029.md`): task 2 of `docs/plans/task-split.md`
section 8, the epic format, in `dispatcher/tasks.py` (`file_split`,
`mark_merged`, `unmet_dependencies`), and `mark_blocked`, the writer for a
task that stopped blocked. The second half of step 3 is built by T-030
(`docs/implementations/T-030.md`): task 3 of that section, which has the
dispatcher call them. Both merge paths record `merged` on the task and close
an epic on its last merged sub-task, `open_cycle` refuses a task with an
unmerged dependency or a malformed hand-written epic, and a cycle that stops
blocked marks its task `blocked`. Task 4 of that section is built by T-031
(`docs/implementations/T-031.md`, `docs/decisions.md` ADR 60): a cycle files
an arquitecto's split through `file_split`, and so does `dispatch
file-split`. Step 4 is built by T-080 (`docs/implementations/T-080.md`, `docs/decisions.md` ADR 124
and ADR 125): the `board_service/` package with `board_list` and `board_get`
over stateless MCP on `POST /mcp?role=<role>`, the role check of section 6,
and a Dockerfile. The write side is this service, and the git credential and
both pushes stay on the dispatcher (ADR 124). Its compose service, approved by the user on 2026-10-18,
publishes on loopback `127.0.0.1:8792`, is on `ia_harness_net` only, and
mounts everything read-only; the operator's session registers it with
`claude mcp add --scope local`, not a committed `.mcp.json`. The first part
of step 5 is built by T-082 (`docs/implementations/T-082.md`,
`docs/decisions.md` ADR 127): the backlog statuses, `priority` and `source`
in the frontmatter, `open_cycle` refusing a backlog item, and the per-task
lock of section 5, a write lock separate from the ownership lock. The second
part of step 5 is built by T-084 (`docs/implementations/T-084.md`,
`docs/decisions.md` ADR 129): `backlog_propose`, `task_edit`, `task_comment`
and `backlog_accept`, written by `dispatcher/tasks.py` under `task_lock`,
over MCP and the console's HTTP endpoints, with the source and author taken
from the role; compose mounts the service's `.hive` read-write and puts it
on `console_net` as well, approved 2026-10-18. The third part of step 5 is
built by T-085 (`docs/implementations/T-085.md`, `docs/decisions.md` ADR
130): `tags`, `rank` inside the priority bucket, and the *Ready* order with
its dependency rule, served by `board_list`. Step 6 is
built by T-081 (`docs/implementations/T-081.md`, `docs/decisions.md` ADR 126).
Step 7 is built by T-083 (`docs/implementations/T-083.md`, `docs/decisions.md`
ADR 128): the arquitecto's phases get `--mcp-config` for the service at
`board_mcp_url?role=arquitecto` and the read tools in their allowed tools.
Step 8 is built by T-090 to T-096 (`docs/implementations/T-090.md` to
`T-096.md`, `docs/decisions.md` ADR 135 to ADR 141): the console's Board,
Backlog, task page, epic view and Debt links on real data.
Every other later step is not built. It records an
idea the user raised on
2026-10-10: the agent, not the operator, keeps the board. It creates epics
and tasks and writes their descriptions and steps, through a service that
owns the board and that agents reach over MCP. The aim is a tidier board.

The user settled four points the same day:

- **No Vibe Kanban.** The board is the harness's own, and the console is
  where it is shown.
- **Descriptions and comments live in their own files.** They are read only
  by the roles that need them.
- **No authentication.** Everything runs on the internal network.
- **The service and the console are integrated.**

And three more after reviewing the console's screens:

- **The operator's session may accept** a backlog item onto the board, on the
  person's explicit word.
- **Debt already filed is imported** into the backlog, tagged `debt`.
- **The auditor sets a debt entry's impact** (2026-10-11). It adds one column
  to the debt index, whose columns are a contract (`COLUMNS` in
  `dispatcher/debt.py`; its `card` column becomes the backlog item's id).

And three on 2026-10-11, when the plan was approved:

- **Existing task files are migrated by `dispatch migrate`**, a one-off the
  operator runs, not on read (step 1).
- **Comments are kept for good.** A merged task's `comments.jsonl` is never
  deleted or summarised; it is the record of its discussion and its review.
- **A verdict in the console authorizes the run it starts**, the C-1 and C-4
  ruling, now `docs/charter.md` C-12.

Written by the operator's session, out of cycle, from the code at `a838508`.

## What exists today

- **The task file is the record.** `<hive_tasks_dir>/<task_id>.md`
  (`context_transfer.TaskFile`) holds three things:
  - the status, owner, `depends_on` and heartbeat in its frontmatter;
  - every handoff, appended to its body;
  - `kanban_issue_id`.

  The operator's description is not in it: since T-026 it lives in
  `.hive/board/<id>/description.md`, read and written through
  `dispatcher/tasks.py`. The console's task screens read the task files and
  descriptions through `/api/tasks`.
- **The description rides once.** Since T-027 `_role_prompt` embeds only its
  `## Goal` and `## Done when` in every phase's prompt, the mapper's included.
  The arquitecto and implementador also get `tasks.description_path` and the
  names of any optional sections present; the revisor, auditor and mapper get
  no path.
- **A board seam with nothing behind it.** `dispatcher/vibe_kanban_client.py`
  holds `NullKanbanClient`, `VibeKanbanClient` and `LocalBoardClient`.
  - `cli._kanban` picks one of them.
  - `open_cycle` opens a card, and `_update_task_status` moves it.
  - `config.yaml` configures neither board, so every run so far has used
    `NullKanbanClient`.
  - Vibe Kanban never got past its cloud login.
  - `LocalBoardClient`'s cards repeat what the task files already say.
  - T-028 removed all of it under `docs/decisions.md` ADR 59 (step 2 below):
    this bullet describes the tree before that task.
- **Phases get their tools from outside.** `docker_exec` passes
  `--allowed-tools` from `config.allowed_tools`, and a phase cannot widen its
  own permissions. A per-phase `--mcp-config` would be added the same way.
- **The writes are scattered, and the api has none.**
  - Task files are written by the dispatcher, and since T-027 only through
    `dispatcher/tasks.py`, whose `_write_task_file` (atomic, over
    `context_transfer.write_task_file`) refuses a file that still carries a
    `description` key. The operator also writes them by hand.
  - `file_split` and `mark_merged` will write them next
    (`docs/plans/task-split.md`).
  - The api is `GET`-only with `:ro` mounts (`docs/plans/front.md`, *Tier 3*).
  - The review flow needs somewhere to write verdicts
    (`docs/plans/review.md`).

## The proposal

### 1. The board is the hive, and the console shows it

- **No external board.** The task files are the board's data, and the
  console is its view. Its task list is already the board in practice; an
  epic view and the editing screens are added to it.
- **The seam is removed.** `KanbanClient`, its three clients, `cli._kanban`,
  the `vibe_kanban` and `local_board` config blocks and `kanban_issue_id` all
  go.
- **The record of the old seam stays.** ADR 1 and the Phase 0 record in
  `docs/plans/board.md` stay as history. The removal gets its own ADR, which
  supersedes ADR 1.

### 2. Each task has three files, each read by whoever needs it

```
.hive/tasks/<task_id>.md                  state: frontmatter + the handoff log
.hive/board/<task_id>/description.md      the ask, in sections
.hive/board/<task_id>/comments.jsonl      one comment per line, append-only
```

- **The task file keeps state only.** It holds the frontmatter and the
  handoffs. The description moves out of it.
- **The description has sections, and only two are required.**
  - *Goal* and *Done when* are required.
  - *Surface*, *Steps* and *Out of scope* are filled when they apply.
  - A debt fix or an investigation does not need all five.
- **Each comment is a JSON line** with `at`, `author` (a role or `operator`)
  and `text`. When the comment is on a diff, it also has `file`, `line` and
  `commit` (`review.md`).
- **What each phase is given:**
  - The prompt keeps embedding *Goal* and *Done when* whole, so no phase
    starts without the ask.
  - The arquitecto and the implementador are pointed to `description.md` for
    the rest.
  - Comments reach a phase only when it needs them. The obvious case is a
    revision round started from the console (`review.md`). The prompt names
    the file; it does not paste it.
- **Why `.hive/board/` and not the scratch dir.** The scratch dir,
  `.hive/tasks/<task_id>/`, is handed to every phase to write its notes in. A
  separate root keeps the ask and the comments out of that.

### 3. Two levels: epic and task

- **Epic.** A goal too big for one cycle. It has a title, a description
  holding the goal, and its tasks in order. It is marked `status: epic` with
  `split_into`, as in `task-split.md`.
- **Task.** One surface, one cycle (P4). A task produced by a split is a task
  like any other, with `epic` set.
- **No story level.** A story can neither be dispatched nor reviewed on its
  own, so it adds a layer to keep tidy and nothing a phase reads. If an epic
  needs grouping inside it, it should be two epics.

### 4. Backlog and board: what may be taken, and what may not yet

The user's ask: the main thread has to know which tasks are in to be done and
which it must not take yet. So every item lives in one of two places. Both
are the same task files, told apart by `status`:

- **The backlog holds what is proposed and not yet accepted.**
  - Its statuses are `proposed`, `deferred` and `dropped`.
  - Items carry a `source` (`operator`, `arquitecto` or `debt`), free
    `tags`, and a priority (below).
  - Nothing in the backlog may be dispatched. `open_cycle` refuses any status
    other than `pending`.
- **The board holds what is accepted, and shows its live state.** Its
  columns follow what is actually running:

  | Column | Comes from |
  |---|---|
  | Ready | `pending`, every `depends_on` merged |
  | Waiting | `pending`, a dependency not merged yet |
  | Arquitecto · Implementador · Revisor · Auditor · Integrador | `in_progress` plus a new `phase` field the dispatcher writes as each phase starts |
  | Awaiting review | the state `review.md` adds between the audit and the merge |
  | Conflict | a task under review whose branch no longer merges into the base cleanly, or whose gate went red after the base moved (`review.md` *Decisions*, 6); while the integrador runs it shows in its own phase column |
  | Blocked | `blocked` |
  | Merged | the `merged` block from `task-split.md` |

  An epic is shown as a swimlane: a row holding its tasks, each in its
  column. A task outside an epic sits in a lane of its own.
- **`phase` closes a known gap.** Today the board has a single *In progress*
  column, because which role is running is written down nowhere. The banner
  in `front/src/routes/index.tsx` and ADR 28 name this gap.
  - The dispatcher writes `phase: <role>` at the start of each phase and
    clears it at the end. The heartbeat is already written the same way.
  - With `phase`, the role lanes come back, along with the role filter the
    banner says went with them.
- **Accepting is a person's act.** Moving an item from the backlog to the
  board (`proposed` → `pending`) is how a person says "this may be done".
  - Agents only propose. This keeps the rule in `dispatcher/debt.py`: an
    agent that can create tasks can assign itself work.
  - A person accepts from the console, or in the operator's session: the
    session accepts on the person's explicit word in the conversation, the
    same way it dispatches (decided by the user on 2026-10-10).
  - Accepting spends no quota. Dispatching an accepted task stays a separate
    act (C-4), the *Start development* button below.
- **Debt enters as backlog.** The flow up to the index does not change: the
  implementador declares, the revisor rules, the auditor files the entry in
  `docs/debt/`. The dispatcher then mirrors each filed entry as a backlog item
  instead of a card:
  - `source: debt`, `debt_id: T-008-D1`, and the tag `debt`;
  - the entry's *what* and *fix* become the description.

  From there a person accepts it into the board, defers it or drops it, like
  any other item. Fixing it closes the row in the index, as today.
- **Tags group, they do not rank.** `tags` is a free list on any item. The
  dispatcher sets `debt` on every debt item, so the backlog and the board can
  be filtered to debt alone or to everything but debt. An area tag such as
  `front` or `dispatcher` may be added by whoever proposes.
- **One priority for everything.** Debt and the other work share a single
  order, so there is never a second scale to reconcile.
  - `priority` is a bucket, `high`, `normal` or `low`, and `rank` is the
    position inside the bucket, changed by dragging in the console or by
    `task_edit`.
  - Filtering to `debt` shows the same order restricted to debt: the order
    among debts is the global one.
  - Whoever proposes suggests the bucket; the person confirms or changes it
    when accepting. A new item enters at the end of its bucket.
- **Debt gets a suggested bucket from the auditor.** The revisor rules on
  each declaration (`accepted`, `rejected` or `blocks` in
  `dispatcher/debt.py`), and the auditor files the accepted ones. The auditor
  also gives each filed entry an impact, which suggests the bucket. It is the
  one writer of the index, and it usually runs on the strongest model
  (decided by the user on 2026-10-11). `blocks` stays what it is: a
  declaration that is not debt at all and stops the task.

  | Impact | Means | Bucket |
  |---|---|---|
  | `risk` | can break something, or makes other work unsafe | `high` |
  | `cost` | makes later work slower or larger | `normal` |
  | `cosmetic` | naming, wording, tidiness | `low` |

  An entry with no impact, such as one filed before this change, enters as
  `normal`.
- **On the board, dependencies come before priority.** The *Ready* column is
  ordered by bucket, then rank. A task is never *Ready* before its
  dependencies, so a dependency takes the highest bucket of the tasks waiting
  on it; otherwise a `high` task could wait forever behind a `low` one. The
  tasks of an epic take the epic's bucket.
- **Debt on a surface already in play is pointed out.** When a debt item's
  *where* falls on the *Surface* of a task on the board, the console shows
  the two side by side. The person may fold the debt into that task, since
  its cycle already holds that context, or leave it in the backlog. Nothing
  folds it in automatically.
- **An epic is accepted whole or task by task.** Accepting an epic accepts
  the tasks listed in its `split_into`. Leaving one of them in `deferred`
  keeps it out without blocking the rest, unless some other task
  `depends_on` it.
- **A split is already accepted.** The sub-tasks the arquitecto files from an
  accepted task enter the board as `pending`. Their parent was accepted, and
  the split is that same work cut up. The cap on splits in `task-split.md`
  keeps this from growing the work without limit.

### Starting development: a button on the board

Added on 2026-10-11, on the user's word, after the plan was approved. It
answers the open question in `main-thread.md` of who starts the next task
when the person does not name it in the conversation. It needs a charter
ruling before it is built (*Open questions*).

- **The button.** Each card in *Ready* has *Start development*. A card in
  *Waiting* shows it disabled, naming the dependency not merged yet. A task
  already queued or running shows no button, so a double click starts
  nothing twice.
- **What it does.** The board service writes an action
  `{kind: "start", task, by, at, note}` to the queue, and the worker opens the
  task's cycle when an account is free, under `balancer.md`'s pick and quota
  rules, and no other task of the same project is running (`projects.md`,
  *Decisions*, 5). Until then the card shows *Queued, waiting for an account*
  or *Queued, waiting for the project*, with *Cancel*. Once a phase has started, cancelling is the dispatcher's own
  stop, not this button.
- **What it authorizes.** The task's cycle, from the arquitecto to *Awaiting
  review*, including the dispatcher's own retries, and nothing after it:
  *request changes* and *resolve conflict* stay their own verdicts. Before it
  queues anything, the button says so, with the roles the cycle runs, the
  state of the pool, and the cost of past cycles when there is one to show.
  It never shows a guessed estimate. It also shows the slots: how many
  accounts may take a phase now and where the task would stand
  (`projects.md`, *Decisions*, 6).
- **A note with the start.** Added on the user's word, 2026-10-11. The
  confirmation has an optional text box. Its text goes in the action's `note`
  and, when the worker takes the start, becomes a comment from `operator` in
  the task's `comments.jsonl`. The arquitecto's prompt names that file when
  the cycle starts with a note, so the first role reads it; later roles find
  it there as the person's word. The note adds context to the task; it does
  not widen what the start authorizes. *Start epic* takes one note for the
  epic, copied to each task it queues.
- **Per epic.** The epic's view has *Start epic*. It queues the epic's
  *Ready* tasks, and each later task as its dependencies merge, in the
  board's order. One task of the epic runs at a time, since its tasks share a
  project (`projects.md`, *Decisions*, 5): the next is queued when the one
  before it is awaiting review. It covers
  the tasks in `split_into` when the button is pressed, and the sub-tasks the
  arquitecto splits them into; a task added to the epic later is not
  covered, because the person has not seen it. *Stop epic* drops what is
  queued and leaves running phases alone. The epic still moves only as fast
  as the person approves, since a dependency counts once it is merged.
  **Built 2026-10-20, backend only** (`docs/decisions.md` ADR 207):
  `POST /api/epics/<id>/start` queues one start per task in *Ready* at the
  press, in the board's order, with the note copied and a null limit; the
  next waits on the project lock (ADR 200). *Stop epic* and both console
  buttons are built 2026-10-20 (ADR 211): `POST /api/epics/<id>/stop` queues
  one cancel per start of the epic still waiting and names the taken ones,
  left running; the epic's task page has *Start epic* and *Stop epic*, each
  behind a confirmation. Not built: queuing each later task as its
  dependencies merge, the arquitecto's sub-tasks, and the epic's limit.
- **Accepting is still not starting.** Accepting moves an item onto the
  board and spends nothing; starting is the separate act that spends quota.
  They stay two clicks, because a person may accept now and start later.

#### A spending limit on what a start authorizes

Added the same day. The pool already stops an account at its threshold and
keeps the primary's reserve (`balancer.md`). What it does not stop is one
start spending more than the person meant: *Start epic* can run until every
worker account is parked, and a task whose gate keeps failing goes through
every retry the dispatcher allows. So each start carries a limit, and
reaching it pauses the work rather than failing it.

- **The measure is one already written.** The usage log records each
  phase's tokens and the `result`'s cost. The limit counts those. It never
  counts an estimate, the same rule as the button's preview.
- **Per task.** A cycle that has spent more than its limit, or retried more
  than a set number of times, ends *Blocked* with the reason
  `budget_reached`, showing what it spent. Continuing is a new *Start*.
- **Per epic.** *Start epic* asks for the epic's limit, prefilled from the
  past cycles of the epic's own tasks, or from the project's median once
  `token-economy.md` P4 has one. When the epic reaches it, nothing new is
  queued, running phases finish, and the epic shows *Paused: limit
  reached* with *Resume*, which asks for a new limit.
- **A floor on the pool.** Either start may also say "stop queuing when the
  pool's free share drops under N%", read from `/usage` as the picker reads
  it. That keeps quota for the conversation and the reviews.
- **Reaching a limit is a notification** of kind *Blocked*
  (`notifications.md`), because it waits on the person.

The default numbers are set when this is built, from real cycles.

### 5. The service

- **One module writes, and two processes import it.**
  - `dispatcher/tasks.py` (the name is open) is the only code that writes the
    three files.
  - Its writes are: create, edit, comment, file a split, mark merged, and
    record a verdict.
  - Each write is validated, takes a per-task lock, and is atomic (as
    `_write_atomic` is).
  - Two processes import the module: the dispatcher and the service.
  - The operator's hand edits are the one path outside it, and validation on
    read catches them, as `task-split.md` already does for a hand-written
    epic.
- **The service is the console's write side.** It is the separate process
  `front.md` *Tier 3* asks for.
  - It mounts the hive read-write and has no docker socket.
  - It serves the console's write routes over HTTP: create and edit tasks,
    comments, and the review verdicts.
  - It serves the agents' tools over MCP (streamable HTTP).
  - Both front the same module.
  - The api stays `GET`-only and keeps reading the files.
- **No authentication.** It listens on the compose network only and
  publishes no port to the host beyond the console's own proxy.
- **The role is a parameter, not a credential.**
  - The dispatcher writes `?role=<role>` into the URL in each phase's
    `--mcp-config`, and the operator's session uses `role=operator`.
  - The service checks each tool against that role. This keeps a confused
    phase from calling a tool outside its role; it does not stop someone on
    the network from naming another role, and is not meant to.

### 6. The tools, and who gets them

Few tools, because the schema of every MCP tool rides in every call of every
phase that has it (`docs/plans/token-economy.md`):

| Tool | What it does | operator | arquitecto | other roles |
|---|---|---|---|---|
| `board_list` | items filtered by place (backlog or board), status, epic or text: ids, titles, states | yes | yes | — |
| `board_get` | one task or epic: frontmatter, description, comment count | yes | yes | — |
| `backlog_propose` | an epic with its tasks, or a single task, into the backlog as `proposed`; an epic is all or nothing | yes | yes | — |
| `task_edit` | title, description, `depends_on`, `priority`, while not started | yes | — | — |
| `task_comment` | append a comment | yes | yes | — |
| `backlog_accept` | `proposed`/`deferred` → `pending`, or → `deferred`/`dropped` | yes, on the person's word | — | — |

- **No tool moves a task once it is on the board, and none dispatches.**
  - `in_progress`, `phase`, `done`, `blocked` and `merged` stay with the
    dispatcher and the merge.
  - Dispatching stays a person's act (C-4).
  - Proposing spends nothing, so an agent may propose. Accepting decides what
    may be worked on, so it belongs to a person (section 4).
- **The arquitecto may propose too.** When it sees work outside its task,
  such as a follow-up or a neighbour that needs the same change, it proposes
  that work into the backlog rather than widening its own surface.
- **The arquitecto still splits through its handoff.**
  - A split filed by tool calls mid-phase is left half-filed if the phase
    dies between calls.
  - A `split` in the handoff is validated whole after the phase, and
    `_file_split` files it all or nothing through the module.
  - The arquitecto gets the read tools so it can see the epic and the sibling
    tasks it is planning next to.
- **The implementador, revisor, auditor and integrador get no tools at
  first.** What
  they need is in their prompt and the files it names. A tool is added later
  only if a phase is seen to need one.
- **Retries make no duplicates.** Every create is idempotent on its title
  within its epic, so a call retried from a conversation does not create the
  same task twice.

### 7. What the operator's session does with it

This is the main use:

1. The person describes what they want, in the conversation.
2. The operator's session proposes it into the backlog, as an epic with its
   tasks. It fills each task's sections and sets its `depends_on`.
3. The person reads the proposal in the console and accepts it, defers it or
   comments on it.
4. When the operator's session asks what to work on next, it takes only from
   the board's *Ready* column. The person dispatches when they choose.

C-1 (one conversation drives) and C-4 (a person authorizes each run) stay as
they are. Writing tidy task files moves from the person to the agent.

### When planning happens

The user asked where planning and splitting fall in this flow. Three levels,
each at the moment it is cheapest:

1. **In the conversation, before the backlog: the what.** The person and the
   operator's session settle what the feature is and when it is done. The
   session proposes it with *Goal* and *Done when*, and no implementation
   plan. Nothing here spends a phase.
2. **In the cycle, after accepting: the how.** When an accepted task is
   dispatched, the arquitecto runs first, as it does today. If the task is
   one surface, it writes the plan and the cycle goes on to the
   implementador. If it spans two to four, it splits it under
   `task-split.md`: the task becomes an epic, the cycle stops before the
   implementador, and the sub-tasks land on the board as `pending`. Each
   sub-task's own arquitecto plans it when it is dispatched. A split is
   already accepted (section 4), so it needs no second trip through the
   backlog.
3. **For a feature too big for one split: a plan document first.** Past four
   surfaces, or with design decisions the person must take, the planning is
   done in the conversation as a plan in `docs/plans/`, as this document
   was, and the session proposes its build steps as an epic with its tasks
   already cut.

So the backlog holds what to do, and the arquitecto decides how, only for work
the person has accepted: planning never spends quota on an item that may
still be deferred or dropped.

### 8. The console's screens

Checked against `front/src/routes` on 2026-10-10. The console's *real* screens
read the api; the others are Lovable drafts over mock data.

| Screen | Today | What this plan needs |
|---|---|---|
| Board (`/`) | real: four status columns, no role lanes (ADR 26, ADR 28) | board items only; the live columns of section 4; epic swimlanes; *Start development* on *Ready* cards |
| Backlog (`/backlog`) | mock: a list with a *completed* toggle | redefined: proposed, deferred and dropped items in priority order, with accept, defer, drop and reorder; no completion, since completed work is on the board |
| Debt (`/debt`) | real, read-only from `docs/debt/` | stays the index's view; each open entry links to its backlog item and shows whether it was accepted |
| Task (`/tasks/$taskId`) | real: status, owner, dependencies, running record, declared debt | adds the description's sections, the comment thread with a form, the epic it belongs to, and its `phase` |
| Epic | missing | new: the epic's tasks with their states, its `split_into` and `depends_on` as a graph or ordered list, and progress to merged |
| Approvals (`/approvals`) | mock: approve or reject pushes, merges, permissions and budgets | becomes `review.md`'s screen: a task's diff with line comments and the verdicts; it lists the *Awaiting review* and *Conflict* columns, with *resolve conflict* on the latter |
| Roadmap | missing | new, in `docs/plans/roadmap-view.md`: plans, epics and tasks in three bands (future, now, past), derived from the files; read-only, after this plan |
| Queue (`/queue`) | mock | the queued starts, in the order the worker will take them, each with *Cancel*; built with *Starting development*, after its ruling |

Two rules keep the screens apart:

- **Accepting is not approving.** Accepting moves an item from the backlog to
  the board, before any work. Approving is the verdict on a finished diff,
  before the merge. They are different screens because they are different
  decisions, taken at different times.
- **Each item shows in one place.** A backlog item is not on the board, and a
  board task is not in the backlog. The debt view is the index, not a third
  list.

## How it would be built

Each step is a single surface, built in this order:

1. **The task module and the file split.**
   - Build `dispatcher/tasks.py`, and move the description into
     `.hive/board/<task_id>/description.md`.
   - `dispatch migrate` moves the description out of every task file that
     still has one as the frontmatter key `description`, once, when the
     operator runs it; reads never
     rewrite a file. The module refuses a task file not yet migrated, naming
     the command.
   - The prompt changes to *Goal* and *Done when* plus the path to the
     description.
   - The dispatcher's own writes move onto the module.
2. **The board seam removed.** `vibe_kanban_client.py`, `cli._kanban`, the
   config blocks and `kanban_issue_id` go, under the ADR that supersedes
   ADR 1.
3. **The epic format.** Tasks 2 and 3 of `task-split.md`, written against the
   module.
4. **The service, reads only.**
   - The process, its compose service, the role check, and `board_list` and
     `board_get`.
   - The operator's session is configured to use it.
   - An ADR for where the write side lives: the decision `front.md` leaves to
     the tier-3 opening task. The git credential `review.md`'s *approve*
     needs for the push, and the task-branch push of C-5 as amended, stays
     out of the service, on the dispatcher's side.
   - **Built 2026-10-18** by T-080 (ADR 124, ADR 125). The compose service
     came after, on the user's approval: loopback `127.0.0.1:8792`,
     `ia_harness_net` only, every mount `:ro`. The operator's session
     registers it with `claude mcp add --scope local`.
5. **The backlog and the write tools.**
   - The backlog statuses, `priority`, `source`, and `open_cycle` refusing
     anything but `pending`.
   - `backlog_propose`, `task_edit`, `task_comment` and `backlog_accept`,
     over MCP and HTTP.
   - `tags`, `priority` and `rank`, with the dependency rule for the
     *Ready* order.
   - The dispatcher files debt as backlog items instead of cards, and the
     open entries already in `docs/debt/` are imported once as `proposed`
     with the tag `debt` (decided by the user on 2026-10-10).
   - The `impact` column in the debt index, set by the auditor.
   - **Built 2026-10-18** by T-082 (`docs/decisions.md` ADR 127), the first
     bullet: the backlog statuses `proposed`, `deferred` and `dropped`;
     `priority` and `source` in the frontmatter, validated on read;
     `open_cycle`, `queue.plan` and `acquire_lock` refusing a backlog item
     (blocked retries and resumes still pass); `board_list` serving both
     fields; and the per-task lock of section 5, a `flock` on
     `<hive root>/locks/<id>.lock` taken by every writer in
     `dispatcher/tasks.py`, separate from the `owner` lock of ADR 126. Not
     built: the write tools and the read-write mount (5b), `tags` and `rank`
     with the *Ready* order (5c), and debt in the backlog with its import
     and `impact` (5d).
   - **Built 2026-10-18** by T-084 (`docs/decisions.md` ADR 129), the second
     bullet: `backlog_propose`, `task_edit`, `task_comment` and
     `backlog_accept` as writers in `dispatcher/tasks.py` (`propose`,
     `edit_task`, `add_comment`, `accept`) under `task_lock`, served over
     `POST /mcp?role=` and over `POST /api/backlog`, `PATCH /api/tasks/<id>`,
     `POST /api/tasks/<id>/comments` and `POST /api/tasks/<id>/accept`.
     `source` and the comment's author come from the role. A create is
     idempotent on a `title` in the frontmatter, and an epic is all or
     nothing. The arquitecto gets `backlog_propose` and `task_comment`, and
     the operator gets all four. Compose mounts `.hive` read-write (config
     stays `:ro`) and adds `console_net`. Not built: `tags` and `rank` (5c),
     and debt in the backlog (5d).
   - **Built 2026-10-18** by T-085 (`docs/decisions.md` ADR 130), the third
     bullet: `tags`, a free list validated on read and normalized on write
     (trimmed, kept once, in order), set by `backlog_propose` and
     `task_edit`; `rank`, a sparse integer spaced `RANK_STEP` (1024) apart,
     handed out at the end of the bucket under `CREATE_LOCK` on a proposal,
     on accepted drafts, and when `backlog_accept` or `task_edit` changes the
     bucket, and set by `task_edit`; and the *Ready* order as pure functions
     in `dispatcher/tasks.py` (`effective_buckets`, `ready_order`): pending
     with every `depends_on` merged, by effective bucket, then rank, then id,
     where a dependency and an epic's task are raised to the highest bucket
     waiting on them. `board_list` serves `tags` and `rank` per row, lists
     the backlog by bucket and rank, and adds `ready`, the listed ids in that
     order. Not built: 5d, debt as backlog items with `tags: [debt]`, the
     one-time import of the open entries in `docs/debt/`, and `impact` in
     the debt index.
   - **Built 2026-10-18** by T-086 (`docs/decisions.md` ADR 131), the fourth
     bullet: `impact` (`risk`, `cost`, `cosmetic`) as the last column of the
     debt index, written by the auditor and read by name, a missing or other
     value being `none`; it maps to `high`, `normal`, `low`, and `none` to
     `normal`. `debt_id` in the frontmatter links an item to its entry,
     checked on read and served by `board_list` and `board_get`; `card`
     stays `none` (ADR 59). `tasks.propose_debt` files an entry as a
     `proposed` item, `source: debt`, `tags: [debt]`, at the end of its
     bucket, under `CREATE_LOCK` and idempotent on `debt_id`. The
     dispatcher files each entry after the auditor, and
     `dispatch import-debt` (`--project` or `--index`, `--dry-run`) files
     the open entries already in the index. Step 5 is complete; step 8 (the
     console) and step 9 remain.
   - **Built 2026-10-18**, three follow-ups to step 5. T-087
     (`docs/decisions.md` ADR 132): `dispatch run-queue --ready N` tops the
     queue up with the first N tasks of `ready_order` not already listed,
     picked once at plan time; the confirmation screen marks them and names
     the ones passed over. T-088 (ADR 133): `/api/tasks` and `/api/tasks/<id>`
     serve `priority`, `source`, `tags`, `rank` and `debt_id` as `board_list`
     shapes them, with a warning per bad field, and `/api/ready` serves the
     Ready order; `/api/debt` already served `impact`. T-089 (ADR 134): an
     epic closes once every task of it is merged or dropped, `done` if one
     merged and `dropped` if none did; a deferred task keeps it open, and
     `backlog_accept` dropping a task of an epic runs the check. This amends
     `docs/plans/task-split.md` section 3, where only a merge closed one.
6. **`phase` in the frontmatter.** The dispatcher writes and clears it around
   each phase, and the api serves it.
   **Built 2026-10-18** by T-081 (`docs/decisions.md` ADR 126): `phase` in
   the task file's frontmatter, written by `tasks.acquire_lock` with the lock
   as each phase starts and cleared by every writer that drops the lock
   (`release_stale_lock`, `handoff`, `mark_blocked`); a killed dispatcher's
   is cleared by `reap_expired_locks` at the next cycle or by `dispatch
   release-account`, and until then `lock_expired` is true beside it.
   `/api/tasks` and `/api/tasks/<id>` serve it, `null` when unset. The
   board's role lanes are step 8.
7. **Each phase gets its config.** The dispatcher passes `--mcp-config` with
   the phase's role, and the arquitecto gets the read tools.
   **Built 2026-10-18** by T-083 (`docs/decisions.md` ADR 128): the role
   table moves to `dispatcher/board_mcp.py`, and the service imports it from
   there. A phase whose role has tools there (the arquitecto, draft runs
   included) gets `--mcp-config` with one http server, `board`, at
   `<board_mcp_url>?role=<role>`, on every call and every resume, and
   `mcp__board__board_list` and `mcp__board__board_get` in its allowed tools.
   `board_mcp_url` is a read-only config key, `http://board-service:8792/mcp`
   by default; `null` or `""` turns it off. The agent containers are already
   on `ia_harness_net`, so compose does not change.
8. **The console.** The screens in section 8: the board's live columns and
   swimlanes, the backlog redefined, the epic view, and the task page's
   description and comments. It is a front task, behind its own ask for a
   build.
   **Built 2026-10-18** by T-090 to T-096 (`docs/decisions.md` ADR 135 to
   ADR 141). The read api serves a task's comments (T-090) and the epic
   fields, `merged` and a title on task rows (T-092). The console forwards
   the backlog's writes, the *Ready* order and comments to the board-service
   behind its own CSRF check (T-091). The Backlog screen reads `/api/tasks`
   and writes through the board-service (T-093). The task page shows the
   description's sections, the comment thread and form, the phase, its epic,
   and an epic's tasks and progress (T-094). The Board shows *Ready* in the
   dispatcher's order, *Waiting*, one lane per role from `phase`, *Blocked*,
   *Conflict* and *Merged*, with one swimlane per epic (T-095). The Debt
   screen links each open entry to its backlog item and says whether it was
   accepted (T-096). Not built: *Awaiting review* stays empty until
   `review.md` gives it a status, an integrador lane waits for that role in
   the dispatcher, and *Start development* and the Queue screen are step 9.
   T-096's two pending items are built 2026-10-20 (ADR 193): the filter
   finds an open entry by its item and its state, and an open entry whose
   *where* falls on the *Surface* of a task in play points at that task.
   The Approvals screen is real since ADR NNN: it lists `done` tasks not yet
   merged, as the stand-in for *Awaiting review*, and `conflict` ones, with
   the task branch's diff and its pinned comments, and it pins new ones
   through `task_comment`'s anchor; the verdicts
   wait for `review.md`.

9. **Starting development.** The `start` action and its cancel in the
   worker, the button on the board and the epic, the spending limit, and the
   queue screen. It needs the queue and worker, and the charter ruling below.
   *Start epic*'s route is built 2026-10-20 (ADR 207); *Stop epic* and the
   two buttons on the epic's page the same day (ADR 211). The spending limit
   and the queue screen are not.

`review.md`'s diff screen and verdicts come after this, on the same service
and the same comments file.

## Open questions

The three questions this section held were answered by the user on
2026-10-11 (the status block at the top): the C-1 and C-4 ruling, migrating
by `dispatch migrate`, and keeping comments for good. New ones are added here
as the build raises them.

- **The ruling for *Start development*.** C-12 lists the console verdicts
  that authorize a run, and C-4 holds for everything else: "nothing in the
  console starts a run without a person's verdict on that task". *Start* is
  a verdict on a task, but it is not in C-12's list, and *Start epic* is a
  verdict on tasks the person has not each looked at. **Ruled 2026-10-11:**
  both are C-12 verdicts. *Start epic* covers the tasks listed when it is
  confirmed, and its spending limit is the ceiling of the authorization
  (C-12, *Amended*).
