# ia-harness

An autonomous, 24/7 multi-agent development platform built on Claude Code.
A **Smart Dispatcher** cycles a task through four roles — architect,
implementer, reviewer, auditor — each run by Claude Code inside an isolated,
per-account container, handing context forward between phases via files on
disk. It exists to keep multiple Claude Pro accounts productive around the
clock without hitting quota limits or corrupting shared state.

Current scope is intentionally **serial**: one account is ever active at a
time, sized for 2 Claude Pro accounts. Parallel/load-balanced dispatch across
accounts is a documented non-goal for now (see the design spec).

## Architecture

```
Smart Dispatcher  ──reads/writes──  per-account state (disk-persisted)
        │                            IDLE / BUSY / PRE_COOLDOWN / COOLING_DOWN
        │
        ├──mirrors status──▶  Vibe Kanban (optional board, stdio MCP;
        │                       control UI loopback-only)
        │
        ▼ docker exec (claude -p ... --output-format json)
Agent container (per Claude Pro account)
   └── docker:dind sidecar (sysbox-runc, DOCKER_HOST → sidecar)
        └── registry mirror + BuildKit cache (shared across sidecars)
        │
        ▼ hooks → HTTP
Observability collector (SQLite/WAL) → authenticated dashboard (Tailscale)
```

- **Smart Dispatcher** (`dispatcher/`) — for one task, runs the role sequence
  `arquitecto → implementador → revisor → auditor`. Before each phase it
  picks an IDLE account, probes `/usage` to keep it under
  `quota_threshold_pct`, and rechecks cooling accounts when none are IDLE.
  That probe reads free text, so it takes only what it decides on: both
  percentages are required and a missing one fails the probe loudly, while
  the `· resets <when>` clause beside each is optional, because the CLI
  drops it from a line reading 0% — and nothing schedules off those
  timestamps anyway, since recovery is a re-probe.
  What the probe cannot see is a refusal. `/usage` is a *local* slash
  command — it reads counters this machine wrote, for $0 and 641 ms, and
  never asks the service — so it says what the account has spent here,
  not whether the next call will be served. Its counters also cover this
  machine only, so an account used from a phone or claude.ai reads low
  here and `quota_threshold_pct` is applied to an undercount; nothing
  local fixes that. What is fixable is that a refusal is observable at
  the moment it happens, so it gets written down: a 429 — from a phase,
  or from the probe call itself, which is classified as a refusal before
  anything tries to read percentages out of it — records
  `rate_limited_at` beside the account's state, and for
  `quota_cooldown_seconds` (1800, half an hour) that mark outranks the
  numbers. The gate will not hand the account out and the recheck will
  not even probe it, so an account the service just turned away can no
  longer be recovered on local counters that never saw the refusal.
  Inside the floor only a turn the service actually served drops the
  mark; a clean `/usage` cannot, being the reading that was blind in the
  first place. Once the floor has passed the probe decides again, and
  clearing the mark is part of letting the account back in, so an
  expired refusal never holds it twice. A probe that errors some other
  way still counts as healthy, since the phase itself is the backstop,
  but the log now says the account was waved through unverified instead
  of reporting a pass.
  Account state is a small JSON file per account (`state_dir`), written
  atomically. Rate-limit responses move an account to `COOLING_DOWN` and
  retry on another account, resuming the same Claude session via
  `--resume <session_id>` once one is available again. The stored ID
  stays good: a resumed session answers under the ID it was given rather
  than minting a new one (V5.1), and because `/root/.claude` is one
  volume shared by both agent containers, the other account resumes from
  the same transcript in the same worktree — measured end to end on
  T-006, where an arquitecto refused on cuenta1 continued on cuenta2 with
  its context intact and the work landed in one commit (V5.2). Any prompt
  that resumes a session carries two notes: one listing the subagents that
  session started, read off disk from the CLI's own state, so the phase
  revives them instead of spawning replacements it would have to brief
  again; and one telling it that the session-start `gitStatus` block is
  the snapshot from *before* its own edits, so it re-reads the worktree
  instead of redoing work already sitting in it. Every step of a
  hand-over is logged by name — the refusal and the session being handed
  on, the account going `COOLING_DOWN`, each account that comes back
  `IDLE`, and the account every phase runs on — so a run that changed
  hands no longer reads like a clean one (T-007, V5.3).
  Each phase runs under an in-container `timeout`
  (`phase_timeout_seconds`, 2 hours by default) and counts as failed
  when it expires.
- **Agent containers** — one per Claude Pro account, each with its own Claude
  Code config home (`CLAUDE_CONFIG_DIR=/root/.claude-account`, backed by that
  account's own `claude_creds_<account>` volume), so `.credentials.json` and
  `.claude.json` never leave that account's volume. Names every account
  shares (session history, skills/agents/commands/plugins, `settings.json`)
  live in the `claude_shared` volume at `/root/.claude` and are symlinked
  into each account's config home by the image entrypoint; that
  `settings.json` also turns the claude.ai skill and plugin sync off, so no
  role pays for skills nobody chose. Each pairs with its own `docker:dind`
  sidecar (`DOCKER_HOST` pointed at the sidecar, `sysbox-runc` runtime, no
  `--privileged`) so agents can build/run containers without touching the
  host Docker daemon.
- **Vibe Kanban** — an optional task board, off unless `config.yaml`
  carries a `vibe_kanban` block. The dispatcher opens one issue per task
  and moves it as the cycle runs; nothing flows back, and a board call
  that fails never fails the phase. Its MCP server is spawned as a
  subprocess and speaks stdio, so nothing dials it over the network; the
  web UI is bound to `127.0.0.1` only, never exposed off-host, and sits
  behind a Compose profile. Its service is retired (see Known gaps), so the
  board with nothing behind it is the one that works: a `local_board` block
  names a directory and the dispatcher keeps one JSON card per issue in it.
  One block or the other, never both.
- **Context handoff** — `.hive/tasks/<task-id>.md`: YAML frontmatter
  (`status`, `owner`, `depends_on`, `heartbeat`, the operator's
  `description`, and `kanban_issue_id` when there's a board) plus a body
  that accumulates each phase's handoff. A phase returns that handoff as a
  schema (`--json-schema`, per role: what changed, what was verified, what's
  pending, risks, the subagents it started, proposed learnings and debt, and
  paths to the detail — plus the revisor's `verdict`), within a per-role byte
  budget the dispatcher enforces with one `--resume`; a phase that answers
  in prose anyway lands clamped to its first 500 and last 1,500 characters.
  The budget is stated to the role twice, as a number of entries and as the
  bytes those entries buy (`handoff.lines_for` derives the first from the
  second, so they can't drift): nothing can count its own bytes while it
  writes, which is why every run before 2026-09-24 overran one. The bytes are
  what the dispatcher measures, and they were tuned from those runs —
  `handoff.py` carries the numbers with the evidence for each. An overage
  inside a 10% margin (floor 256 bytes) is taken as it came rather than spent
  on a model call, and the size is logged at INFO either way, which is the
  data the next tuning uses. A WARNING means a handoff went into the task
  file over budget anyway: the rewrite was refused, failed, or came back long.
  Detail belongs in files, cited by path and a stable anchor: durable docs
  on the task branch, per-round scratch under `.hive/tasks/<task-id>/`.
  Used for cold-start role transitions; mid-role quota exhaustion instead
  resumes the same Claude session directly via `--resume`. Stale locks
  (heartbeat older than `heartbeat_ttl_seconds`) are reaped at the start of
  each task cycle; a live lock held by another owner is refused, and the
  task goes `blocked`. A task has one branch,
  `agent/task/<task-id>`: the roles that leave something behind
  (arquitecto, implementador, the auditor, and the mapping phase when it
  runs) share one worktree checked out on it, and the dispatcher commits
  what each of those phases left before the next role runs. The revisor
  gets its own checkout, detached at that branch's tip and rebuilt every
  round, so it always reads the code as it stands rather than a pristine
  `HEAD`. Nothing it writes there survives: a detached `HEAD` is not a
  branch, so the dispatcher has nowhere to commit it, and the checkout is
  deleted at the end of the round. A revisor's finding has to travel as a
  change request in its handoff, never as an edit, and its prompt opens
  with that. When it writes anyway, the dispatcher says so instead of
  letting the round swallow it: every phase outside the writing set is
  asked for `git status --porcelain` on its own checkout the moment it
  returns, and a dirty one is a WARNING naming the role, the checkout and
  the paths, plus one `--resume` telling the phase its edits are already
  lost and asking for the change back as a finding — `verdict:
  CHANGES_REQUESTED` for the revisor — with every other field as it was.
  The corrected return is the one that lands in the task file. The edit
  itself is unrecoverable either way: committing it would put the
  reviewer's own change onto the branch it is in the middle of approving.
  What the retry buys is that the next phase hears about it at all. If the
  phase left no session to resume, or the resume fails, its first return
  stands and the WARNING is the whole record. What decides the split is
  what a role has to leave behind, not where in the sequence it
  runs — the auditor is the phase that writes the indexes, so it shares
  the writers' worktree for the same reason. It runs last, though, after
  the revisor has approved, so its commit is held to `docs/`: the paths
  its duties actually cover. Anything it changed outside them is named in
  a warning and left in the worktree rather than landing on a branch after
  the review that read it. When the cycle ends —
  `done`, `blocked`, or a crash — the review checkout is removed, since the
  next round would rebuild it anyway; the writers' one stays, holding
  whatever a failed phase left uncommitted. The exception is a task this
  run never owned: if it bounced off another dispatcher's lock, that other
  run is still working in those worktrees, so they are left alone.
- **Project docs** (`dispatcher/project_docs.py`) — what a target repo knows
  about itself, written by the roles as a side effect of the work and
  committed with its code, so a re-clone still has it. The layout is the
  contract: `docs/README.md` is the index, `docs/decisions.md` the numbered
  ADRs (appended to and struck through, never rewritten), `docs/architecture.md`
  and `docs/business.md` the map and the domain, `docs/learnings/` and
  `docs/debt/` one file per entry behind an index whose every row carries a
  trigger — the condition that says when to open it — and
  `docs/implementations/<task-id>.md` how one task was built. Each role is
  handed its duty in the prompt: the arquitecto records ADRs, the
  implementador writes that implementation doc and proposes learnings and
  debt in its handoff, the revisor treats a contract change with no doc
  change as a finding, and the auditor is the only phase that writes the
  indexes, so two phases never edit one. A business rule inferred from the
  code rather than read somewhere is filed unconfirmed, for a human to
  confirm or kill. About this project the docs outrank the role skills, which
  describe method and travel between projects. The index opens with YAML
  frontmatter carrying `build:` and `test:`, because later gates run those
  through `docker exec` with no model in the loop. A project with no index
  gets one mapping phase (`cartografo`) ahead of the arquitecto, on
  `mapping_model` and under `claude --max-turns mapping_max_turns`: it reads,
  writes those three docs, and touches no code. It is off unless
  `mapping_enabled` is set, since it spends quota and ships nothing, and it
  is never fatal — a failed map runs the task anyway. What it wrote when the
  budget cut it off is committed regardless, because the writer roles share
  one worktree and uncommitted docs would otherwise ride into the
  arquitecto's commit under the arquitecto's name. Two caveats: the index is
  looked for in the project's checkout, so under `merge_on_done: false` a map
  living only on an unmerged task branch reads as missing and the next task
  maps again; and `--max-turns` works but is absent from `claude --help`
  (2.1.273), so a CLI bump could drop it.
- **Shared learnings** (`dispatcher/learnings.py`) — the traps a project's
  docs cannot hold yet, because the task that hit one may never merge. It is
  an inbox at `.hive/learnings/inbox/`, outside every worktree and mounted in
  both agent containers, so any phase can write to it at any time: one
  Markdown file per entry, frontmatter (`project`, `task`, `phase`, `scope`,
  `status`) plus a `## Symptom` block holding the error **verbatim**, so the
  next phase finds it by grepping for the message in front of it. Every
  role's prompt carries the table and is told to grep before debugging; the
  auditor alone files entries into the project's `docs/learnings/`, and the
  dispatcher moves them with no model in the loop — it stamps the entries a
  task is carrying before the auditor runs, deletes them once that branch
  actually merges, and, when the cycle ends any other way, releases them
  unconfirmed for the next task rather than losing them with the phase that
  found them. An entry is a claim until a second, distinct task hits the same
  wall or a human says so (`dispatch learnings --confirm`): one phase's
  wrong guess repeated to every later phase is worse than no note at all.
  Entries leave the same way they arrive, on evidence: each is stamped with
  a fingerprint of the permissions it was written under and stops counting
  once those change, and a task that walked through another task's trap
  unharmed retires it by writing `refutes: <ref>` on an entry of its own.
  Two scopes — a project's own trap, and one about this harness, which is the
  only kind that reaches `.hive/learnings/harness/` and only through a human
  running `dispatch learnings --promote`. The line it draws: a trap is "don't
  step on this"; half-finished code is debt, not a learning.
- **Dispatcher gates** (`dispatcher/gates.py`) — what the dispatcher checks
  for itself between the implementador and the revisor: code that changed
  with no test beside it, the project's own `test:` command, a contract that
  moved with no documentation, and a pointer in `docs/` that lands nowhere.
  All four run through `docker exec` with no `claude` in the loop, so they
  spend no quota and sit out of reach of the phase they judge — and they
  check the claim that the tests pass instead of believing it. Three levels:
  `BLOCKING` (the suite is red) skips the revisor and sends the round around
  again with the log's path, saving a review call; `ASK` (code changed, no
  test did) buys exactly one `--resume` into the session that just ended,
  asking for a test or a one-line reason the revisor then judges; `NOTE`
  (everything else) rides along under the handoff. They are on unless
  `gates_enabled: false`, unlike the other optional phases, because they net
  quota back rather than spending it; `gates_test_timeout_seconds` bounds
  the suite. Each gate errs toward saying nothing: a false finding costs a
  real round and teaches the roles to argue with a shell script.
- **Observability** (`observability/`) — Claude Code hooks
  (`hooks/emit_event.py`, registered by `hooks/install_settings.py` on
  container start) POST events to a collector (`observability/collector`,
  Flask + SQLite/WAL), rendered by an authenticated dashboard
  (`observability/dashboard`), meant to be reached over Tailscale rather than
  exposed publicly.

Full design rationale, decisions, and open caveats live in
[`docs/superpowers/specs/2026-09-13-ia-harness-design.md`](docs/superpowers/specs/2026-09-13-ia-harness-design.md);
the implementation task breakdown is in
[`docs/superpowers/plans/2026-09-13-ia-harness.md`](docs/superpowers/plans/2026-09-13-ia-harness.md).

## Repository layout

```
dispatcher/       Smart Dispatcher: config, state machine, docker exec, CLI
observability/    Event collector (Flask/SQLite) + dashboard
hooks/            Claude Code hooks that emit events to the collector
docker/           Dockerfiles + compose files (control-plane and agents)
scripts/          Volume setup, dind image pruning
tests/            pytest suite (unit + integration)
docs/superpowers/ Spec and implementation plan
```

## Setup

Requires Docker with the `sysbox-runc` runtime installed (for the dind
sidecars) and, per account, an existing Claude Pro OAuth login. Everything
below runs on the server itself (e.g. right after `git clone`), no Python
required for the host-level steps — only the `dispatcher`/`collector`/
`dashboard` containers need Python, and they get it from their own images.

### 1. Clone and configure

```bash
git clone <this-repo> ia-harness && cd ia-harness
```

Generate `config.yaml` (and, optionally, `docker/compose/.env` with hashed
dashboard credentials) with the interactive wizard — pure `bash` +
coreutils, so it needs nothing beyond a POSIX shell and `sha256sum`:

```bash
scripts/configure.sh
```

It defaults the paths/URLs that must match the compose files' volume mounts
and internal service DNS, and only asks about them under `--advanced`.
Prefer editing by hand? Copy the template instead:

```bash
cp config.example.yaml config.yaml
```

### 2. Build the account-specific images

`dispatcher`, `collector` and `dashboard` have `build:` stanzas in
`docker/compose/docker-compose.yml` and build automatically on first
`docker compose up`. The agent and dind-sidecar images do **not** — they're
referenced by name only in `docker-compose.agents.yml` and must be built
once, by hand, before that file will come up:

```bash
docker build -f docker/agent/Dockerfile -t ia-harness-agent .
docker build -f docker/dind-sidecar/Dockerfile -t ia-harness-dind-sidecar docker/dind-sidecar
```

Note the different build contexts: the agent image needs the repo root
(it copies in `hooks/`), the dind-sidecar image is self-contained in its
own directory.

The agent image sets `safe.directory=*` and a fallback `user.name` /
`user.email` in its system gitconfig. Both are load-bearing: `/data/projects`
is a host-owned bind mount, so without the first every git command in the
container — `git worktree add` included, the first thing a role needs —
fails with `fatal: detected dubious ownership`, and without the second the
commit after it fails with `Author identity unknown`. The pattern is the
literal `*` because this image's git is 2.39.5, where `safe.directory`
matches only an exact path or `*`; the trailing-`/*` form needs git ≥2.46.
The identity is only a floor — the dispatcher passes `-c user.name=… -c
user.email=…` per commit to attribute work to the role and account.

One consequence to know about: the agents run as root, so anything they
write under `.data/projects` on the host is root-owned and your own user
can't delete it. Remove such leftovers from inside a container
(`docker exec agent-cuenta1 rm -rf /data/projects/<path>`), not with
`sudo` on the host.

### 3. Create volumes

```bash
scripts/setup_volumes.sh cuenta1 cuenta2
```

Creates the shared `claude_shared` volume and one `claude_creds_<account>`
volume per account — both declared `external: true` in
`docker-compose.agents.yml`, so they must exist before step 4. (The
per-account `dind_<account>_data` volumes are *not* external; Compose
creates those itself.)

`claude_shared` mounts at `/root/.claude` on every agent and holds only the
names every account shares — session history, skills/agents/commands/
plugins, `settings.json` — symlinked into place by the agent image
entrypoint. Each `claude_creds_<account>` mounts at `/root/.claude-account`
(`CLAUDE_CONFIG_DIR`, set in `docker/agent/Dockerfile`) and is that account's
real Claude Code config home: `.credentials.json`, `.claude.json` and
everything else the CLI keeps in its config home live there. One known
exception: the CLI hardcodes the path of `.device-keys.json` to
`~/.claude/`, i.e. `claude_shared`, whatever `CLAUDE_CONFIG_DIR` says, so if
that file is ever created, every account shares it. Login, `/status` and a
recreate didn't create it in the 2026-09-19 V0.4 re-run.

That shared `settings.json` also carries `syncClaudeAiSkills: false` and
`syncClaudeAiPlugins: false`, written by `hooks/install_settings.py` on
every container start. Without them Claude Code downloads the signed-in
account's claude.ai skills into `~/.claude/skills/synced/`, which is shared
here, so every role's system prompt carries *both* accounts' lists on every
turn — 20 skills and 13,535 bytes of name and description when this was
measured, none of them chosen for a coding role. These two keys are the
only switch that works: `CLAUDE_CODE_SYNC_SKILLS` is an enable gate rather
than a kill switch, and the keys are read from user or managed settings
only, never from a project's `.claude/settings.json`. Turning the sync off
also moves what was already downloaded from `skills/synced` to
`skills/.trash`, where `cleanupPeriodDays` deletes it. To opt back in, set
either key to `true` in `/root/.claude/settings.json`: the merge is
additive, so a value already in the file is never rewritten, and the
entrypoint prints a warning on each start while the sync is on.

> **Upgrading from the shared-login layout:** before this change a login
> landed in `claude_shared` (`/root/.claude/.credentials.json`, and
> possibly `backups/`), readable by every agent, and `/root/.claude.json`
> lived in the container layer, so a recreate loses it. Either log in again
> per account after upgrading (`docker exec -it agent-cuentaN claude`, then
> `/login`), or move the existing login **before** recreating. Under the
> old layout that account's `claude_creds_<account>` volume is mounted at
> `/root/.claude/credentials`, so a `cp -p` inside the still-running old
> container moves it without printing anything or mounting `claude_shared`
> anywhere new (shown for cuenta1):
>
> ```bash
> docker exec agent-cuenta1 sh -c 'cp -p /root/.claude/.credentials.json /root/.claude/credentials/ && chmod 600 /root/.claude/credentials/.credentials.json && { [ ! -f /root/.claude.json ] || { cp -p /root/.claude.json /root/.claude/credentials/.claude.json && chmod 600 /root/.claude/credentials/.claude.json; }; } && rm -rf /root/.claude/.credentials.json /root/.claude/backups'
> ```
>
> That login belongs to whichever one account logged in before the
> upgrade; copy it into that account's volume only. Every other account
> logs in again after upgrading (`docker exec -it agent-cuentaN claude`,
> then `/login`). The command above is a single `&&` chain, so nothing is
> removed from `claude_shared` unless the copies (and their `chmod`)
> already succeeded.
>
> Then rebuild the agent image (see above) and recreate the agents with
> named services and `--no-deps`, from the repo root:
> `docker compose -f docker/compose/docker-compose.agents.yml up -d --force-recreate --no-deps agent-cuenta1 agent-cuenta2`.
> The entrypoint warns on every start while `.credentials.json`,
> `.claude.json` or `backups/` is still in `claude_shared`. The empty
> `credentials/` directory left in `claude_shared` is harmless. Unverified:
> whether a missing `.claude.json` forces onboarding again.

Cloned project repos are **not** a named Docker volume — `agent-*` bind-mounts
`.data/projects` from the repo root itself (`../../.data/projects:/data/projects`
in `docker-compose.agents.yml`), the same repo-relative pattern already used
for `.hive`. `scripts/setup_volumes.sh` creates that directory for you so it's
owned by your user rather than root. This is intentional for a host that only
ever runs this one repo: cloned project checkouts land at
`<repo-root>/.data/projects/<slug>`, directly inspectable from the host, and
`.data/` is already gitignored so those nested checkouts never pollute
ia-harness's own git status.

> **Root-owned files:** `docker/agent/Dockerfile` has no `USER` directive, so
> the agent container runs as root — anything it writes under
> `.data/projects` (clones, commits, build output) is root-owned as it is
> created. `dispatch_phase` reads the project directory's owner before the
> phase and `chown -R`s the tree back to it afterwards, on the failure paths
> too, so a finished phase leaves the checkout yours. That's best-effort
> hygiene, not a guarantee: a phase killed outside the dispatcher (a `docker
> kill`, a host reboot) skips it, so `ls -la .data/projects` and
> `sudo chown -R` are still worth knowing.

### 4. Bring up the stack

Control plane first — it creates the shared `ia_harness_net` network that
the agents' compose file joins as `external: true`, so order matters:

```bash
docker compose -f docker/compose/docker-compose.yml up -d
docker compose -f docker/compose/docker-compose.agents.yml up -d
```

`docker/compose/docker-compose.yml` reads `DASHBOARD_USERNAME`/
`DASHBOARD_PASSWORD_HASH` from `docker/compose/.env` automatically if
`scripts/configure.sh` wrote one; otherwise pass them inline. This step
brings up three **persistent** control-plane services (`collector`,
`dashboard`, `registry-mirror`) plus, from the second file, one persistent
`agent-<name>`/`dind-<name>` pair per configured account.

Two services in those files are deliberately kept out of that default
set, each behind a Compose profile. Stage-0 verification found both by
running the two-liner above:

- `dispatcher` (`profiles: ["dispatcher"]`) is a template, not a service
  (`restart: "no"`, placeholder `--task-id`/`--project`). Ungated, a bare
  `up -d` fired it and ran a task. See step 5.
- `vibe-kanban` (`profiles: ["kanban"]`) is the optional board's web UI.
  Its image (`ghcr.io/bloopai/vibe-kanban:latest`) is denied on an
  anonymous pull, and Compose pre-pulls every named image before starting
  any service — so while it sat in the default set, that pull failure
  took down the `up -d` for all four. Nothing in the harness dials this
  service; see *The board, if you want one* in step 5. Bring it up with
  `--profile kanban` once `image:` points at something you can pull.

### 5. Run a task

Bootstrap a project directory on an account's container, then run a task
through the full role cycle:

```bash
python -m dispatcher.cli --config config.yaml bootstrap-project \
  --account cuenta1 --project my-project
python -m dispatcher.cli --config config.yaml run-task \
  --task-id T-001 --project my-project \
  --description "Add a /healthz endpoint that returns 200 without auth."
```

(Or, without a host Python install, run the same commands inside the
already-built dispatcher image: `docker compose -f docker/compose/docker-compose.yml --profile dispatcher run --rm dispatcher --config /app/config.yaml run-task --task-id T-001 --project my-project --description "…"`.)

`--description` is what every role is actually told to work on: it goes
into each phase's prompt verbatim and is stored in the task file's
frontmatter (`<hive_tasks_dir>/T-001.md`), separate from the body where
the phase summaries accumulate. Re-running the same task ID reuses the
stored description, so `--description` can be omitted on a resume;
running a task that has none anywhere is rejected as a usage error
rather than spending four phases on roles that know only an ID. For
anything longer than a sentence use `--description-file spec.md`, or
`--description-file -` to read stdin — which is the shape that works
from a container without bind-mounting the file:

```bash
docker compose -f docker/compose/docker-compose.yml --profile dispatcher \
  run --rm -T dispatcher --config /app/config.yaml run-task \
  --task-id T-001 --project my-project --description-file - < spec.md
```

`bootstrap-project` only creates the directory; cloning the actual project
repository into it is still a manual, one-time step.

Every worktree is checked out under `<project>/worktrees/`, inside the
repository being worked on, so the first one the dispatcher creates also adds
`/worktrees/` to that checkout's `.git/info/exclude`. It goes there rather
than in a `.gitignore` because it is a fact about this clone — where the
harness keeps its scratch checkouts — not something the project should carry
in its own history. The entry is anchored to the repository root, so a
project with a `src/worktrees/` of its own goes on seeing it, and the write
is idempotent and best-effort: a directory nobody has cloned a repository
into yet simply gets nothing. Without it, any project that has ever run a
task reports an untracked `worktrees/` for good, and is one `git add -A` away
from committing a worktree as an embedded repository.

A finished cycle drops its reviewing worktrees by itself, but the writers'
one is kept on purpose. Once you are done reading the result, reclaim it:

```bash
python -m dispatcher.cli --config config.yaml cleanup-task \
  --task-id T-001 --project my-project
```

That deletes `worktrees/T-001/` whole. The branch `agent/task/T-001` is
untouched — the commits are the work, these are only checkouts of them, so
`git worktree add <path> agent/task/T-001` brings any of it back.

The task branch is also where a finished task stops by default: nothing
merges it unless you ask. Once you have read the result, offer it to the
branch the project's own checkout is on:

```bash
python -m dispatcher.cli --config config.yaml merge-task \
  --task-id T-001 --project my-project
```

That is a `--no-ff` merge, and the target is read rather than configured:
whatever `.data/projects/my-project` is checked out on is the branch
whoever set the project up works from. It refuses — printing why, and
exiting non-zero — on a detached `HEAD`, on a checkout sitting on the task
branch itself, or on uncommitted tracked changes; a conflict is rolled back
with `git merge --abort`. A branch already merged is reported as up to
date, not as a failure, so re-running it is harmless. No path through it
touches, rewrites or deletes `agent/task/T-001`: the branch stays as the
record of the work and as the way back if the merge turns out to be wrong.

To have a cycle do this by itself the moment the auditor signs off, set
`merge_on_done: true` in `config.yaml`. It defaults to `false`, because
the merge is the one thing a run writes into the branch you work from. A
refused merge there is logged and the task still ends `done` — the work is
already committed on its own branch, and `merge-task` is the way back to
it.

**Resuming a run that died mid-cycle.** `run-task` always begins at the
arquitecto, so a run interrupted late — a Ctrl+C, a container that went
away, a host that rebooted — could once only be continued by paying for
every phase again. `run-phase` runs the one phase that is missing:

```bash
python -m dispatcher.cli --config config.yaml run-phase \
  --task-id T-001 --project my-project --role revisor --round 2 \
  --note "the reboot took the revisor's turn with it; round 2 is commit 1d4ca62"
python -m dispatcher.cli --config config.yaml run-phase \
  --task-id T-001 --project my-project --role auditor --final
```

Everything a phase needs around it still happens — the account and card
locks, the worktree, the commit, the gates, and the handoff appended to the
task file that the next phase reads. What does not happen is the cycle's own
judgement: it reads no verdict, starts no further round, records no resolved
debt, files no debt card and merges nothing, because each of those needs
handoffs from phases this call did not run. Those stay `run-task`'s, and the
log says so rather than leaving it to be assumed; `merge-task` is still the
way to offer the branch back.

`--round` is not cosmetic. It is what the phase is told it is on, what
labels its section in the task file (`## revisor (round 2)`), and what
decides whether `escalated_effort` applies — so a resumed second round that
does not say `2` is dispatched as though the first had never happened. It
counts from 1, and 0 is a usage error. `--role` is checked against the roles
the prompt builder has a case for, so a typo costs a usage error rather than
a full-price run. `--note` is the operator's only channel into a resumed
phase: what the dead process took with it is, by definition, not in the task
file.

`--final` is the operator saying this phase closes the task — in the role
set, the auditor. It buys exactly what the full cycle gives its last phase:
the learnings carried in beforehand, `status: done` in the task file and on
the board, and no orphaning of the entries on the way out. Without it the
task stays `pending` and the entries this run wrote go back to being
unowned, which is what every phase before the last one should do. A phase
that did not land exits non-zero and blocks the card, so a hand-driven cycle
stops rather than running the next phase over the top of it.

The verb only resumes; it does not start. A task with no stored description
is a usage error naming `run-task`, and there is no `--description`: a task
already under way has its ask on disk, and rewriting it underneath a phase
that is picking up somebody else's work is not a thing to make easy.

**Looking at the pool, and unsticking it.** Two verbs answer the question
"why is nothing running", and neither costs a turn:

```bash
python -m dispatcher.cli --config config.yaml status
python -m dispatcher.cli --config config.yaml status --probe
python -m dispatcher.cli --config config.yaml release-account --name cuenta2
```

`status` reads and prints: every account with its state, the task it is on,
how much of `quota_cooldown_seconds` a recorded refusal has left to run, and
every task card that is in progress or owned, with the age of its heartbeat
and whether that is still a live lock against `heartbeat_ttl_seconds`. It
writes nothing — deliberately, because the gate's own probe (`check_quota_ok`)
parks accounts and records refusals as it goes, and a look at the pool must
not change it. `--probe` adds each container's `/usage` numbers on the same
terms: the result goes through the same refusal detection a phase's does, so
a probe the service turns down is reported as `probe REFUSED`, but nothing is
written to the state files either way. A container that is down becomes a
`probe failed:` note on that one account rather than an error for the run.

`release-account` is the way out of the state a crash leaves behind: an
account `BUSY` on a task whose dispatcher is gone. Nothing else can reach it
— `list_idle_accounts` only returns `IDLE`, and the recheck that revives a
parked account skips any state that is not `PRE_COOLDOWN`/`COOLING_DOWN` — so
before this verb the only remedy was editing a root-owned JSON file by hand.
It sets the account back to `IDLE` and clears the lock on whatever card it
holds, dropping `owner` and `heartbeat` and leaving `status` alone: whether
the task is still in progress is the task's business, not the account's. It
finds that card by the union of what the state file names and what the cards
themselves say they are owned by, so a card the state file never knew about
still comes back.

It refuses, printing why and exiting non-zero, in the two cases where
releasing would be the wrong thing:

- **The lock is live.** A heartbeat refreshed inside the TTL means a
  dispatcher is still running that phase, and releasing would let a second
  phase start in the same container. Wait for the TTL to lapse, or pass
  `--force` if you know that process is gone.
- **A refusal is still inside its cooldown.** The account was turned down by
  the service less than `quota_cooldown_seconds` ago; handing it out now
  spends the next phase on an account known to refuse. `--force` overrides
  this too and keeps the mark, so the gate still honours it; `--clear-rate-limit`
  is the one thing that erases it, for when the refusal is known to be stale.

An account that is already `IDLE` is a no-op and exits zero — but its
orphaned card, if it has one, is still released, because that is the other
half of the same crash. An account name the config does not have is a usage
error naming the ones it does.

**The map, if the project has none.** A target repo the agents have never
seen has nothing written down for them, and every task rediscovers it from
the source. Set `mapping_enabled: true` and a `run-task` on a project with
no `docs/README.md` runs one extra phase ahead of the arquitecto: a
read-only `cartografo` that writes that index — `build:` and `test:` in its
frontmatter, then the stack, the modules, and a table of the docs that
already exist — plus `docs/architecture.md` and `docs/business.md`, and
changes no code. It runs on `mapping_model` (sonnet by default, not
`default_model`) under `mapping_max_turns` turns, and is cut off there;
whatever it wrote by then is committed, and the next task extends it. It is
off by default because it spends quota on a phase that ships nothing —
with it off the docs still grow, one task at a time, from the roles that do
the work. The phase is never fatal: if it fails the task runs anyway, with
a warning and whatever docs exist. The check is made against the project's
checkout, so with `merge_on_done: false` a map still sitting on an unmerged
task branch reads as missing and the next task maps again. Merge the first
task, or expect a second map.

**The gates, before anyone pays for a review.** Between the implementador
and the revisor the dispatcher checks the worktree itself, through
`docker exec`, with no model in the loop:

- **Tests in the diff.** Code changed against the branch's fork point and
  nothing that looks like a test did. Untracked files count, since a brand
  new test is exactly the file a plain diff would miss.
- **Tests run.** The `test:` command from the project's `docs/README.md`
  frontmatter, run in the worktree under `gates_test_timeout_seconds` (900
  by default). A project with no index has no command and gets no gate.
- **Contracts without docs.** An OpenAPI or GraphQL file, a `.proto`, a
  migration, `.env.example` or `schema.prisma` moved and no `.md` did.
- **Broken pointers.** Every backticked path and link target under `docs/`
  is resolved from the repo root and from beside the file citing it; one
  that lands nowhere either way is reported.

What a finding is worth depends on what it costs to answer. A red suite is
**blocking**: the revisor is never called, the round goes around again, and
the next implementador is handed the log, written to
`.hive/tasks/<task-id>/gates-round-N.log` — a path both the dispatcher and
the agents mount at the same name. If every round ends that way the task
ends `blocked`, as any exhausted revision loop does. Code with no test is an
**ask**: one `--resume` into the session that just finished, requesting a
test or a one-line reason under `risks`, and the gates re-run on whatever
comes back. Everything else is a **note** appended under the phase's handoff
in the task file, for the revisor and the auditor to weigh.

Set `gates_enabled: false` to turn all of it off. It defaults to `true`,
unlike `mapping_enabled` and `merge_on_done`: the gates spend no quota and
what they catch would otherwise cost a revisor call plus another round. The
implementador is the only role gated, only when its phase actually finished,
and a gate that crashes lets the review proceed ungated rather than failing
the task — the finding it would have made is worth less than the phase
already paid for.

Two things they deliberately do not do. A worktree with no `node_modules`
makes the test command fail to *start*, and that is reported as a note, not
as a red suite, because no implementador can fix it from inside its session
— installing dependencies per worktree is still open. And the contract gate
only knows what a filename shows: a public export or a CLI flag is just as
much a contract, and those stay where they were, in the revisor's duties.

**Picking a subagent back up, instead of starting one over.** A phase that
delegated work to a subagent and then lost its session — to a rate limit, to
a gate asking for a test — used to hand the next attempt nothing but a
briefing: the replacement re-derived context the original still held, at full
quota cost. It does not have to. The subagent outlives its parent, and the
CLI announces it on the resume by itself (`task_notification`, `status:
stopped`), so the dispatcher appends a note to any prompt that resumes a
session, listing what that session started and telling the role to revive it
with `SendMessage` addressed to its raw agent ID before spawning anything
new. A revived subagent still holds its original instructions and every turn
it completed; only the turn the kill interrupted is lost, so a single-turn
subagent redoes its work but still knows what the work was — which beats a
respawn that also has to be briefed again.

The IDs come off disk rather than out of the model: the Agent tool tells it
never to repeat an agent ID, and phases run under `--output-format json`,
with no event stream to read one from — but the CLI leaves one
`agent-<id>.meta.json` per subagent beside the session transcript, and the ID
is the filename. That read is a single `docker exec`, swallowed like a gate:
an ID the dispatcher could not read costs a respawn, which is what happened
before any of this existed, and must never cost a phase. The note rides on
the prompt rather than in the role skills, which are charged on every call,
and is left off the one resume that merely asks a phase to shorten an
oversized return — there is no work to hand back there. The same list fills
the handoff's `subagents` field, over whatever the phase put in it. Failing
over to the other account keeps all of it: the per-agent transcript lives in
`claude_shared`, so the account picking the session up reads the same file
(D1 in [`docs/ROADMAP.md`](docs/ROADMAP.md)).

**What one task learned, before the next one pays for it.** A project's
`docs/learnings/` only helps once the branch that wrote it merges, and the
task most worth learning from is the one that ended `blocked`. So there is
an inbox outside every worktree, at `.hive/learnings/inbox/`, mounted in
both agent containers at the same path the dispatcher uses. Any phase can
write to it at any moment — the prompt asks for it *when the trap is
understood*, not at the end, because a phase that times out takes with it
everything it had not written to a file. One Markdown file per entry, named
`<task-id>-<slug>.md`, never edited in place: two tasks write here at once,
and one file per entry is what keeps them apart.

Every role is handed the same table — `# | Learning | When it applies |
Status` — and the same instruction: before spending a turn debugging, grep
that directory for the exact error text in front of you. That is why an
entry quotes its `## Symptom` verbatim. A project sees everything a human
has reviewed plus everything it found itself; another project's unreviewed
note stays out of its prompts.

The dispatcher moves the entries with no model in the loop. It opens the
directories before the first phase; stamps the entries this task is carrying
just before the auditor runs, so the auditor knows which rows are its to
file into `docs/learnings/` on the task branch; deletes them once that
branch actually merges, automatically or via `merge-task`, because the docs
now hold them; and, when a cycle ends any other way — blocked, refused
merge, crash — clears the stamp and leaves them in the inbox, marked with
the task that dropped them, for whoever hits the same wall next. A run that
bounced off another dispatcher's lock touches nothing: those entries belong
to a run still working.

Entries start `unconfirmed` and are promoted by evidence, not by assertion:
when a second, *distinct* task files an entry whose symptom flattens to the
same fingerprint, both go `confirmed`. That is the whole guard against
poisoning — one phase's wrong guess, repeated to every later phase, is worse
than no note at all — and it is why the prompt says an unconfirmed entry is
a lead, not an answer.

Nothing in there is true for ever, and an entry is a claim about a harness as
much as about a project: "running `node` needs approval" was right until the
allowlist shipped, and "a review phase's edits never reach the branch" was
right until the auditor was given the writers' worktree. Both outlived their
truth in the inbox, and one of them cost three phases of one task a turn each
arguing with it. So there are two ways out besides `--drop`. Every entry is
stamped, when its task ends, with a fingerprint of the permission surface it
was written under — `permission_mode`, `allowed_tools`, and which roles get
the writers' worktree, which is exactly what made both of those false — and
one written under a different surface is *stale*: still in the table, marked,
because it may well still be right, but no longer counted as the second
sighting that confirms anything. An entry from before the stamp existed is
unknown, not stale, so the feature does not retire the inbox the day it
ships. The other way out is the mirror of the confirmation rule: a phase that
was in an entry's exact situation and found no trap writes its own entry with
`refutes: <ref>` in the frontmatter, and the next reconcile marks the old one
`refuted` — off every phase's table, still on disk, still in yours, with the
task that retired it named in `refuted_by`. A task cannot refute itself, and
a promoted entry is never retired without a human: it is in the shared store
on every project's behalf, so one project's counter-example is a reason to
look, not a verdict. That refutation runs unattended where promotion does not
because of the asymmetry — a wrong refutation costs a later phase the debug
it would have had before anyone wrote the entry, while a wrong confirmation
actively misleads every phase that reads it.

The calls a script should not make are yours:

```bash
python -m dispatcher.cli --config config.yaml learnings
python -m dispatcher.cli --config config.yaml learnings --project my-project
python -m dispatcher.cli --config config.yaml learnings --confirm inbox/T-001-pg.md
python -m dispatcher.cli --config config.yaml learnings --promote inbox/T-001-pg.md
python -m dispatcher.cli --config config.yaml learnings --refute inbox/T-001-pg.md
python -m dispatcher.cli --config config.yaml learnings --drop inbox/T-001-pg.md
```

With no flag it prints every entry the harness holds, in the same table the
phases are shown, so what you rule on is what they read. `--confirm` is the
other half of the confirmation rule, for a trap you have hit yourself
(`--unconfirm` takes it back); `--refute` retires one the harness has
outgrown, keeping the file and the row; `--drop` deletes one that was wrong
to begin with and is worth nobody's screen space. The listing marks a stale
row as stale against the permissions the config would give a run right now,
which is the same mark the next task's phases will see.
`--promote` moves an entry into `.hive/learnings/harness/`, the cross-project
store, which every project's phases then read: that is one phase's word
applied to every project at once, so there is deliberately no automatic path
in. Scope is the dividing line the roles are given — `project` is this
repo's own code, config or tests; `harness` is what every project shares,
the container, the CLI, the worktree, the tooling. And the closing rule, in
every role's prompt: a trap is "don't step on this"; work you chose not to
finish is debt, and that goes in the handoff instead.

**The board, if you want one.** A board is optional and unconfigured by
default: with neither board block in `config.yaml` the dispatcher runs
exactly as it does above and says nothing about a board. Add one — copy a
commented block from `config.example.yaml` — and each `run-task` opens an
issue for the task, then moves it as the cycle goes `in_progress:<role>` →
`blocked`/`done`. `--kanban-issue-id <uuid>` points a task at an issue that
already exists instead; ids are uuids either way, server-assigned by Vibe
Kanban and minted by the local board, so `list_issues` is how you find one.
The id lands in the task file's frontmatter as `kanban_issue_id`, which is
what survives a restart. The board is a visibility aid and never dispatch
state: every call to it is best-effort, and one that fails is logged rather
than failing the phase.

There are two blocks to choose between, `vibe_kanban` and `local_board`,
and configuring both is a startup error naming which one to drop rather
than a silent precedence rule (`docs/decisions.md` ADR 2).

Two things to know before uncommenting the `vibe_kanban` block:

- **Its MCP server speaks stdio, not HTTP.** The dispatcher spawns
  `vibe_kanban.command` as a subprocess; there is no URL to point at. The
  `vibe-kanban` compose service (`http://127.0.0.1:9100`, loopback-only,
  `--profile kanban`) is the web UI for you, not something the dispatcher
  reaches over the network. The shipped dispatcher image has no Node, so
  a bare `npx vibe-kanban@0.1.44 mcp` will not run inside it: add Node to
  `docker/dispatcher/Dockerfile`, or point `command` at a wrapper of your
  own.
- **`status_map` is a guess to correct, not a default to trust.**
  `update_issue` only accepts a status name the project already has, and
  no MCP tool lists a project's names, so the shipped `In Progress` /
  `In Review` / `Done` are a starting point. An unmapped status is
  skipped with a warning rather than sent. Creating or reading issues
  also needs that server signed in to Vibe Kanban's cloud — see Known
  gaps.

**The board that is a directory.** `local_board:` with a `dir:` — say
`/state/board`, on the `dispatcher_state` mount so the cards outlive the
container — is the other block, and the one that works today: no service,
no Node, no account. The dispatcher writes one JSON document per issue
into that directory, atomically, and reads them back, so an `ls` is the
whole board until Phase 2 of [`docs/plans/board.md`](docs/plans/board.md)
renders it. It has no `status_map` and needs none: there is no column here
to rename, so a card carries the dispatcher's own status verbatim,
`in_progress:<role>` included — the one dimension a generic board flattens.
Why each of those is the way it is: `docs/decisions.md` ADR 1.

**What "issuing commands from the interface" means today:** nothing flows
the other way. Creating or updating an issue on the board does not make
anything run. The dispatcher is a one-shot CLI, not a daemon watching for
new tasks, so `run-task` above still has to be invoked manually (or from
your own automation/cron) per task-id. There is no push-button "run" in
the UI yet.

**The work a task chose not to do.** That is the other half of the closing
rule above: a trap is "don't step on this", debt is work left undone, and
debt that lives only in a handoff is read by the next phase of that task and
by nobody else. So it takes a fixed route through the cycle and comes out as
a row in `docs/debt/README.md` — versioned with the code, so a re-clone still
has it, and read like the learnings index: every row carries a `where`, the
condition a later task checks against its own work to know whether the entry
bites it.

The implementador declares, one entry per piece of work, with six fields:
`origin` (`introduced` if this task created the debt, `found` if it was
already there), `what` it is, `where` it bites, `why` it stays, the `cost` of
leaving it, and the `fix` that would resolve it. Found debt counts only in
files this task touched and only if the index does not already carry it — the
rest of the project's backlog is not this task's to re-declare. And declaring
is never a way out of blocking: whatever would block the task, like a
decision it was never given or a schema change, still blocks it.

The revisor rules on each declaration, and the ruling decides where the round
goes. `accepted` moves on. `rejected` is a finding like any other — the task
goes round again and it counts against `max_revision_rounds` — which is why a
round that comes back APPROVED over a rejected declaration is not an
approval, and is logged as the contradiction it is. `blocks` is for a block
wearing a debt costume, and ends the task `blocked` with no further round,
because another round cannot supply a decision the task was never given.
Anything the revisor did not rule on is accepted: a missed ruling costs one
duplicate card, a dropped declaration costs the entry itself.

Then the dispatcher files them — the dispatcher and not an agent, because an
agent that can create cards can assign itself work, and an agent re-run for a
second review round would create the same card twice. Each accepted
declaration gets an id, `<task-id>-D<n>`, and one card, opened with
`create_issue` *before* the auditor runs, so the row the auditor writes can
already point at it. The card carries the whole declaration rather than a
summary — a human triaging the board should not have to clone the repo to
know what they are approving — plus the id of the entry, which stays the
source of truth. `create_issue` takes no label argument, so the `debt` label
rides in the title as a `[debt] ` prefix, where a board filter can still find
it. A declaration that flattens to the same fingerprint as a row already in
the index gets no id and no card; the index is read from the worktree this
task is being built in, so a re-run that already filed its entries sees them.
The auditor then writes the rows and the entry files under `docs/debt/`,
still the only phase that writes an index.

A card in the backlog is a record, not work: moving it out of the backlog is
how a human approves the work, and nothing dispatches it on its own. The way
back in is `resolved_debt` — an implementador that resolved an entry the
index already carries names its id there, and the dispatcher stores it in the
task file rather than leaving it in the handoff, because the merge that
closes the card can happen days later. Both the automatic merge and
`merge-task` read it, and say what they closed.

The two halves of "resolved" deliberately land in different places. The
auditor marks the row resolved where it stands, on the task branch, with the
task that resolved it and never by deleting it — a fix that gets reverted
should still have its row — because it is the only writer of the indexes and
the mark belongs in the commit that made it true. The dispatcher closes the
card, and only after the merge: a branch that never lands leaves the board
exactly as it was.

A project with no `vibe_kanban` block loses only the cards. Every other step
runs identically, the entries are filed the same way, and a merge says
nothing about a board it was never given — the index is the source of truth,
and the board is the aid.

Additional accounts are added by duplicating an `agent-*`/`dind-*` pair in
`docker/compose/docker-compose.agents.yml` and the matching entry in
`config.yaml`'s `accounts` list — no dispatcher code changes required.

### Deploying with Coolify

**Recommended: single-file import.** `docker/compose/docker-compose.coolify.yml`
merges both compose files into one self-building resource — point Coolify's
**Docker Compose** resource type at it and it builds *every* image itself
(agent and dind-sidecar included, via `build:` stanzas that the split files
don't have), so none of steps 2–3 above need to run by hand first. It
differs from the split files in three ways, all in the interest of a clean
one-click import:

- `claude_shared`/`claude_creds_cuenta1`/`claude_creds_cuenta2` are plain
  named volumes instead of `external: true`, so Coolify creates them itself
  on first deploy — `scripts/setup_volumes.sh` isn't needed. The tradeoff:
  if you ever remove this resource in Coolify with its "delete volumes"
  option on, your Claude Pro OAuth sessions go with it, where the external
  volumes in the split-file path deliberately survive a `docker compose down`.
- `ia_harness_net` is declared once, in this file, instead of being created
  by one compose file and joined `external: true` by the other.
- `dispatcher` is gated behind a Compose **profile** (`profiles: ["dispatcher"]`)
  so it never joins the resource's default running set. Run it ad hoc —
  Coolify's "Execute Command" on this resource, or plain SSH:
  `docker compose -f docker/compose/docker-compose.coolify.yml --profile dispatcher run --rm dispatcher --config /app/config.yaml run-task --task-id <id> --project <name>`.

Two things still can't be automated by any compose file, Coolify import
included:

- **`config.yaml` must exist before first deploy.** It holds no secrets
  (just paths, thresholds, and account names — see `config.example.yaml`),
  so a Coolify pre-deployment command is safe: `test -f config.yaml || cp config.example.yaml config.yaml`.
  Set `DASHBOARD_USERNAME`/`DASHBOARD_PASSWORD_HASH` through Coolify's own
  environment-variables UI on the resource instead of a `.env` file —
  Coolify substitutes `${VARS}` into compose the same way.
- **Per-account Claude Pro OAuth login** is an interactive, browser-based
  step done once per agent container after it's up (e.g.
  `docker exec -it agent-cuenta1 claude`, then `/login`). No import can do
  this for you.

**Alternative: two Compose resources.** If you're already managing the
agent/dind-sidecar images or the `claude_shared`/`claude_creds_<account>`
volumes yourself outside Coolify (e.g. reusing them across a fleet, or you
want them to outlive a resource deletion), import the split files instead
of the merged one, as two separate Coolify Compose resources:

1. `docker/compose/docker-compose.yml` (control plane: `collector`,
   `dashboard`, `registry-mirror`, plus `vibe-kanban` behind the `kanban`
   profile).
2. `docker/compose/docker-compose.agents.yml` (one `agent-<name>`/
   `dind-<name>` pair per account — scale this file, not individual
   services, when adding accounts).

This preserves the external network/volume wiring between the two files
and the `depends_on` ordering, which hand-recreating each container as a
separate Coolify resource would not. Unlike the merged file, steps 2–3
above (build the agent/dind-sidecar images, create the `external: true`
volumes) still have to happen on the host — or in a Coolify pre-deployment
command — before either resource comes up, since neither split file builds
those images or creates those volumes itself.

`registry-mirror` is a soft dependency in either path: the dind sidecars
pass its address straight to `dockerd` and fall back to pulling from Docker
Hub directly if it's unreachable, so it's fine to keep it in the
control-plane resource/service without it blocking startup.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

## Future work

Documented but **not designed or implemented** — evaluate when the work is
actually taken on, not before. The known gaps come first; the items after
them are ordered by priority, highest first.

Stage-0 no-quota checks against the real stack ran on 2026-09-16; each
Known-gaps bullet below cites the result rows that confirmed it. Stage-0
covered stack plumbing and the Vibe Kanban MCP surface; the checks that
needed quota came later, on 2026-09-23 — a first end-to-end task (V3),
with headless permissions and the `/usage` probe answered for free inside
that same run (V1.2, V1.3), plus cross-account resume (V5.1) and a real
rate-limit result (V5.4). The same cycle has been dispatched eight times
in all — three V3 runs, one config change apiece, the third being the
first to reach `done`; a fourth on 2026-09-24 that changed no config and
measured the fixes that run forced; then Stage 1's two-account acceptance
(T-005) and the failover check with an injected 429 (T-006, V5.2), both
the same day; and a seventh on 2026-09-25 (T-007) that re-ran that
failover against the two fixes it forced and, the 429 now spent once
rather than for good, caught an account coming back from `COOLING_DOWN`
mid-task (V5.3); and an eighth, also on 2026-09-25, the first aimed at
this repo rather than the toy project, which a host reboot cut off
mid-cycle — no defect of its own, but the only run so far to show what
the harness does when it is killed rather than finished, which is where
the last two known gaps below come from. The bullets below are what those
runs left open. Still
unverified: V1.1, the failure paths, and a failover on a phase whose
predecessor had already committed — see
[`docs/ROADMAP.md`](docs/ROADMAP.md) for the full results log and
what's still pending. Their results decide how the known gaps get
fixed, and each prioritized item lists the checks it depends on.

### Known gaps (fix first)

The core pipeline works end to end as of 2026-09-24: the third dispatched
run of the same task took it from `blocked` to `done`, with the revisor
approving and the tests green on the branch, and the fourth — same task
shape, same config, one rebuilt dispatcher image — closed two of the four
gaps that run had opened, landing a `docs/`-scoped auditor commit on the
task branch and reading a 0% session without a warning. The last two —
nothing retired a learning the harness had disproved, and the handoff
budgets had been sized before a phase could write anything — were closed
after it, and the fifth run carried them: Stage 1's acceptance passed on
all four criteria, on two accounts and the default three-round config,
without the task file being seeded by hand. The sixth forced the thing
five clean runs had never produced — a phase changing hands mid-flight,
with a 429 injected into cuenta1's CLI — and the failover held: one
session, two accounts, one commit, and the cross-account `--resume` that
had been an unverified assumption since the design spec is now a measured
fact. It also opened two gaps about what the harness fails to *say* — a
failover invisible in the log, and a resumed phase reading a status block
taken before its own edits — and the seventh run, same injected 429 but
spent once, measured both fixes on the only path that produces them: four
lines naming the refusal, the cool-down, the two recoveries and the
account each phase ran on, and a resumed arquitecto that opened with
`git status` and its own log and then re-verified instead of rewriting.
What is left below is what a no-quota check couldn't settle, and the unit
tests mock Claude Code, Docker, and Vibe Kanban, so they don't settle it
either.

- **Unverified assumptions.** Worth a manual check before building on
  them. The check for each is in [`docs/ROADMAP.md`](docs/ROADMAP.md),
  along with others not listed here (dind isolation, the registry mirror
  and bind mounts — V0.7, not yet run, blocked by V0.1 (no `sysbox-runc`
  on the verification host)):
  - Vibe Kanban's responses and status names: **not verifiable, and no
    longer worth verifying.** `vibe_kanban_client.py` speaks the surface
    measured live against `vibe-kanban@0.1.44` (stdio,
    `create_issue`/`list_issues`/`get_issue`/`update_issue`, keyed on
    `issue_id` — V2.1–V2.2), but no issue has ever been created or read
    back and none can be: the 2026-09-25 re-run found the remote service
    retired, not gated. `api.vibekanban.com` answers the same SPA shell
    to `/`, `/api/organizations` and `/health`; `create_issue` fails with
    `project_id is required` because projects are the feature
    `vibe-kanban` PR #3387 sunset in 0.1.44; and 0.1.45, the build whose
    notes promise local projects back, was unpublished from npm two hours
    after release, leaving 0.1.44 as `latest`. So V2.3–V2.5 have nothing
    to read an id, a status name or a description off, and V2.6 fails
    outright — for everyone, not for this machine. The 33 tools the MCP
    server still advertises are registered unconditionally; advertising
    is not capability. What the client assumed stays assumed and stops
    mattering: that ids are server-assigned uuids, and that
    `update_issue.status` takes a fixed per-project set of names. The
    seam it sits behind is what survives, and Phase 0 of
    [`docs/plans/board.md`](docs/plans/board.md) has put a local
    implementation behind it — `local_board` in `config.yaml`,
    `LocalBoardClient`, `docs/decisions.md` ADR 1 — so the two prioritized
    items that waited on `create_issue` no longer wait on this. What that
    client does is covered by the suite and has not yet run in a real
    dispatch; V2.6 is the check that would say otherwise. (V2.1–V2.6)

- **The handoff budgets are sized on a toy repo.** `_BUDGET_BYTES` in
  `dispatcher/handoff.py` was tuned on four runs against the toy project,
  where each role overran by tens of percent at worst and the shrink
  retry brought it back. T-008, the first dispatch against this repo,
  broke that: all three roles that ran blew the budget on the first
  attempt — arquitecto 6540/4096 (+60%), implementador 8905/5120 (+74%),
  revisor 5226/4096 (+28%) — and two of the three were still over after
  the rewrite, which the dispatcher accepts as is by design. Three roles
  failing the same way in one run is a sizing problem, not three
  incidents, and the likely driver is the target: a real repo has more
  paths worth citing than the toy one. `lines_for` is suspect for the
  same reason, since it derives its entry count from the same budget.
  Raising the numbers is not the whole fix — the point of the budget is
  that detail goes to files and the handoff cites paths — so the sizing
  has to be re-derived from what the over-budget returns actually
  contained. The four-run history is in
  [`docs/ROADMAP.md`](docs/ROADMAP.md), Stage 1 item 2.

### Prioritized

1. **Project memory: role skills, per-project docs, and a pointer-based
   handoff.** Highest leverage for the least code. Every phase is a fresh
   `claude -p` session that starts cold: a role is only a name in the
   prompt, it rediscovers the target repo from scratch, and what it leaves
   the next role is truncated freeform prose. Ten pieces, which pay off
   together:
   - *Role skills (method).* Give Claude in each agent container
     (arquitecto, implementador, revisor, auditor) a small set of skills
     on how to work, instead of one flat system prompt per role. Skills
     describe how to work, never a project's conventions, which belong in
     that project's docs; a project that ships its own `.claude/skills`
     wins on its domain. Keep each skill a short `SKILL.md` plus reference
     files opened on demand, so a role doesn't pay context for sections it
     doesn't use. Deliver them per call, not by installing them in the
     shared `/root/.claude` (see item 5): candidates are
     `--append-system-prompt-file`, `--agents`, or a per-role
     `--plugin-dir`, the same flag item 5 uses for packs. Three sources,
     all vendored and trimmed rather than installed, and a fourth not yet
     read:
     - [superpowers](https://github.com/obra/superpowers), for the
       general method. Don't install the plugin in the agent image: its
       `SessionStart` hook injects `using-superpowers`, whose "invoke a
       skill if there's even a 1% chance it applies" rule spends turns in
       every phase, and some of its skills wait on a human
       (`brainstorming` wants the design approved before any code,
       `finishing-a-development-branch` asks whether to merge or open a
       PR), so under `-p` the phase ends on a question after spending the
       quota. Worth copying: `writing-plans` (arquitecto),
       `test-driven-development` and `verification-before-completion`
       (implementador, auditor), `receiving-code-review` (implementador in
       revision rounds), and `systematic-debugging` (any role stuck across
       more than one retry), plus the reading discipline from item 2. Skip
       `subagent-driven-development` and `using-git-worktrees`: the
       dispatcher already splits the work by role and creates the
       worktrees.
     - Cursor's
       [thermos](https://github.com/cursor/plugins/tree/main/thermos)
       plugin, for the revisor and auditor (a correctness/security review
       plus a code-quality review, run in parallel and synthesized). Worth
       borrowing: diff-only scope, verifying a finding before reporting
       it, severity calibration, a devex breakage checklist (env vars,
       ports, secrets), an explicit approval bar, and reading the diff
       before the implementador's summary. `thermo-nuclear-review` becomes
       the revisor skill, blocking only on correctness, security, and
       clear regressions; quality suggestions block nothing, or a capped
       revision loop never converges. `thermo-nuclear-code-quality-review`
       becomes a non-blocking pass in the auditor phase, whose notes go to
       a human. Drop the `thermos` orchestrator skill: its packaging
       assumes Cursor, its PR/BugBot step has no PR to read here, and two
       reviewers per round double the quota. Tone down its all-caps
       "nothing can slip through" wording too, which invites
       over-reporting.
     - [ponytail](https://github.com/DietrichGebert/ponytail), for scope
       control, where the other two cover method and review. It's a YAGNI
       ladder: before writing code, stop at the first rung that solves the
       problem actually stated instead of building for the next one.
       Independent testing by JetBrains measured −15% code, −10.3% cost
       and −11% time against a no-skill baseline, with no quality
       difference detectable at their ~80 pairs — a small sample, but on
       exactly the axis that bottlenecks this harness (item 2), and less
       code per round is also fewer revision rounds. Give it to the
       arquitecto and implementador only: a revisor carrying a YAGNI bias
       approves thin work instead of flagging it, and its bar already
       comes from `thermo-nuclear-review` above. Vendor and trim it like
       the other two rather than `/plugin install`-ing it, for a reason
       beyond context cost: `claude_shared` is mounted by both agents and
       tracks no version, so a third-party skill installed there drifts
       inside running containers independently of the image — the same
       class of defect as the CLI auto-updater (see
       [`docs/ROADMAP.md`](docs/ROADMAP.md)). Unmeasured here: whether an
       always-on ladder also suppresses work the task did ask for, which
       would land on the revisor.
     - [ECC](https://github.com/affaan-m/ECC), unread as of 2026-09-25 and
       recorded here so the evaluation has a home rather than living in a
       conversation. The test to apply when it is read is the one the
       three above were judged against: a skill describes method, never a
       project's conventions, and only the first half belongs in this
       item. Anything that survives that filter has two possible homes,
       and they are not equivalent. One is `ROLE_SKILLS` in
       `dispatcher/role_skills.py`, where a skill is bound to a role and
       where `cartografo` currently has none at all. The other is the root
       thread, which runs no skills today and is the harder case, because
       a skill loaded there is paid on every turn of a long conversation
       instead of once per phase — the cost argument of item 2 applies to
       it with the multiplier reversed.
   - *Revive before respawn.* A phase that resumes a session is told this
     rule: to resume work delegated to a subagent, first revive that
     subagent by its ID (Claude Code's `SendMessage` to the agent ID
     continues it with its context intact), and only spawn a fresh one,
     briefed from the handoff, if the revive fails. A fresh spawn starts
     cold and re-derives context the original already had, which costs
     quota. Another multi-agent setup on the same CLI found that a
     subagent killed by a rate limit revives only by its raw agent ID
     (not by name, and `ListAgents` doesn't list it), and only while its
     parent session is alive. D1 (`docs/ROADMAP.md`) answered what that
     left open: a `--resume` of the parent does make its subagents
     addressable again, on the other account too, so the fallback of
     resuming the role's previous session is not needed. Shipped on the
     prompt of a resume rather than in every role skill, which is
     charged on every call — see "Picking a subagent back up" above.
   - *Per-project docs.* Replaces a hand-written per-project `CLAUDE.md`.
     Projects checked out under `.data/projects/<slug>` (see "Create
     volumes" above) are arbitrary target repos; their docs live in the
     project and are committed with its code, so they survive a re-clone.
     A possible layout under the project's `docs/`:
     - `decisions.md`: numbered ADRs, business and architecture alike
       (context, decision, consequences, and a status: closed, paused, or
       reopened). A superseded decision is struck through with a pointer
       to its replacement, never rewritten.
     - `learnings/`: typical failures and traps, one file per entry.
     - `debt/`: declared debt.
     - `architecture.md` and `business.md`: the map and the domain.
     - `implementations/<task-id>.md`: how each task was built and why.

     When a project has no docs index (e.g. no `docs/README.md`), a
     bounded mapping phase runs before the arquitecto, on a cheaper model
     with a turn budget: stack, modules, how to build and test, visible
     contracts, and an index of the docs that already exist, without
     rewriting them. After that the map grows with each task, which
     documents what it touched instead of re-exploring the repo. Make it
     opt-in, or at least announced, since it spends quota. Per role: the
     arquitecto reads the indexes and records an ADR when a task decides
     something; the implementador declares debt and proposes learnings;
     the revisor treats a contract change without a doc update as a
     finding; the auditor is the **single writer** of the indexes, taking
     the others' proposals from the handoff, so no two phases ever edit
     them. Business decisions an agent infers from code are marked
     unconfirmed and surfaced to a human: docs take precedence over skills
     and `CLAUDE.md`, so a hallucinated decision would become canon, and a
     contradiction between those layers is a doc bug to fix. Frictions
     with the skills themselves (a rule that misled, a missing step) are
     logged back to ia-harness and staged for human review, never
     installed automatically; traps in the code go to the project's
     `learnings/`.
   - *Documenting is a method, and this repo is now a target too.* The
     bullets above say what the docs are and who writes them; nothing
     says how to write one. `skills/` holds eight vendored skills and all
     eight are about code — plans, TDD, debugging, review, scope — so a
     role told to record an ADR, open a `learnings/` entry or keep an
     index current is improvising the form every time. That half is
     method and belongs in a ninth skill: what an entry contains, how to
     phrase a trigger column, when to supersede instead of rewrite, and
     when a change is too small to document. The other half is not a
     skill at all, by this item's own rule above — conventions belong in
     the project's docs — and ia-harness has never written its own,
     because until T-008 it was only ever the dispatcher, never a
     dispatch target. Its conventions live in this README and in the
     history of the conversations that set them: a fixed gap is deleted
     from *Known gaps* rather than annotated, its record goes to Stage 1
     item 2 of [`docs/ROADMAP.md`](docs/ROADMAP.md), operational prose
     goes to the relevant body section here, and the spec bullets under a
     *Prioritized* item are never trimmed when a piece of it lands.
     T-008's roles could read neither half, which is the first evidence
     that this piece is load-bearing and not just tidy.
   - *Limits go on what travels, not on what exists.* The question that
     produced this bullet was whether to cap documents and source files at
     some number of lines. The answer the harness's own machinery already
     gives is to cap what a phase is handed, in bytes, and to leave alone
     what merely sits on disk. Three caps exist and all three are of the
     first kind: `_BUDGET_BYTES` in `dispatcher/handoff.py` (2048–5120 per
     role), `MAX_ROWS = 40` with `_CELL_CHARS = 160` in
     `dispatcher/learnings.py`, and the `wc -c` threshold in *Indexes and
     pointers* below that decides inline against by-path. Every byte there
     is paid again by every phase of every task, which is what makes a
     number defensible; a file nobody opens costs nothing. The cautionary
     half is *The handoff budgets are sized on a toy repo* under *Known
     gaps* above: T-008 had three roles blow the byte budget in one run,
     so the mechanism is right and the constants are not, which is what
     happens to a limit chosen without data.
     A line cap on the docs a human reads is rejected. This README is past
     1900 lines and [`docs/ROADMAP.md`](docs/ROADMAP.md) past 1600, and
     that length is an index of decisions doing its job; a cap would not
     delete content, it would move it somewhere nobody reads. What bounds
     them is structural and already in force, and it is the convention the
     piece above names: a fixed gap is deleted rather than annotated, its
     record goes to Stage 1 item 2 of the roadmap, operational prose goes
     to the body section it belongs to. A cap on a `SKILL.md` is the
     opposite case and is accepted, because a skill is read by a phase —
     that is the short-`SKILL.md`-plus-references rule this item already
     states under *Role skills*, arrived at there for the same reason.
     A line cap on source files is rejected too, but a signal is not. A
     hard cap produces `utils2.py` and splits coherent modules to satisfy
     a number. The evidence for measuring anyway is good:
     `dispatcher/dispatcher.py` is 1420 lines against 786 for the next
     largest, and it is the module [`docs/plans/balancer.md`](docs/plans/balancer.md)
     independently decided to refactor in its Phase 1, because the
     per-phase bookkeeping is buried inside `run_task_cycle`. A
     measurement would have named the same file with no argument attached.
     So the form is a fifth gate in `dispatcher/gates.py`, `NOTE` level,
     looking only at files the diff touched and reporting that one crossed
     a threshold. 800 lines for Python: today that names `dispatcher.py`
     alone out of seventeen modules, and leaves `learnings.py` at 786 just
     under, a margin thin enough that the number is provisional by
     construction. It blocks nothing, which is that module's stated
     posture — *every gate errs toward saying nothing*.
   - *Indexes and pointers.* What keeps that memory cheap to read as it
     grows:
     - Every index has a trigger column ("when it applies" for learnings,
       "where" for debt), written as a condition an agent can check
       against its own task. Agents read the whole index and open an
       entry only when its trigger matches.
     - `CLAUDE.md` is an index into docs and skills, never a source:
       anything it says also lives somewhere else.
     - Cite by path plus a stable anchor (ADR or entry ID, heading, or
       symbol name), never by line number: a stale line reference that
       still resolves points confidently at the wrong thing.
     - Debt whose fix a later spec decides gets a pointer to that spec's
       section, not a copy of it.
     - Large artifacts (diffs, logs, plans) travel as paths. Content goes
       inline only below a byte threshold measured with `wc -c` (a few KB),
       since anything inline is reread on every later turn; above it, pass
       the path and the section to read.
     - A pointer is only worth its bytes if the reader may open it. V3
       measured the opposite: every `/data/.hive` path handed to a phase
       was refused by the file tools, so the pointers cost context and
       returned nothing. Whatever this scheme points at has to sit in the
       worktree or in a directory the phase command declares.
   - *Structured handoff.* The YAML frontmatter
     (`status`/`owner`/`depends_on`/`heartbeat`) is already structured, but
     the body is prose that `run_task_cycle` truncates and accumulates, so
     every later phase rereads all earlier ones. The truncation is blind
     too: `_truncate_for_handoff` keeps the first 500 and last 1,500
     characters, so the middle of a long return, often the findings, is
     lost. Instead, each role returns a short schema through
     `--json-schema` (status, what changed, what was verified, what's
     pending, known risks, subagent IDs and what each was doing, proposed
     learnings and debt, paths to the detail, and the revisor's verdict as
     a field instead of a regex) within a per-role byte budget. The
     dispatcher enforces the budget itself: a return over it gets one
     `--resume` asking for a shorter one, the retry is accepted as is, and
     the overage is logged so the budgets can be tuned from data. The
     detail goes to files, split by lifetime: durable docs (the ADRs,
     learnings, and `docs/implementations/<task-id>.md` above) on the task
     branch, and per-round scratch (review findings, test logs) under
     `.hive/tasks/<task-id>/`. The `.hive` task file keeps the summary plus
     the paths. Detail on disk only survives if it's committed or lives
     outside the worktree — the dispatcher's per-phase commit covers the
     first case for the roles that share the task branch's worktree, and
     the auditor is one of them for exactly this reason: a reviewing
     role's detached checkout is rebuilt every round and deleted at the
     end, so anything written there is gone, which is what the auditor's
     indexes hit until its commit was scoped to `docs/`.
     The recorded subagent IDs are what let a resumed or later phase
     attempt the revive above.
   - *Docs and tests kept current, enforced outside the model.* Today
     nothing requires either: `_role_prompt` sends only the role, the task
     ID, and the task file; the dispatcher never runs tests or looks at the
     diff; and the only hook (`hooks/emit_event.py`) is for observability.
     The duties above (TDD, the revisor's contract rule, the auditor's
     indexes) are instructions, which hold only while one model follows
     them and another notices when it doesn't. Three layers, strictest
     first:
     - Dispatcher gates between the implementador and the revisor. They
       are deterministic, spend no quota, and are out of the agent's reach.
       - Tests in the diff: if `git diff --name-only` shows code changed
         and no test, the task doesn't reach the revisor. The implementador
         gets a `--resume` asking for tests or an explicit justification,
         which the revisor then judges.
       - Tests run: the project's test command, recorded by the mapping
         phase, runs through `docker exec` without `claude`. A failure
         starts another round with the log's path, without spending a
         revisor call. This also checks the implementador's claim that
         tests pass instead of trusting it. Open: a fresh worktree has no
         `node_modules`, so dependencies need installing or a cache.
       - Contracts without docs: a changed OpenAPI spec, `.env.example`,
         migration, public export, or CLI flag with no doc change becomes
         a finding the revisor must answer.
       - Broken pointers: docs cite by path plus a stable anchor (see
         indexes and pointers above), so a grep tells whether the target
         still exists. A missing one becomes a task for the auditor.
     - Claude Code hooks in the agent container, for feedback within the
       session only. A `PostToolUse` hook on Edit/Write that runs the
       formatter or typecheck on the touched file is cheap and catches
       errors early. A `Stop` hook that refuses to finish until tests ran
       is not recommended: under `-p` every refusal is another paid turn,
       it needs the `stop_hook_active` guard to avoid looping, and it sees
       only its own session, while the dispatcher gate sees the whole diff
       for free.
     - Role instructions: the arquitecto's acceptance criteria name the
       tests that prove the task and the docs it changes, so the revisor
       has something concrete to check against.

     Two risks. A "no code without tests" rule is easy to meet with
     trivial tests, hence "tests or a justification", tests that must pass,
     and a revisor that still judges their quality. And the docs gate stays
     limited to contracts: requiring docs on every change piles up docs
     nobody reads.
   - *Declared debt, mirrored to Vibe Kanban.* The `debt/` index above
     stays the source of truth. Agents read it filtered by its "where"
     column, it's versioned with the code, and it doesn't depend on Vibe
     Kanban, which in this design is a visibility aid and is optional in
     `config.yaml` besides. The flow:
     1. The implementador declares debt in its structured return: whether
        it was introduced or found, what it is, why it stays, the cost of
        leaving it, and what would fix it. Found debt counts only in files
        the task touched and not already in the index. Declaring debt
        never replaces blocking: whatever would block the task (e.g. a
        decision the task doesn't specify, or a schema change) still
        blocks it.
     2. The revisor rules on each declaration. Accepted debt moves on.
        Rejected debt becomes a finding to fix in the next round, and like
        any finding it counts toward `max_revision_rounds`, after which
        the task ends blocked. A disguised block marks the task blocked.
     3. The auditor, as single writer, adds accepted entries to `debt/`.
        When a later spec decides the fix, the entry points to that
        section.
     4. The dispatcher, not an agent, creates one card per accepted entry
        with `VibeKanbanClient.create_issue`, which the dispatcher
        already calls once per task cycle. The card is labeled `debt`, sits in the backlog, and is
        never dispatched on its own. The entry and the card each record
        the other's ID. Cards created by agents would be duplicated every
        round, and an agent that can create tasks can assign itself work.
     5. A human moving the card out of the backlog approves the work. The
        task that resolves the debt says so in its return, and on merge
        the dispatcher marks the entry resolved and closes the card.

     To keep the board from flooding, only accepted debt gets a card, and
     only after checking the index for a duplicate.
   - *Shared learnings that survive a dead phase.* The `learnings/` index
     above already gives a two-level read: agents read the whole index and
     open an entry only when its "when it applies" matches their task.
     What's missing is how a learning reaches other agents. Every phase is
     an isolated `claude -p` and the entry travels on the task branch, so
     a concurrent task doesn't see it until merge, and a rejected task
     loses it. A phase killed by a rate limit or timeout, or a task that
     ends blocked, never reaches the auditor at all, and those walls are
     the ones most worth recording. So:
     - Inbox: any phase can add an entry at any time, including right
       before it dies, under `.hive/learnings/inbox/`. That directory is
       outside the worktree and already mounted in both agent containers.
       Each entry is its own file, so concurrent phases never edit the
       same one. Agents grep the inbox along with the index, so an entry
       reaches other tasks right away instead of waiting for a merge.
     - Single writer: the auditor promotes its task's inbox entries into
       the index on the task branch; they leave the inbox when that branch
       merges. When a task fails, ends blocked, or its branch is discarded,
       the dispatcher, without an LLM, marks its entries unconfirmed and
       leaves them in the inbox, and the next task in that project to reach
       the auditor carries them into its branch.
     - Two scopes. Traps in the project go to its `learnings/` and are
       committed on merge. Traps in the environment, the harness, or the
       CLI (e.g. "a fresh worktree has no `node_modules`") go to a
       cross-project store in ia-harness. As with the skill frictions
       above, a human reviews those before agents in other projects see
       them.
     - Format: the index has `# | Learning | When it applies | Status`
       columns. An entry has when it applies, where it was discovered
       (task and phase), the symptom with the exact error, why, and the
       rule. Since the error is verbatim, role skills can say "before
       debugging, grep the learnings and the inbox for the exact message",
       so an agent that hits a wall finds the entry even when its task
       didn't look related. As the index grows, the dispatcher passes each phase only
       the rows matching the task's profile, labels, or the paths its plan
       names.
     - Poisoning guard: an agent that misunderstood something would turn
       that misunderstanding into a rule for everyone. An entry stays
       unconfirmed until another task hits the same wall or a human
       confirms it; agents still read unconfirmed entries, marked as such.
       Every entry needs evidence (the error and the command), and one
       whose pointers no longer resolve is flagged (see the broken-pointer
       gate above).

     A trap is "don't step on this"; half-finished code is debt, not a
     learning.

   Its two prerequisites are now in place: agents are given the task
   description, and the dispatcher commits each writer phase, so docs a
   role writes survive it. The debt cards still need an issue created on
   a real board, which no run has yet confirmed (V2.6). Not designed:
   the handoff schema, where skill files live (baked into `docker/agent/`
   at build time vs. mounted alongside `.hive`), how a role is told which
   skill applies (for skills that depend on the kind of task rather than
   the role, see item 5), the inbox entry format, and where the
   cross-project learnings store lives.
2. **Token economy.** With two Claude Pro accounts quota is the bottleneck,
   so measure where it goes before optimizing. Another multi-agent setup
   on the same CLI measured its own transcripts and found that rereading
   context, not writing output, dominated (cache reads and writes were 74%
   of subagent cost; all prose they wrote was 1.3%), that an agent carries
   23–35K tokens of fixed context before doing anything, that cost tracked
   verification and iteration rounds rather than task size, and that
   intuition about where tokens went was wrong all three times it was
   checked against the data. What carries over:
   - *Record usage per phase.* The `claude -p` JSON already carries
     `usage` (input, cache creation, cache read, output), `total_cost_usd`,
     `num_turns`, and `duration_ms`, which `exec_claude` keeps in
     `ClaudeResult.raw` and then discards. Send them to the collector
     tagged with role, model, effort, round, and a fingerprint of the
     agent config (CLI version, skills, MCP servers, compaction window).
     Compare rates across config versions (cache reads per turn, turns per
     phase), not totals, and count a task that spans a config change as
     mixed. Item 4's model split depends on this.
   - *Stop probing quota.* `check_quota_ok` runs a whole `claude -p
     "/usage"` before every dispatch, `_recheck_cooling_accounts` runs one
     per cooling account, and both parse free text. It costs less than it
     looks: V1.3 measured `/usage` under `-p` as a *local* command — no
     assistant turn, no result record, no cost — so the case for
     replacing it is the free-text parsing, not the spend, plus the
     blindness that local counters cannot cure: a recorded
     `rate_limited_at` and its fixed cool-down work around that, they do
     not remove it. The CLI
     (checked in 2.1.273) defines a `rate_limit_event` stream message whose
     `rate_limit_info` carries `status`, `utilization`, `resetsAt`,
     `rateLimitType`, and `surpassedThreshold`. If
     `--output-format stream-json --verbose` emits it for a Pro account
     (unverified), every phase reports quota as a by-product: no extra CLI
     run, no text parsing, and a machine-readable reset time for item 3's
     retries — and for the cool-down, which would then wait out the real
     reset instead of `quota_cooldown_seconds`' fixed guess. `exec_claude` would then read the final `result` message
     from the stream instead of a single JSON object.
   - *Trim the fixed startup context.* Every phase pays its startup
     context (system prompt, tool and MCP schemas, `CLAUDE.md`, skill
     listings) and rereads it on every turn. Ship only the plugins and MCP
     servers a role uses, keep `CLAUDE.md` an index (item 1), and load
     skill bodies on demand. The first turn's recorded `usage` measures
     the result.
   - *Fewer turns.* Put this in the role skills: batch independent tool
     calls into one response, read files by range, pipe long command
     output through `tail`, and pass artifacts as paths. The arquitecto
     also picks the cheapest verification that proves each step: in that
     setup, three tasks touching two files each took 42, 59, and 69
     responses, and the spread came from verification and iteration
     rounds, not from files or steps.
   - *Cap what a role returns.* Every later phase rereads the handoff, so
     item 1's byte budget is a cost control, not tidiness. It applies at
     two levels. A role's return to the dispatcher is read by Python,
     which costs nothing, but it lands in the handoff; item 1's schema and
     dispatcher-side cap handle that. A role's own subagents return to the
     role's session, an LLM that rereads every byte on every later turn.
     For those, the agent image's settings install a `SubagentStop` hook
     that blocks an overlong return once, asking for the detail on disk and
     a short summary, and lets the retry through. That setup holds its
     subagents to 3–4 KB this way. Its hook identifies the role from a
     sentinel comment in the subagent's prompt rather than `agent_type`,
     which it found populated in only ~7% of closes, and logs every
     overage to tune the caps. Before the hook, it measured a 14,030-character
     return where the contract asked for seven lines, so a prompt rule
     alone doesn't hold. Compact line formats for those returns are borrowed
     from caveman's `cavecrew` (see the caveman bullet below).
   - *Resume or restart.* `--resume` rereads the whole previous transcript,
     and after a cooldown, or on another account, its prompt cache is
     almost certainly cold, so the transcript is paid again. Resuming still
     wins when the phase had done real work (that setup revived a reviewer
     killed by a rate limit, and it finished in 2 tool calls and ~52K
     tokens instead of redoing the review); for a phase that barely
     started, a fresh run from the handoff is cheaper. The recorded usage
     sets the threshold.
   - *Compaction window.* A lower `autoCompactWindow` in the agent image's
     settings bounds how much context a long phase rereads per turn (that
     setup simulated 120K as ~12% cheaper than 150K). It only matters for
     long single phases, since each role already starts fresh; this is the
     cheap version of item 8. Not set today, and the image is not where it
     would go: the agent image ships no `settings.json` of its own, because
     the `claude_shared` volume mounts over `/root/.claude` and shadows
     anything baked in. `hooks/install_settings.py` is the only writer —
     it runs from `docker/agent/entrypoint.sh` at container start and
     merges in the hook registration plus `syncClaudeAiSkills` and
     `syncClaudeAiPlugins`, so a window belongs in that same merge. The
     merge is additive and never rewrites a key the operator already set,
     which means a default there stays overridable by hand in the volume.
     The window is per-account only in appearance: every account symlinks
     the one shared `settings.json`, so setting it sets it for all of them.
   - *caveman, piece by piece.*
     [caveman](https://github.com/JuliusBrussee/caveman) bundles several
     token savers with very different evidence behind them; judged for
     Claude inside the agent containers:
     - Output-style skill (MIT): not by default. It makes the agent write
       terse prose, but in an agentic phase most tokens are rereading
       context, and code and tool calls it never touches. The one
       third-party A/B on real Claude Code tasks (JetBrains, 86 tasks)
       measured 8.5% fewer output tokens, about 10% of cost, with no
       quality change, while its ~1K-token ruleset is reread on every
       turn. Its own `docs/HONEST-NUMBERS.md` lists net-negative cases
       (terse Q&A, re-injection and retries outweighing the savings) and
       says to A/B it on provider-reported totals. Its boundaries (code,
       commits, docs, and PRs stay in normal prose; security warnings and
       irreversible actions get full clarity) don't conflict with item 1.
       Worth an A/B behind a flag once per-phase usage is recorded, scoped
       to what later phases reread (returns and handoff fields), which the
       schema and byte caps already bound deterministically.
     - `cavecrew` (MIT): borrow its return contracts, not its agents. The
       investigator returns `path:line — symbol — note` lines, the builder
       `path:line-range — change` plus `verified:` or a one-word refusal
       (`too-big.`, `needs-confirm.`, `ambiguous.`, `regressed.`), and the
       reviewer `path:line: severity: problem. fix.` plus totals, or
       `No issues.` They fit the subagent returns above and the revisor's
       findings file. The agents themselves carry their own policy (the
       reviewer is pinned to haiku, the builder refuses edits over two
       files), which the role skills and item 4 should decide instead.
     - `caveman-compress` (MIT): no. It spends Claude calls to rewrite
       `CLAUDE.md` and memory files in place, keeps the backup outside the
       repo (lost with the container), and its ~46% input cut on five
       fixtures comes with no quality-equivalence claim. The per-project
       docs in item 1 are committed in the target repo and read by humans
       too; keeping `CLAUDE.md` an index and opening entries by trigger
       goes after the same cost without a lossy rewrite.
     - Proxy and compression engine (`caveman wrap claude`; MIT CLI,
       BSL-1.1 runtime): the only piece that attacks rereading, and the
       one with the strongest numbers. A pinned benchmark on Claude Code
       with 60–95 KB tool outputs (logs, test output, JSON, CSV, YAML)
       measured 33.2% fewer provider-reported input tokens with 18 of 18
       answers correct (95% CI 14.6–48.5%), which fits implementador and
       auditor phases that run test suites and read logs. The fine print:
       controlled fixtures, not production, and HTML regressed 9.9%; the
       compression is lossy (originals stay in a local store with a
       recovery handle, so an agent can still act on an elided log);
       their deploy docs say a shared, token-authenticated proxy doesn't
       work with Claude Pro/Max logins, so it would run as a local wrap
       inside each agent container (a Node.js 22 CLI plus a Go binary in
       the image, and one more hop between the account and Anthropic);
       and BSL-1.1 allows first-party self-hosted use, production
       included, but offering ia-harness to third parties as a hosted
       service would need a commercial license. The best candidate of the
       set, as an experiment on one account's image, A/B'd on per-phase
       usage before adopting. Unverified: that `-p --output-format json`,
       its `usage` figures, and `--resume` behave the same through the
       wrap.

   What doesn't carry over: compaction discipline for a long-lived main
   thread (compact between tasks, never with a subagent running), since
   here no session outlives its phase; and the finding that the
   Opus/Sonnet split barely moved cost, which came from per-token pricing
   and doesn't map directly onto Pro plan limits.
3. **Unattended 24/7 operation.** The dispatcher is a one-shot CLI (see
   "Run a task" above), so running around the clock still depends on
   external automation. Pieces that would make it hold up unattended:
   - A long-running loop that picks up ready tasks, honoring `depends_on`
     (stored in the task file today but never checked).
   - Telling quota-blocked apart from failed: a task that finds no
     account available goes `blocked` and stays there. Quota-blocked
     tasks could be retried on a schedule instead of waiting for a human,
     at the `resetsAt` time from item 2's `rate_limit_event` if the CLI
     emits it, else at `/usage`'s free-text reset times where they parse,
     else on a periodic re-check.
   - A single-instance guard (e.g. `flock` on `state_dir`) so overlapping
     runs can't claim the same account. `acquire_lock` already refuses a
     live lock on the task, but two runs of different tasks still race on
     account state files.
   - A reaper for accounts left `BUSY` by a SIGKILL, OOM, or host reboot,
     which the `except` in `dispatch_phase` can't catch. Ctrl+C also leaves
     the account `BUSY`, on purpose: killing the host `docker exec` doesn't
     stop the `claude` inside the container, which keeps running until
     `phase_timeout_seconds`. A candidate rule: reap a `BUSY` account
     whose state file is older than `phase_timeout_seconds` plus the
     30-second kill grace.
   - An orphan phase after Ctrl+C: the heartbeat stops with the host
     process, so the task lock expires after `heartbeat_ttl_seconds`
     (120 by default) while that orphan `claude` may run for up to
     `phase_timeout_seconds` (7200). A re-run in that window takes the
     task on another account and can work the same worktree
     concurrently, since checkouts under `projects_root` are shared
     across agent containers. The reaper above could hold the lock (or
     skip the task) while an account is still `BUSY` with that task ID.
   - A second run that hits a live lock marks the task `blocked` in Vibe
     Kanban, overwriting the `in_progress:<role>` the first run is still
     working under. Check the lock before writing `in_progress`, or report
     a distinct status.
   - A per-call timeout for Vibe Kanban: `VibeKanbanClient._call_async`
     passes no `read_timeout_seconds` to `ClientSession`/`call_tool`, so a
     server that accepts the connection and never answers stalls a
     best-effort status update indefinitely.
   - Logging setup: nothing under `dispatcher/` or `docker/` configures
     logging at all, so the root logger sits at WARNING and all 12
     `logger.info` calls are dropped — the gates' account of what they
     skipped and why among them. V3's entire run log was five lines, and
     a task that ends blocked says so nowhere: `_update_task_status` only
     writes to the Kanban card, which with no board returns silently,
     while the frontmatter still reads `status: pending` and the process
     exits 0. What does reach stderr (a failed Kanban update, a lock held
     by another owner, an unparseable `/usage`) arrives through logging's
     last-resort handler, without timestamps or context.
   - A short "continue where you left off" prompt when resuming after a
     rate limit, instead of re-sending the full role prompt.
4. **Per-role model selection and richer effort escalation.** Running
   opus for all four roles spends the quota fastest. Today `default_model`
   (`config.example.yaml`) applies the same model to every role, and
   effort only escalates once the implementador/revisor loop crosses
   `escalate_effort_after_round`. V3 measured that escalation as buying
   nothing today: the arquitecto runs with `round_num=None` and so gets
   no `--effort` flag at all, while `high` — the escalated value — is
   already the CLI's own default for opus, so all three phases in that
   run reported the same effort whether or not the flag was passed. Two
   refinements were discussed but not implemented: (1) a per-role model
   override, e.g. opus for arquitecto and auditor (planning/judgment
   roles) and sonnet for implementador (execution), possibly also for
   early revisor rounds, instead of one `default_model` for all four;
   (2) escalating effort (or switching model) on signals other than round
   count, e.g. the revisor repeating the same `CHANGES_REQUESTED`
   complaint, or a role's result text coming back suspiciously short.
   Make the split data-driven first, from item 2's per-phase usage
   records, which show which roles actually consume the quota. Not
   designed: a config schema for per-role model overrides
   (`default_model` becoming a fallback vs. a
   `models: {arquitecto: opus, ...}` map), and how "the same complaint"
   or "suspiciously short" would be detected from freeform `result_text`
   without over-engineering a heuristic that never fires as intended.
5. **Task profiles: skill packs per kind of task, not new roles.** Role
   skills (item 1) say how a role works, not what the task is about. A
   frontend task gains from design and browser-verification skills that a
   backend task would pay for and never use: every installed skill's
   description is in context on every turn, and a subagent's `skills:`
   preload draws from the same installed skills, so hiding a pack behind
   a subagent doesn't keep it out of the parent's context. So a pack isn't
   installed by default; each phase gets only the one its task needs:
   - *Mechanism.* A task carries a profile (`web-frontend`, `e2e`, `3d`,
     …): from a label a human puts on the Vibe Kanban card or, failing
     that, from the arquitecto, which picks from a short catalog of
     profile names and one-line descriptions, never the skills
     themselves. The dispatcher records the profile in the `.hive` task
     file and adds `--plugin-dir /opt/packs/<profile>/<role>` to that
     phase's `claude -p` in the same account container (the flag is
     repeatable, so packs stack). No orchestrating session needs to know
     the packs exist. Pass the flag again on every `--resume`, a quota
     handoff included: whether a resumed session keeps the original
     call's plugins is unverified. A second image (`agent-web`) is only
     worth it for heavy dependencies; the agent image (`node:20-slim`)
     has no Chromium today.
   - *Project default profile.* Most tasks in a project want the same
     packs, so the project carries default profiles and a task label adds
     to them: a phase gets the project's profiles plus the task's.
     - Enable, never disable. `/root/.claude` is the `claude_shared`
       volume, shared by both accounts and every project — it's the part
       of each account's config home (`skills/`, `agents/`, `commands/`,
       `plugins/`, `settings.json`) the entrypoint symlinks in from that
       one volume, while logins and everything else per-account stay in
       `claude_creds_<account>` — so disabling a skill there for one
       project disables it for all of them. CLI
       2.1.273 also has no per-call switch for a single skill
       (`--disable-slash-commands` turns them all off). Hence nothing
       project-specific in the shared volume: a minimal base of role skills,
       always on for their role and delivered per call like the packs
       (item 1), plus packs added per call with `--plugin-dir`.
     - The signal is a file ia-harness owns, committed in the project
       (e.g. `.ia-harness.yaml` with `profiles: [web-frontend, e2e]` and
       the evidence for each), not `CLAUDE.md` or `.claude/`. Many projects
       already have those, written for humans, and they say nothing about
       packs.
     - When the file is missing, detection reads manifests and spends no
       quota:

       | If the project has… | Profile |
       |---|---|
       | react, vue, svelte, or next in `package.json` | `web-frontend` |
       | `playwright.config.*` or cypress | `e2e` |
       | `three` | `3d` |
       | a Dockerfile or terraform | `infra` |
       | prisma or `migrations/` | `migrations` |

       An LLM decides only the ambiguous cases, such as a monorepo, inside
       item 1's mapping phase, and a human approves the result in Vibe
       Kanban.
     - The file stores a hash of the manifests it read. When dependencies
       change, detection reruns and the change is proposed to a human,
       never applied on its own.
     - When in doubt, a pack stays off. A missing pack shows up in that
       task's review; an extra one spends tokens on every turn without
       anyone noticing.
   - *`web-frontend` candidates, evaluated.*
     - [playwright-skill](https://github.com/willmarple/playwright-skill):
       yes, and the most valuable of the set, because it closes the loop
       the others leave open: the agent renders the page, takes a
       screenshot (`playwright-cli screenshot`), and reads the PNG,
       instead of judging a UI from its code. Needs Chromium and
       `@playwright/cli`.
     - [impeccable](https://github.com/pbakaus/impeccable): yes, adapted.
       Its commands map onto the roles: `shape` for the arquitecto, its
       default build flow for the implementador, `audit` (a11y,
       performance, responsive) for the revisor, `critique` and `polish`
       for the auditor. For headless use, strip the stops that wait on
       AskUserQuestion (`init`, `document`, `extract`, `quieter`,
       `overdrive`, and `critique`'s closing question) and the plugin's
       `PostToolUse` (Edit|Write) and `Stop` hooks.
     - [ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill):
       only the `ui-ux-pro-max` skill, whose `search.py` queries a local
       database of styles, palettes, and font pairings on demand (for
       the arquitecto). Drop the rest: `design`, `banner-design`,
       `brand`, and `slides` generate images through external APIs that
       need their own keys, and `ui-styling` overlaps impeccable.
     - [taste-skill](https://github.com/leonxlnx/taste-skill): mostly no.
       Its main `SKILL.md` is about 87 KB (~22K tokens) every time it
       triggers, and its full-output enforcement contradicts the capped
       role returns of item 2. At most, borrow a few of its anti-patterns
       into the impeccable pack.
     - [awesome-design-skills](https://github.com/bergside/awesome-design-skills):
       not in the image. Each entry is one visual style; the project
       pulls the one it chose into its own repo (`npx typeui.sh pull
       <style>`) as part of its design docs.
     - [img2threejs](https://github.com/img2threejs/img2threejs) (rebuilds
       an object from a reference image as a procedural Three.js model):
       only in an opt-in `3d` profile, for tasks that build such scenes.
   - *Pack rules.* One design direction per pack: two style skills pull
     different ways, and the revisor can't tell which one the diff should
     follow. The style itself (tokens, components, tone) lives in the
     target project's `DESIGN.md` and `PRODUCT.md`, which win over any
     pack, as project docs win over role skills. Vendor, pin, and trim
     every pack for headless use (no questions to a human, no hooks it
     doesn't need), and keep a profile only after comparing a few real
     tasks with and without it on item 2's per-phase usage records.
   - *A designer role? Not as a phase.* A fixed designer phase adds a
     cold start and a handoff to every task on scarce quota, and it
     designs before anything renders. The design decision that most
     needs judgment, the visual direction, belongs to a human; impeccable's
     own stops for a human sit exactly on the commands that set it.
     Instead:
     - A one-off, opt-in design-system bootstrap task per project, like
       item 1's mapping phase. It writes `PRODUCT.md` and `DESIGN.md`
       (from the existing UI on an old project, from the brief on a new
       one), marks inferred choices unconfirmed, and a human approves it
       in Vibe Kanban. UI tasks `depends_on` that approval, not just on
       the pipeline finishing.
     - The `web-frontend` profile on the existing roles. The arquitecto
       specifies the UI (empty, loading, and error states; breakpoints;
       which `DESIGN.md` tokens and components). The implementador builds
       it and checks its own screenshots. The revisor reviews screenshots
       against `DESIGN.md` and blocks only on accessibility (WCAG)
       failures, broken layout, or regressions. The auditor leaves design
       polish as non-blocking notes to a human.

     Revisit a designer phase only if item 2's per-phase usage records
     show UI tasks bouncing between implementador and revisor on design
     findings.
   - *Other roles.* A new role has to bring something a profile can't: an
     independent agent checking the work, an artifact with its own
     lifetime, or a human gate. By that bar:
     - Mapper: already item 1's mapping phase. Formalize it with its own
       skill and a cheaper model (item 4).
     - Tester/QA: no separate phase. The arquitecto writes the acceptance
       criteria and the auditor runs e2e checks under an `e2e` profile.
       Tests written before the code by a different agent (adversarial
       tests) are worth an experiment, not a default.
     - Security reviewer: a revisor pack (thermos's correctness/security
       review plus Semgrep, item 7), turned on for tasks that touch auth,
       payments, or secrets.
     - Documenter: no. It would break the auditor's single-writer rule
       for the doc indexes (item 1).
     - Integrator: committing, rebasing, and opening the PR is
       deterministic dispatcher code (see the known gaps). An LLM phase
       only earns its quota resolving rebase conflicts, which grow with
       parallel dispatch (item 9).
     - Epic decomposition: an arquitecto mode that proposes child tasks,
       each with `depends_on` and a profile, for a human to approve.
       Whether a board accepts an issue created this way is unverified
       (V2.6).
     - Bug fixing (reproduce before fixing), infra/DevOps, performance,
       and data migrations (a human gate before anything irreversible):
       profiles or task types, not roles.

   Depends on item 1, whose role skills the packs extend, and on the
   known gaps. Unverified: whether a dev server plus headless Chromium fit
   in the agent container's 4 GB `mem_limit`
   (`docker/compose/docker-compose.agents.yml`), or whether the browser
   can run in the account's dind sidecar instead (e.g. the Playwright
   image) and still reach the dev server. Not designed: the
   profile catalog, the `.ia-harness.yaml` schema, the Vibe Kanban label
   convention, and where packs live (baked under `/opt/packs` vs.
   mounted).
6. **Observability and hardening.** Acceptable for a single operator on
   loopback plus Tailscale, but worth tightening, since agent containers
   run arbitrary code from cloned repos on the same network:
   - The collector's `POST`/`GET /events` have no auth, so anything on
     `ia_harness_net`, agent containers included, can read or forge
     events. A shared token would close that.
   - Hook payloads include tool inputs and outputs (file contents, env
     files, tokens) and land in SQLite as-is. Redact them in
     `hooks/emit_event.py`.
   - The dashboard compares in constant time (`hmac.compare_digest`), but
     the stored digest is still an unsalted SHA-256 of the password. A
     salted digest (e.g. `salt$sha256(salt + password)`) keeps
     `scripts/configure.sh` Python-free; mind that compose interpolates
     `$` in `.env` values. The collector and dashboard also run Flask's
     development server rather than a production WSGI server.
   - The dashboard shows only the last 200 raw events. A per-task view
     (phase, account, duration, rounds, verdict, cost) would answer "what
     happened to this task" directly.
   - The dispatcher mounts the host's `/var/run/docker.sock`, which is root
     on the host. A socket proxy limited to `exec` would narrow that.
7. **Code-inspection/code-intelligence tooling in agent containers.**
   Personal setups (e.g. a tree-sitter-parsed knowledge-graph MCP server
   queried for callers/callees/impact) give an agent structural answers
   ("what calls this," "what would break") far cheaper than grep. Nothing
   like this ships in `docker/agent/` today — each role works from plain
   file reads and shell commands. Start with static analysis for
   revisor/auditor, which is deterministic and cheap; navigation servers
   pay off mainly on large repos, and every MCP server's tool definitions
   take context on every turn, which on Claude Pro is quota (see item 2).
   A few real options exist,
   none evaluated in this repo yet:
   - Knowledge-graph/semantic-navigation MCP servers — the same category
     as a personal CodeGraph setup — e.g.
     [CodeGraphContext](https://github.com/CodeGraphContext/CodeGraphContext)
     (tree-sitter, CLI + MCP, graph database) or
     [Serena](https://github.com/oraios/serena) (LSP-backed symbol-level
     retrieval/editing across 20+ languages via MCP). Best fit for
     arquitecto/implementador: cheaper "where is X" / "what calls Y"
     answers when onboarding onto a project or planning a change.
     Whichever is picked, the index has to be *created and kept fresh by
     the dispatcher*, not by asking roles to do it in their skills: a role
     that forgets, or that is killed mid-phase, leaves the next one
     querying a stale graph and trusting it, which is worse than having no
     index at all. The shape that fits this repo's worktree layout is one
     index per worktree, initialized in `create_worktree` and re-synced
     around each phase, because the alternative — a single index at the
     project root — would walk `worktrees/` and index every role's copy of
     every file (CodeGraph's default `exclude` list has no `worktrees/`
     entry). Cost, not yet measured: the indexer in the agent image, index
     build time on a real target repo, and disk under `.data/projects`
     multiplied by the number of live worktrees.
   - Pattern-based static/security analysis with MCP support, e.g.
     [Semgrep MCP](https://mcp.directory/blog/semgrep-mcp-complete-guide-2026)
     (Semgrep's Guardian product scans agent-written code for
     vulnerabilities/bug patterns before commit, also usable ad hoc via
     its MCP server). Best fit for revisor/auditor: a systematic
     security/quality pass instead of relying on the LLM's own read of
     the diff.
   - The target project's own linters, which the role skills tell every
     role to run. For TS/JS targets,
     [anti-slop](https://github.com/dmmulroy/anti-slop) is a ready-made
     set: Oxlint rules against low-evidence patterns agents tend to write
     (unchecked `unknown`, chained type assertions, module mocking). It's
     vendored into the target repo once, by its `install-anti-slop` skill:
     that copies the rules to `tools/oxlint/anti-slop/`, pins `oxlint` and
     `@oxlint/plugins`, merges `oxlint.config.ts`, and turns every generic
     rule on as `error` (its Effect rules only if the repo uses Effect).
     After that it's deterministic and costs no tokens per run, so the
     revisor stops spending turns on those patterns. Not automatic, though:
     it changes the project's dependencies and lint policy, it encodes one
     author's taste (`no-module-mocking` bans `vi.mock`/`jest.mock`), and
     on an existing repo all-`error` floods the lint run with violations
     unrelated to the task, so the implementador burns quota fixing old
     code or the revisor blocks on it. The skill can ship in the agent
     image (only its description loads) but runs only when a human task
     asks for it; greenfield TS projects fit best, with the arquitecto
     proposing it as an ADR. Once installed it's just the project's
     linter: block only on violations in lines the diff touches.
   - These are not mutually exclusive — since each role already runs as
     a separate `claude -p` invocation, different roles could get
     different MCP servers configured (navigation-oriented for
     arquitecto/implementador, analysis-oriented for revisor/auditor)
     rather than every role carrying every tool. Not designed: whether
     tooling is baked into `docker/agent/Dockerfile` (one image, all MCP
     servers available) or made role-conditional at container-start time,
     and — for anything indexing the whole checkout — added container
     build time/size cost per project. Item 5's per-phase flags are a
     third option: tools stay in the image, and each `claude -p` call
     loads only its role's and task profile's servers (`--mcp-config`,
     like `--plugin-dir`), with no container restart.
8. **Mid-phase context-window compaction.** For a single role's run that
   fills its context window before finishing (long implementation with
   many tool calls), watch context usage and, past a threshold, dump a
   role-specific summary to disk (for implementador: what's implemented,
   current state, what's left, considerations) then `/clear` and reinject
   that summary so the role continues with a compacted context instead of
   running out mid-task. Low priority: Claude Code already auto-compacts a
   session that nears its context limit, and item 2's `autoCompactWindow`
   moves that threshold without code, so this only matters if long runs
   are observed failing or degrading anyway. Not implemented: today
   `dispatch_phase` → `exec_claude` runs each phase as a single one-shot,
   non-interactive `claude -p ... --output-format json` call that returns
   one JSON blob after exit — the dispatcher never observes context usage
   or intervenes mid-call, so there's no live session to inject a `/clear`
   into. Doing this for real needs a driven/streaming session (or
   SDK-style loop) the dispatcher can watch turn-by-turn, which is a
   bigger change than a threshold check. Also note this only helps
   *within* one role's run — the *between*-phase case is already handled
   by `context_transfer`'s handoff file (role-specific dump → next phase
   reads it fresh; see item 1).
9. **Parallel/load-balanced dispatch across accounts.** The dispatcher
   currently serializes on a single active account (see "Architecture"
   above). A configurable parallel mode is noted as future work in the
   design spec (section 8), with two candidate variants: per-account
   subagents scheduled by remaining quota, or independent task-claiming
   per account with quota-triggered handoff instead of end-of-phase
   handoff. Low priority with two Pro accounts: the limit is quota, not
   throughput, so running both at once mostly spends it faster and adds
   merge conflicts between concurrent branches. Revisit with more
   accounts, and prefer parallelism across independent tasks (via
   `depends_on`) over splitting one task.

   Not to be confused with the steppable cycle in
   [`docs/plans/balancer.md`](docs/plans/balancer.md), which shares the
   word "balancer" and none of the mechanism: that plan is about
   *sequencing one task under a human* — a conversational account that
   dispatches one phase at a time and ranks the pool behind itself — and
   it stays single-active-account throughout. This item is concurrency,
   and is still low priority for the reason above.
10. **Multi-provider agent containers.** Lowest priority: the most work,
    and if the goal is more capacity, adding another Claude account is
    config-only (see "Run a task" above). Everything under
    `docker/agent/`, `dispatcher/docker_exec.py`, and `dispatcher/quota.py`
    is Claude-specific today: the agent image installs only
    `@anthropic-ai/claude-code` (`docker/agent/Dockerfile`), credentials
    are isolated per Claude Pro account via that account's own
    `claude_creds_<account>` volume, mounted at the image's
    `CLAUDE_CONFIG_DIR` (`/root/.claude-account`, see
    `scripts/setup_volumes.sh`), `exec_claude` shells out to the `claude`
    binary with `--output-format json`, and `quota.parse_usage_output`
    parses Claude Code's `/usage` text verbatim. Extending this to other AI
    coding CLIs/accounts (e.g. ChatGPT/Codex CLI, Gemini CLI) would need,
    per provider:
    - A dedicated agent image (or a `provider` build arg) installing that
      CLI instead of/alongside Claude Code.
    - Its own account-scoped credential volume and config-dir mount
      point, mirroring the `claude_creds_<account>` pattern but at that
      CLI's config location (e.g. `~/.codex`, `~/.gemini`) rather than
      `CLAUDE_CONFIG_DIR`.
    - A `provider` field on `AccountConfig` (`dispatcher/config.py`), and a
      small provider abstraction behind `docker_exec.exec_claude` so the
      dispatcher can invoke the right binary/flags and parse that CLI's
      session-id/result/usage output instead of assuming Claude Code's JSON
      shape.
    - Confirmation that the target CLI supports a session-resume
      equivalent to `--resume <session_id>` — the context-transfer design
      (section 4a of the spec, mechanism 2) leans on that for mid-role
      quota-exhaustion handoff.

    This is not designed in detail; the bullets above are the seams the
    current Claude-only implementation already has, not a spec.
