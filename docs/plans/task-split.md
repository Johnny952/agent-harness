# Task split — the arquitecto's backstop for a task that spans surfaces

**Status:** design only. Nothing here is built, and nothing here is approved:
it settles the decisions `docs/plans/token-economy.md` P4 leaves "for an ADR",
so that once P4 is approved the work can be dispatched in one-surface tasks.
No ADR is appended to `docs/decisions.md` until the user approves; the text
ready to append is under *Proposed ADR*. Written 2026-10-10 by the operator's
session, out of cycle, from the code at `c7a643e`.

## What P4 asks for

P4's rule is one surface per task, applied first by the operator when writing
the task, and then by the arquitecto as a backstop: "if its plan spans more
than one surface, it returns a split, with sub-tasks and their order, instead
of a plan". That needs two things the harness does not have:

- a `split` outcome in the arquitecto's handoff schema (`dispatcher/handoff.py`);
- a dispatcher path that files the sub-tasks and stops the cycle before the
  implementador runs.

The point is cost, not tidiness. The split is decided after one arquitecto
phase, which is the cheapest phase of the cycle; every surface that would
have ridden along in the implementador's context is a quadratic cost avoided.

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
- **Ids derived from a parent.** `debt.entry_id(task_id, n)` is
  `f"{task_id}-D{n}"`, derived rather than assigned so the dispatcher can name
  an id before the phase that writes it. T-024-D1 shows the same suffix has
  also been used by hand as a task id for out-of-cycle debt work.
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
                    "description": "1-based positions of earlier entries in this list that must be done first. Empty for none.",
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

### 2. The dispatcher path

**Where it is detected.** In `run_task_cycle`, immediately after the
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
split_from: T-025
description: |
  <the entry's description, verbatim>

  Split from T-025 by its arquitecto. Surface: <surface>.
---
```

- `depends_on` holds the sibling ids named by `after`, which is what the
  field was always meant for and what the api already serves.
- `split_from` is a new optional `TaskFile` field, written only when set, the
  way `kanban_issue_id` and `resolved_debt` are, so existing files and
  `TaskFile(...)` constructions are unaffected.
- The body starts empty: a sub-task's phases begin on a clean trail, not on
  the parent's arquitecto section. The parent's file is one `depends_on`
  hop away for any role that wants it.
- No board card is opened at filing time. `open_cycle` already opens one on a
  task's first `run-task`, which keeps cards for sub-tasks nobody ran off the
  board.
- **Idempotent.** A sub-task file that already exists with the same
  description is left alone; one that exists with a different description
  makes the filing refuse (logged, `CycleOutcome.BLOCKED`), because a second
  split of the same parent disagreeing with the first is for a person to sort
  out, and overwriting would destroy whatever the first one's phases did.

**The parent's state.** Task file `status: split`, plus a new optional
frontmatter field `split_into: [T-025-S1, T-025-S2]`. `split` is a new value
of a free-string field, chosen over `blocked` because the two need opposite
things: a blocked task needs a decision, a split task needs nothing and is
replaced by its children. The board has no `split` column and
`KanbanClient.set_status` skips an unmapped status with a warning, so the
dispatcher sends `"blocked"` to the board — a person has to act — and the log
line says why. The parent's branch has only the arquitecto's commit, if any;
it is never merged and is cleaned up like a blocked task's.

**Running the parent again.** `run_task_cycle` refuses a task whose file says
`status: split` before dispatching anything (log, `CycleOutcome.BLOCKED`):
running it again would pay an arquitecto to split it a second time. To
re-split, the operator deletes the sub-task files and sets the status back to
`pending` by hand.

**The exit code.** `CycleOutcome.SPLIT` maps to a new `EXIT_SPLIT = 3` in
`dispatcher/cli.py`, printed as `task T-025 was split into T-025-S1, T-025-S2;
run them in that order`. Not 0: the task did not ship, and a script reading 0
would treat it as done. Not 1: nothing is wrong and nobody has to read a log.
Not 2 (argparse) and not 75 (`EXIT_HELD`, ADR 52).

**`run-phase`.** `run_single_phase --role arquitecto` runs the phase and
nothing else, today including blocks; it keeps doing so. If its handoff has a
split it logs that `run-task` files splits and the payload is on disk. This is
an open question below, not a decision.

### 3. Safety limits and what the operator approves

- **At most four sub-tasks, at least two.** A one-entry split is a plan with
  extra steps; more than four means the task was a project, and the operator
  should cut it, not a phase. `split_problems` checks: 2 ≤ len ≤ 4; every
  `title`, `surface` and `description` non-blank; every `after` entry an
  integer between 1 and its own position minus one; no duplicates in an
  `after`.
- **A bad split ends the task as a block, not as a plan.** If
  `split_problems` returns anything, `_file_split` logs the problems, files
  nothing, moves the card to blocked and returns `CycleOutcome.BLOCKED`. Running
  the implementador on a task its arquitecto said was too big is the one
  outcome this whole path exists to prevent.
- **No split of a split.** A task with `split_from` set that returns a split
  is treated as a block with the reason "a sub-task cannot be split again".
  Depth stays one, the fan-out stays at four, and a sub-task that still does
  not fit means the first split was wrong — that is a person's call.
- **Nothing auto-dispatches.** The dispatcher has no queue, and this design
  does not add one: the split writes files and stops. The operator reads the
  parent's `**Split**` section and the sub-task files, may edit or delete any
  of them, and runs `run-task --task-id T-025-S1 --project <slug>` with no
  `--description` (`open_cycle` reads it from the file). Approval is the act of
  running it. This keeps the cost of a bad split at one arquitecto phase.
- **`depends_on` becomes a gate.** `open_cycle` refuses (log, card blocked,
  returns None, so the CLI exits `EXIT_BLOCKED`) a task whose `depends_on`
  names a task file that is missing or whose `status` is not `done`. This is
  the first reader of the field in `dispatcher/`, and it protects every
  dependency, not only those a split wrote. `done` is what the auditor's
  `final=True` handoff writes; whether it should also require the merge is
  an open question.
- **Quota and failover are untouched.** A split costs exactly one arquitecto
  phase, dispatched by `dispatch_phase` through the same account pick,
  PRE_COOLDOWN threshold and rate-limit failover as today. The filing runs no
  model and picks no account. A held arquitecto (`ctx.held`, ADR 52) returns
  `CycleOutcome.HELD` before the split check is reached, so held still means
  "wait for a reset". The usage log (ADR 53) records the arquitecto's call
  under the parent's id; each sub-task gets its own log when it runs.

### 4. The arquitecto's prompt

One paragraph appended to `project_docs._ARQUITECTO`, after the block
paragraph, so the two ways of ending a task early are read together:

> If the plan would change more than one surface — one api route with its
> tests and ADR, one screen, one region of a screen, or one module's contract
> — do not plan it. Return a split instead: in `split`, two to four sub-tasks
> in the order they run, each one surface, each with a description complete
> enough to be its own task (what it builds, what it reads from an earlier
> sub-task, when it is done), and in `after` the earlier entries it needs
> first. The contract one surface hands the next — a route's row shape, a
> function's signature — is fixed by the sub-task that serves it, so put the
> server first. A split writes nothing to the repo: no ADR, no doc. Leave
> `changed` empty and `status` complete. A task already split from another
> cannot be split again; if it still spans surfaces, block it and say which.
> Split for size, not for difficulty: a single surface that is hard is still
> one task.

The schema descriptions in `_SPLIT_PROPERTY` repeat the rule in one line each
because they ride on every call; the paragraph is the only place the reasons
are.

### 5. Tests the implementing tasks write

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
  `after`, `split_from`, the description and the surface line; sets the
  parent's `status: split` and `split_into`.
- Re-filing the same split is a no-op; a different description for an
  existing sub-task raises and writes nothing.
- A file with no `split_from`/`split_into` round-trips unchanged (old files
  stay readable and are written back byte-identical).

`tests/dispatcher/test_dispatcher.py` (with the existing fakes for
`dispatch_phase`):
- An arquitecto returning a valid split: the cycle returns
  `CycleOutcome.SPLIT`, no implementador/revisor/auditor is dispatched, the
  card gets `"blocked"`, the sub-task files exist.
- Blocked plus split: `CycleOutcome.BLOCKED`, nothing filed.
- Invalid split: `CycleOutcome.BLOCKED`, nothing filed, the implementador is
  not dispatched.
- A task with `split_from` returning a split: blocked, nothing filed.
- `run_task_cycle` on a task with `status: split` dispatches nothing.
- `open_cycle` refuses a task whose `depends_on` names a missing task or one
  not `done`, and accepts it when all are `done`.

`tests/dispatcher/test_cli.py`: `run-task` exits 3 on `CycleOutcome.SPLIT`
and prints the sub-task ids.

`tests/dispatcher/test_project_docs.py`: `duties("arquitecto", …)` mentions
`split`; no other role's duties do.

### 6. How the implementation is itself split

Per P4's own rule, the build is four tasks, each one module's contract with its
tests, and ordered so nothing is live until the last one lands. Adding
`split` to `_ROLE_EXTRAS` is the switch: once the arquitecto's schema offers
the field it may use it, so the field and the dispatcher branch that acts on
it ship together.

1. **handoff: the split shape, inert.** `_SPLIT_PROPERTY`, `SubTask`,
   `split_of`, `split_problems`, the `render` section. `_SPLIT_PROPERTY` is
   defined but not yet in `_ROLE_EXTRAS`. Tests in `test_handoff.py`.
2. **context_transfer: filing sub-tasks, inert.** `TaskFile.split_from` /
   `split_into`, `file_split`, and a reader `unmet_dependencies(hive_dir,
   task) -> list[str]`. Tests in `test_context_transfer.py`.
3. **dispatcher: `depends_on` as a gate.** `open_cycle` refuses a task with
   unmet dependencies, using task 2's reader. Useful alone; depends on 2.
4. **dispatcher + cli + prompt: turn it on.** `_SPLIT_PROPERTY` into
   `_ROLE_EXTRAS["arquitecto"]`, the branch and `_file_split` in
   `run_task_cycle`, the `status: split` refusal, `CycleOutcome.SPLIT`,
   `EXIT_SPLIT`, the `_ARQUITECTO` paragraph, and the ADR below. Depends on
   1–3. It touches three files, but they are one contract — the split
   outcome end to end — and none of them works without the others.

## Open questions

- **Is `done` enough for a dependency, or must it be merged?** With
  `merge_on_done` off, `T-025-S1` can be `done` while its route is not on the
  base `T-025-S2` branches from. Requiring the merge needs a merged marker the
  task file does not carry today. Proposed: `done` for now, and the gate warns
  when `merge_on_done` is off.
- **Does `run-phase --role arquitecto` file a split?** Filing there would let
  a task driven by hand be split; not filing keeps `run-phase` "the phase and
  nothing else". Proposed: not, with a log line; revisit if hand-driving
  becomes common.
- **Should the board get its own `split` status?** That is a `status_map`
  entry in `config.yaml` and a column on the board, which is the operator's to
  add. Until then the card reads blocked.
- **The surface proxy for dispatcher work.** P4 defines a surface by the api
  and the console. This page adds "one module's contract" so that harness
  tasks have a unit too; P4's approval should say whether that is the right
  unit.
- **Does the operator's own task-writing need a check too?** The split is a
  backstop for a task the operator sized wrong. Nothing here measures that
  after the fact against `git diff --stat`, which P4 also proposes; that is
  left to P5's usage numbers.

## Proposed ADR

To append to `docs/decisions.md` with the next number when the user approves
P4 and this design. Not appended now.

```markdown
## ADR <next> — The arquitecto may return a split instead of a plan

**Context.** `docs/plans/token-economy.md` P4 sizes a task to one surface so
its cycle fits one account's window, and makes the arquitecto a backstop for a
task that spans more. Until now its only early exit was a block, so a task too
big for one window ran the implementador anyway. The design is
`docs/plans/task-split.md`.

**Decision.** The arquitecto's handoff gains a required `split` array
(`handoff._SPLIT_PROPERTY` in `_ROLE_EXTRAS["arquitecto"]`), empty unless the
task spans more than one surface. A non-empty split, after a block check that
wins over it, stops `run_task_cycle` before the implementador:
`_file_split` validates it (two to four entries; `after` names earlier entries
only; no split of a task that has `split_from`), writes one task file per entry
as `<parent>-S<n>` with `depends_on`, `split_from` and the entry's
description, sets the parent to `status: split` with `split_into`, moves the
card to blocked and returns `CycleOutcome.SPLIT`, exit code 3. An invalid split
blocks the task. Nothing is auto-dispatched: the operator runs each sub-task.
`open_cycle` refuses a task whose `depends_on` is missing or not `done`. A task
with `status: split` is not dispatched again.

**Consequences.** A task too big for one window costs one arquitecto phase
instead of a full cycle. Sub-task ids never collide with debt ids
(`<task>-D<n>`). `depends_on` is enforced for every task, not only split ones.
The board shows a split parent as blocked until it has a status of its own.
A dependency counts when its task is `done`, which is not the same as merged
when `merge_on_done` is off.

**Status.** Accepted.
```
