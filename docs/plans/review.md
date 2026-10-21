---
status: building
approved: 2026-10-11
built: null
supersedes: []
---

# Review — a person approves a task's diff in the console before it merges

**Status:** design, approved in outline by the user on 2026-10-11. The console
review is built but for one of its verdicts (ADR 219, ADR 222, ADR 270): the
api serves a task branch's diff, and `/approvals` draws it with the comments
pinned to it, pins new ones to a line, records the *comment* verdict at
the tip, and has *approve* and *request changes* buttons. The dispatcher writes the
`awaiting_review` status (step 1, ADR 240), and pushes the task branch after
each committing phase for a project that turns it on (step 5, ADR 271); *approve* is served by the board
service's bearer route and drained by the worker (step 4); *request changes*
is served by its own bearer route and drained by the worker, which reruns the
cycle from the arquitecto on the task's branch (step 6, ADR 272, T-128), and
the console's button for it calls that route (step 7, ADR 276, T-129). Two pieces landed on
the CLI side through `docs/plans/autonomy.md`, and they narrow *Decisions*, 4 and 6 as the notes
there say:

- the merge and the base push of *approve*, run on an automatic approve
  (ADR 77) or on `merge-task --push` (ADR 78, which is also the credential's
  ADR);
- step 1 of *Decisions*, 6, as a pre-merge check on every merge, with the
  `conflict` status (autonomy A-2).

It began as a stub on 2026-10-10 recording a goal the user set; the user
answered its open questions on 2026-10-11, and those answers are under
*Decisions*. It is built after `docs/plans/board-service.md`, on the same
service and the same comments file. The charter ruling it needs was given by
the user the same day and is `docs/charter.md` C-12 (*Decisions*, 1).

## The goal

In the user's words, translated: once a task is developed and has passed its
reviews, the console shows its changes and lets a person comment on them, then
approve, comment or reject — like a GitHub pull request, or the same thing —
before anything is merged and pushed.

So the merge stops being a dispatcher setting or an operator's CLI call and
becomes a person's decision, taken over a diff, in the console.

## What exists today

- **Every task already works on its own branch.** The writing roles share one
  worktree on `agent/task/<task_id>` (`docker_exec.task_branch`); the
  reviewing roles get a detached checkout of its tip. Parallel tasks never
  share a tree.
- **The cycle ends at `awaiting_review`, unmerged, by default.** A task that
  passes the revisor and the auditor and is not merged ends `awaiting_review`
  with its work on its task branch (ADR 240; `done` unmerged is how older
  tasks look). `merge_on_done` is a deprecated alias, off by default
  (`dispatcher/config.py`): it merges before that move, and a merge leaves
  the task `done` and merged. The merge is a later
  `dispatch merge-task`, which calls `docker_exec.merge_task_branch` into
  whatever branch the project's checkout is on. Nothing in the dispatcher
  pushes yet; the push is the operator's. C-5 as amended on 2026-10-11 lets
  the dispatcher push task branches, and C-12 the base on *approve*.
  (Since 2026-10-17 the dispatcher pushes the base after an automatic
  approve or `merge-task --push`, per ADRs 77 and 78, and every merge runs
  the pre-merge check first. Since 2026-10-21 it also pushes the task
  branch after each committing phase, for a project with `push_task_branch`
  on, per ADR 271.)
- **A screen without verdicts.** `front/src/routes/approvals.tsx` was a Lovable
  draft over mock data until ADR 219. It now lists the tasks that are `done`
  and not merged, as the stand-in for *Awaiting review* (until the console
  reads `awaiting_review`, steps 1 and 7), and the `conflict`
  ones, and draws each one's diff from the api's diff route, by hand, with
  the line comments `comments.jsonl` already holds; a line number pins a
  new one at the tip. The verdict is the `merge-task` CLI line it prints,
  not a button.
- **A request for changes reruns the cycle.** Built (ADR 272, T-128):
  `POST /api/tasks/<id>/request-changes`, on an `awaiting_review` task, writes
  the `request_changes` verdict line and queues a `request_changes` action.
  The worker moves the task back to `pending` and runs the cycle from the
  arquitecto on its existing branch, unauthorized, with the person's review
  comments in the arquitecto's prompt, so the task ends in `awaiting_review`
  again at a new tip. It spends quota (C-12) and holds the project as a
  start does.
- **The api writes nothing.** Every route in `observability/api/app.py` is a
  `GET` and every mount is `:ro`. Writes go through the board service of
  `docs/plans/board-service.md`, never the api.
- **The epic design depends on it.** `docs/plans/task-split.md` makes a
  sub-task wait until its dependencies are *merged*, not `done`, because under
  this plan approving is the merge.

## Decisions

### 1. A verdict in the console authorizes what it starts

The user ruled on 2026-10-11 that a verdict given in the console counts as a
person authorizing the run it starts. That settles the C-1 and C-4 question
`docs/plans/front.md` and `board-service.md` left open:

- *approve* spends no quota, and starts the merge and the push;
- *request changes* spends quota, because it starts a phase, and the verdict
  is that phase's authorization;
- *comment* starts nothing;
- *resolve conflict*, on a task in *Conflict* (*Decisions*, 6), spends quota:
  it starts the integrador, and the verdict is that phase's authorization.

It is recorded as `docs/charter.md` C-12, written by the main thread at the
user's word on 2026-10-11, with the fourth verdict added the same day. C-12
also covers C-5: the push on *approve* is the person's, run by the
dispatcher's side, and no role pushes.

### 2. Our own screen, built on a diff library

The review is the harness's own: a screen in the console, its comments in the
hive, no GitHub pull request. It fits the user's other decisions (a board of
our own, comments in files, the internal network) and works for a project
with no GitHub remote.

To keep the code small, the screen uses a React diff component instead of
drawing hunks by hand, and the api sends the raw unified diff for it to
parse. Candidates, to be checked for upkeep and React 19 support when the
screen is built (installing one is its own ask):

- `@git-diff-view/react` — split and unified views, syntax highlighting, and
  widgets under a line for comments, close to GitHub's review;
- `react-diff-view` — with `gitdiff-parser`; hunks, widgets for comments,
  tokenization.

This also answers `front.md`'s "who parses the diff": the library, in the
browser. The api only runs `git diff` and returns its text.

### 3. Comments live in `comments.jsonl`, and are kept for good

A review comment is a line in the task's `comments.jsonl`
(`board-service.md` section 2), with `{file, line, commit}` added. A comment
whose commit is no longer the branch's tip is shown as *outdated* beside its
file, as GitHub does, rather than moved. Comments are never deleted or
summarised; a merged task keeps them as the record of its review.

### 4. A task is a branch, and the review is its pull request

The user asked whether, with many agents, the flow should be a branch and a
pull request. It is, in all but the host:

- each task's work is on `agent/task/<task_id>`, cut from the base when the
  cycle opens;
- the review is that branch against its merge base, at a named commit;
- *approve* merges the branch into the base (`merge_task_branch`) and pushes
  the base to its upstream, then writes the `merged` block
  (`task-split.md`), which releases the task's dependents and may close its
  epic;
- a sub-task whose dependency has merged is cut from the base that now holds
  it, so siblings never build on unapproved work.

The base is the branch the project's checkout is on, as `merge-task` uses
today. A conflict is caught before the person approves, and resolved by its
own role (*Decisions*, 6). If the push is refused all the same, because the
upstream moved, nothing is pushed: the task goes to *Blocked* with a comment
saying why.

Roles commit on the task branch and never push (C-5). The dispatcher pushes
the task branch after each phase that commits, when the project turns that on,
so GitHub keeps the record of each role's work; it is never a pull request
there, and never forced (C-5 as amended on 2026-10-11). The base is pushed
only on *approve*.

Both pushes need a git credential, which neither the api nor the board service
may hold. The dispatcher's side holds it; where it lives is an ADR written
when step 4 below is built.

How the dispatcher learns of a verdict the service wrote, by reading the hive
as it reads tasks today or by a signal from the service, is decided when step
4 is built. Both were decided ahead of the build, on 2026-10-21, by ADR 258:
the credential is ADR 78's, and the dispatcher learns of an *approve* through
the action queue (an `approve` action the worker drains), neither of the two.

*Narrowed* 2026-10-17, from what `docs/plans/autonomy.md` built:

- *The credential* is ADR 78: the push uses what the fetch already uses, the
  project's deploy key or the mounted https:// token. The push is
  fast-forward only and is never forced.
- *The base push* runs after an automatic approve (ADR 77) or
  `merge-task --push`. `merge_on_done` never pushes.
- *A refused push does not move the task to Blocked.* The merge stays on the
  local base and the task stays `done` with its `merged` block. After an
  automatic approve, its `## automatic approve` comment says why the push
  did not land. After `merge-task --push`, the reason goes to stderr and the
  command exits 4. Reconciling with the remote is a person's decision, and
  then `merge-task --push` pushes again (ADR 78, 5).
- *A conflict is caught at the merge.* The `awaiting_review` status exists
  (step 1, ADR 240), but the pre-merge check (*Decisions*, 6, step 1) still
  runs before every merge, not when a task enters review.
- *Pushing the task branch after each phase* (step 5 below) is built (ADR
  271, T-127): per project, off by default, never forced, and a push that
  does not land is a note in the phase's handoff, never a failed phase.

### 5. *Request changes* goes back to the arquitecto

A person's comments are a new requirement, so they get a plan before code:

1. The verdict reopens the task's cycle at the arquitecto, with the comments
   handed to it alongside the task's *Goal* and *Done when*.
2. The arquitecto writes a formal plan for the changes. If the changes span
   more than one surface it may split, under `task-split.md`'s limits.
3. The implementador, the revisor and the auditor run again on the same
   branch, and the task returns to *Awaiting review* with a new commit.

No new CLI verb: it is the same cycle, entered with comments. *Reject* in the
user's list is read as request changes; dropping a task for good is a
separate, rarer action (the branch abandoned, the task to `dropped`).

### 6. A conflict is caught early, and resolved by the integrador

Asking for changes is the wrong tool for a conflict: it re-runs four roles for
work that is mostly mechanical. Three steps instead, decided by the user on
2026-10-11:

1. **Caught without quota.** Before a task enters *Awaiting review*, and
   again for every task already there each time another task merges, the
   dispatcher merges the base into the task branch (a merge, not a rebase, so
   the reviewed commits and the comments anchored to them stay valid), and
   runs the test gate. Both are commands, not model calls. If both pass,
   nothing else happens and the task's own diff is unchanged. If the merge
   conflicts, or the gate goes red where it was green (a conflict git did not
   see), the merge is aborted and the task moves to the board's *Conflict*
   column with a comment naming the files and the task that merged. The
   person learns of it before approving, not at the button. Tasks still in
   their cycle are left alone; they are checked when they reach review.
2. **Resolved by a role of its own: the integrador.** On the person's
   *resolve conflict* verdict, the dispatcher re-runs the merge in the task's
   worktree and dispatches the integrador on it. Its job is not the
   implementador's: it carries out no plan, it keeps two intents.
   - *It reads* the conflict markers, its own task's plan and handoffs, and
     the handoff of the task that merged.
   - *It may write* only the files in conflict, or, for a red gate, the files
     the failure names. It finishes the merge commit.
   - *It reports* each file and how it was resolved, in a handoff the
     revisor can check against the diff.
   - *It is small*: its own prompt, a short budget, and a model that may be
     cheaper than the implementador's for a plain textual conflict; the
     profile is set when it is built.
   - *It blocks* when the two sides want incompatible things: a conflict of
     design is not its to settle, as a plan the arquitecto cannot write is
     not its. The person then sends the task back with *request changes*.
3. **Checked again, only where it changed.** The test gate and the revisor
   run on the resolution alone; the auditor runs only if the integrador
   touched lines outside the conflicting hunks. The task returns to *Awaiting
   review*, and the screen shows what changed since the person last looked.
   An approval given before the conflict does not carry over: it was on
   another diff.

*Narrowed* 2026-10-17: step 1 is built as the pre-merge check
(`docs/plans/autonomy.md` *Decisions*, 2, task A-2;
`dispatcher.pre_merge_check`). It runs at each merge, from `merge_on_done`,
`merge-task` or an automatic approve. It does not run when a task enters
`awaiting_review` (ADR 240 adds the status, not the check), and it does not
re-run on the other tasks after a merge, since the queue does not exist.

- The base is merged into the task branch in a detached pre-merge checkout.
- If the base is already in the branch, nothing new came in, and the gate is
  not run again.
- A conflict, or a gate that goes red, drops the pre-merge and leaves the
  base and the branch unchanged. The task moves to `conflict` with a
  `## pre-merge check` note naming the files or the gate and the base commit.
- A clean, green pre-merge is kept on the branch, and the merge lands it. A
  pool with `gates_enabled: false` keeps a clean merge without running the
  gate.
- A check that cannot run stops the merge without changing the status.
- A task in `conflict` that later passes goes to `awaiting_review` (ADR 240).

The integrador (steps 2 and 3) is not built, so for now the operator
resolves a conflict: they merge the base into the branch, never rebasing,
and run `merge-task` again.

## The flow

1. **A state between `done` and merged: awaiting review.** The cycle ends
   there instead of at `done` when review is on, and the task shows in the
   board's *Awaiting review* column (`board-service.md` section 4).
2. **The api serves the diff, read-only.** `GET /api/tasks/<id>/diff` returns
   the raw unified diff of the task branch against its merge base, and the
   commit it was taken at.
3. **The console shows it like a pull request.** `/approvals` becomes this
   screen (`board-service.md` section 8): files and hunks through the diff
   library, the handoffs' summary beside them (what the implementador
   changed, what the revisor and the auditor said), and comments anchored to
   a file, a line and that commit.
4. **The verdicts are written through the board service**, never the
   api, and act as *Decisions* 4, 5 and 6 say.

## How it would be built

After `board-service.md`'s steps, each a single surface:

1. The *awaiting review* state, replacing `merge_on_done`. **Built
   2026-10-21** (ADR 240): `tasks.mark_awaiting_review` moves a `done`
   unmerged task to `awaiting_review` at the cycle's end, `mark_merged`
   moves it back to `done`, and `record_verdict` accepts it. `merge_on_done`
   stays as a deprecated alias. Server side only: the console still lists
   `done` unmerged tasks.
2. The diff route in the api. **Built 2026-10-20** (ADR 219):
   `GET /api/tasks/<id>/diff?project=<slug>` runs `git` read-only on the
   project's clone, against the merge base with the base branch, capped in
   size; the api image installs git for it. The console forwards it.
3. The *comment* verdict and line comments in `comments.jsonl`. **Built
   2026-10-20** (ADR 219, ADR 222): the line comments were already in the
   board-service's anchored `task_comment`, and the review writes them at
   the tip. The *comment* verdict is a `comments.jsonl` line with
   `verdict: {kind, commit}`, written by the board-service's HTTP-only
   `review_verdict` (`POST /api/tasks/<id>/verdict`) for a task `awaiting_review`,
   `conflict` or `done` and not merged, and listed on the review with its tip. It is
   recorded only: no dispatcher reads it, which is step 4's.
4. The *approve* verdict: merge, push and the `merged` block, with the
   credential's ADR, and the dispatcher picking up verdicts. **Built
   2026-10-21** (ADR 258, 264, 267): the route by T-125-S2, the worker's
   handler by T-125-S1; the console's button is step 7's. A bearer route `POST
   /api/tasks/<id>/approve` writes the `approve` verdict line at the
   reviewed tip and an `approve` action (`/verdict` refuses `approve`);
   the worker drains it and, under the base lock, refuses a stale
   verdict, then runs the pre-merge check, the merge, ADR 78's push and `record_merge`, and comments the outcome.
   The credential is ADR 78's; step 5 needs no other.
5. The task-branch push after each phase, per project, off by default.
   Independent of the console, so it may be built first; it needs the same
   credential, and the ADR goes with whichever of 4 and 5 lands first.
   **Built 2026-10-21** (ADR 271, T-127): `push_task_branch`, a per-project
   boolean parsed like `auto_approve`, turns it on; `dispatch_phase` reads
   the branch's tip before and after the phase and, when it moved, pushes
   `agent/task/<id>` alone to the base's remote with ADR 78's credential,
   never forced. A push that does not land is logged and noted in the
   handoff under `## Task-branch push`; the phase carries on.
6. The *request changes* verdict: the cycle reopened at the arquitecto.
   **Built 2026-10-21** (ADR 272, T-128): a bearer route `POST
   /api/tasks/<id>/request-changes`, approve's with the kind swapped, takes
   it on an `awaiting_review` task only (a `conflict` waits on *resolve
   conflict*), writes the verdict line and queues a `request_changes` action;
   it starts nothing, and `/verdict` refuses it. One approve or request for
   changes per task is queued at a time. The worker, holding the project and
   an account, moves the task to `pending` and runs the cycle unauthorized
   from the arquitecto on the task's branch, with the person's comments on
   the reviewed tip in the arquitecto's prompt below the Goal and Done when;
   the task ends in `awaiting_review` at a new tip, so an earlier approval
   does not carry over. No new CLI verb. The console's button is step 7's.
7. The screen, with the diff library. A front task, behind its own ask for a
   build and for the package. **Partly built 2026-10-20** (ADR 219): the
   screen reads and draws, with a hand-written diff view instead of a
   library, comments shown at their line on the tip and as outdated past
   it, a line number pinning a new one at the tip, and the *Conflict* list
   shown read-only. The *approve* button is in too (**built 2026-10-21**,
   ADR 270, T-125-S3): in the verdict form, at the tip on screen, off on
   *Conflict* and merged tasks, with the dispatcher's outcome comment listed.
   The *request changes* button is in too (**built 2026-10-21**, ADR 276,
   T-129, over step 6's route, ADR 272): beside *approve*, at the tip on
   screen, on only for an `awaiting_review` task not on main and with words
   typed, its refusals (a 409 included) shown in a toast; the task leaving
   the list and returning at a new tip is its outcome.
   Still open: the library and the handoffs' summary beside the diff; the
   *comment* verdict's form is in (step 3).
8. The *Conflict* state: the base merged into each branch at review and after
   every merge, and the test gate run on it. No model.
9. The integrador role: its prompt, handoff schema, profile and write limit,
   and the *resolve conflict* verdict. Under an ADR, since it adds a role.

Steps 4, 6 and 9 start runs from the console under C-12.

## Open questions

- **Which diff library.** Chosen when step 7 is built, from the two
  candidates above.
- **Does a request for changes re-run every role?** *Decisions* 5 re-runs all
  four. A one-line wording fix might skip the arquitecto; whether the person
  may choose that on the verdict is left to step 6. *Answered* by step 6 (ADR
  272): every role reruns from the arquitecto, and the verdict offers no
  choice. A lighter path for a wording fix would be its own ADR.
- **The integrador's model.** A cheaper model for textual conflicts, or the
  implementador's; set by its profile when step 9 is built, and measured
  against the first conflicts it resolves.

## What it is not

Not a replacement for the revisor and the auditor: the person reviews after
they pass, not instead. Not part of P4: the epic design only needs the
`merged` fact, which `merge-task` can write today.
