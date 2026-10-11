# Review — a person approves a task's diff in the console before it merges

**Status:** a stub. It records a goal the user set on 2026-10-10 and what the
code and the other plans already say about it, so the decisions it needs are
asked in one place. Nothing here is designed in detail, built or approved, and
part of it waits on a charter ruling only the user can make.

## The goal

In the user's words, translated: once a task is developed and has passed its
reviews, the console shows its changes and lets a person comment on them, then
approve, comment or reject — like a GitHub pull request, or the same thing —
before anything is merged and pushed.

So the merge stops being a dispatcher setting or an operator's CLI call and
becomes a person's decision, taken over a diff, in the console.

## What exists today

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
  `GET` and every mount is `:ro`. `docs/plans/front.md` *Tier 3 — the write
  surface* says every write needs a different service, a queue and a worker,
  never a docker socket in a web process (`docs/plans/board.md`, *Actions go
  through a queue, not a socket*).
- **The ruling is already on the books as open.** `front.md` lists "Approving
  from the console, against C-1 and C-4" as a charter question for the tier-3
  opening task: C-1 makes the operator's conversation the one place a human
  drives from, and C-4 says nothing spends quota without a human authorizing
  the run. A reject that sends the task back to the implementador spends
  quota, so it is a C-4 authorization given from a page.
- **The epic design depends on it.** `docs/plans/task-split.md` makes a
  sub-task wait until its dependencies are *merged*, not `done`, because under
  this plan approving is the merge.

## The flow this plan proposes, in outline

1. **A state between `done` and merged: awaiting review.** The cycle ends
   there instead of at `done` when review is on. The card moves to the
   board's review column. (`docs/plans/board-service.md` section 4 names it
   *Awaiting review*, and section 8 turns `/approvals` into this screen.)
2. **The api serves the diff, read-only.** A route like
   `GET /api/tasks/<id>/diff` returns the task branch against its merge base,
   parsed per file on the server (the "who parses the diff" question in
   `front.md`), together with the commit it was taken at.
3. **The console shows it like a pull request.** Files, hunks, the handoffs'
   summary next to it (what the implementador changed, what the revisor and
   the auditor said), and comments anchored to a file, a line and that commit.
4. **Three verdicts, written through the write service, never the api:**
   - *approve* — merge the task branch (`merge_task_branch`), push, and record
     the merge on the task (`task-split.md`'s `merged` block), which also
     releases its dependents and may close its epic;
   - *request changes* — the comments go back to the implementador as a new
     revision round, with the same account pick and quota rules as any phase;
   - *comment* — the comments are stored and nothing runs.
   *Reject* in the user's list is read here as request changes; dropping a task
   for good is a separate, rarer action (abandon the branch, card to blocked).

## Open questions

- **The charter ruling (C-1, C-4).** Whether a verdict given in the console
  counts as the human authorizing that run. `docs/charter.md` is the user's to
  edit; this plan does not draft the ruling for it.
- **Our own screen, or real GitHub pull requests.** "Like a GitHub PR, or the
  same thing" leaves room for the dispatcher to open a real PR (`gh pr create`)
  and the console to show it, or for the review to be the harness's own. A
  real PR gets diff, comments and approval for free and moves the push before
  the review; our own keeps everything in the hive and works offline and for
  projects with no GitHub remote. This is the first decision, because it
  decides most of the others.
- **Where comments live.** A section in the task file's body, a
  `<task_id>/review.json` beside the saved handoffs, or the write service's
  own table; and how a line comment survives a new round that moves the line.
- **Where the write service lives, and its credential.** The tier-3 opening
  task's ADR. Pushing needs a credential the api must never hold.
- **Which revision loop a "request changes" reuses.** The cycle's own loop
  between implementador and revisor, re-entered with the person's comments, or
  a new `dispatch revise --task-id` verb; and whether the revisor and auditor
  run again after it.
- **Where the merge goes and when it pushes.** Into the branch the checkout is
  on, as `merge-task` does, and pushed to its upstream; what happens when the
  push is refused or the merge conflicts.

## What it is not

Not a replacement for the revisor and the auditor: the person reviews after
they pass, not instead. Not built before the ruling and the GitHub-or-ours
decision. Not part of P4: the epic design only needs the `merged` fact, which
`merge-task` can write today.
