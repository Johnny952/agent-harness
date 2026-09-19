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
Vibe Kanban (control UI, loopback-only)
        │
        ▼
Smart Dispatcher  ──reads/writes──  per-account state (disk-persisted)
        │                            IDLE / BUSY / PRE_COOLDOWN / COOLING_DOWN
        │
        ▼ docker exec (claude -p ... --output-format json)
Agent container (per Claude Pro account)
   └── docker:dind sidecar (sysbox-runc, DOCKER_HOST → sidecar)
        └── registry mirror + BuildKit cache (shared across sidecars)
        │
        ▼ hooks → HTTP
Observability collector (SQLite/WAL) → authenticated dashboard (Tailscale)
```

- **Vibe Kanban** — task backlog / control UI (web UI bound to
  `127.0.0.1` only, never exposed off-host); its MCP server runs over
  stdio, not the network.
- **Smart Dispatcher** (`dispatcher/`) — for one task, runs the role sequence
  `arquitecto → implementador → revisor → auditor`. Before each phase it
  picks an IDLE account, probes `/usage` to keep it under
  `quota_threshold_pct`, and rechecks cooling accounts when none are IDLE.
  Account state is a small JSON file per account (`state_dir`), written
  atomically. Rate-limit responses move an account to `COOLING_DOWN` and
  retry on another account, resuming the same Claude session via
  `--resume <session_id>` once one is available again. Each phase runs
  under an in-container `timeout` (`phase_timeout_seconds`, 2 hours by
  default) and counts as failed when it expires.
- **Agent containers** — one per Claude Pro account, each with its own Claude
  Code config home (`CLAUDE_CONFIG_DIR=/root/.claude-account`, backed by that
  account's own `claude_creds_<account>` volume), so `.credentials.json` and
  `.claude.json` never leave that account's volume. Names every account
  shares (session history, skills/agents/commands/plugins, `settings.json`)
  live in the `claude_shared` volume at `/root/.claude` and are symlinked
  into each account's config home by the image entrypoint. Each pairs with
  its own `docker:dind` sidecar (`DOCKER_HOST` pointed at the
  sidecar, `sysbox-runc` runtime, no `--privileged`) so agents can build/run
  containers without touching the host Docker daemon.
- **Context handoff** — `.hive/tasks/<task-id>.md`: YAML frontmatter
  (`status`, `owner`, `depends_on`, `heartbeat`) plus a body that accumulates
  each phase's handoff notes. Used for cold-start role transitions; mid-role
  quota exhaustion instead resumes the same Claude session directly via
  `--resume`. Stale locks (heartbeat older than `heartbeat_ttl_seconds`) are
  reaped at the start of each task cycle; a live lock held by another owner
  is refused, and the task goes `blocked`. A task has one branch,
  `agent/task/<task-id>`: the roles that write to it (arquitecto,
  implementador) share one worktree checked out on it, and the dispatcher
  commits what each of those phases left before the next role runs. The
  reviewing roles (revisor, auditor) get their own checkout, detached at
  that branch's tip and rebuilt every round, so they always read the code
  as it stands rather than a pristine `HEAD`.
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
brings up four **persistent** control-plane services (`vibe-kanban`,
`collector`, `dashboard`, `registry-mirror`) plus, from the second file, one
persistent `agent-<name>`/`dind-<name>` pair per configured account.

The `dispatcher` service is deliberately **not** a persistent service —
its compose entry exists only as a template (`restart: "no"`, placeholder
`--task-id`/`--project`). See step 5.

Stage-0 verification found two problems with the two-liner above. One is
still open: a plain `up -d` will hit the `vibe-kanban` pull failure (the
image can't be pulled) — see Known gaps below. The other is fixed: that
same bare `up -d` used to also fire the `dispatcher` service and run a
task, which is now gated behind `profiles: ["dispatcher"]` in both
compose files.

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

**What "issuing commands from the interface" means today:** Vibe Kanban
(`http://127.0.0.1:9100`, loopback-only) is a task backlog/MCP store —
useful for tracking and for driving it via MCP tools from your own Claude
session (once logged in to its cloud account — see Known gaps) — but it
is *not* wired to the dispatcher. Creating or updating a
task in Vibe Kanban does not make anything run. The dispatcher is a
one-shot CLI, not a daemon watching for new tasks, so `run-task` above
still has to be invoked manually (or from your own automation/cron) per
task-id. There is no push-button "run" in the UI yet.

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

1. `docker/compose/docker-compose.yml` (control plane: `vibe-kanban`,
   `collector`, `dashboard`, `registry-mirror`).
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
covered stack plumbing and the Vibe Kanban MCP surface; CLI contracts
beyond the flag listing (V1.1–V1.3), a first end-to-end task, failure
paths, and cross-account failover needed quota and remain unverified — see
[`docs/ROADMAP.md`](docs/ROADMAP.md) for the full results log and what's
still pending. Their results decide how the known gaps get fixed, and each
prioritized item lists the checks it depends on.

### Known gaps (fix first)

These sit in the core pipeline rather than on top of it: most likely keep
a task from producing a usable result end to end today, and the rest tax
every phase that runs. The unit tests mock Claude Code, Docker, and Vibe
Kanban, so none of them catch these.

- **A finished task goes nowhere.** The work now accumulates on
  `agent/task/<task-id>` (see "Context handoff" above), but nothing merges
  that branch, opens a PR, or deletes the worktrees when the task ends
  `done` — the result sits in `.data/projects/<slug>` for a human to find.
  The half of this gap that kept the revisor and auditor reviewing a
  pristine `HEAD` is fixed; what's left is the ending. Candidate fixes,
  none designed: the dispatcher merging to the default branch after an
  approving auditor verdict, or `gh pr create` from the task branch,
  which needs a remote and a token neither container has today.
- **Vibe Kanban's MCP surface doesn't match `vibe_kanban_client.py`.**
  Verified against `vibe-kanban@0.1.44` (the compose image is unobtainable,
  see below): the server speaks stdio via an `mcp` subcommand, not the
  assumed SSE transport; there is no `list_tasks`/`create_task`/
  `update_task` — the real vocabulary is `list_issues`/`create_issue`/
  `get_issue`/`update_issue`/`delete_issue`, keyed on `issue_id`, not
  `id`; every schema types its id fields `format: "uuid"`, so IDs look
  server-assigned, not caller-minted (not yet confirmed live — no issue
  could be created, see V2.6); and `update_issue.status` is documented as
  a fixed, per-project set of names, not arbitrary strings, though the
  live names and the update-rejection text are still unconfirmed. A cloud
  login at `api.vibekanban.com` appears to gate every project/issue call
  (`list_organizations` returns 401, and `list_projects` needs an
  organization ID that can't be obtained without it), so the description
  round-trip (`get_issue`) couldn't be confirmed live either — re-run once
  credentials exist. `vibe_kanban_client.py` needs a full rewrite, not a
  patch. (V2.1–V2.4, V2.5, V2.6)
- **The Vibe Kanban image can't be pulled.**
  `ghcr.io/bloopai/vibe-kanban:latest` (`docker-compose.yml:10`) is denied
  on an anonymous pull — anonymous GHCR pulls work on this host for other
  images, so this looks like an image-reference defect (private or
  nonexistent image), not a credentials problem. Step 4's plain `up -d`
  above will hit this same pull failure, since Compose pre-pulls every
  named image before starting any of them. Until an image source is
  picked (build from upstream, `npx vibe-kanban@0.1.44`, or a vetted
  community image), bring up the other three control-plane services by
  name instead: `docker compose -f docker/compose/docker-compose.yml
  up -d collector dashboard registry-mirror`. (V0.2 control plane, V0.8)
- **Every phase pays for skills nobody chose.** The CLI syncs each
  account's claude.ai skills into `~/.claude/skills/synced/<uuid>/`, and
  the entrypoint symlinks `skills` in from `claude_shared`, which both
  agents mount — so each container also carries the *other* account's
  synced set. Measured in `agent-cuenta1` on 2026-09-19: two UUID-named
  sets, 20 skills, 13,535 bytes of name and description (~3,400 tokens)
  in the system prompt of every turn of every phase, for `docs`, `docx`,
  `pdf`, `pptx`, `xlsx`, `morning`, `import-memory`, `skill-creator`,
  `chrome-browser`, `computer-use`, `deep-research` and the rest — none
  of which a coding role would ever invoke. It is exactly what item 5
  rules out ("a pack isn't installed by default"), arriving by sync
  instead of by install, and it leaks one account's skill list into the
  other's container. Candidate fixes, none verified: a CLI setting or env
  var that turns skill sync off; dropping `skills` from the entrypoint's
  shared allowlist so each account's sync stays in its own
  `claude_creds_<account>` (per-call delivery in items 1 and 5 leaves the
  shared `skills/` with no other user); or pruning `synced/` at container
  start, which the next sync undoes.
- **Unverified assumptions.** Worth a manual check before building on
  them. The check for each is in [`docs/ROADMAP.md`](docs/ROADMAP.md),
  along with others not listed here (the `/usage` probe under `-p` — V1.3,
  not yet run; dind isolation, the registry mirror and bind mounts — V0.7,
  not yet run, blocked by V0.1 (no `sysbox-runc` on the verification
  host)):
  - Headless permissions: `exec_claude` passes no `--permission-mode` and
    `hooks/install_settings.py` sets no `permissions`, so tools that need
    approval (Edit, Bash) may be denied under `-p`. The agent image also
    runs as root, where Claude Code may refuse to bypass permissions
    outside a declared sandbox. (V1.2)
  - Cross-account `--resume`: the design spec only tested one account.
    (V5.1)
  - Vibe Kanban's MCP surface: confirmed mismatched live against
    `vibe_kanban_client.py` (tool names, transport, V2.1–V2.2); ID and
    status-name behavior is known only from the schemas, not confirmed
    live (V2.3–V2.4),
    and the description round-trip (V2.5) still needs a run past the
    cloud-login gate (V2.6). See the Known-gaps bullet above. (V2.1–V2.6)

### Prioritized

1. **Project memory: role skills, per-project docs, and a pointer-based
   handoff.** Highest leverage for the least code. Every phase is a fresh
   `claude -p` session that starts cold: a role is only a name in the
   prompt, it rediscovers the target repo from scratch, and what it leaves
   the next role is truncated freeform prose. Eight pieces, which pay off
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
     all vendored and trimmed rather than installed:
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
   - *Revive before respawn.* Every role skill gets this rule: to resume
     work delegated to a subagent, first try to revive that subagent by its
     ID (Claude Code's `SendMessage` to the agent ID continues it with its
     context intact), and only spawn a fresh one, briefed from the handoff,
     if the revive fails. A fresh spawn starts cold and re-derives context
     the original already had, which costs quota. Another multi-agent
     setup on the same CLI found that a subagent killed by a rate limit
     revives only by its raw agent ID (not by name, and `ListAgents`
     doesn't list it), and only while its parent session is alive.
     Unverified: whether `claude -p --resume` of the parent, possibly on
     another account, makes its subagents addressable again. If it
     doesn't, the dispatcher can resume the role's previous session for
     the next revision round instead of starting that role cold.
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
     first case for writer roles, but a reviewing role's detached checkout
     is rebuilt every round, so anything it writes there is gone. The
     recorded subagent IDs
     are what let a resumed or later phase attempt the revive above.
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
     Kanban, which in this design is a visibility aid whose MCP surface
     doesn't yet match the dispatcher's client (see Known gaps). The
     flow:
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
        with `VibeKanbanClient.create_task`, which exists but nothing
        calls yet. The card is labeled `debt`, sits in the backlog, and is
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
   role writes survive it. The debt cards still need Vibe Kanban's MCP to
   create tasks, which is unverified. Not designed:
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
     per cooling account, and both parse free text. The CLI (checked in
     2.1.273) defines a `rate_limit_event` stream message whose
     `rate_limit_info` carries `status`, `utilization`, `resetsAt`,
     `rateLimitType`, and `surpassedThreshold`. If
     `--output-format stream-json --verbose` emits it for a Pro account
     (unverified), every phase reports quota as a by-product: no extra CLI
     run, no text parsing, and a machine-readable reset time for item 3's
     retries. `exec_claude` would then read the final `result` message
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
     cheap version of item 8.
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
   - Logging setup: `dispatcher/cli.py` configures no handler, so the
     dispatcher's warnings (a failed Kanban update, a lock held by another
     owner, an unparseable `/usage`) reach stderr only through logging's
     last-resort handler, without timestamps or context.
   - A short "continue where you left off" prompt when resuming after a
     rate limit, instead of re-sending the full role prompt.
4. **Per-role model selection and richer effort escalation.** Running
   opus for all four roles spends the quota fastest. Today `default_model`
   (`config.example.yaml`) applies the same model to every role, and
   effort only escalates once the implementador/revisor loop crosses
   `escalate_effort_after_round`. Two refinements were discussed but not
   implemented: (1) a per-role model override, e.g. opus for arquitecto
   and auditor (planning/judgment roles) and sonnet for implementador
   (execution), possibly also for early revisor rounds, instead of one
   `default_model` for all four; (2) escalating effort (or switching
   model) on signals other than round count, e.g. the revisor repeating
   the same `VERDICT: CHANGES_REQUESTED` complaint, or a role's result
   text coming back suspiciously short. Make the split data-driven first,
   from item 2's per-phase usage records, which show which roles actually
   consume the quota. Not designed: a config schema for per-role model
   overrides (`default_model` becoming a fallback vs. a
   `models: {arquitecto: opus, ...}` map), and how "the
   same complaint" or "suspiciously short" would be detected from
   freeform `result_text` without over-engineering a heuristic that never
   fires as intended.
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
       Whether Vibe Kanban's MCP can create tasks is unverified.
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
