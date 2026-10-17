# Projects — one board per project, chosen from a sidebar, each with its own key

**Status:** design, agreed in outline by the user on 2026-10-11, not built. It
records three points the user raised the same day, after the models-5-5 bump
(`docs/plans/models-5-5.md`) and before its step 2:

- the GitHub key is set from the console, as a priority;
- the console is organised by project, from a sidebar, rather than around one
  shared board;
- a task approved while another runs opens its own execution thread beside
  the main one (`docs/plans/main-thread.md`, *Decisions*, 8).

The user settled three points in the same conversation:

- **A task's thread is a view, and talking to it is optional.** The task is
  already written and designed by the time it starts, so the thread follows
  the workflow rather than drives it. The person may leave a note with the
  approval (`board-service.md`, *Starting development*).
- **Parallel work is across projects, for now.** Two tasks of the same
  project never run at once until the dispatcher is built for it, and the
  race between projects for the same quota is handled later too. Both are the
  last items of the roadmap (*Decisions*, 5; `docs/ROADMAP.md` Stage 2, item
  9).
- **The console shows the limit.** How many tasks can run at once depends on
  how many accounts there are, and the console says so where a task is
  started and where its thread is chosen (*Decisions*, 6).

## What exists today

Checked on 2026-10-11.

- **The dispatcher is already per project.** `run`, `phase`, `bootstrap`,
  `merge` and `cleanup` take a slug, and the project's clone is
  `{projects_root}/{slug}`. The learnings are filtered by project.
- **The board and the hive are not.** `local_board.dir` is one directory,
  `/state/board`, and `hive_tasks_dir` is one `.hive/tasks`. A task does not
  say which project it belongs to; the run names it.
- **The console is not.** Its routes are global: `index`, `approvals`,
  `backlog`, `debt`, `learnings`, `models`, `pool`, `queue`, `sessions`,
  `tail`, `tasks.$taskId` and `tokens`.
- **`.data/projects/` holds two clones**, `ia-harness` and `scratch`.
- **A project is a directory, today.** Every non-dotted subdirectory of
  `projects_root` is one (`observability/api/app.py:_project_slugs`;
  `docs/business.md`), so a checkout parked there counts as a project.
- **The Git credential is one HTTPS token for every project.** It is the
  read-only mount `/run/secrets/git-credentials`
  (`dispatcher/docker_exec.py`, `GIT_CREDENTIALS_PATH`), from
  `.data/credentials/git-credentials`, in git's `store` format. It is mounted
  rather than passed with `docker exec -e`, which `ps` would show.
  `update_project_branch` only fetches, and its comment notes the mount can
  only answer HTTPS. A personal access token usually reaches every repository
  of its owner.
- **SSH with a deploy key, since step 2 (2026-10-11).** Both images carry
  `openssh-client`; the agent image pins github.com's host keys in
  `/etc/ssh/ssh_known_hosts`. `project-keygen --project <slug>` makes a pair
  at `.data/credentials/projects/<slug>/id_ed25519`, mounted writable in the
  dispatcher and read-only in the agents at `/run/secrets/projects`, and
  prints only the public half and its fingerprint. `update_project_branch`
  fetches over SSH when the project has a key (an HTTPS GitHub remote through
  `url.git@github.com:.insteadOf`, never rewritten), and through the HTTPS
  store otherwise. The push of C-5 is not built yet, so it has no SSH path.
  `docker-compose.coolify.yml` mounts neither credential.
  *Narrowed 2026-10-17:* since T-059 it mounts the deploy keys, not the
  https:// file (`docs/decisions.md` ADR 91).
- **Locks.** A task is locked to one dispatcher by `acquire_lock`
  (`dispatcher/context_transfer.py`), and a phase locks one account through
  `pick_account`. Nothing locks a project: two cycles of one project could
  both be started today, and nothing would stop their merges from meeting.

## Decisions

### 1. A project is a record

Each project has a record, `.hive/projects/<slug>.json`, with:

- `slug`, which is the clone's directory name under `projects_root`, and
  `title`;
- `remote`, in SSH form, and `base_branch`;
- `key`: the public key's fingerprint and when it was made, never the key
  itself (*Decisions*, 2);
- the settings that are global today and belong to a project:
  `allowed_tools`, `merge_on_done`, the gates, and the task-branch push of
  C-5;
- `created` and `status` (`active` or `archived`).

The record is written by the board service, on a person's act, and read by the
dispatcher. It replaces the directory rule of `docs/business.md`: once records
exist, a project is a record whose `slug` has a clone. The migration of step 1
writes a record for each directory there is, so nothing that counts today
stops counting; a directory with no record afterwards is shown in the sidebar
as *Not registered*, with *Register*, and is not a project until then. A setting the record leaves out falls back to `config.yaml`, so a
harness with one project configured by hand keeps working.

### 2. Each project has its own deploy key, made on the server

The user asked for this first. An SSH deploy key opens one repository and is
revoked from that repository, where a personal token opens all of them.

- **The pair is made on the dispatcher's side.** The console asks for a key;
  the board service writes a `keygen` action to the queue; the worker runs
  `ssh-keygen -t ed25519` into the credentials volume. The api and the board
  service never hold the private key, which C-5 requires ("the credential it
  needs stays there"), and it never goes through the browser.
- **The console shows the public key and its fingerprint**, with the steps:
  the repository's *Settings → Deploy keys*, with write access when the
  project turns on the task-branch push, read-only otherwise. *Check* runs
  `git ls-remote` through the queue and says whether GitHub accepts the key.
- **One key per project.** GitHub refuses the same deploy key on two
  repositories, so the key lives at `<credentials>/projects/<slug>/id_ed25519`.
- **The credentials volume is never backed up**, as
  `server-deployment.md` *Decisions*, 8 already says of every credential.
  Losing it means making a new key and pasting it again, which is the safe
  failure.
- **Replacing a key** makes a new pair and shows it. The old public key stays
  on GitHub until the person deletes it there, and the console says so.

What the dispatcher needs:

1. `openssh-client` in the agent image, and github.com's host key pinned in
   the image's `known_hosts`. Accepting it on first use would trust whoever
   answers first.
2. Fetch and push with `GIT_SSH_COMMAND="ssh -i <key> -o IdentitiesOnly=yes"`,
   the key mounted read-only like the token is today.
3. Remotes in SSH form, or `url.<ssh>.insteadOf` for a clone made over HTTPS.
4. `update_project_branch` and the push of C-5 choose SSH when the project
   has a key, and the HTTPS store otherwise, so the token keeps working until
   every project has moved.

### 3. One board, and each card names its project

Every task, epic and backlog item gets a `project` field. The store stays
one: the sidebar filters it, and the global screens read it without joining
several stores. A card with no `project` belongs to the one project a harness
had before this, which `dispatch migrate` writes once
(`board-service.md`, *How it would be built*, 1).

### 4. The console: a sidebar, and screens that are either the project's or the harness's

- **The sidebar** lists the active projects, each with what it has running
  and waiting, and *New project* at the bottom. Archived projects sit behind
  a toggle.
- **A project's screens**, under `/p/$slug/`: the board, the backlog, the
  debt, the learnings, and the task view.
- **The harness's screens**, the same whichever project is chosen: the main
  thread, the pool, the models, the approvals, the tokens and the queue. They
  are global because the accounts and their quota are shared by every
  project. The approvals and the queue show each item's project, and can be
  filtered by it.
- **New project** is a short form that queues one action at a time:
  1. *Clone* a remote, or *create* an empty repository;
  2. make its key and show the public half (*Decisions*, 2), and check it;
  3. run the existing `bootstrap`.

  Each step shows its own result, so a key GitHub refuses stops the flow at
  step 2 rather than failing the bootstrap.

### 5. Parallel work: across projects now, within a project later

- **Across projects, now.** Two tasks of two projects may run at once.
  Their clones, worktrees and merges never meet, so the only thing they share
  is the pool, which `pick_account` already locks one phase at a time.
- **Within a project, later.** The worker runs at most one cycle per project.
  A second start on the same project waits as *Waiting for the project*, behind
  the one running. Lifting this needs two things the user put at the end of
  the roadmap: a project lock in the dispatcher that holds across a crash, as
  the task lock does, and the Conflict state and the integrador of
  `review.md`, for two diffs on one base.
  *Narrowed 2026-10-15:* `docs/plans/autonomy.md` (*Decisions*, 6) designs
  this as a write window serialized per *Surface*, with the cartografo and
  the arquitecto free to run at once; it applies to a queue started with
  `--parallel` above 1, and until it is built this rule holds.
  *Built 2026-10-17* for `run-queue --parallel N` (`docs/decisions.md`
  ADR 83); every other start still runs one cycle per project.
- **What "running" means.** A project is busy from its cycle's first phase
  until the task is *Awaiting review*, and again for a revision round. Two
  tasks awaiting review on one project can still diverge from the base, as
  they can today with one cycle at a time; that is the merge-time conflict
  `review.md` already handles, not the parallel case above.
- **The race for quota, later.** Today the worker takes starts in queue
  order, whatever their project, and the picker gives the next phase the
  first free account. So one project's epic can take every phase while
  another waits. That is acceptable while starts are few. A fair share or a
  priority between projects is designed with the item above, not before.
- **An epic is one task at a time** while the rule above holds.
  `board-service.md`'s *Start epic* queues the next task when the one before
  it is awaiting review, not as many as there are accounts.

### 6. The console shows how many tasks can run at once, and why

A task's cycle does not hold an account: each phase takes one when it starts
and gives it back when it ends. So the limit is on phases, and the console
shows it in those terms, where a task is started and where a thread is chosen
(`main-thread.md`, *Decisions*, 8).

- **Slots.** One per account that may take a phase. A worker account counts
  while it is under its threshold; the primary counts as a slot with its
  reserve, marked as such, because a phase there spends the conversation's
  quota. A cooling or parked account is drawn as a slot that is closed, with
  its reset time.
- **What fills them.** Each open slot shows the phase on it, its task and its
  project. An empty slot says so.
- **What waits, and on what.** Under the slots, the started tasks that are not
  in a phase, each with the reason:
  - *Waiting for an account*: every slot is full or closed. It shows the
    soonest reset.
  - *Waiting for the project*: another task of the same project is running
    (*Decisions*, 5).
  - *Between phases*: the cycle is moving to its next role and will take the
    next free slot.
- **Before a start.** The *Start development* confirmation
  (`board-service.md`) says how many slots are free now and where the task
  would stand, such as "2 accounts, 1 free; this task would start now" or
  "behind 1 task of this project". It never guesses a time.

Two accounts are two slots. The screen does not imply more: adding an
account is the only thing that adds one.

## How it would be built

Each step is a single surface. Steps 1, 3 and 4 need `board-service.md`'s
module; steps 2, 4 and 5 need its queue and worker.

1. **The project record**, and `project` on every card, with its migration.
   The dispatcher reads the record's settings over `config.yaml`'s.
2. **SSH in the dispatcher.** Built 2026-10-11. The image's `openssh-client`
   and pinned host key, `GIT_SSH_COMMAND`, and a CLI command, `project-keygen
   --project <slug>`, that makes the pair and prints the public half. This is
   the part the user put first, and it needs no console: until the queue
   exists, the operator runs the command.
3. **The worker's one-cycle-per-project rule**, and the epic taking one task
   at a time.
4. **The sidebar and the routes**, read-only first: the project's screens
   filtered by slug, the harness's screens unchanged. A front task, behind its
   own ask for a build.
5. **New project and the key from the console**: the `clone`, `init`,
   `keygen` and `check` actions in the worker, and the form.
6. **The slots** (*Decisions*, 6), on the pool's api and the queue's. A front
   task.

## Open questions

- **Where the record lives.** `.hive/projects/` keeps it beside the board; a
  file in the clone would travel with the repository but be written by a
  phase. The hive is assumed until step 1 says otherwise.
- **Creating a repository on GitHub.** *Create* makes a local repository with
  no remote. Creating one on GitHub needs a token with that scope, which a
  deploy key cannot be, so it is left to the person.
- **Projects with no remote.** `scratch` has none that matters. A project with
  no remote has no key, and the console says it never pushes.

## What it is not

Not more capacity: projects share the accounts, and only an account adds a
slot. Not a credential in the browser: the console only ever sees a public
key. Not parallel work inside a project, which comes last.
