---
status: built
approved: 2026-10-15
built: 2026-10-17
supersedes: []
---

# Autonomy — fewer stops for a person, and more than one task per project

**Status:** built. Designed by the main thread on 2026-10-15 at the user's
word; all ten build tasks have landed. A-1 to A-4 landed on 2026-10-15, and
A-5, A-6 and B-1 to B-3 on 2026-10-17. B-4 landed on 2026-10-17 as T-057
(ADR 85) and T-058 (ADR 88), after the measurement of *Decisions*, 8 (T-056,
ADR 84, *go*). `max_concurrent_phases` defaults to 1, so a second phase on
an account starts only when the owner raises it in the config. Priority 2
is other backlog items and is not tracked here. The decisions below are the
design as written, and the ADRs named narrow them. The charter rulings it needs were given the same day:
C-4 (*Amended* 2026-10-15, the queue), C-5 and C-12 (*Amended* 2026-10-15,
the automatic approve). It builds on `docs/plans/review.md` (the pre-merge
check, the integrador), `docs/plans/board-service.md` (*Start epic*, the
spending limit) and `docs/plans/projects.md` (*Decisions*, 5, which it
narrows for parallel work within a project).

## The goal

The measured pace of the harness is about one task a day when a person
dispatches, reads and merges every task, and about eight a day when the
main thread chains them with subagents (2026-10-10 to 2026-10-14). The gap
is not quota: it is the person's turn between tasks. The user named three
ways to close it on 2026-10-15:

1. **Approve without a person, when nothing needed one.** A task that passed
   every gate, the revisor and the auditor, merges cleanly and stays inside
   its surface is merged and pushed by the dispatcher.
2. **Authorize a batch, not a task.** The owner confirms a list of tasks and
   a spending limit once, and the dispatcher chains them.
3. **Agents draft the specs.** A plan in `docs/plans/` becomes task
   descriptions written by an agent, which the owner approves before any is
   a task.

And a fourth, about parallel work within one project. In the user's words,
translated: what makes two tasks that edit the same file block each other
is the implementador, the revisor and the auditor; the arquitectos and the
other roles could run in parallel. So the unit that is serialized is a
*write window* over a surface, not a project.

## What exists today

- **`merge_on_done`** (`dispatcher/config.py`, off by default) merges a task
  after the auditor (`_merge_task_branch` in `dispatcher/dispatcher.py`),
  with no condition beyond the cycle finishing. Nothing in the dispatcher
  pushes.
- **`## Surface`** is an optional section of a task's description
  (`dispatcher/tasks.py` `OPTIONAL`), free prose. Nothing reads it as paths.
- **The gates** already compute the paths a task changed
  (`dispatcher/gates.py` `changed_paths`).
- **Locks.** A per-task lock with a heartbeat (`tasks.acquire_lock`) and a
  per-claim account lock (ADR 71). No lock covers the project's checkout,
  where `update_project_branch` and the merge run.
- **One phase per account.** An account is `BUSY` with one phase at a time
  (`dispatcher/state_machine.py`), so the pool's slots are its accounts
  (`projects.md`, *Decisions*, 6).
- **The usage log** measures every call (`.hive/tasks/<id>/usage/calls.jsonl`,
  ADR 53), so a spending limit can be measured, never estimated.
- **Specs** are written by the main thread, by hand, as
  `docs/plans/console-config.md`'s tasks were.

## Decisions

### 1. The automatic approve is a check, and the merge it allows

Turned on per project by the owner (`auto_approve`, off by default), it runs
where `merge_on_done` runs today, after the auditor, and approves only when
every condition of C-12 (*Amended* 2026-10-15) holds:

- every gate green, the revisor `approved` with no open finding, the auditor
  with no finding and no debt filed this cycle;
- the pre-merge check of *Decisions*, 2 passes;
- every path the task changed is under one of its *Surface* paths
  (*Decisions*, 5), and none matches the protected list: `docs/charter.md`,
  `.env*`, `config.yaml`, `docker/compose/**`, `.data/**`, the credential
  paths named in the config, plus the project's own `protected_paths`;
- the cycle was started by a person's authorization (a run-task the operator
  typed, a verdict, or a queue of *Decisions*, 3).

When one fails, the task ends `done` and unmerged, as today, with a comment
naming the condition that failed, so the owner knows what to look at. When
all hold, the dispatcher merges, pushes the base (*Decisions*, 4) and writes
a comment naming each condition checked and the merge commit. A task with no
*Surface* paths is never approved automatically.

`merge_on_done` stays as it is for projects that do not turn this on. A
project with both on gets the automatic approve: the conditions win over the
unconditional merge.

### 2. The pre-merge check runs before every merge, and spends no quota

It is `review.md` *Decisions*, 6, step 1, built on the CLI side first:
merge the base into the task branch (a merge, never a rebase), run the test
gate on the result, and abort on a conflict or a gate that goes red. A task
that fails it moves to `conflict` with a comment naming the files and the
commit the base was at. Resolving it is the integrador's, under its own
verdict (C-12); until that role exists, the operator resolves it.

### 3. A queue is a list the owner confirmed, run to its limit

`dispatch run-queue` takes a project, a list of task ids and a spending
limit, and prints what C-4 (*Amended* 2026-10-15) requires before it starts:
each task's id and title, the roles its cycle will run, the order, the
parallelism (*Decisions*, 6) and the limit. It starts only on the operator's
yes (or `--yes` on a line the operator typed).

- **Order** follows `depends_on`. A task whose dependency is not merged
  waits; with the automatic approve on, it is merged by then, and without
  it, the queue moves to the next task that does not depend on it.
- **The limit** is in the usage log's measured tokens. Each finished phase
  is added to the queue's total; when the total reaches the limit, no new
  phase starts and the queue stops with `limit_reached`. A phase already
  running is let finish. Which token sum counts is decided by the task's ADR.
- **A task that fails** (blocked, red, past its budget, out of retries) is
  not retried. Its dependents are skipped with a comment; the others go on.
- **Waiting for quota is free.** When no account is open, the queue waits
  for the soonest reset, as `run-phase` does today, and resumes.
- **It survives a crash.** The queue is a file in `state_dir` with each
  task's state; `run-queue --resume <queue-id>` goes on from it, under the
  same authorization, because nothing new is added.
- **The console's *Start epic*** (`board-service.md`) is this queue started
  from a page; building the page is not part of this plan.

### 4. The base push is the dispatcher's, on an approve

After a merge on an automatic approve, or on `merge-task --push`, the
dispatcher pushes the base to its upstream, never with force. A refused
push (the upstream moved) leaves the base merged locally, the task `done`
with a comment, and the queue stopped, because every later task would build
on a base that is not the remote's. Where the credential lives is an ADR of
that task (`review.md`, *Decisions*, 4, the same ADR).

### 5. *Surface* names paths

For the two decisions that read it, a task's *Surface* section must name
repo paths: each backtick-quoted path or glob in the section is one entry
(`dispatcher/config.py`, `tests/dispatcher/`, `docs/plans/*.md`). A
directory covers what is under it. Prose around them is kept for the roles.
A *Surface* with no path in it means the whole repo: it never auto-approves,
and in parallel work it overlaps everything.

Some paths are shared by nearly every task and are not counted against
overlap: the append-only logs (`docs/decisions.md`, the *Results log* of
`docs/ROADMAP.md`, `docs/implementations/`, `docs/learnings/`). Two tasks
appending to them do not conflict in intent; *Decisions*, 7 makes them not
conflict in git either. They still count for the automatic approve's
"inside the surface" check only if the task did not declare them, which it
need not: an append to those logs is allowed to every task.

### 6. Parallel within a project: the write window is serialized, not the project

This replaces the "one cycle per project" rule of `projects.md`
(*Decisions*, 5) for a queue started with `--parallel N` (default 1, which
keeps today's behaviour). The cycle is split in two by what it touches:

- **Free roles**: the cartografo and the arquitecto. They run on their own
  task branch and worktree, which parallel tasks never share (`review.md`,
  *What exists today*), so any number of them may run at once, up to the
  open slots. The cartografo maps the project once: a second task that
  needs mapping waits for the first mapping and re-checks `_needs_mapping`.
- **The write window**: the implementador, its revision rounds with the
  revisor, the auditor, the pre-merge check, the merge and the push. A task
  takes a *surface lock* on its *Surface* paths before its first
  implementador phase and holds it until it merges or leaves the cycle. Two
  tasks whose surfaces overlap (a path of one is a prefix of a path of the
  other) are never in their write window at once; tasks with disjoint
  surfaces are.
- **The plan may go stale, and is checked cheaply.** Before the first
  implementador the dispatcher merges the base into the task's branch, in
  every path that reaches one (a queue, a worker start grouped or not, a
  request for changes, the CLI `run-task`, a verdict's resume; ADR 339). A
  scheduler only decides *when*: with one, the merge happens as the task
  takes its surface lock. If the merge brought in changes to a path the
  arquitecto's handoff lists under *Changed* or *Surface*, the implementador
  is told so in its prompt, with the merged task's id and changed paths, and
  may block back to the arquitecto (`blocks` with a re-plan reason) instead
  of following a plan for a base that moved. No phase is spent when nothing
  moved. A conflict is left in progress in the writers' checkout and the
  implementador resolves it by editing the files; the dispatcher's round
  commit is the merge commit and refuses markers. No phase ever merges the
  base, plans to, or files a `git merge` request (ADR 340).
- **The base is one resource.** Every operation on the project's checkout
  (`update_project_branch`, the mapping commit, the pre-merge check, the
  merge, the push) takes a *base lock*, held only for that operation. It is
  crash-safe like the task lock: a heartbeat, a TTL, and a holder that is
  checked before it is broken.
- **Slots still bound it.** `--parallel N` is a ceiling, not a promise: with
  two accounts there are two slots, and an account holds one phase. More
  than one phase per account is *Decisions*, 8. Since T-123 the ceiling is
  the sum of each account's `max_concurrent_phases` (`docs/decisions.md`
  ADR 252).
- **Order between tasks of one queue** stays the confirmed order for who
  takes a free slot first, and for who takes a contested surface lock first.

### 7. Append-only files merge without conflict, and ADR numbers are reserved

- **Union merge.** The project's `.gitattributes` marks the append-only logs
  of *Decisions*, 5 `merge=union`, so two appends at the end merge as both.
- **ADR numbers are handed out.** Two arquitectos running at once would both
  write the next ADR number. The dispatcher keeps a counter per project in
  `state_dir`, seeded from the highest number in `docs/decisions.md`, and
  reserves a block (default 3) for each task when its arquitecto starts. The
  prompt tells the role "your ADR numbers are N to N+2". An unused number is
  a gap, and gaps are allowed: the file is ordered by number, not dense.
- **A gate catches the rest.** After the pre-merge check's merge, a gate
  fails on a duplicate ADR number or heading, which sends the task to
  `conflict` instead of merging a broken log.

### 8. More than one phase per account is an experiment, later

A Max account can run more than one session at a time; the harness counts
it as one slot. `max_concurrent_phases` per account (default 1) would turn
`BUSY` into a count. It is the last task, and it starts with a measurement:
two phases on one account, its rate limits and its usage, on a task the
owner names. Its value is nil on a pool of Pro accounts, where each account
is already one slot.

### 9. Agents draft the specs; the owner approves them

`dispatch draft-specs --project <slug> --plan <path>` runs one phase of the
arquitecto in *draft* mode: it reads the plan and the code it names, and
writes one draft per build step, each a description with *Goal*, *Done
when*, *Surface* (as paths, *Decisions*, 5), *Out of scope* and
`depends_on` between drafts. It is a run like any other: the operator
authorizes it (C-4), and its role model may be cheaper than the
arquitecto's (`role_model`, keyed `arquitecto-draft`).

- **Drafts are not tasks.** They land in `.hive/board/drafts/<draft-id>/`,
  validated by `tasks.py`'s section rules, and nothing dispatches them
  (C-4, *Amended* 2026-10-15: an agent's draft never puts itself on a
  queue).
- **The owner approves them.** `dispatch accept-drafts <draft-id>` creates
  the pending tasks with fresh ids and their `depends_on` rewritten to those
  ids, optionally `--only` some drafts. Editing a draft before accepting it
  is editing a file. The console's backlog *accept* (`board-service.md`
  section 5) is the same act from a page, later.
- **The main thread keeps writing the plans**, which hold the decisions a
  person takes. The drafts only cut a plan already decided into tasks.

## The tasks, in order

Each is one surface. Each arquitecto reads this file and the charter entries
named first; a task that finds a decision here wrong appends a narrowing ADR
and says so in `risks`, without editing this file's decisions.

### Priority 1 — the person stops being the bottleneck

#### A-1. *Surface* as paths

**Surface:** `dispatcher/surface.py` (new), `tests/dispatcher/test_surface.py`.

**Builds.** Parsing a description's *Surface* into a set of path entries
(*Decisions*, 5); `covers(entries, path)`; `overlaps(a, b)`; the shared
append-only list as a module constant; an empty set meaning "whole repo".

**Done when.** Tests cover a file, a directory, a glob, prose with no path,
two overlapping and two disjoint surfaces, and an append-only path that
overlaps nothing. `python3 -m pytest` is green.

**Out of scope.** Reading it from the cycle; changing how tasks are written.

#### A-2. The pre-merge check

**Surface:** `dispatcher/docker_exec.py` (a merge of the base into the task
branch and its abort), the call in `dispatcher/dispatcher.py` before
`_merge_task_branch`, and their tests.

**Builds.** *Decisions*, 2: merge, test gate, abort, the `conflict` status
and its comment. Run before every merge, `merge_on_done` and `merge-task`
included.

**Done when.** Tests cover a clean merge, a textual conflict, and a merge git
accepts whose gate goes red; in the last two the base is unchanged and the
task branch is back at its tip. `python3 -m pytest` is green.

**Out of scope.** The integrador; re-checking tasks already done when
another merges.

#### A-3. The automatic approve

**Depends on:** A-1, A-2.

**Surface:** `dispatcher/auto_approve.py` (new, the check as a pure
function over the cycle's results), the `auto_approve` and
`protected_paths` keys in `dispatcher/config.py` and
`config.example.yaml`, the call in `run_task_cycle`, and tests.

**Builds.** *Decisions*, 1: the check, with one named reason per failed
condition; the comment on either outcome; the merge on success. An ADR for
the protected list and the key's shape (per project, a map of slug to
boolean, until `projects.md`'s project record exists).

**Done when.** Tests drive each condition false alone and see the task stay
unmerged with that reason, and all true to see it merged. A project without
the key behaves exactly as today. `python3 -m pytest` is green.

**Out of scope.** The push (A-4); the console.

#### A-4. The base push

**Depends on:** A-3.

**Surface:** a push in `dispatcher/docker_exec.py`, its call after an
automatic approve and behind `merge-task --push`, and tests.

**Builds.** *Decisions*, 4, with the credential's ADR (`review.md`,
*Decisions*, 4). Never `--force`; a project with no upstream never pushes.

**Done when.** Tests against a local bare remote cover a push, a refused
push (the remote moved) and a project with no remote. `python3 -m pytest` is
green.

**Out of scope.** The task-branch push of C-5 (`review.md` step 5).

#### A-5. The queue

**Surface:** `dispatcher/queue.py` (new), the `run-queue` verb in
`dispatcher/cli.py`, and tests.

**Builds.** *Decisions*, 3 with `--parallel` accepted only as 1: the
confirmation screen, order by `depends_on`, the measured limit, skipping a
failed task's dependents, waiting for quota, the crash-safe queue file and
`--resume`. An ADR for the token sum the limit counts.

**Done when.** Tests with a stubbed cycle cover the order, a dependency that
fails, the limit reached between tasks, a wait for quota, and a resume after
a kill mid-queue that starts no task twice. `python3 -m pytest` is green.

**Out of scope.** Parallel cycles (B-3); the console's *Start epic*.

#### A-6. Drafted specs

**Depends on:** A-1.

**Surface:** the `draft-specs` and `accept-drafts` verbs in
`dispatcher/cli.py`, the arquitecto's draft-mode section in its prompt
(`roles/`), `.hive/board/drafts/` in `dispatcher/tasks.py`, and tests.

**Builds.** *Decisions*, 9.

**Done when.** Tests cover a draft that fails the section rules being
refused, `accept-drafts` rewriting `depends_on` to the new ids, `--only`,
and a draft never being runnable by `run-task` or `run-queue`. One real draft
run on this repo's `docs/plans/autonomy.md` is reported to the owner, on
their authorization. `python3 -m pytest` is green.

**Out of scope.** The console's backlog.

### Priority 2 — spend less per task

Not cut here: it is `docs/plans/backlog.md` item 4 (per-role models, the
`arquitecto-draft` model of *Decisions*, 9 included) and the open points of
item 2 (`docs/plans/token-economy.md`). It comes second because a queue that
runs unattended spends faster, so what each phase costs matters more.

### Priority 3 — more than one task per project

#### B-1. The base lock

**Surface:** a lock in `dispatcher/tasks.py` beside `acquire_lock`, taken
around every base operation in `dispatcher/dispatcher.py`, and tests.

**Builds.** *Decisions*, 6, *The base is one resource*. With one cycle per
project it changes nothing but adds safety against two operators.

**Done when.** Tests cover two holders contending, a stale lock broken after
its TTL, and a live one never broken. `python3 -m pytest` is green.

#### B-2. Append-only merges and ADR reservation

**Surface:** the ADR counter in `dispatcher/` and its line in the
arquitecto's prompt, the duplicate-ADR gate in `dispatcher/gates.py`, this
repo's `.gitattributes`, and tests.

**Builds.** *Decisions*, 7.

**Done when.** Tests cover two reservations never overlapping, seeding from
a `docs/decisions.md` with gaps, a union merge of two appends, and the gate
failing on a duplicate number. `python3 -m pytest` is green.

#### B-3. The role-aware parallel scheduler

**Depends on:** A-1, A-2, A-5, B-1, B-2.

**Surface:** `dispatcher/queue.py` (`--parallel N`), the surface lock, the
cycle's split before the first implementador phase in `run_task_cycle`, the
stale-plan note in the implementador's prompt, and tests.

**Builds.** *Decisions*, 6, under an ADR that narrows `projects.md`
*Decisions*, 5.

**Done when.** Tests with a stubbed cycle show two arquitectos running at
once on overlapping surfaces, their write windows never overlapping, two
disjoint write windows overlapping, the stale-plan note given only when the
base moved under the plan, and a single mapping. `--parallel 1` reproduces
A-5 exactly. `python3 -m pytest` is green.

**Out of scope.** Fair share between projects; the console's slots screen.

#### B-4. More than one phase per account

**Depends on:** B-3, and the measurement of *Decisions*, 8, on the owner's
authorization.

**Surface:** `dispatcher/state_machine.py` and `pick_account`, and tests.

**Builds.** *Decisions*, 8, only if the measurement shows a second phase does
not throttle the first.

**Built** 2026-10-17. The measurement is T-056 (ADR 84, *go*: +11% wall time
per phase, no refusals, and the same quota as two phases in a row). The state
file's holders are T-057 (ADR 85), and the dispatcher's wiring with
`max_concurrent_phases` is T-058 (ADR 88). The pick order still keeps the
primary for last: a worker's second slot ranks above an idle primary.

## Why this order

- **A-1 to A-4 remove the person's turn** from tasks that needed nothing from
  them. They are small, need no console, and are worth having even with one
  cycle at a time.
- **A-5 removes the person's turn between tasks.** With A-3 on, a queue runs
  an epic end to end; without it, it still chains every task that does not
  wait on another's merge.
- **A-6 removes the main thread's turn of writing each spec**, the work this
  conversation did by hand for T-055 and T-056.
- **Priority 2 before 3**, because parallel work multiplies the spend of
  each phase, and on a pool of two Pro accounts it adds almost nothing: two
  slots are already used by one cycle's phases in turn more often than not.
  B-1 and B-2 are cheap and safe to build early; B-3 pays only with more
  slots, and B-4 only on a Max account.

## Open questions

- **How the owner is told.** The comment on each automatic approve is
  written by A-3; the notification of C-12 (*Amended* 2026-10-15) waits for
  `docs/plans/notifications.md`. Until then, the queue's end prints a
  summary, and the owner reads the comments.
- **Whether a revert re-opens the task.** A wrong automatic merge is fixed by
  a revert commit (C-12). Whether that reverts the `merged` block and
  re-opens dependents is left to the first time it happens.
- **The size of an ADR block.** Three is a guess; B-2 measures how many ADRs
  tasks of this repo wrote.
