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
        ▼ docker exec (claude -p ... --output-format json)
Agent container (per Claude Pro account)
   └── docker:dind sidecar (sysbox-runc, DOCKER_HOST → sidecar)
        └── registry mirror + BuildKit cache (shared across sidecars)
        │
        ▼ hooks → HTTP
Observability collector (SQLite/WAL) → read API → authenticated console (Tailscale)
```

- **Smart Dispatcher** (`dispatcher/`) — for one task, runs the role sequence
  `arquitecto → implementador → revisor → auditor`. Before each phase it
  picks an IDLE account, probes `/usage` to keep it under
  `quota_threshold_pct`, and rechecks cooling accounts when none are IDLE.
  The pool it picks from is ordered, not partitioned. The account the
  operator talks to — `primary_account` — is in it like any other and simply
  ranks last, which is what lets "every worker is out of quota" need no
  special case at all: it is the ordering running off its end. What keeps
  that from eating the console is three limits rather than an exclusion.
  `reserve_pct` (60) is the primary's own ceiling, stricter than the workers'
  `quota_threshold_pct` (90) and a config error if it is ever set looser; it
  holds the primary's five-hour session, while its week is paced against the
  reset `/usage` reports — 10% of the week just after a reset rising to 95% in
  its last day, `pace_primary_week: false` to go back to the flat reserve
  (`docs/decisions.md` ADR 48).
  `fallback_roles` (`revisor` and `auditor` by default) is the list of phases
  it will take at all: a revisor or an auditor reads a diff and writes a
  verdict, bounded and cheap, while an implementador writes code across up to
  `max_revision_rounds` rounds and is the phase most likely to drain the
  reserve it was just handed — so a phase outside the list is told the pool is
  dry instead. And a worker parked on a *local counter* is waited for rather
  than fallen back from: waiting out a counter costs nothing, and the reserve
  exists for the case where a worker was actually refused and cannot come back
  on its own. A one-account pool is none of these decisions — with no worker
  to fall back *from*, the single account takes every role, since the
  alternative is a harness that takes itself out of service.
  That probe reads free text, so it takes only what it decides on: both
  percentages are required and a missing one fails the probe loudly, while
  the `· resets <when>` clause beside each is optional, because the CLI
  drops it from a line reading 0% — and nothing schedules off those
  timestamps anyway, since recovery is a re-probe. Since ADR 48 the week's
  clause is nonetheless *read*: the paced ceiling above is computed from it
  on every probe, so a missing or unparseable one costs the pacing — the
  week falls back to `reserve_pct` — rather than costing the reading.
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
- **No board beside the task files** — the task files are the record of
  what every task is doing, and the dispatcher mirrors them nowhere: the
  Vibe Kanban client and the local JSON board were removed
  (`docs/decisions.md` ADR 59). The console reads the task files through
  the api. A `vibe_kanban`, `local_board` or `vibe_kanban_mcp_url` key in
  `config.yaml` is a startup error that names the key.
- **Context handoff** — `.hive/tasks/<task-id>.md`: YAML frontmatter
  (`status`, `owner`, `depends_on`, `heartbeat`, `resolved_debt`, and
  `epic`, `split_into` or `merged` on a task that was split or merged)
  plus a body that accumulates each phase's handoff;
  the operator's description lives in `board/<task-id>/description.md`
  beside the hive tasks dir. A phase returns that handoff as a
  schema (`--json-schema`, per role: what changed, what was verified, what's
  pending, risks, the subagents it started, proposed learnings and debt, and
  paths to the detail — plus the revisor's `verdict`), within a per-role byte
  budget the dispatcher enforces with one `--resume`; a phase that answers
  in prose anyway lands clamped to its first 500 and last 1,500 characters.
  The budget is stated to the role twice, as a number of entries and as the
  bytes those entries buy (`handoff.lines_for` derives the first from the
  second, so they can't drift): nothing can count its own bytes while it
  writes, which is why every run before 2026-09-24 overran one. The bytes are
  what the dispatcher measures, and the numbers come from measuring what
  phases file: every accepted return on record was inverted back into the
  canonical JSON the dispatcher counted and priced per entry, and an entry is
  charged at the p90 of that density rather than the mean, so a role that
  spends the entries it was given fits its budget about nine times in ten
  instead of about one in two. The one entry that is not a line is a debt
  declaration, which carries several fields and is stated to the role as
  three. `handoff.py` carries each number with the evidence for it. An overage
  inside a 10% margin (floor 256 bytes) is taken as it came rather than spent
  on a model call, and the size is logged at INFO either way, which is the
  data the next tuning uses. A WARNING means a handoff went into the task
  file over budget anyway: the rewrite was refused, failed, or came back long.
  Detail belongs in files, cited by path and a stable anchor: durable docs
  on the task branch, per-round scratch under `.hive/tasks/<task-id>/`.
  Used for cold-start role transitions; mid-role quota exhaustion instead
  resumes the same Claude session directly via `--resume`. Stale locks
  (heartbeat older than `heartbeat_ttl_seconds`) are reaped at the start of
  each task cycle; a live lock held by another owner is refused: the run
  exits 1 and the task file is left as it was. A task has one branch,
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
  contract: `docs/README.md` is the index, `docs/charter.md` the rulings a
  human made that no role may edit or re-decide, `docs/decisions.md` the
  numbered ADRs an agent made (appended to and struck through, never
  rewritten), `docs/architecture.md` and `docs/business.md` the map and the
  domain, `docs/learnings/` and
  `docs/debt/` one file per entry behind an index whose every row carries a
  trigger — the condition that says when to open it — and
  `docs/implementations/<task-id>.md` how one task was built. Each role is
  handed its duty in the prompt: the arquitecto reads the charter and records
  ADRs, and blocks the task when what it read does not specify it; the
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
  task is carrying before the auditor runs, moves them out of the inbox once
  that branch actually merges *and* the task file shows the auditor ran,
  into a `dropped/` sibling no prompt reads rather than to deletion
  (`docs/debt/T-012-D1.md`), and, when the cycle ends any other way,
  releases them unconfirmed for the next task rather than losing them
  with the phase that found them. An entry is a claim until a
  second, distinct task hits the same wall or a human says so
  (`dispatch learnings --confirm`): one phase's wrong guess repeated to every
  later phase is worse than no note at all. Entries leave the same way they
  arrive, on evidence: each is stamped with a fingerprint of the permissions
  it was written under and stops counting once those change, and a task that
  walked through another task's trap unharmed retires it by writing
  `refutes: <ref>` on an entry of its own. Two scopes — a project's own trap,
  and one about this harness, which is the only kind that reaches
  `.hive/learnings/harness/` and only through a human running
  `dispatch learnings --promote`. The line it draws: a trap is "don't step on
  this"; half-finished code is debt, not a learning.
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
  Flask + SQLite/WAL). A second service (`observability/api`) serves read-only
  JSON over the same state the dispatcher keeps on disk — task files, account
  state, events, the debt index — behind Basic auth, on
  `127.0.0.1:8789`: `GET /api/tasks`, `/api/tasks/<id>`, `/api/accounts`,
  `/api/events`, `/api/debt`. It is Phase 1 of
  [`docs/plans/board.md`](docs/plans/board.md); see `docs/decisions.md` ADR 3–5
  for the envelope and the error half, and ADR 59 for the board entry a
  task row no longer carries.
  Phase 2 of the same plan was a third service, `observability/board` on
  `127.0.0.1:8790`: four server-rendered Flask/Jinja screens — the accounts strip
  over the tasks table, one task, the debt index, the event log — over those five
  endpoints and no other source of truth. **It was retired on 2026-10-04**, when
  `front/` took over those four screens against the real api
  ([`docs/charter.md`](docs/charter.md) C-8, `docs/decisions.md` ADR 32). What it
  settled outlives it: the browser never talks to the api — every fetch is
  server-side, so the api's credential stays on a server instead of in a page an
  operator can view-source — and `docs/decisions.md` ADR 6–9 still hold the
  states, the token, the clock and the project a console reads. It had itself
  replaced a 43-line events dashboard on 8788, whose three columns `/events`
  supersedes.

  C-7 — Flask and Jinja, no Node in the control plane — was superseded on
  2026-09-28 by **C-8**, which names `front/`, a TanStack Start app written
  against these same plans, the console this repo ships, and which retires the
  board in the same task that closes the last of its four screens. That task has
  now run; the integration it finished is planned in
  [`docs/plans/front.md`](docs/plans/front.md).

Full design rationale, decisions, and open caveats live in
[`docs/superpowers/specs/2026-09-13-ia-harness-design.md`](docs/superpowers/specs/2026-09-13-ia-harness-design.md);
the implementation task breakdown is in
[`docs/superpowers/plans/2026-09-13-ia-harness.md`](docs/superpowers/plans/2026-09-13-ia-harness.md).

## Repository layout

```
dispatcher/       Smart Dispatcher: config, state machine, docker exec, CLI
observability/    Event collector (Flask/SQLite) + read API (Flask, JSON)
front/            Operations console (TanStack Start) — C-8, the `console` service
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
required for the host-level steps — only the `dispatcher`/`collector`/`api`
containers need Python, and they get it from their own images. None of those
three needs Node, all three being on the same `python:3.11-slim` base. That was
[`docs/charter.md`](docs/charter.md) C-7 and it survives C-8, which buys Node for
`front/` alone — the console is a fourth service on its own runtime, and the
three above stay as they are. It is the `console` compose service
(`docs/decisions.md` ADR 97), built for Node by its own image.

### 1. Clone and configure

```bash
git clone <this-repo> ia-harness && cd ia-harness
```

Generate `config.yaml` (and, optionally, `docker/compose/.env` with the hashed
credentials the api checks, plus the `API_TOKEN` the console authenticates to
the api with, the collector's `COLLECTOR_READ_TOKEN`, and one
`COLLECTOR_WRITE_TOKEN_<ACCOUNT>` per account) using the interactive wizard — pure `bash` +
coreutils, so it needs nothing beyond a POSIX shell and `sha256sum`:

```bash
scripts/configure.sh
```

It defaults the paths/URLs that must match the compose files' volume mounts
and internal service DNS, and only asks about them under `--advanced`.

The first question is the account list, and everything else follows from it.
One account is a complete pool: the dispatcher hands every role to it, and
`fallback_roles` only rules on falling back from a worker, so it does not
apply. Any list but the shipped `cuenta1,cuenta2` also writes
`docker/compose/docker-compose.agents.yml` (or the path given with
`--agents-out`) with one `agent-<name>`/`dind-<name>` pair per account, its
`claude_creds_<name>` and `dind_<name>_data` volumes, and the shared
`claude_shared` volume and `ia_harness_net` network; an existing file is only
replaced after you say so. The generated file is what
`docker compose -f docker/compose/docker-compose.agents.yml up -d` brings up in
step 4, and `scripts/setup_volumes.sh` in step 3 takes the same list. For the
shipped list the file is left as it is, since it already holds those two pairs.
Names that differ only by `-` versus `_` (`a-b` and `a_b`) are refused: they
would share one `COLLECTOR_WRITE_TOKEN_A_B` key in `.env`.

Prefer editing by hand? Copy the template instead:

```bash
cp config.example.yaml config.yaml
```

`config.yaml` holds the defaults. A `config.d/overrides.yaml` beside it, if
present, is merged over it: each top-level key in the overrides replaces the
whole value in `config.yaml`, maps and lists included. Only the keys that
`CONFIG_KEYS` in `dispatcher/config.py` marks editable may appear, and a reset
is deleting the key (`null` is refused). A bad overrides file stops every verb;
the dispatcher never falls back to `config.yaml` alone. The directory is
git-ignored. The `config-writer` service writes it (`docs/plans/console-config.md`
task 2); the dispatcher and the api mount it `:ro`. `scripts/configure.sh`
creates the directory beside `config.yaml`, so docker does not make it
root-owned at the first `up` (a host that skips the script still works: the
root writer writes it, and a hand repair then needs `sudo`). See
`docs/decisions.md` ADR 75 and ADR 110.

### 2. Build the account-specific images

`dispatcher`, `collector` and `api` have `build:` stanzas in
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

Pass the same account list you gave `scripts/configure.sh` (it prints the
exact command in its next steps): a single account is
`scripts/setup_volumes.sh <name>`.

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
`DASHBOARD_PASSWORD_HASH`/`API_TOKEN` from `docker/compose/.env` automatically
if `scripts/configure.sh` wrote one; otherwise pass them inline. The two
credential variables keep their `DASHBOARD_` names although the dashboard they
were written for is gone — renaming them means editing a `.env` that exists on
every running host to buy a spelling. `API_TOKEN` is required too, since the
`console` service refuses to boot without it: an `.env` written before it
existed makes compose warn that the variable is unset and the console exit at
boot, so add a long random string (`scripts/configure.sh` writes one). The
collector is stricter (`docs/decisions.md` ADR 72): it reads
`COLLECTOR_READ_TOKEN` and its map of write tokens from the same `.env` and
refuses to start, naming the variable, without them. `scripts/configure.sh`
writes that map as `COLLECTOR_WRITE_TOKENS=agent-<name>=<token>,...`, one entry
per account with the same value as that account's
`COLLECTOR_WRITE_TOKEN_<NAME>`, so the collector follows whatever pool you
configured. An `.env` without `COLLECTOR_WRITE_TOKENS` still works for the
shipped pool: the compose file falls back to building the map from
`COLLECTOR_WRITE_TOKEN_CUENTA1`/`_CUENTA2`. An `.env` written before the
collector tokens existed at all needs `scripts/configure.sh` rerun or the keys
added by hand (distinct long random strings). The read token is the
operator's for `GET /events` (`curl -H "Authorization: Bearer $COLLECTOR_READ_TOKEN"
127.0.0.1:8787/events`) and goes to no other service; each write token goes to
its agent as `COLLECTOR_TOKEN` and to the collector, which stores the agent it
is bound to whatever the event body claims.
`CONFIG_WRITER_TOKEN` is required as well (`scripts/configure.sh` writes it): the
`config-writer` service exits 2 naming it without one, and the `console` is
given the same value. The writer listens on `127.0.0.1:8791`. The console,
`caddy`, the api and the writer share `console_net`, which no agent joins
(`docs/decisions.md` ADR 109).
This step brings up six **persistent** control-plane services (`collector`,
`api`, `console`, `config-writer`, `caddy`, `registry-mirror`) plus, from the second file, one persistent
`agent-<name>`/`dind-<name>` pair per configured account. The console answers at
`http://127.0.0.1:8790` (from another machine, through an SSH tunnel: it is
published on loopback only; see *TLS* below for serving it on a domain; `caddy` needs `CONSOLE_AUTH_HASH`
there). Its first
`up` builds `front/` into an image, which takes a few minutes.

The console's `CONSOLE_PROJECT` does what `BOARD_PROJECT` did here until
2026-10-04: it is optional, read by the `console` service from `.env`, and worth
setting on a host that holds more than one checkout. It names the slug under the
dispatcher's `projects_root` whose debt the console asks for — `ia-harness` on a
host that only works on this repo. Where `projects_root` holds several checkouts
`/api/debt` has no default to fall back on and answers `400 project is
required`, listing what it found, so the debt screen is an Error state until the
variable is set; the tasks, task and tail screens are unaffected
(`docs/decisions.md` ADR 24 for the name, ADR 32 for the retirement that freed it).
`docs/decisions.md` ADR 9 says why `scripts/configure.sh` does not prompt for it.

`BOARD_ACTIONS_TOKEN` is optional (`scripts/configure.sh` writes it): it is the
bearer of the board service's action routes, given to the `board-service` and
the `console` only. Without it both answer 503 on Start and Cancel; after
rotating it, restart both. The board service also mounts
`dispatcher_state/actions` (never the whole state dir), the queue the `worker`
below drains (`docs/decisions.md` ADR 142, ADR 166).

Two services in those files are deliberately kept out of that default set,
behind a Compose profile. Stage-0 verification found the first by running the
two-liner above:

- `dispatcher` (`profiles: ["dispatcher"]`) is a template, not a service
  (`restart: "no"`, placeholder `--task-id`/`--project`). Ungated, a bare
  `up -d` fired it and ran a task. See step 5.
- `worker` (`profiles: ["worker"]`) is the one long-running service with the
  docker socket, with the dispatcher's mounts. Start it with
  `docker compose -f docker/compose/docker-compose.yml --profile worker up -d worker`.
  It carries out the queue actions the console files, so Start and Cancel from
  the console need it running. `dispatcher_state/actions` does not exist until
  docker creates it, root-owned, on the first mount.

There was a second, `vibe-kanban` behind a `kanban` profile. The service,
its profile and its volume declaration were removed with the board
(`docs/decisions.md` ADR 59). Removing a declaration deletes no volume: a
host that ran that profile keeps `vibe_kanban_data` until someone removes
it by hand.

### TLS

The plain compose file (`docker/compose/docker-compose.yml`) has a `caddy`
service in front of the console (`docs/decisions.md` ADR 100). It is the only
service that publishes beyond loopback, on 80 and 443 (TCP, and 443 UDP for
HTTP/3); the api and the collector stay unexposed. To serve a domain over HTTPS:

1. Point a DNS A record (and AAAA, if the host has IPv6) for the domain at the
   host, and open ports 80 and 443 to the internet before `up`: Let's
   Encrypt's challenge arrives on them.
2. Set `HARNESS_DOMAIN` in the .env file to the bare host name, no scheme
   (`HARNESS_DOMAIN=console.example.com`), and optionally `ACME_EMAIL` for the
   certificate account. `scripts/configure.sh` writes both as commented lines.
3. `docker compose -f docker/compose/docker-compose.yml up -d`. Caddy gets the
   certificate itself, redirects HTTP to HTTPS and sends HSTS. Certificates
   live in the `caddy_data` volume, so a restart does not ask again.

With `HARNESS_DOMAIN` unset, Caddy serves `http://localhost` over plain HTTP,
with no certificate and no HSTS, for development.

The console has no auth of its own, so Caddy asks for a Basic credential on
every request it proxies, with or without a domain (`docs/decisions.md`
ADR 103). Make the hash and put it in the .env file:

```bash
docker run --rm -it caddy:2.10.2 caddy hash-password
# .env: single quotes, because compose expands a $ in anything else
CONSOLE_AUTH_HASH='$2a$14$...'
CONSOLE_AUTH_USER=console   # optional, this is the default
```

With no `CONSOLE_AUTH_HASH`, Caddy refuses its config and does not start; the
other services still do. On plain `http://localhost` the credential travels in
clear, so use the domain when the console is reachable from outside. This is a
stopgap: the sign-in of `docs/plans/server-deployment.md` replaces it.

### 5. Run a task

Bootstrap a project directory on an account's container, then run a task
through the full role cycle:

```bash
python -m dispatcher.cli --config config.yaml bootstrap-project \
  --account cuenta1 --project my-project
python -m dispatcher.cli --config config.yaml run-task \
  --task-id T-001 --project my-project \
  --description "## Goal

Add a /healthz endpoint that returns 200 without auth.

## Done when

GET /healthz answers 200 with no credentials."
```

(Or, without a host Python install, run the same commands inside the
already-built dispatcher image: `docker compose -f docker/compose/docker-compose.yml --profile dispatcher run --rm dispatcher --config /app/config.yaml run-task --task-id T-001 --project my-project --description "…"`.)

`--description` is what every role is actually told to work on. It is
stored in its own file, `board/T-001/description.md` beside the hive
tasks dir, separate from the task file (`<hive_tasks_dir>/T-001.md`)
where the phase summaries accumulate. It is written in sections:
`## Goal` and `## Done when` are required, `## Surface`, `## Steps` and
`## Out of scope` optional, and one missing a required section is a usage
error that names it. The two required sections go into each phase's
prompt whole; the optional ones do not, and the arquitecto and the
implementador are given the file's path to read them. Re-running the
same task ID reuses the stored description, so `--description` can be
omitted on a resume; running a task that has none anywhere is rejected as
a usage error rather than spending four phases on roles that know only an
ID. A hive from before the description had its own file is moved over
once with `python -m dispatcher.cli --config config.yaml migrate`, which
leaves alone, and reports, a task whose cycle is still running; until
then `run-task`, `run-phase` and `release-account` refuse and name that
command. For
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

`run-task`'s exit code says how the cycle ended (`docs/decisions.md` ADR 52,
and ADR 60 for the split):

| Code | The cycle | What to do |
|---|---|---|
| 0 | finished: the auditor closed the task, whether or not `merge_on_done` merged it | read the result |
| 75 | held: no account could take a phase the task needs (all tried, cooling, or over their quota) | run the same command again once an account resets |
| 3 | split: the arquitecto split the task and the split was filed. The task file says `status: epic`, and stdout has `task <id> is now an epic: <ids>; run them in that order` | run each sub-task with `run-task`, in that order; nothing runs them for you |
| 1 | blocked: a phase failed or blocked, the rounds ran out, a split could not be filed, another run holds the lock, or the project branch diverged. Also a task refused before it started: one with a `depends_on` task that is not merged, a child of a malformed epic (`task <id> cannot start: <reasons>` in the log for both), or one whose file says `status: epic` | read the log; a person decides |
| 2 | a usage error, from argparse | fix the command line |

75 is sysexits.h's `EX_TEMPFAIL`, "try again later". A refused merge after a
finished cycle is still 0: the task is done, and `merge-task`'s exit code is
the one that reports a merge.

A cycle that stops blocked after one of its phases took the task's lock
writes `status: blocked`, with no owner, to the task file: a phase that
failed, the arquitecto's block, the rounds running out, the revisor's block,
the auditor failing, a split that could not be filed. The other stops leave
the file as it was: a held cycle, a task refused before it started, a
diverged project branch, a lock another run holds, and a task with no file.
So does `run-phase`. `blocked` in a task file therefore means a cycle ran
and stopped, not every exit 1.

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

A merge that lands is recorded on the task file, as a `merged` block naming
the branch and the time, and `merge-task` prints `task <id>: recorded as
merged into <branch>`. That block is what another task's `depends_on` waits
for: `done` is not enough. When the task was the last open sub-task of an
epic, the others merged or dropped, the epic's own file is set to `done`
and the verb prints
`epic <id>: every sub-task is merged or dropped, so it is done`. A re-run on
a branch already merged writes the block only where the file has none, which
is how a task merged before the record existed gets one. A record that
could not be written is reported on stderr and the exit is still 0, because
the branch did land (`docs/decisions.md` ADR 60).

To have a cycle do this by itself the moment the auditor signs off, set
`merge_on_done: true` in `config.yaml`. It defaults to `false`, because
the merge is the one thing a run writes into the branch you work from. The
cycle's own merge records the same `merged` block and closes an epic the
same way, in the log rather than on stdout. A
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

Everything a phase needs around it still happens — the account and task
locks, the worktree, the commit, the gates, and the handoff appended to the
task file that the next phase reads. What does not happen is the cycle's own
judgement: it reads no verdict, starts no further round and merges nothing.
Those stay `run-task`'s, and the log says so rather than leaving it to be
assumed; `merge-task` is still the way to offer the branch back. It refuses
what `run-task` refuses before a phase starts, with exit 1 and the same log
line: a task whose file says `status: epic`, an unmet dependency, a child of
a malformed epic.

It files no split either. When `run-phase --role arquitecto` comes back
with one, the verb prints `phase arquitecto proposes splitting task <id>
into <n> sub-tasks; nothing was filed:`, the sub-tasks, and the `file-split`
command that would file them (see *The split, when the task is more than
one* below), and still exits 0.

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
the learnings carried in beforehand, the debt the earlier phases declared
recorded and filed, `status: done` in the task file, and no
orphaning of the entries on the way out. The debt half is possible because
every phase leaves its structured return under the task's scratch directory
(`.hive/tasks/<task-id>/handoffs/<role>.json`): the prose appended to the
task file is for whoever reads next, the JSON is for whoever has to act, and
only the second survives the process that produced it. A task whose earlier
phases ran before the dispatcher stored them has no such record, and closes
the way this path always did with nothing filed — the closing log names
which of the two happened, because "no debt filed" means something very
different when there was a record to file from. Without it the
task stays `pending` and the entries this run wrote go back to being
unowned, which is what every phase before the last one should do. A phase
that did not land exits non-zero, so a hand-driven cycle stops rather than
running the next phase over the top of it; it does not write `status:
blocked` to the task file, which only a `run-task` cycle does. The codes are
`run-task`'s: 1 for a phase that failed, 75 for one that never ran because no
account could take it, so a loop can wait out the second and stop on the
first.

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
the ceiling it answers to — `quota_threshold_pct` for a worker, and for the one
marked `primary` both of its own: `reserve_pct` on the session and the paced
weekly ceiling read from its last probe's reset, so the numbers are read off
the pool and that probe rather than off the config — how much of
`quota_cooldown_seconds` a recorded
refusal has left to run, and every task file that is in progress or owned,
with the age of its heartbeat and whether that is still a live lock against
`heartbeat_ttl_seconds`. It writes nothing — deliberately, because the gate's
own probe (`check_quota_ok`) parks accounts and records refusals as it goes,
and a look at the pool must not change it. `--probe` adds each container's
`/usage` numbers on the same terms: the result goes through the same refusal
detection a phase's does, so a probe the service turns down is reported as
`probe REFUSED`, but nothing is written to the state files either way. A
container that is down becomes a `probe failed:` note on that one account
rather than an error for the run.

`release-account` is the way out of the state a crash leaves behind: an
account `BUSY` on a task whose dispatcher is gone. Nothing else could reach
it — `list_idle_accounts` only returns `IDLE`, and the recheck that revives a
parked account skips any state that is not `PRE_COOLDOWN`/`COOLING_DOWN` — so
before this verb the only remedy was editing a root-owned JSON file by hand.
It sets the account back to `IDLE` and clears the lock on whatever task it
holds, dropping `owner` and `heartbeat` and leaving `status` alone: whether
the task is still in progress is the task's business, not the account's. It
finds that task by the union of what the state file names and what the task
files themselves say they are owned by, so a task the state file never knew
about still comes back.

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
orphaned task, if it has one, is still released, because that is the other
half of the same crash. An account name the config does not have is a usage
error naming the ones it does.

Most of the time you will not have to run it. The picker reaps the same state
on its own before every pick, on a TTL that reads the task's heartbeat rather
than the clock: a phase still beating is left alone however long it runs, and
one whose heartbeat is older than `heartbeat_ttl_seconds` gives its account
back. An account holding no task — or a task nobody ever beat on — has no
heartbeat to read and is judged against `phase_timeout_seconds` instead, the
longest a phase is allowed to live at all. Under both sits a floor: an
account that went `BUSY` more recently than the TTL is never reaped, so a
phase that has just started cannot be taken from it by a second dispatcher
that looked before the first heartbeat landed. Each release says in the log
which of the two clocks decided it. What is left for the verb is the case the
TTL must not touch — a phase that is genuinely alive and has to be stopped
anyway, which is `--force` — and not waiting out the TTL when you already
know the process is gone.

**The map, if the project has none.** A target repo the agents have never
seen has nothing written down for them, and every task rediscovers it from
the source. Set `mapping_enabled: true` and a `run-task` on a project with
no `docs/README.md` runs one extra phase ahead of the arquitecto: a
read-only `cartografo` that writes that index — `build:` and `test:` in its
frontmatter, then the stack, the modules, and a table of the docs that
already exist — plus `docs/architecture.md` and `docs/business.md`, and
changes no code. It runs on `mapping_model` (sonnet by default, not
`default_model`), the one key that sets its model: `role_model` refuses a
`cartografo` entry at load (`docs/decisions.md` ADR 66). It runs under
`mapping_max_turns` turns and is cut off there;
whatever it wrote by then is committed, and the next task extends it. It is
off by default because it spends quota on a phase that ships nothing —
with it off the docs still grow, one task at a time, from the roles that do
the work. The phase is never fatal: if it fails the task runs anyway, with
a warning and whatever docs exist. The check is made against the project's
checkout, so with `merge_on_done: false` a map still sitting on an unmerged
task branch reads as missing and the next task maps again. Merge the first
task, or expect a second map.

**The block, before anything is written.** The arquitecto is the only phase
that can end a task with no code written, and the one phase where that costs
less than the alternative: it reads the plan, the charter and the indexes
before anyone touches the worktree, so a decision the task was never given is
visible there and nowhere earlier. `status: blocked` in its handoff ends the
cycle at that point — the task file goes `status: blocked`, and what it
listed under `pending` is what a human has to answer to restart it. No other phase's
`status` is read: it is rendered into the task file and the cycle runs on
regardless, because a status is cheap for a role to set and every place that
acts on one is a place a role can end a task from. The prompt draws the line
— block for a decision nobody has made, not for a problem that is merely
hard, since anything the arquitecto can decide itself and record as an ADR is
its to decide. Work a user sees is where this bites most often: a screen
whose empty, stale and failed states nobody specified is not a detail the
implementador fills in. It invents one, and the revisor then reviews the
invention.

**The split, when the task is more than one.** The arquitecto's other early
end (`docs/decisions.md` ADR 60). A handoff whose `split` lists two to four
sub-tasks says the task is more than one piece of work; a block in the same
handoff wins over it. The dispatcher files one task per entry,
`<task-id>-S<n>`, each with its own description, its `depends_on` and the
`epic` it came from, sets the parent's file to `status: epic` with the ids
under `split_into`, and stops before the implementador with exit 3. Nothing
is dispatched for you: run the sub-tasks with `run-task`, in the order the
printed line gives. A split that cannot be filed blocks the task instead,
with `task <id>: the split cannot be filed: <problems>` in the log.

A split that was only proposed, by `run-phase --role arquitecto` or by a
run that died before filing, is filed by its own verb:

```bash
python -m dispatcher.cli --config config.yaml file-split \
  --task-id T-001 --project my-project
```

It reads the task's saved arquitecto handoff and runs no model. When it
files, it prints the same `task <id> is now an epic: <ids>; run them in that
order` and exits 0. When it refuses, it prints `task <id>: the split was not
filed; the log above says why` to stderr, exits 1 and leaves the task as it
was: no saved handoff, a blocked one, no split or an invalid one, and a task
that is `done`, already an epic or still locked by a live run.

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
file into `docs/learnings/` on the task branch; takes them out of the inbox
once that branch actually merges, automatically or via `merge-task`, and the
task file carries the auditor's own section, because that section is what says
the docs now hold them — into a `dropped/` sibling rather than to deletion,
since no reader enumerates that directory, so the prompt cost the drop exists
for is gone either way; and, when a cycle ends any other way — blocked,
refused merge, crash — clears the stamp and leaves them in the inbox, marked
with the task that dropped them, for whoever hits the same wall next. A run
that bounced off another dispatcher's lock touches nothing: those entries
belong to a run still working.

A `merge-task` whose cycle never reached an auditor moves nothing either. It
keeps the entries in the inbox, prints how many it kept and that the task
file carries no auditor section, and exits zero: landing a branch the cycle did not
land is what that verb is for, and the cycle that died before its auditor is
the one whose entries were never promoted anywhere
(`docs/debt/T-012-D1.md`, `docs/decisions.md` ADR 33 and ADR 34).

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

**There is no board to configure.** The dispatcher once mirrored each task
to an optional board, Vibe Kanban or a directory of JSON files. That seam
was removed (`docs/decisions.md` ADR 59): a task's state is its task file,
and the console shows it through the api. A `vibe_kanban`, `local_board` or
`vibe_kanban_mcp_url` key left in `config.yaml` is a startup error that
names the key and that ADR, rather than a block silently ignored, and
`run-task --kanban-issue-id` is a usage error.

**What "issuing commands from the interface" means today:** nothing. No
screen of the console makes anything run. The dispatcher is a one-shot CLI,
not a daemon watching for new tasks, so `run-task` above still has to be
invoked manually (or from your own automation/cron) per task-id. There is
no push-button "run" in the UI yet.

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
row that should not be there, a dropped declaration costs the entry itself.

Then the dispatcher names them — the dispatcher and not an agent, because an
agent that numbers its own entries numbers them again when it is re-run for a
second review round. Each accepted declaration gets an id, `<task-id>-D<n>`,
*before* the auditor runs, and the auditor's prompt carries the ids, so the
row it writes already has its name. Nothing is opened anywhere else
(`docs/decisions.md` ADR 59): the auditor writes `none` in the index's `card`
column. A declaration that flattens to the same fingerprint as a row already
in the index gets no id; the index is read from the worktree this
task is being built in, so a re-run that already filed its entries sees them.
The auditor then writes the rows and the entry files under `docs/debt/`,
still the only phase that writes an index.

A row in the index is a record, not work: nothing dispatches it on its own,
and a person decides when a task takes it on. The way back in is
`resolved_debt` — an implementador that resolved an entry the index already
carries names its id there, and the dispatcher stores it in the task file
rather than leaving it in the handoff, and the api serves it from there. No
merge reads it or closes anything.

"Resolved" lands in one place. The auditor marks the row resolved where it
stands, on the task branch, with the task that resolved it and never by
deleting it — a fix that gets reverted should still have its row — because
it is the only writer of the indexes and the mark belongs in the commit that
made it true.

Accounts are added or removed by rerunning `scripts/configure.sh` with the
new list: it rewrites `config.yaml`'s `accounts`, the agents compose file,
the Coolify compose file and the `.env` tokens together, so they cannot
disagree. Editing them by hand still works (duplicate an `agent-*`/`dind-*`
pair, add the `accounts` entry, add a `COLLECTOR_WRITE_TOKEN_<NAME>` and its
`COLLECTOR_WRITE_TOKENS` entry) — no dispatcher code changes are required
either way.

### Deploying with Coolify

**Recommended: single-file import.** `docker/compose/docker-compose.coolify.yml`
merges both compose files into one self-building resource — point Coolify's
**Docker Compose** resource type at it and it builds *every* image itself
(agent and dind-sidecar included, via `build:` stanzas that the split files
don't have), so none of steps 2–3 above need to run by hand first. Its
agent pairs are rendered by `scripts/configure.sh` for the pool it writes
into `config.yaml` (`--coolify-out PATH` to put it elsewhere); the shipped
file is the cuenta1/cuenta2 pool. It
differs from the split files in three ways, all in the interest of a clean
one-click import:

- `claude_shared` and each `claude_creds_<account>` are plain
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
- `worker` is gated behind its own profile (`profiles: ["worker"]`), so the
  default running set does not include it either. Start it with
  `docker compose -f docker/compose/docker-compose.coolify.yml --profile worker up -d worker`;
  see step 4 for what it does.

TLS here is Coolify's, not Caddy's: set the domain on the `console` service in
Coolify's UI (port 8790) and let Coolify's proxy issue the certificate. Do not
add a `caddy` service or set `HARNESS_DOMAIN` in this resource; the Caddy service
exists in the plain compose only (`docs/decisions.md` ADR 100), and a second
proxy would fight Coolify's for ports 80 and 443.

**The console has no auth of its own** (it forwards every call with its own
bearer token), and in Coolify nothing asks for a credential in front of it. Do
not give the `console` service a public domain without Coolify's own basic auth
on it, or keep it on a private network (`docs/decisions.md` ADR 103).

A private project is reached through its SSH deploy key: run
`project-keygen` in the dispatcher, which writes the pair under
`.data/credentials/projects` (mounted into the dispatcher and, read-only, into
the agents), and add the public key to the repository with write access when
the project pushes. This file does not mount the https:// `git-credentials`
token file, so a project without a key reaches only public https:// remotes.

Two things still can't be automated by any compose file, Coolify import
included:

- **`config.yaml` must exist before first deploy.** It holds no secrets
  (just paths, thresholds, and account names — see `config.example.yaml`),
  so a Coolify pre-deployment command is safe for the shipped cuenta1/cuenta2
pool: `test -f config.yaml || cp config.example.yaml config.yaml`. For
another pool, place the `config.yaml` from the `scripts/configure.sh` run
that rendered this file instead (it is gitignored, so it does not travel
with the repo).
  Set `DASHBOARD_USERNAME`/`DASHBOARD_PASSWORD_HASH`, `COLLECTOR_READ_TOKEN`,
  one `COLLECTOR_WRITE_TOKEN_<ACCOUNT>` per account (required: the collector
  will not start without them), and `API_TOKEN` (required: the resource now
  includes the `console` service, which will not start without it),
  `CONFIG_WRITER_TOKEN` (required: the `config-writer` service exits without it)
  and optionally `CONSOLE_PROJECT` and `BOARD_ACTIONS_TOKEN` (the console's Start
  and Cancel answer 503 without it), through Coolify's own environment-variables UI on
  the resource instead of a `.env` file — Coolify substitutes `${VARS}` into
  compose the same way.
- **Per-account Claude Pro OAuth login** is an interactive, browser-based
  step done once per agent container after it's up (e.g.
  `docker exec -it agent-<account> claude`, then `/login`). No import can do
  this for you.

**Alternative: two Compose resources.** If you're already managing the
agent/dind-sidecar images or the `claude_shared`/`claude_creds_<account>`
volumes yourself outside Coolify (e.g. reusing them across a fleet, or you
want them to outlive a resource deletion), import the split files instead
of the merged one, as two separate Coolify Compose resources:

1. `docker/compose/docker-compose.yml` (control plane: `collector`, `api`,
   `registry-mirror`, plus `dispatcher` behind the `dispatcher` profile).
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

This section is a status list, not a spec. It used to hold the full text of
every item below; most of that has since been built, planned or overtaken,
and each line now says which and points at where the thing lives. What is
built is described in the sections above and under `docs/`; what is planned
has a file in `docs/plans/` or an entry in
[`docs/ROADMAP.md`](docs/ROADMAP.md); what a decision overtook cites its ADR
in `docs/decisions.md`. What is still open and written down nowhere else was
moved, word for word, to
[`docs/plans/backlog.md`](docs/plans/backlog.md) on 2026-10-13 — nothing
there is designed or approved, so evaluate it when the work is taken on, not
before. The history of the verification runs is the *Results log* of
[`docs/ROADMAP.md`](docs/ROADMAP.md). Still unverified there: V1.1, the
failure paths (V4), and a failover on a phase whose predecessor had already
committed.

### Known gaps (fix first)

The gaps the first runs of the pipeline opened are fixed, and their record
is *Stage 1 — Fix the known gaps, then accept* in
[`docs/ROADMAP.md`](docs/ROADMAP.md). One is left, which a no-quota check
couldn't settle and the unit tests, which mock Claude Code and Docker, don't
settle either:

- **Unverified assumptions.** dind isolation, the registry mirror and bind
  mounts: V0.7 in [`docs/ROADMAP.md`](docs/ROADMAP.md), not yet run, blocked
  by V0.1 (no `sysbox-runc` on the verification host). Worth a manual check
  before building on them.

### Prioritized

Ordered by priority, highest first, with the numbers the items have always
had. Each line names a piece and says whether it is **built**, **planned**,
**superseded** or **open**. The text of an **open** piece is in
[`docs/plans/backlog.md`](docs/plans/backlog.md), under the item's own
number and title.

0. **Editing `config.yaml` from the console** — **built** 2026-10-17 by
   T-064 to T-067, planned in
   [`docs/plans/console-config.md`](docs/plans/console-config.md): the
   `load_config` merge of `config.d/overrides.yaml`, the `config-writer`
   service, and the Role models screen's saves and resets. Added 2026-10-15
   and placed first; numbered 0 so the items below keep their numbers. The
   credentials and the login, that plan's section 4, are the Tokens task of
   [`docs/plans/front.md`](docs/plans/front.md), not built.
1. **Project memory: role skills, per-project docs, and a pointer-based
   handoff.**
   - *Role skills (method)*, from superpowers, thermos and ponytail —
     **built**: `skills/`, `skills/NOTICE.md`, and `ROLE_SKILLS` and
     `plugin_dirs` in `dispatcher/role_skills.py`.
   - *Role skills*, evaluating ECC — **open**.
   - *Revive before respawn* — **built**: `revive_note` in
     `dispatcher/subagents.py`, and "Picking a subagent back up" above.
   - *Per-project docs* and the mapping phase — **built**:
     `dispatcher/project_docs.py` (`MAPPER_ROLE`), `mapping_model` in
     `config.example.yaml`, and `docs/README.md` for this repo's own.
   - *Documenting is a method*, a ninth skill on how to write a doc entry —
     **open**.
   - *Documenting is a method*, this repo's own conventions — **built**:
     `docs/README.md` and `docs/charter.md`. One of them, that the bullets
     under a *Prioritized* item are never trimmed, is **superseded** by
     this list (`docs/implementations/T-037.md` *Decisions*,
     `docs/decisions.md` ADR 63).
   - *Limits go on what travels*, the byte caps on what a phase is handed —
     **built**: `_BUDGET_BYTES` in `dispatcher/handoff.py`, `MAX_ROWS` and
     `_CELL_CHARS` in `dispatcher/learnings.py`.
   - *Limits go on what travels*, a gate that notes a touched source file
     past a size — **open**.
   - *Indexes and pointers* — **built**: the duties and `_ANCHORS` in
     `dispatcher/project_docs.py`, the `pointers` gate in
     `dispatcher/gates.py`, `docs/learnings/README.md` and
     `docs/debt/README.md`.
   - *Structured handoff* — **built**: `dispatcher/handoff.py`, and
     `_shrink_over_budget` in `dispatcher/dispatcher.py`.
   - *Docs and tests kept current*, the dispatcher gates and the role
     instructions — **built**: `dispatcher/gates.py`, and `docs/decisions.md`
     ADR 31, ADR 35 and ADR 39.
   - *Docs and tests kept current*, a formatter or typecheck hook inside
     the agent's session — **open**.
   - *Declared debt* — **built**: `dispatcher/debt.py`,
     `docs/debt/README.md`, and `docs/decisions.md` ADR 59.
   - *Shared learnings that survive a dead phase* — **built**:
     `dispatcher/learnings.py`, and `docs/decisions.md` ADR 33 and ADR 34.
   - *Shared learnings*, handing a phase only the index rows that match its
     task — **open**.
2. **Token economy.** The measurement the item asked for is
   `docs/plans/token-economy.md` *What the transcripts show*.
   - *Record usage per phase* — **built**: `append_usage` in
     `dispatcher/context_transfer.py`. The collector as its home is
     **superseded** by `docs/decisions.md` ADR 53 and ADR 54.
   - *Stop probing quota* (`rate_limit_event`) — **planned**:
     [`docs/ROADMAP.md`](docs/ROADMAP.md) *Deferred gates*, D2.
   - *Pace the primary's weekly spend* — **built**: `weekly_ceiling_pct` in
     `dispatcher/quota.py`, `docs/decisions.md` ADR 48.
   - *Trim the fixed startup context* — **built**: skills are delivered per
     role and per call (`dispatcher/role_skills.py`), and no MCP server is
     left since `docs/decisions.md` ADR 59.
   - *Fewer turns* — **built**: `_BATCH` in `dispatcher/project_docs.py`,
     and P1 to P3 of `docs/plans/token-economy.md` *Proposals*.
   - *Cap what a role returns*, the dispatcher's cap on a handoff —
     **built**: `_BUDGET_BYTES` in `dispatcher/handoff.py`.
   - *Cap what a role returns*, a `SubagentStop` hook on a role's own
     subagents — **open**.
   - *Resume or restart* — **open**.
   - *Compaction window* (`autoCompactWindow`) — **open**.
   - *caveman, piece by piece* — **open**. The check its wrap would need is
     D3 in [`docs/ROADMAP.md`](docs/ROADMAP.md) *Deferred gates*.
   - Compaction for a long-lived main thread — **planned**:
     `docs/plans/balancer.md` *Context compaction is the primary thread's
     problem, not a phase's*.
3. **Unattended 24/7 operation.**
   - A long-running loop that picks up ready tasks — **planned**:
     `docs/plans/server-deployment.md` *The order across the plans*, and
     `docs/plans/board-service.md` *Starting development: a button on the
     board*. Refusing a task whose dependency is not merged is **built**
     (`docs/decisions.md` ADR 60).
   - Telling quota-blocked apart from failed — **built**: a held cycle
     exits 75 (`docs/decisions.md` ADR 52), and `_recheck_cooling_accounts`
     in `dispatcher/dispatcher.py` brings an account back. Retrying at the
     CLI's own reset time is **planned**:
     [`docs/ROADMAP.md`](docs/ROADMAP.md) *Deferred gates*, D2.
   - A single-instance guard — **built** as a lock on each account claim
     rather than on the whole run (`docs/decisions.md` ADR 71).
   - A reaper for accounts left `BUSY` — **built**:
     `reap_stale_busy_accounts` in `dispatcher/dispatcher.py`, and
     `release_account` in `dispatcher/operator.py`.
   - An orphan phase after Ctrl+C — **open**. V4.2 in
     [`docs/ROADMAP.md`](docs/ROADMAP.md) only sizes the race.
   - Logging setup — **built**: `main` in `dispatcher/cli.py` configures it.
   - A short "continue where you left off" prompt after a rate limit —
     **open**.
4. **Per-role model selection and richer effort escalation.**
   - Effort only on escalation — **superseded**: `docs/decisions.md`
     ADR 57 sets an effort per role.
   - A per-role model map — **built**: `role_model` and `Config.model_for` in
     `dispatcher/config.py`, `docs/decisions.md` ADR 65 and ADR 66.
   - Escalating on signals other than the round count — **open**.
5. **Task profiles: skill packs per kind of task, not new roles.**
   - The profile mechanism, a project's default profiles, and the pack
     rules — **open**.
   - *`web-frontend` candidates*, playwright-skill — **planned**:
     `docs/plans/browser.md` *Skills: the ways an agent can drive
     Playwright*. Whether a Chromium fits in the agent container is
     **superseded** by `docs/decisions.md` ADR 43.
   - *`web-frontend` candidates*, the other five — **open**.
   - *A designer role? Not as a phase.* — **open**.
   - *Other roles*, Mapper — **built**: `MAPPER_ROLE` in
     `dispatcher/project_docs.py`.
   - *Other roles*, Tester/QA — **planned**: `docs/plans/skills-adoption.md`
     *2. An E2E test skill and gate*.
   - *Other roles*, Documenter — **built**, as the rule it defends: the
     auditor is the single writer of the indexes
     (`dispatcher/project_docs.py`).
   - *Other roles*, Integrator — **planned**: `docs/plans/review.md` *6. A
     conflict is caught early, and resolved by the integrador*.
   - *Other roles*, Epic decomposition — **built**: `docs/decisions.md`
     ADR 60, `docs/plans/task-split.md`.
   - *Other roles*, a security reviewer pack, and bug fixing, infra,
     performance and migrations as profiles — **open**.
6. **Observability and hardening.**
   - Auth on the collector's `/events` — **built**: `docs/decisions.md`
     ADR 72, a read token for the operator and a write token per agent.
   - Redacting hook payloads — **open**.
   - A salted password digest — **planned**:
     `docs/plans/server-deployment.md` *1. Sign-in is a real login, with
     sessions, behind HTTPS*. The constant-time compare is **built**
     (`observability/auth.py`).
   - The collector and the read API run Flask's development server —
     **open**.
   - A per-task view — **planned**: Phase 5 in `docs/plans/board.md`
     *Phases*. The phase rows and the usage log are **built**
     (`docs/decisions.md` ADR 27 and ADR 54).
   - A socket proxy for the dispatcher's `docker.sock` — **open**.
7. **Code-inspection/code-intelligence tooling in agent containers.**
   **Open**, whole. Its checks are D4 and D7 in
   [`docs/ROADMAP.md`](docs/ROADMAP.md) *Deferred gates*.
8. **Mid-phase context-window compaction.** **Open**, whole.
9. **Parallel/load-balanced dispatch across accounts.** **Planned**:
   `docs/plans/projects.md` *5. Parallel work: across projects now, within a
   project later*, and [`docs/ROADMAP.md`](docs/ROADMAP.md) *Stage 2 —
   Prioritized future work*.
10. **Multi-provider agent containers.** **Open**, whole. A different route
    to other models, through the same CLI, is planned in
    `docs/plans/omniroute.md`.
