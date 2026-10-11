# Review — a person approves a task's diff in the console before it merges

**Status:** design, approved in outline by the user on 2026-10-11, not built.
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
- **The cycle ends at `done`, unmerged, by default.** `merge_on_done` is off
  (`dispatcher/config.py`), so a task that passes the revisor and the auditor
  is `done` with its work on its task branch. The merge is a later
  `dispatch merge-task`, which calls `docker_exec.merge_task_branch` into
  whatever branch the project's checkout is on. Nothing in the dispatcher
  pushes; the push is the operator's.
- **A draft screen.** `front/src/routes/approvals.tsx` approves or rejects
  "pushes with diffs, merges, tool permissions and budget overrides" over mock
  data. It splits a raw unified diff in the browser and has no line comments.
  It is a Lovable draft, not a spec.
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

Roles commit on the task branch and do not push it (C-5). The only push is
the base's, on *approve*.

The push needs a git credential, which neither the api nor the board service
may hold. The worker that runs the merge, on the dispatcher's side, holds it;
where it lives is an ADR written when step 4 below is built.

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

1. The *awaiting review* state, replacing `merge_on_done`.
2. The diff route in the api.
3. The *comment* verdict and line comments in `comments.jsonl`.
4. The *approve* verdict: merge, push and the `merged` block, with the
   credential's ADR.
5. The *request changes* verdict: the cycle reopened at the arquitecto.
6. The screen, with the diff library. A front task, behind its own ask for a
   build and for the package.
7. The *Conflict* state: the base merged into each branch at review and after
   every merge, and the test gate run on it. No model.
8. The integrador role: its prompt, handoff schema, profile and write limit,
   and the *resolve conflict* verdict. Under an ADR, since it adds a role.

Steps 4, 5 and 8 start runs from the console under C-12.

## Open questions

- **Which diff library.** Chosen when step 6 is built, from the two
  candidates above.
- **Does a request for changes re-run every role?** *Decisions* 5 re-runs all
  four. A one-line wording fix might skip the arquitecto; whether the person
  may choose that on the verdict is left to step 5.
- **The integrador's model.** A cheaper model for textual conflicts, or the
  implementador's; set by its profile when step 8 is built, and measured
  against the first conflicts it resolves.

## What it is not

Not a replacement for the revisor and the auditor: the person reviews after
they pass, not instead. Not part of P4: the epic design only needs the
`merged` fact, which `merge-task` can write today.
