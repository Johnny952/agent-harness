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

- **Vibe Kanban** — task backlog / control UI, runs as a local MCP server
  bound to `127.0.0.1` only (never exposed off-host).
- **Smart Dispatcher** (`dispatcher/`) — for one task, runs the role sequence
  `arquitecto → implementador → revisor → auditor`. Before each phase it
  picks an IDLE account, probes `/usage` to keep it under
  `quota_threshold_pct`, and rechecks cooling accounts when none are IDLE.
  Account state is a small JSON file per account (`state_dir`), written
  atomically. Rate-limit responses move an account to `COOLING_DOWN` and
  retry on another account, resuming the same Claude session via
  `--resume <session_id>` once one is available again.
- **Agent containers** — one per Claude Pro account, each with its own OAuth
  session isolated via a shadowed `/root/.claude/credentials` volume over a
  shared `/root/.claude` volume (`claude_shared` + `claude_creds_<account>`).
  Each pairs with its own `docker:dind` sidecar (`DOCKER_HOST` pointed at the
  sidecar, `sysbox-runc` runtime, no `--privileged`) so agents can build/run
  containers without touching the host Docker daemon.
- **Context handoff** — `.hive/tasks/<task-id>.md`: YAML frontmatter
  (`status`, `owner`, `depends_on`, `heartbeat`) plus a body that accumulates
  each phase's handoff notes. Used for cold-start role transitions; mid-role
  quota exhaustion instead resumes the same Claude session directly via
  `--resume`. Stale locks (heartbeat older than `heartbeat_ttl_seconds`) are
  reaped at the start of each task cycle. Each role works on its own branch,
  `agent/<role>/<task-id>`, in its own git worktree.
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

### 3. Create volumes

```bash
scripts/setup_volumes.sh cuenta1 cuenta2
```

Creates the shared `claude_shared` volume and one `claude_creds_<account>`
volume per account — both declared `external: true` in
`docker-compose.agents.yml`, so they must exist before step 4. (The
per-account `dind_<account>_data` volumes are *not* external; Compose
creates those itself.)

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
> `.data/projects` (clones, commits, build output) will show up root-owned on
> the host. `ls -la .data/projects` and `sudo chown -R` are your friends if
> you need to touch those files as your own user.

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

### 5. Run a task

Bootstrap a project directory on an account's container, then run a task
through the full role cycle:

```bash
python -m dispatcher.cli --config config.yaml bootstrap-project \
  --account cuenta1 --project my-project
python -m dispatcher.cli --config config.yaml run-task \
  --task-id T-001 --project my-project
```

(Or, without a host Python install, run the same commands inside the
already-built dispatcher image: `docker compose -f docker/compose/docker-compose.yml run --rm dispatcher --config /app/config.yaml run-task --task-id T-001 --project my-project`.)

`bootstrap-project` only creates the directory; cloning the actual project
repository into it is still a manual, one-time step.

**What "issuing commands from the interface" means today:** Vibe Kanban
(`http://127.0.0.1:9100`, loopback-only) is a task backlog/MCP store —
useful for tracking and for driving it via MCP tools from your own Claude
session — but it is *not* wired to the dispatcher. Creating or updating a
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
  `docker exec -it agent-cuenta1 claude login`). No import can do this for
  you.

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
actually taken on, not before:

- **Parallel/load-balanced dispatch across accounts.** The dispatcher
  currently serializes on a single active account (see "Architecture"
  above). A configurable parallel mode is noted as future work in the
  design spec (section 8), with two candidate variants: per-account
  subagents scheduled by remaining quota, or independent task-claiming per
  account with quota-triggered handoff instead of end-of-phase handoff.
- **Multi-provider agent containers.** Everything under `docker/agent/`,
  `dispatcher/docker_exec.py`, and `dispatcher/quota.py` is Claude-specific
  today: the agent image installs only `@anthropic-ai/claude-code`
  (`docker/agent/Dockerfile`), credentials are isolated per Claude Pro
  account via a shadowed `/root/.claude/credentials` volume
  (`claude_creds_<account>`, see `scripts/setup_volumes.sh`), `exec_claude`
  shells out to the `claude` binary with `--output-format json`, and
  `quota.parse_usage_output` parses Claude Code's `/usage` text verbatim.
  Extending this to other AI coding CLIs/accounts (e.g. ChatGPT/Codex CLI,
  Gemini CLI) would need, per provider:
  - A dedicated agent image (or a `provider` build arg) installing that
    CLI instead of/alongside Claude Code.
  - Its own account-scoped credential volume and shadow-mount path,
    mirroring the `claude_creds_<account>` pattern but at that CLI's config
    location (e.g. `~/.codex`, `~/.gemini`) rather than `~/.claude`.
  - A `provider` field on `AccountConfig` (`dispatcher/config.py`), and a
    small provider abstraction behind `docker_exec.exec_claude` so the
    dispatcher can invoke the right binary/flags and parse that CLI's
    session-id/result/usage output instead of assuming Claude Code's JSON
    shape.
  - Confirmation that the target CLI supports a session-resume equivalent
    to `--resume <session_id>` — the context-transfer design (section 5 of
    the spec) leans on that for mid-role quota-exhaustion handoff.
  This is not designed in detail; the bullets above are the seams the
  current Claude-only implementation already has, not a spec.
