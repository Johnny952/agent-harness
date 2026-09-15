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
sidecars) and, per account, an existing Claude Pro OAuth login.

1. Copy the config template and adjust values for your deployment:
   ```bash
   cp config.example.yaml config.yaml
   ```
2. Create the shared and per-account credential volumes:
   ```bash
   scripts/setup_volumes.sh cuenta1 cuenta2
   ```
3. Bring up the control plane (Vibe Kanban, collector, dashboard, registry
   mirror) — this also creates the shared `ia_harness_net` network that the
   agents' compose file joins:
   ```bash
   DASHBOARD_USERNAME=... DASHBOARD_PASSWORD_HASH=... \
     docker compose -f docker/compose/docker-compose.yml up -d
   ```
4. Bring up the agent + dind-sidecar pairs:
   ```bash
   docker compose -f docker/compose/docker-compose.agents.yml up -d
   ```
5. Bootstrap a project directory on an account's container, then run a task
   through the full role cycle:
   ```bash
   python -m dispatcher.cli --config config.yaml bootstrap-project \
     --account cuenta1 --project my-project
   python -m dispatcher.cli --config config.yaml run-task \
     --task-id T-001 --project my-project
   ```
   `bootstrap-project` only creates the directory; cloning the actual project
   repository into it is still a manual, one-time step.

Additional accounts are added by duplicating an `agent-*`/`dind-*` pair in
`docker/compose/docker-compose.agents.yml` and the matching entry in
`config.yaml`'s `accounts` list — no dispatcher code changes required.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```
