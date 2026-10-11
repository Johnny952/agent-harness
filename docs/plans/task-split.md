# Task split — epics, and the arquitecto's backstop for a task that spans surfaces

**Status:** design only. Nothing here is built, and nothing here is approved:
it settles the decisions `docs/plans/token-economy.md` P4 leaves "for an ADR",
so that once P4 is approved the work can be dispatched in one-surface tasks.
No ADR is appended to `docs/decisions.md` until the user approves; the text
ready to append is under *Proposed ADR*. Written 2026-10-10 by the operator's
session, out of cycle, from the code at `c7a643e`, and revised the same day
with the user's answers to its first open questions: a split parent is an
**epic** its sub-tasks are tagged with, a dependency counts once it is
**merged** rather than `done` (because `docs/plans/review.md` makes the merge a
person's approval), and `run-phase` proposes a split without filing it.

## What P4 asks for

P4's rule is one surface per task, applied first by the operator when writing
the task, and then by the arquitecto as a backstop: "if its plan spans more
than one surface, it returns a split, with sub-tasks and their order, instead
of a plan". That needs two things the harness does not have:

- a `split` outcome in the arquitecto's handoff schema (`dispatcher/handoff.py`);
- a dispatcher path that files the sub-tasks and stops the cycle before the
  implementador runs.

The user asked for a third, on 2026-10-10: a workflow for big tasks in which
the sub-tasks stay associated with the task they came from, tagged as one
epic. So the same files an arquitecto's split writes can also be written by
the operator, by hand, for a task known up front to be big.

The point of the backstop is cost, not tidiness. The split is decided after
one arquitecto phase, which is the cheapest phase of the cycle; every surface
that would have ridden along in the implementador's context is a quadratic
cost avoided.

## What exists today

- **The schema.** `handoff.schema_for(role)` merges the shared `_PROPERTIES`
  (`status`, `changed`, `verified`, `pending`, `risks`, `subagents`,
  `learnings`, `debt`, `paths`) with the role's entry in `_ROLE_EXTRAS`, and
  makes every property required. The arquitecto's extras are `{}` today. The
  revisor's extras (`_VERDICT_PROPERTY`, `_DEBT_RULINGS_PROPERTY`) and the
  implementador's (`_RESOLVED_DEBT_PROPERTY`) are the pattern a role-only field
  follows.
- **The one outcome the arquitecto can end a task with** is a block:
  `status: blocked` from the shared `_STATUS_VALUES`, read by
  `handoff.blocked(payload)`, with the reasons in `handoff.pending(payload)`.
  `run_task_cycle` checks it right after `run_phase(ctx, "arquitecto")`, moves
  the card with `_update_task_status(..., "blocked")` and returns
  `CycleOutcome.BLOCKED`, which the CLI maps to `EXIT_BLOCKED` (1).
- **The budget.** `_BUDGET_BYTES["arquitecto"]` is 5120 bytes;
  `lines_for` turns that into the entry count the role is told.
- **Task files.** `context_transfer.task_file_path(hive_dir, task_id)` is
  `<hive_tasks_dir>/<task_id>.md`: YAML frontmatter (`task_id`, `status`,
  `owner`, `depends_on`, `heartbeat`, then optional `kanban_issue_id`,
  `resolved_debt`, `description`) and a markdown body every handoff appends
  to. `TaskFile`, `read_task_file`, `write_task_file` and `_read_or_new` are
  the whole format. `depends_on` is read, written and served by the api
  (`observability/api/app.py`), but **nothing in `dispatcher/` enforces it**.
- **Nothing records a merge on the task.** A branch lands either in-cycle,
  through `dispatcher._merge_task_branch` when `merge_on_done` is on (off by
  default, `config.py`), or later through `dispatch merge-task`, both calling
  `docker_exec.merge_task_branch`. Neither writes anything to the task file,
  so "is T-025-S1 merged?" has no answer a reader of `.hive/tasks/` can get.
- **Ids derived from a parent.** `debt.entry_id(task_id, n)` is
  `f"{task_id}-D{n}"`, derived rather than assigned so the dispatcher can name
  an id before the phase that writes it. T-024-D1 shows the same suffix has
  also been used by hand as a task id for out-of-cycle debt work.
- **Labels ride in the title.** `KanbanClient.create_issue` takes a title and
  a description and nothing else, so `debt.LABEL` puts the `debt` label in the
  card title, "where a board filter can still find it". A task card's title is
  `dispatcher._issue_title(task_id, description)`.
- **No queue.** `run-task --task-id X --project Y` runs one task. Nothing in
  the dispatcher picks the next task by itself; the operator (or whatever
  drives the CLI) chooses every dispatch.

## Decisions

### 1. The handoff schema: a `split` list on the arquitecto only

A new role-only property, `_SPLIT_PROPERTY`, added to
`_ROLE_EXTRAS["arquitecto"]` the same way the revisor's verdict is added to
its role:

```python
_SPLIT_PROPERTY = {
    "split": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "The sub-task in one line."},
                "surface": {
                    "type": "string",
                    "description": "The one surface it changes: an api route, a screen, a region of a screen, or one module's contract.",
                },
                "description": {
                    "type": "string",
                    "description": "The sub-task as its own task description: what to build, what it reads from earlier sub-tasks, and when it is done.",
                },
                "after": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "1-based positions of earlier entries in this list that must be merged first. Empty for none.",
                },
            },
            "required": ["title", "surface", "description", "after"],
            "additionalProperties": False,
        },
        "description": "Empty unless the task spans more than one surface. Then: the sub-tasks in the order they run, and no plan.",
    },
}
```

- **The signal is a non-empty `split`, not a new status.** `_STATUS_VALUES`
  is shared by every role, and adding `split` to it would offer every role an
  outcome only one may use. A required array that is empty when there is
  nothing to say is how the schema already works ("every field is required,
  with an empty array" — `schema_for`). A split is returned with
  `status: complete`: the arquitecto finished its job, which was to decide
  that this task is too big.
- **A block wins over a split.** `run_task_cycle` checks `handoff.blocked`
  first. A task that is both underspecified and too big needs the decision
  before anyone can split it well.
- **Order is the list order; `after` is backward-only.** An entry may only
  name positions before its own, which makes a cycle impossible by
  construction and leaves the validation one comparison per edge. The list
  order is also the run order the operator follows.
- **New functions in `handoff.py`**, next to `blocked` and `pending`:
  - `SubTask`, a frozen dataclass (`title`, `surface`, `description`,
    `after: tuple[int, ...]`).
  - `split_of(payload) -> list[SubTask]`: the entries as stated, empty for a
    payload with no split, tolerant of junk the way `_lines` is.
  - `split_problems(subtasks) -> list[str]`: what makes a split unusable,
    one line each. Empty means it can be filed. The checks are listed in
    *Safety limits*.
  - `render` grows a `**Split**` section, one line per entry:
    `1. <title> (<surface>) — after 1, 2`. The description is not rendered
    into the parent's body: it goes to the sub-task's own file, where its
    roles read it.
- **Budget.** `_BUDGET_BYTES["arquitecto"]` stays 5120. A split carries no
  plan, so `changed`, `verified` and most of `risks` are empty, and four
  descriptions of about 900 bytes each fit beside the envelope. An
  over-budget split goes through `_shrink_over_budget` like any other return.
  `split: []` adds 11 bytes to every arquitecto envelope; `_ENVELOPE_BYTES`
  (160) already has that room.

### 2. The epic: what a split writes, and what an operator can write by hand

An epic is a parent task file plus two to four sub-task files that point at
it. The arquitecto's split is one way to write them; the operator writing the
same fields by hand is the other, and the dispatcher treats both the same.

**Naming: `<parent>-S<n>`, not letters and not `-D<n>`.** T-025 splits into
`T-025-S1`, `T-025-S2`, … in list order.

- Not `-D<n>`: that suffix is `debt.entry_id`'s, and a sub-task named
  `T-025-D1` would collide with the first debt entry T-025 files — same
  string in the debt index, the card title and the docs/debt path.
- Not `-a`/`-b`: letters run out at 26 and say nothing about kind, while the
  numbered suffix matches the one convention the harness already has for ids
  derived from a parent.
- Derived, not chosen by the arquitecto: the dispatcher can name the ids
  before the files exist, and a re-run of the same split produces the same
  names.
- The ids are single path components (`_is_single_path_component` holds), so
  `task_file_path`, the scratch dir and the api's task routes take them
  unchanged. A sub-task's own debt is `T-025-S1-D1`.

**Sub-task files.** One `<hive_tasks_dir>/T-025-S<n>.md` per entry, written
by a new `context_transfer.file_split(hive_dir, parent_id, subtasks)` through
`write_task_file` (atomic):

```yaml
---
task_id: T-025-S2
status: pending
owner: null
depends_on:
- T-025-S1
heartbeat: null
epic: T-025
description: |
  <the entry's description, verbatim>

  Part of epic T-025. Surface: <surface>.
---
```

- `depends_on` holds the sibling ids named by `after`, which is what the
  field was always meant for and what the api already serves.
- `epic` is a new optional `TaskFile` field naming the parent, written only
  when set, the way `kanban_issue_id` and `resolved_debt` are, so existing
  files and `TaskFile(...)` constructions are unaffected. It is the tag: the
  api serves it on every task row, so a screen can group or filter by epic
  without a new route.
- The body starts empty: a sub-task's phases begin on a clean trail, not on
  the parent's arquitecto section. The parent's file is one `epic` hop away
  for any role that wants it.
- No board card is opened at filing time. `open_cycle` already opens one on a
  task's first `run-task`, which keeps cards for sub-tasks nobody ran off the
  board. When it does, `_issue_title` prefixes `[epic T-025]` for a task with
  `epic` set — the same title-borne label `debt.LABEL` uses, because
  `create_issue` takes no labels.
- **Idempotent.** A sub-task file that already exists with the same
  description is left alone; one that exists with a different description
  makes the filing refuse (logged, `CycleOutcome.BLOCKED`), because a second
  split of the same parent disagreeing with the first is for a person to sort
  out, and overwriting would destroy whatever the first one's phases did.

**The parent's state.** Task file `status: epic`, plus a new optional
frontmatter field `split_into: [T-025-S1, T-025-S2]`. `epic` is a new value
of a free-string field, chosen over `blocked` because the two need opposite
things: a blocked task needs a decision, an epic needs nothing but its
children run. Its card's title gains the `[epic]` prefix and the card is sent
`"in_progress"` — the epic has open work — rather than `"blocked"`, which on a
`vibe_kanban` board maps to *In Review* and would say a person must act. The
parent's branch has only the arquitecto's commit, if any; it is never merged
and is cleaned up like a blocked task's.

**An epic closes itself.** When a sub-task is marked merged (below), the
dispatcher reads its `epic`, and if every id in the parent's `split_into` is
merged it sets the parent to `done` and moves its card to done. Nothing else
closes an epic, and an epic with an unmerged child stays open however long
that takes.

**An epic written by hand.** The operator may write the parent with
`status: epic` and `split_into`, and each child with `epic` and
`depends_on`, without any arquitecto phase. No command is needed for that:
the format is the contract, and `dispatch` validates it the first time it
reads one (see the limits). A `dispatch new-epic` helper is not part of this
design; it is worth writing only if hand-made epics become common.

**Running the parent again.** `run_task_cycle` refuses a task whose file says
`status: epic` before dispatching anything (log, `CycleOutcome.BLOCKED`):
running it again would pay an arquitecto to split it a second time. To
re-split, the operator deletes the sub-task files and sets the status back to
`pending` by hand.

### 3. The dispatcher path

**Where a split is detected.** In `run_task_cycle`, immediately after the
`handoff.blocked(arquitecto.handoff)` branch and before the revision loop:

```python
subtasks = handoff.split_of(arquitecto.handoff)
if subtasks:
    return _file_split(ctx, subtasks)
```

`_file_split` (new, in `dispatcher.py`) validates, writes, moves the card,
logs, and returns a new `CycleOutcome.SPLIT`. The `finally: close_cycle(ctx,
completed=False)` that already wraps the cycle does the cleanup: review
worktrees dropped, learnings stamped and orphaned, exactly as for a block. The
implementador, revisor and auditor never run, so no further account is picked.

`run_phase` already did the rest before the cycle sees the split: the parent's
body has the arquitecto's rendered section (with the `**Split**` list) and
`context_transfer.save_handoff` has kept the raw payload at
`<task_id>/handoffs/arquitecto.json`. That file is the source of truth for the
split: if the process dies between the handoff and the filing, the filing can
be redone from disk.

**The exit code.** `CycleOutcome.SPLIT` maps to a new `EXIT_SPLIT = 3` in
`dispatcher/cli.py`, printed as `task T-025 is now an epic: T-025-S1,
T-025-S2; run them in that order`. Not 0: the task did not ship, and a script
reading 0 would treat it as done. Not 1: nothing is wrong and nobody has to
read a log. Not 2 (argparse) and not 75 (`EXIT_HELD`, ADR 52).

**`run-phase` proposes, `file-split` files.** `run_single_phase --role
arquitecto` stays "the phase and nothing else": if its handoff has a split it
prints the proposed sub-tasks and files nothing. A run-phase is the
operator's trial run, so a split there is a proposal to read. To accept it, a
new `dispatch file-split --task-id T-025` reads the saved
`<task_id>/handoffs/arquitecto.json` and calls the same `_file_split`, with
the same validation, without running any model. That is also the recovery
path for a `run-task` that died between the handoff and the filing.

**Merged is recorded on the task.** Both merge paths — `_merge_task_branch`
when it returns True and `dispatch merge-task` on a `MERGED` outcome — call a
new `context_transfer.mark_merged(hive_dir, task_id, target)`, which writes a
`merged: {into: <branch>, at: <ISO-8601 UTC>}` block into the task file. Then
they run the epic check above. The field is optional and written only when
set, like `epic`. It is the fact the dependency gate reads, and it is the same
fact `docs/plans/review.md`'s *approve* produces, since approving there is the
merge.

### 4. Safety limits and what the operator approves

- **At most four sub-tasks, at least two.** A one-entry split is a plan with
  extra steps; more than four means the task was a project, and the operator
  should cut it, not a phase. `split_problems` checks: 2 ≤ len ≤ 4; every
  `title`, `surface` and `description` non-blank; every `after` entry an
  integer between 1 and its own position minus one; no duplicates in an
  `after`. A hand-written epic gets the equivalent checks on its files —
  `split_into` of 2 to 4 existing files, each with `epic` naming the parent,
  `depends_on` naming only siblings — the first time `open_cycle` reads one
  of its children; a failure blocks that child with the reasons.
- **A bad split ends the task as a block, not as a plan.** If
  `split_problems` returns anything, `_file_split` logs the problems, files
  nothing, moves the card to blocked and returns `CycleOutcome.BLOCKED`. Running
  the implementador on a task its arquitecto said was too big is the one
  outcome this whole path exists to prevent.
- **One level deep.** A task with `epic` set that returns a split is treated
  as a block with the reason "a task inside an epic cannot become an epic".
  The fan-out stays at four, and a sub-task that still does not fit means the
  first split was wrong — that is a person's call.
- **Nothing auto-dispatches.** The dispatcher has no queue, and this design
  does not add one: the split writes files and stops. The operator reads the
  parent's `**Split**` section and the sub-task files, may edit or delete any
  of them, and runs `run-task --task-id T-025-S1 --project <slug>` with no
  `--description` (`open_cycle` reads it from the file). Approval is the act of
  running it. This keeps the cost of a bad split at one arquitecto phase.
- **`depends_on` becomes a gate, on merged.** `open_cycle` refuses (log, card
  blocked, returns None, so the CLI exits `EXIT_BLOCKED`) a task whose
  `depends_on` names a task file that is missing or carries no `merged`
  block. `done` is not enough: with `merge_on_done` off, and under the review
  flow of `docs/plans/review.md`, `done` means "ready for a person to review",
  and a sub-task started on an unmerged sibling would branch from a base that
  does not have the work it reads — work the person may still reject. This
  is the first reader of the field in `dispatcher/`, and it protects every
  dependency, not only those a split wrote.
- **Quota and failover are untouched.** A split costs exactly one arquitecto
  phase, dispatched by `dispatch_phase` through the same account pick,
  PRE_COOLDOWN threshold and rate-limit failover as today. The filing runs no
  model and picks no account. A held arquitecto (`ctx.held`, ADR 52) returns
  `CycleOutcome.HELD` before the split check is reached, so held still means
  "wait for a reset". The usage log (ADR 53) records the arquitecto's call
  under the parent's id; each sub-task gets its own log when it runs.

### 5. What counts as one surface

P4 defines a surface by what the operator can see: one api route with its
tests and ADR, one screen, or one region of a screen. Work on the harness
itself — `dispatcher/`, mostly — has no route and no screen, so the
arquitecto needs a unit for it too. This design uses **one module's
contract**: the public functions and types one module offers others, with
their tests. Building this very design is the example: the handoff shape, the
task-file format and the cycle are three contracts, so three surfaces (the
build below has a fourth, the switch, for a reason it gives).

The alternative is a measured cap — "a task whose implementador is expected
to cost more than X% of a window" — which needs the median P5 was built to
produce and does not have yet (`token-economy.md`, *This does not give P4 its
budget*). So: module contract now, replaced or checked by P5's numbers once
enough usage logs exist. P4's approval is where the user confirms the unit.

### 6. The arquitecto's prompt

One paragraph appended to `project_docs._ARQUITECTO`, after the block
paragraph, so the two ways of ending a task early are read together:

> If the plan would change more than one surface — one api route with its
> tests and ADR, one screen, one region of a screen, or one module's contract
> — do not plan it. Return a split instead: in `split`, two to four sub-tasks
> in the order they run, each one surface, each with a description complete
> enough to be its own task (what it builds, what it reads from an earlier
> sub-task, when it is done), and in `after` the earlier entries that must be
> merged before it starts. The contract one surface hands the next — a
> route's row shape, a function's signature — is fixed by the sub-task that
> serves it, so put the server first. A split writes nothing to the repo: no
> ADR, no doc. Leave `changed` empty and `status` complete. A task that is
> already part of an epic cannot be split again; if it still spans surfaces,
> block it and say which. Split for size, not for difficulty: a single
> surface that is hard is still one task.

The schema descriptions in `_SPLIT_PROPERTY` repeat the rule in one line each
because they ride on every call; the paragraph is the only place the reasons
are.

### 7. Tests the implementing tasks write

`tests/dispatcher/test_handoff.py`:
- `schema_for("arquitecto")` has `split` and requires it; no other role has it.
- `split_of` returns `[]` for `None`, for `split: []`, and skips non-dict
  entries; returns `SubTask`s in order for a valid list.
- `split_problems`: one entry; five entries; blank title/surface/description;
  `after` naming itself, a later entry, 0, or a negative; duplicate `after`;
  a valid two- and four-entry split returns `[]`.
- `render` prints a `**Split**` section with positions and `after`, and no
  description text.

`tests/dispatcher/test_context_transfer.py`:
- `file_split` writes `T-x-S1..Sn` with `status: pending`, `depends_on` from
  `after`, `epic`, the description and the surface line; sets the parent's
  `status: epic` and `split_into`.
- Re-filing the same split is a no-op; a different description for an
  existing sub-task raises and writes nothing.
- `mark_merged` writes the `merged` block and leaves the rest of the file as
  it was; `unmet_dependencies` lists a missing dependency and an unmerged one,
  and nothing once both are merged.
- A file with no `epic`/`split_into`/`merged` round-trips unchanged (old
  files stay readable and are written back byte-identical).

`tests/dispatcher/test_dispatcher.py` (with the existing fakes for
`dispatch_phase`):
- An arquitecto returning a valid split: the cycle returns
  `CycleOutcome.SPLIT`, no implementador/revisor/auditor is dispatched, the
  parent's card gets `"in_progress"` and an `[epic]` title, the sub-task files
  exist.
- Blocked plus split: `CycleOutcome.BLOCKED`, nothing filed.
- Invalid split: `CycleOutcome.BLOCKED`, nothing filed, the implementador is
  not dispatched.
- A task with `epic` returning a split: blocked, nothing filed.
- `run_task_cycle` on a task with `status: epic` dispatches nothing.
- `open_cycle` refuses a task whose `depends_on` names a missing task, a
  `done` but unmerged one, and accepts it when all are merged; it refuses a
  child of a malformed hand-written epic with the reasons.
- A merge (in-cycle and through `merge-task`) marks the task merged; merging
  the last child of an epic sets the parent `done` and its card to done, and
  merging any other child does not.
- A sub-task's card title starts with `[epic T-x]`.

`tests/dispatcher/test_cli.py`: `run-task` exits 3 on `CycleOutcome.SPLIT`
and prints the sub-task ids; `run-phase --role arquitecto` with a split prints
it and files nothing; `file-split` files from a saved handoff and refuses an
invalid one.

`tests/dispatcher/test_project_docs.py`: `duties("arquitecto", …)` mentions
`split`; no other role's duties do.

### 8. How the implementation is itself split

Per P4's own rule, the build is four tasks, each one module's contract with its
tests, and ordered so nothing is live until the last one lands. Adding
`split` to `_ROLE_EXTRAS` is the switch: once the arquitecto's schema offers
the field it may use it, so the field and the dispatcher branch that acts on
it ship together. Task 3 is useful on its own, and hand-written epics work as
soon as it lands.

1. **handoff: the split shape, inert.** `_SPLIT_PROPERTY`, `SubTask`,
   `split_of`, `split_problems`, the `render` section. `_SPLIT_PROPERTY` is
   defined but not yet in `_ROLE_EXTRAS`. Tests in `test_handoff.py`.
2. **context_transfer: the epic format, inert.** `TaskFile.epic` /
   `split_into` / `merged`, `file_split`, `mark_merged`, and a reader
   `unmet_dependencies(hive_dir, task) -> list[str]`. Tests in
   `test_context_transfer.py`.
3. **dispatcher: merges recorded, dependencies gated, epics closed.** Both
   merge paths call `mark_merged` and the epic check; `open_cycle` refuses a
   task with unmet dependencies or a malformed epic; `_issue_title` adds the
   `[epic …]` prefix. Depends on 2.
4. **dispatcher + cli + prompt: turn the backstop on.** `_SPLIT_PROPERTY`
   into `_ROLE_EXTRAS["arquitecto"]`, the branch and `_file_split` in
   `run_task_cycle`, the `status: epic` refusal, `CycleOutcome.SPLIT`,
   `EXIT_SPLIT`, `run-phase`'s proposal output, `file-split`, the
   `_ARQUITECTO` paragraph, and the ADR below. Depends on 1–3. It touches
   three files, but they are one contract — the split outcome end to end —
   and none of them works without the others.

The console's view of an epic — tasks grouped under their epic, its progress
— is a screen, so a fifth task in `front/`, after 2 makes the api serve the
fields. It is not needed for the dispatcher side to work.

## Open questions

- **Should the board get its own `epic` column?** On a `vibe_kanban` board
  that is a `status_map` entry in `config.yaml` and a column, which is the
  operator's to add. Until then the parent reads *In Progress* with an
  `[epic]` title.
- **The surface unit for harness work.** *What counts as one surface*
  proposes one module's contract; P4's approval confirms it or replaces it.
- **Does the operator's own task-writing need a check too?** The split is a
  backstop for a task the operator sized wrong. Nothing here measures that
  after the fact against `git diff --stat`, which P4 also proposes; that is
  left to P5's usage numbers.

## Proposed ADR

To append to `docs/decisions.md` with the next number when the user approves
P4 and this design. Not appended now.

```markdown
## ADR <next> — Epics: the arquitecto may split a task, and a dependency waits for its merge

**Context.** `docs/plans/token-economy.md` P4 sizes a task to one surface so
its cycle fits one account's window, and makes the arquitecto a backstop for a
task that spans more. Until now its only early exit was a block, so a task too
big for one window ran the implementador anyway. The user also asked that the
sub-tasks of a big task stay tagged with it, as an epic. The design is
`docs/plans/task-split.md`.

**Decision.** The arquitecto's handoff gains a required `split` array
(`handoff._SPLIT_PROPERTY` in `_ROLE_EXTRAS["arquitecto"]`), empty unless the
task spans more than one surface. A non-empty split, after a block check that
wins over it, stops `run_task_cycle` before the implementador:
`_file_split` validates it (two to four entries; `after` names earlier entries
only; no split of a task that already has `epic`), writes one task file per
entry as `<parent>-S<n>` with `depends_on`, `epic` and the entry's
description, sets the parent to `status: epic` with `split_into`, titles its
card `[epic]` and moves it to in progress, and returns `CycleOutcome.SPLIT`,
exit code 3. An invalid split blocks the task. `run-phase` only prints a
split; `dispatch file-split` files it from the saved handoff. The operator may
write the same files by hand. Nothing is auto-dispatched. Both merge paths
write a `merged` block to the task file; `open_cycle` refuses a task whose
`depends_on` names one that is missing or not merged; merging an epic's last
child closes the epic. A task with `status: epic` is not dispatched again.

**Consequences.** A task too big for one window costs one arquitecto phase
instead of a full cycle. Sub-task ids never collide with debt ids
(`<task>-D<n>`). `depends_on` is enforced for every task, not only split ones,
and against the merge, not `done`, so a sub-task waits for a person's approval
of the one before it once `docs/plans/review.md` is built. The board shows an
epic as an in-progress card with an `[epic]` title until it has a column of
its own.

**Status.** Accepted.
```
