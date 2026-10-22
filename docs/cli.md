# Dispatcher CLI reference

Every subcommand of `dispatcher/cli.py`, its arguments with their unit and
default, and its exit codes. `tests/dispatcher/test_cli_reference.py` builds the
parser (`cli.build_parser()`) and fails when a subcommand, an option or an exit
code is here and not in the parser, or the other way round. It checks names
only: units, defaults and prose are kept true by whoever changes the parser
(`docs/decisions.md` ADR 334).

Invoked as `python -m dispatcher.cli --config config.yaml <command>`, or through
the compose `dispatcher` service (README, *5. Run a task*).

## Global options

| Argument | Unit | Default | What it does |
|---|---|---|---|
| `--config PATH` | file path | none, required | The `config.yaml` to load. Goes before the command. |

## Exit codes

| Code | Name | Commands | Meaning |
|---|---|---|---|
| `0` | | all | Done. |
| `1` | `EXIT_BLOCKED` | most | A run that stopped and needs a person: a blocked task, a refused merge, an entry that is not there, a queue that was not confirmed or has a failed task. An uncaught exception is also 1, with a traceback. |
| `2` | | all | Usage error from argparse (`--limit` below the floor included). `worker` also exits 2 when another worker holds `worker.lock`. |
| `3` | `EXIT_SPLIT` | `run-task`, `command-verdict`, `command-resume` | The arquitecto split the task and the split was filed (ADR 60). |
| `4` | `EXIT_NOT_PUSHED` | `merge-task --push`, `run-queue` | Merged, but the push was refused (ADR 78). |
| `5` | `EXIT_LIMIT` | `run-queue` | The queue stopped on its token limit with tasks left (ADR 79). |
| `75` | `EXIT_HELD` | `run-task`, `run-phase`, `command-verdict`, `command-resume`, `draft-specs` | No account could take the next phase, or the project is busy; try again later (ADR 52, ADR 200). |

## One cycle per project

A project runs one cycle at a time (ADR 200). `run-task`, `run-phase`,
`command-verdict` and `command-resume` on a busy project print
`project <slug> is busy: ...; <task> was not started`, write nothing and exit
75. `run-queue` waits instead (it polls every 300 s,
`queue.WAIT_POLL_SECONDS`) and holds the project for its whole run. The worker
waits as well.

From the CLI, more than one cycle of one project at once only runs through
`run-queue --parallel N` (ADR 200, ADR 83 for the write windows). The worker's
`project_parallel` (ADR 306) is the other path, and is not a CLI verb.

## Commands

### `run-task`

The whole role cycle for one task, from the arquitecto.

| Argument | Unit | Default | What it does |
|---|---|---|---|
| `--task-id` | task id | required | The task to run. |
| `--project` | project slug | required | The project it belongs to. |
| `--description` | text, in sections | none | What the task asks for (`## Goal` and `## Done when` required). Optional only when the task has a stored description. |
| `--description-file` | file path, `-` is stdin | none | The same, read from a file. Exclusive with `--description`. |

Exits: 0, 1, 2, 3, 75.

### `run-phase`

One role's phase on a task already under way; the repair path.

| Argument | Unit | Default | What it does |
|---|---|---|---|
| `--task-id` | task id | required | The task. |
| `--project` | project slug | required | The project. |
| `--role` | role name | required | Which role's turn to run. |
| `--round` | revision round, from 1 | none | The round the phase belongs to. |
| `--final` | flag | off | The phase closes the task (the auditor). |
| `--note` | text | empty | Extra context appended to this phase's prompt. |

Exits: 0, 1, 2, 75.

### `file-split`

Files the split in a task's saved arquitecto handoff. Runs no model.

| Argument | Unit | Default | What it does |
|---|---|---|---|
| `--task-id` | task id | required | The task. |
| `--project` | project slug | required | The project. |

Exits: 0, 1, 2.

### `command-verdict`

Run, deny or allow the command request a task is blocked on, then carry the cycle on.

| Argument | Unit | Default | What it does |
|---|---|---|---|
| `task` | task id | required | The blocked task. |
| `n` | request number, from 1 | required | Which accepted request. |
| `verdict` | `run`, `deny` or `allow` | required | The ruling. |
| `--project` | project slug | required | The project. |
| `--comment` | text | none | What the phase is told with the verdict; the reason on deny. |
| `--prefix` | token count, from 2 | none | On allow only: allow the first N tokens followed by anything. |

Exits: 0, 1, 2, 3, 75.

### `command-resume`

Resumes the phase on the verdict a command-request block already has.

| Argument | Unit | Default | What it does |
|---|---|---|---|
| `task` | task id | required | The blocked task. |
| `--project` | project slug | required | The project. |

Exits: 0, 1, 2, 3, 75.

### `allowlist`

Lists a project's allowlist by role, or revokes one entry.

| Argument | Unit | Default | What it does |
|---|---|---|---|
| `--project` | project slug | required | The project. |
| `--role` | role name | all roles | Only this role's entries; required with `--revoke`. |
| `--revoke` | Bash pattern | none | Remove the role's entry for this exact pattern. |

Exits: 0, 1, 2.

### `bootstrap-project`

Creates the per-project directory on one account's container.

| Argument | Unit | Default | What it does |
|---|---|---|---|
| `--account` | account name | required | The account from `config.yaml`. |
| `--project` | project slug | required | The project. |

Exits: 0, 1, 2.

### `cleanup-task`

Deletes every worktree of a task; the branch `agent/task/<task-id>` is kept.

| Argument | Unit | Default | What it does |
|---|---|---|---|
| `--task-id` | task id | required | The task. |
| `--project` | project slug | required | The project. |

Exits: 0, 1, 2.

### `close-task`

Sets `status: done` on a task that has a `merged` block (ADR 255).

| Argument | Unit | Default | What it does |
|---|---|---|---|
| `--task-id` | task id | required | The task. |
| `--project` | project slug | required | The project. |

Exits: 0, 1, 2.

### `merge-task`

Merges `agent/task/<task-id>` into the branch the project is checked out on,
after the pre-merge check.

| Argument | Unit | Default | What it does |
|---|---|---|---|
| `--task-id` | task id | required | The task. |
| `--project` | project slug | required | The project. |
| `--push` | flag | off | Push the base to its upstream afterwards, never forced. |

Exits: 0, 1, 2, 4.

### `resolve-conflict`

Resolves a task the pre-merge check moved to `conflict` (ADR 277).

| Argument | Unit | Default | What it does |
|---|---|---|---|
| `--task-id` | task id | required | The task. |
| `--project` | project slug | required | The project. |

Exits: 0, 1, 2.

### `learnings`

Reads and rules on the traps the phases filed. With no flag it prints every entry.

| Argument | Unit | Default | What it does |
|---|---|---|---|
| `--project` | project slug | all | Only the entries that project's phases would be shown. |
| `--promote` | entry ref | none | Move one inbox entry into the cross-project store. |
| `--confirm` | entry ref | none | Mark an entry confirmed. |
| `--unconfirm` | entry ref | none | Take a confirmation back. |
| `--refute` | entry ref | none | Retire an entry on evidence; it stays on disk. |
| `--drop` | entry ref | none | Delete an entry. |

The last five are mutually exclusive.

Exits: 0, 1, 2.

### `status`

What every account and every locked task is doing now. Writes nothing.

| Argument | Unit | Default | What it does |
|---|---|---|---|
| `--probe` | flag | off | Also run `/usage` in each account's container. |

Exits: 0, 1, 2.

### `release-account`

Hands one account back to the pool after a cycle died without returning it.

| Argument | Unit | Default | What it does |
|---|---|---|---|
| `--name` | account name | required | The account. |
| `--force` | flag | off | Release over every guard, including a live heartbeat. |
| `--task` | task id | all holds | Free only this task's hold. |
| `--clear-rate-limit` | flag | off | Also forget the recorded refusal. |

Exits: 0, 1, 2.

### `project-keygen`

Makes an SSH deploy key for one project; prints the public half only.

| Argument | Unit | Default | What it does |
|---|---|---|---|
| `--project` | project slug | required | The project. |
| `--replace` | flag | off | Make a new pair over an existing one. |
| `--keys-dir` | directory path | `project_keys_dir` | Where the keys live on this machine. |

Exits: 0, 1, 2.

### `run-queue`

Runs a confirmed list of tasks, one cycle each, in dependency order, until they
are done or the token limit is reached (ADR 79). A new queue needs `--project`,
`--task-id` and/or `--ready`, and `--limit`; `--resume` goes on with a saved one
and takes only `--limit`, `--parallel` and `--yes` (ADR 336).

| Argument | Unit | Default | What it does |
|---|---|---|---|
| `--project` | project slug | none; required unless `--resume` | The project the queue runs in. |
| `--task-id` | task ids, repeatable | none | The tasks to run, each with a stored description; the listed order is kept. |
| `--ready` | `N` tasks | `0` | Add the first N tasks of the board's Ready order after the listed ones (ADR 132). At least one of `--task-id` and `--ready` is needed. |
| `--limit` | `TOKENS`, measured tokens, not tasks | none; required unless `--resume` | The ceiling on the queue's spend: input + output + cache creation + cache read, summed from the usage log (ADR 79). At least `100000` (ADR 333); a smaller value is a usage error (exit 2) and nothing is saved or started. |
| `--parallel` | cycles at a time | `1` for a new queue; the queue's own on `--resume` | At most the sum of `max_concurrent_phases` over the accounts (ADR 252); tasks with overlapping surfaces never write at once (ADR 83). With `--resume` it changes the queue's parallelism (ADR 336). |
| `--yes` | flag | off | Start without asking; the confirmation screen is still printed. |
| `--resume` | `QUEUE_ID` | none | Go on with a saved queue under its own project and tasks. Only `--limit`, `--parallel` and `--yes` may go with it; `--project`, `--task-id` and `--ready` are a usage error (exit 2) that names them (ADR 336). |

The limit is checked before each task and before each phase. A phase that has
started finishes, so a queue can pass its limit by one phase. When the spend has
reached the limit no new phase or task starts, the queue's status becomes
`limit_reached`, the tasks it stopped are left in that state, and the command
exits 5. Raise the limit on the same queue with `--resume QUEUE_ID --limit
TOKENS` (ADR 336): the new limit must be at least `100000` and above the spend
already recorded in the queue, otherwise it is a usage error (exit 2) naming
both numbers and nothing runs. The tasks the limit stopped go back to pending
and run again from the arquitecto, their earlier spend still counted; tasks
already merged, finished or failed do not run again. A resume that changes the
limit or `--parallel` asks the same yes as a new queue (unless `--yes`), prints
the effective values and a "Changed on resume" list, and records each change
(old and new value, UTC time) under `changes` in the queue file. A plain
`--resume` keeps the saved limit and asks nothing.

Queue state is saved in `<state_dir>/queues/<queue_id>.json`, written
atomically on every change; ids look like `q-20261022T120000Z-ab12`. The
command prints the id and the path when it saves, and `--resume <queue_id>` goes
on from there after a kill or a stop. A queue that was not confirmed (no `--yes`
and no `y`) is not saved and exits 1.

Exits: 0, 1 (not confirmed, or a task failed), 2, 4, 5.

### `draft-specs`

Has the arquitecto draft a plan's tasks as specs for the owner to accept (ADR 82).

| Argument | Unit | Default | What it does |
|---|---|---|---|
| `--project` | project slug | required | The project. |
| `--plan` | path inside the project | required | The plan to draft from, e.g. `docs/plans/x.md`. |

Exits: 0, 1, 2, 75.

### `accept-drafts`

Files drafts as pending tasks with fresh ids (ADR 82).

| Argument | Unit | Default | What it does |
|---|---|---|---|
| `DRAFT_ID` | draft set id | required | The set `draft-specs` printed. |
| `--only` | draft names (D1, D2...), repeatable | all | Accept only these drafts. |

Exits: 0, 1, 2.

### `worker`

Drains the action queue under `<state_dir>/actions` until signalled, one at a
time (ADR 142). No arguments.

Exits: 0, 2 (another worker holds `worker.lock`).

### `migrate`

Moves descriptions out of task frontmatter, writes missing project records and
stamps cards that name no project (ADR 199). Idempotent.

| Argument | Unit | Default | What it does |
|---|---|---|---|
| `--project` | project slug | the only project, if there is one | The project the cards that name none belong to. |

Exits: 0, 1, 2.

### `import-debt`

Files every open entry of a debt index as a proposed backlog item (ADR 131).

| Argument | Unit | Default | What it does |
|---|---|---|---|
| `--project` | project slug | one of `--project`, `--index` required | Read the debt index of this project's checkout. |
| `--index` | file path | see `--project` | Read the index from this file instead. |
| `--dry-run` | flag | off | Print what would be filed; write nothing. |

`--project` and `--index` are mutually exclusive.

Exits: 0, 1, 2.
