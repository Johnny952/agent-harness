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
