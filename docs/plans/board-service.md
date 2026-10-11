# Board service — the harness's own board, kept by agents over MCP

**Status:** design only, not approved. It records an idea the user raised on
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

Written by the operator's session, out of cycle, from the code at `a838508`.

## What exists today

- **The task file is the record.** `<hive_tasks_dir>/<task_id>.md`
  (`context_transfer.TaskFile`) holds four things:
  - the status, owner, `depends_on` and heartbeat in its frontmatter;
  - the operator's description;
  - every handoff, appended to its body;
  - `kanban_issue_id`.

  The console's task screens read these files through `/api/tasks`.
- **The description rides in two places.** `_role_prompt` embeds it whole in
  every phase's prompt, and every phase is told to read the task file, which
  holds it again along with all the handoffs.
- **A board seam with nothing behind it.** `dispatcher/vibe_kanban_client.py`
  holds `NullKanbanClient`, `VibeKanbanClient` and `LocalBoardClient`.
  - `cli._kanban` picks one of them.
  - `open_cycle` opens a card, and `_update_task_status` moves it.
  - `config.yaml` configures neither board, so every run so far has used
    `NullKanbanClient`.
  - Vibe Kanban never got past its cloud login.
  - `LocalBoardClient`'s cards repeat what the task files already say.
- **Phases get their tools from outside.** `docker_exec` passes
  `--allowed-tools` from `config.allowed_tools`, and a phase cannot widen its
  own permissions. A per-phase `--mcp-config` would be added the same way.
- **The writes are scattered, and the api has none.**
  - Task files are written by the dispatcher (`write_task_file`, atomic) and
    by the operator, by hand.
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
  | Arquitecto · Implementador · Revisor · Auditor | `in_progress` plus a new `phase` field the dispatcher writes as each phase starts |
  | Awaiting review | the state `review.md` adds between the audit and the merge |
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
    act (C-4).
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
- **The implementador, revisor and auditor get no tools at first.** What
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

### 8. The console's screens

Checked against `front/src/routes` on 2026-10-10. The console's *real* screens
read the api; the others are Lovable drafts over mock data.

| Screen | Today | What this plan needs |
|---|---|---|
| Board (`/`) | real: four status columns, no role lanes (ADR 26, ADR 28) | board items only; the live columns of section 4; epic swimlanes |
| Backlog (`/backlog`) | mock: a list with a *completed* toggle | redefined: proposed, deferred and dropped items in priority order, with accept, defer, drop and reorder; no completion, since completed work is on the board |
| Debt (`/debt`) | real, read-only from `docs/debt/` | stays the index's view; each open entry links to its backlog item and shows whether it was accepted |
| Task (`/tasks/$taskId`) | real: status, owner, dependencies, running record, declared debt | adds the description's sections, the comment thread with a form, the epic it belongs to, and its `phase` |
| Epic | missing | new: the epic's tasks with their states, its `split_into` and `depends_on` as a graph or ordered list, and progress to merged |
| Approvals (`/approvals`) | mock: approve or reject pushes, merges, permissions and budgets | becomes `review.md`'s screen: a task's diff with line comments and the three verdicts; it lists the *Awaiting review* column |
| Queue (`/queue`) | mock | out of scope here; dispatching from the console waits for the C-4 ruling |

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
   - On read, a task file that still has a description in its body is
     migrated.
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
     the tier-3 opening task.
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
6. **`phase` in the frontmatter.** The dispatcher writes and clears it around
   each phase, and the api serves it.
7. **Each phase gets its config.** The dispatcher passes `--mcp-config` with
   the phase's role, and the arquitecto gets the read tools.
8. **The console.** The screens in section 8: the board's live columns and
   swimlanes, the backlog redefined, the epic view, and the task page's
   description and comments. It is a front task, behind its own ask for a
   build.

`review.md`'s diff screen and verdicts come after this, on the same service
and the same comments file.

## Open questions

- **Approving from the console, against C-1 and C-4.** This is the charter
  question `front.md` and `review.md` already name. Steps 5 and 8 add console
  writes that spend no quota (propose, edit, comment, accept), and those do
  not need the ruling. The verdicts in `review.md` do.
- **Migrating existing task files.** Moving the description out on read
  (step 1) means a task file changes the first time it is read after the
  upgrade. The alternative is a one-off `dispatch migrate` that the operator
  runs.
- **Retiring comments.** `comments.jsonl` only grows. Should a merged task's
  comments be kept for good, or summarised into the task's handoff log?
