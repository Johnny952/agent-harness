# Roadmap

The order in which the work in `README.md` (*Future work*) gets taken on,
and the checks against the real stack that come before it. This file
decides the order only. What each item is, and why, stays in the README
and the design spec
(`docs/superpowers/specs/2026-09-13-ia-harness-design.md`, section 8).

Nothing has run end to end against real containers yet. The unit tests
mock `docker exec`, the Claude Code CLI, and the Vibe Kanban MCP client,
so they only prove the dispatcher's logic given the assumed contracts.
Several of those contracts decide how the known gaps get fixed: the
permission mode, where the task description comes from, and how the
dispatcher commits. So the checks come first:

| Stage | What | Blocks |
|---|---|---|
| 0 | Verify the real flows (V0–V5) | Stages 1 and 2 |
| 1 | Fix the known gaps with V0–V2's answers, then re-run V3 as acceptance | Stage 2 |
| 2 | Prioritized future work, items 1–10, each after its own deferred gates (D1–D6) | — |

V0–V3 block stage 1. V4 and V5 don't depend on the known gaps, so they can
run alongside stage 1, but they must be done before stage 2 starts.

## How to use this file

- Every check has an ID, a cost, how to run it, and what passing means.
  - **no quota:** no model turn.
  - **minimal quota:** one or two trivial prompts ("Reply with exactly: OK").
  - **task quota:** a full `run-task` cycle on a tiny task.
  Ask before anything that isn't **no quota**.
- A failed check isn't something to work around. It's a finding:
  1. Write it in the results log at the end.
  2. Update *Known gaps* or *Unverified assumptions* in the README, and the
     spec if it changes the design.
  3. Record the decision it forces, e.g. which permission flag to use.
- A check that passes removes its assumption from the README's
  *Unverified assumptions* (or from the item that marks it "unverified").
- Work against a throwaway target repo, never a real project.
- Record the CLI version with each result, and — for a check that touches
  the board — what `vibe_kanban.command` resolves to. The board is
  optional, and the surface below was verified against
  `npx vibe-kanban@0.1.44`, not against the compose image.

### Shared setup

Run from the repo root:

```bash
DC="docker compose -f docker/compose/docker-compose.yml"
DA="docker compose -f docker/compose/docker-compose.agents.yml"

# A scratch copy of config.yaml with low limits, kept under the gitignored
# .data/ and mounted next to the real one.
mkdir -p .data/verify && cp config.yaml .data/verify/config.yaml
dispatch() {
  $DC run --rm -v "$PWD/.data/verify/config.yaml:/app/verify.yaml:ro" \
    dispatcher --config /app/verify.yaml "$@"
}

# node:20-slim has no `ps`, and `docker top` shows host PIDs.
procs() {
  docker exec "$1" sh -c 'for p in /proc/[0-9]*; do
    c=$(tr "\0" " " < "$p/cmdline" 2>/dev/null); [ -n "$c" ] && echo "${p#/proc/} $c"
  done'
}
```

The throwaway target is a TS/JS-style repo, like real target projects.
It's created on the host as your user, which is how README step 5 leaves a
manually cloned repo:

```bash
mkdir -p .data/projects/scratch && cd .data/projects/scratch
git init -q && npm init -y >/dev/null
cat > sum.js <<'EOF'
function sum(a, b) { return a + b; }
module.exports = { sum };
EOF
cat > sum.test.js <<'EOF'
const test = require('node:test');
const assert = require('node:assert');
const { sum } = require('./sum');
test('sum', () => assert.strictEqual(sum(2, 3), 5));
EOF
git add -A && git -c user.name=verify -c user.email=verify@local commit -qm init
cd -
```

## Stage 0 — Verify the real flows

### V0 — Stack and plumbing (no quota)

**V0.1 Prerequisites.**
- Run: `docker info --format '{{json .Runtimes}}'`, then `docker compose version`.
- Pass: `sysbox-runc` is listed.

**V0.2 Build and start.**
- Run README steps 1–4:
  1. `scripts/configure.sh`
  2. Both `docker build` commands.
  3. `scripts/setup_volumes.sh cuenta1 cuenta2`
  4. `$DC up -d`, then `$DA up -d`.
- Pass:
  - `$DC ps` and `$DA ps` show every persistent service running.
  - `docker logs` for each container shows no crash loop.
  - `docker exec agent-cuenta1 cat /root/.claude/settings.json` shows the
    `emit_event.py` hook registered.

**V0.3 Invocation mode.** Use the dispatcher container (`dispatch` above) for
every check.
- README step 5 also shows a host run, `python -m dispatcher.cli --config
  config.yaml`, but that can't work with the shipped config:
  - `state_dir: /state` and `hive_tasks_dir: /data/.hive/tasks` are
    container paths.
  - `collector` only resolves on `ia_harness_net`. (The board doesn't
    count any more: its MCP server is a subprocess, not a host there.)
  - The prompt's `task_file` is built from `hive_tasks_dir`, so it has to
    be the path the agent sees. A host path in the config would break the
    agents.
- This is already a finding (stage 1 fix). No run needed.

**V0.4 Login and credential isolation.** The highest-risk assumption.
Failover between accounts means nothing if both containers use the same
login.
- Run:
  1. Log in on each account: `docker exec -it agent-cuentaN claude`, then
     `/login`.
  2. Find where the credentials landed:
     `docker exec agent-cuentaN sh -c 'ls -la "$CLAUDE_CONFIG_DIR" /root/.claude'`.
  3. In each container, check which account `/status` reports (an
     interactive session; no model turn).
- Pass:
  - `.credentials.json` and `.claude.json` sit under `$CLAUDE_CONFIG_DIR`
    (`/root/.claude-account`, that account's own `claude_creds_<account>`
    volume) — never in `/root/.claude` (`claude_shared`).
  - In `$CLAUDE_CONFIG_DIR`, each shared name is a symlink to
    `/root/.claude/<name>` (`projects`, `todos`, `file-history`,
    `session-env`, `plans`, `skills`, `agents`, `commands`, `plugins`,
    `output-styles`, `settings.json`, `CLAUDE.md`); `/root/.claude`
    (`claude_shared`) holds the real shared entries and no
    `.credentials.json`, `.claude.json` or `backups/`.
  - `docker logs agent-cuentaN 2>&1 | grep -c 'still in the shared claude_shared volume'`
    prints `0` (no leftover warning).
  - `/status` reports a different account in each container.
  - After login, record whether `/root/.claude/.device-keys.json` exists
    (`ls -la /root/.claude`, names only).
- History: first run (2026-09-17, see Results log below) failed. The
  per-account `claude_creds_<account>` volume was mounted at
  `/root/.claude/credentials` but stayed empty: Claude Code writes
  `.credentials.json` at the root of its config home (`/root/.claude`,
  i.e. `claude_shared`), never into a `credentials/` sub-directory, and it
  kept `.claude.json` at `/root/.claude.json`, in the container layer.
  Fixed in stage 1 by giving each agent its own
  `CLAUDE_CONFIG_DIR=/root/.claude-account`, backed by that account's
  `claude_creds_<account>` volume, with the shared names symlinked back in
  by the entrypoint. Re-run against the fix on 2026-09-19: PASS (see
  Results log below).
- Persistence:
  1. Run `$DA up -d --force-recreate --no-deps agent-cuenta1 agent-cuenta2`.
  2. Repeat the `/status` step.
  3. Pass: no re-login and no onboarding prompt.
  - This matters because `.claude.json` now lives at
    `$CLAUDE_CONFIG_DIR/.claude.json`, on that account's
    `claude_creds_<account>` volume, so it persists across a recreate
    (before the fix it was `/root/.claude.json`, in the container layer,
    and was lost on recreate).

**V0.5 Hooks reach the collector.**
- Run:
  ```bash
  echo '{"hook_event_name":"VerifyProbe","session_id":"verify"}' \
    | docker exec -i agent-cuenta1 python3 /usr/local/lib/ia-harness/emit_event.py
  curl -s '127.0.0.1:8787/events?source_app=agent-cuenta1&limit=5'
  ```
- Pass: the probe event is listed with `source_app` `agent-cuenta1`.
- Events from a real session are checked in V1.1.

**V0.6b Board auth, and the board over the api.**
- A new id rather than a re-use of `V0.6`: that id belongs to the events
  dashboard's auth on 8788, which T-010 deleted, so the
  `2026-09-16 | V0.6 | PASS` row in the results log is that superseded check
  and says nothing about the board. This one has never been run.
- The port moved: T-010 deleted the events dashboard on 8788 and put the board
  on 8790, which reads the api on 8789. The credentials are the same two
  `DASHBOARD_*` variables, which kept their names.
- Run:
  0. `docker/compose/.env` predates the board, so it needs two keys added before
     anything comes up: `API_TOKEN` (any `python3 -c 'import secrets;
     print(secrets.token_urlsafe(32))'` value) and `BOARD_PROJECT=ia-harness`.
     Without the first, `observability/board/app.py` sends no `Authorization`
     header and the api 401s every region; without the second, `/debt` gets the
     api's `400 project is required`.
  0b. `docker compose build api board` -- **both** images, not just the board.
     The api gained its bearer path in T-010 too, so a host whose
     `compose-api:latest` predates that merge answers 401 to a correct token,
     which reads exactly like a credential problem and is not one. Then
     `docker compose -f docker/compose/docker-compose.yml up -d --no-deps
     --force-recreate api board`.
  1. `curl -si 127.0.0.1:8790/ | head -1`
  2. The same request with `-u <user>:<password>`.
  3. With the credentials, check the page came from the api rather than from a
     browser fetch: `curl -s -u <user>:<password> 127.0.0.1:8790/ | grep -c
     '<script'` is `0`, and the accounts and tasks tables have rows.
  4. The other two screens, with the credentials: `127.0.0.1:8790/events` and
     `127.0.0.1:8790/debt`. `/debt` is the one that needs `BOARD_PROJECT` set
     when the dispatcher's `projects_root` holds more than one checkout — this
     host holds `ia-harness` and `scratch` — so run it with the variable set,
     and once without it if you want to see the api's 400 for yourself.
  5. `docker stop compose-api-1`, reload, then `docker start compose-api-1`.
     Give the restart a second before reloading again: docker's embedded DNS
     re-registers the container after it starts, so a reload inside that window
     still shows the same `no answer` region and is not a failure.
- Pass: 401 without credentials, 200 with them; both accounts visible with
  their state and the newest task with its card; `/events` lists rows and
  `/debt` lists this project's entries rather than showing the api's
  `400 project is required`; with the api stopped, each region shows
  `GET /api/… → no answer` and the page keeps its stamp, its nav and its other
  regions rather than 500-ing.

**V0.6c The console's bearer forward, and the five reads behind it.**
- A new id beside V0.6b rather than a change to it: V0.6b checks
  `observability/board/`, which C-8 keeps until the console reaches parity, and
  both services read the same api with the same token. This one has never been
  run, **and no phase can run it**: `front/node_modules` does not exist and
  `bun install` is not authorised for a role, so there is no way to start the
  console from inside a phase — and `/data/.hive/learnings/inbox/T-010-docker-is-refused-in-a-phase.md`
  says the container half is a human's row too. It is a recipe with no results
  row on purpose.
- There is no compose service for the console yet. It runs on the host, against
  the api on 8789, which is why step 1 points `API_BASE_URL` at `127.0.0.1`
  rather than at the compose network name.
- Run:
  0. The same `API_TOKEN` V0.6b added to `docker/compose/.env`, and the api up:
     `docker compose -f docker/compose/docker-compose.yml up -d api`.
  1. `cd front && bun install`, then start it with the three names — none of them
     `VITE_`-prefixed:
     `API_TOKEN=<the token> API_BASE_URL=http://127.0.0.1:8789 CONSOLE_PROJECT=ia-harness bun run dev`.
  1b. Then once **without** `API_TOKEN`, to see the refusal: the console must fail
     rather than start and answer 401s from a screen.
  2. `bun run build`, then `grep -ro "<the token>" dist .output 2>/dev/null` and
     `grep -rc API_TOKEN dist .output 2>/dev/null` over whatever the client bundle
     directory turns out to be. The token must not appear in anything a browser
     downloads.
  3. The four screens in a browser: `/` (board), a task from it, `/pool`, `/debt`.
     Each one lists rows from the real harness, and the Board and the task detail
     each show an empty state naming `/api/phases` — and the detail screen one for
     `/api/learnings` — rather than a fixture.
  4. `/tail`, for two minutes. Watch the `id` column.
  5. With the console still up, `docker stop compose-api-1`, reload a screen, then
     `docker start compose-api-1` and reload again.
  6. `curl -si 127.0.0.1:3000/api/phases` and `curl -si -X POST
     127.0.0.1:3000/api/tasks` (whatever port `bun run dev` prints).
- Pass:
  - Step 1b fails at startup with the `API_TOKEN is not set` message, not with a
    screen showing 401.
  - Step 2 finds the token nowhere in the built client bundle.
  - Step 3's rows are this harness's — the same task ids and accounts V0.6b sees on
    the board — and no screen shows a fixture's `cuenta6` or a `D-1NN` debt id.
    Where the api sends a warning, the screen shows it in a banner **above the
    rows, with the rows still there**.
  - Step 4's ids go **up**. A tail whose newest id stops changing while the harness
    is busy is the cursor walking backwards, which is what
    `docs/decisions.md` ADR 22 and the reverse in `client.ts` exist to prevent.
  - Step 5 shows an error state naming what failed, never an empty state, and
    recovers on the second reload.
  - Step 6 answers JSON `404` and JSON `405` respectively, from the console and not
    from the api: the forward is a whitelist, not a proxy (ADR 24).

**V0.6d The phase timeline, and the Board's banner after `/api/phases`.**
- A new id beside V0.6c rather than a change to it: V0.6c covers the five reads,
  has never been run and carries no results row, so it stays exactly as written —
  including its steps 3 and 6, which describe the console *before* `/api/phases`
  landed and are a true record of what T-012 shipped. This one checks the delta
  T-013 added, and it inherits V0.6c's reason for having **no results row**: no
  phase can run it, because `front/node_modules` does not exist and `bun install`
  is not authorised for a role.
- Setup is V0.6c's steps 0 and 1 unchanged — the api up, the console started on
  the host with the three un-prefixed names.
- Run:
  1. A task with phase records in a browser: `/tasks/T-011` and `/tasks/T-012`.
     On 2026-10-03 three of the thirteen task ids under `.hive/tasks/` hold a
     `handoffs/` directory — those two and `T-013`, whose own cycle was still
     writing into it.
  2. A task older than the record: any task id whose scratch dir has no
     `handoffs/` — ten of the thirteen on 2026-10-03.
  3. `/` (the board), reading the banner above the columns.
  4. Through the console, not the api: `curl -si
     '127.0.0.1:3000/api/phases?task_id=T-011'`, then `?task_id=a/b`, then
     `curl -si 127.0.0.1:3000/api/learnings` (whatever port `bun run dev` prints).
- Pass:
  - Step 1's timeline lists one row per role that has finished — four for `T-011`
    and two for `T-012` on 2026-10-03 — in cycle order, each labelled **ended**
    with an absolute
    time and a ticking age, and carrying that role's own lists. No byte bar, no
    gate chips, no account, no model and no commit: `docs/decisions.md` ADR 27
    says the harness records none of them per phase.
  - Step 2 shows the empty state about the *harness* — no phase of this task has
    left a handoff — and **not** one naming a route, because the route answered.
  - Step 3's banner names the missing *record*: which role is running is written
    down nowhere. It must not name `/api/phases` as something being waited for,
    and In progress must still be one column (ADR 28).
  - Step 4 answers `200` with `{"data": […], "warnings": []}`, then `400` from the
    api naming `task_id`, then a JSON `404` from the console — `/api/learnings` is
    not one of the six routes the forward carries.
  - The detail screen's learnings region still names `/api/learnings`. Parity is
    not reached and this check does not claim it.

**V0.7 Docker-in-docker.**
- Run:
  - `docker exec agent-cuenta1 docker info`
  - `docker exec agent-cuenta1 docker run --rm hello-world`
  - `curl -s 127.0.0.1:5000/v2/_catalog`
  - `docker exec agent-cuenta2 docker images`
  - `docker exec agent-cuenta1 docker run --rm -v /data/projects:/p alpine ls /p`
- Pass:
  - The agent reaches its own sidecar.
  - The pull goes through the mirror: the catalog lists `library/hello-world`.
  - cuenta2's sidecar doesn't see cuenta1's image.
- Expected failure: the last command lists nothing. The dind sidecars
  don't mount `.data/projects`, so a bind mount from the agent's path is
  empty in dind. Target projects whose tests use `docker compose` or
  testcontainers with bind mounts would break.
  - Record it, and add it to the README *Known gaps* if it's confirmed.

**V0.8 The board's web UI comes up.** Only if you want one. The service
sits behind the `kanban` profile and nothing in the harness dials it: the
dispatcher reaches a board over stdio instead (V2), so this is a check on
the UI an operator looks at, not on the dispatcher's path to the board.
- Run:
  - `$DC --profile kanban up -d vibe-kanban`
  - `curl -si 127.0.0.1:9100/ | head -1`
  - `$DC logs vibe-kanban | tail`
- Pass: the UI answers on 9100, and `docker ps` shows the port published
  on `127.0.0.1` only.
- `ghcr.io/bloopai/vibe-kanban:latest` is denied on an anonymous pull, so
  this needs an `image:` you can actually pull first.

**V0.9 Target repo, git, and worktrees.**
- Run:
  ```bash
  dispatch bootstrap-project --account cuenta1 --project bootstrap-probe
  ls -la .data/projects/                       # bootstrap-probe exists, root-owned
  docker exec -w /data/projects/scratch agent-cuenta1 git status
  docker exec -w /data/projects/scratch agent-cuenta1 \
    git worktree add -b verify/probe /data/projects/scratch/worktrees/probe/x
  docker exec agent-cuenta1 git config --global --get user.name
  docker exec -w /data/projects/scratch/worktrees/probe/x agent-cuenta1 \
    sh -c 'echo x > probe.txt && git add probe.txt && git commit -m probe'
  ```
- Pass: `git status` and `git worktree add` work as root in the container
  on the host-owned repo.
- Expected findings:
  - "detected dubious ownership" (candidate fix: `safe.directory` in the
    image).
  - No git identity, so the commit fails. The stage 1 fix for "roles don't
    see each other's code" commits from the dispatcher, so it needs one.
    Set the identity in the image or entrypoint, or pass `-c user.name=…`
    on each commit. `/root/.gitconfig` isn't persisted.
- Both findings reproduced as expected, and both are fixed as of
  2026-09-19: the agent image's system gitconfig carries `safe.directory=*`
  and a fallback identity (see the stage-1 decisions below). Re-running the
  probe above on a rebuilt image needs no `-c safe.directory` workaround.
- Clean up:
  ```bash
  docker exec -w /data/projects/scratch agent-cuenta1 git worktree remove --force worktrees/probe/x
  docker exec -w /data/projects/scratch agent-cuenta1 git branch -D verify/probe
  ```

**V0.10 In-container timeout.**
- Run:
  ```bash
  docker exec agent-cuenta1 timeout --kill-after=30 5 sleep 100; echo $?
  docker exec agent-cuenta1 timeout --kill-after=2 2 sh -c 'trap "" TERM; sleep 100'; echo $?
  ```
- Pass: 124, then 137. These are the two exit codes `exec_claude` maps to
  "timed out".

### V1 — Claude Code CLI contracts (minimal quota)

Check these first, with no quota:
- `docker exec agent-cuenta1 claude --version` shows `2.1.273`.
- `claude --help` lists `--resume`, `--model`, `--effort`, and
  `--output-format`.

`exec_claude` passes `--effort` from round `escalate_effort_after_round + 1`
on. An unknown flag there would fail every escalated round.

**V1.1 JSON result shape.**
- Run:
  ```bash
  docker exec -w /data/projects/scratch agent-cuenta1 \
    claude --model opus -p "Reply with exactly: OK" --output-format json
  ```
- Pass: one JSON object with `session_id`, `result`, and `is_error: false`.
- Record the full key set, the `usage` breakdown, `total_cost_usd`,
  `num_turns`, `duration_ms`, and `permission_denials`. Item 2's
  per-phase usage records and item 4's model split build on them.
  (`is_error` and `api_error_status` were checked against 2.1.273 when
  `is_rate_limit_error` was written.)
- Also pass: `curl -s '127.0.0.1:8787/events?source_app=agent-cuenta1'`
  shows `SessionStart`, `UserPromptSubmit`, and `Stop` events carrying the
  same `session_id`. Session ID is the only way to tie events to a phase.

**V1.2 Headless permissions as root.**
- Run:
  ```bash
  docker exec -w /data/projects/scratch agent-cuenta1 claude -p \
    "Create probe.txt containing hello, then run 'node --test' with Bash and report the result." \
    --output-format json
  docker exec agent-cuenta1 cat /data/projects/scratch/probe.txt
  ```
- Pass:
  - The file exists and the test ran.
  - `permission_denials` is empty.
- If denied, try in order and record the exact messages:
  1. `--permission-mode acceptEdits` with `--allowedTools "Bash"`.
  2. `permissions` in `settings.json` (via `install_settings.py`).
  3. `--dangerously-skip-permissions`. It may be refused as root; if so,
     record what it asks for.
- Decision: the flag or setting that the stage 1 fix adds to
  `exec_claude` or the image.
- Clean up: `git -C .data/projects/scratch clean -fd`, with `sudo` if the
  file is root-owned.

**V1.3 `/usage` in headless mode.**
- Run: `docker exec agent-cuenta1 claude -p "/usage" --output-format json`
- Pass:
  - `result` holds the "Current session: N% used · resets …" and
    "Current week …" lines. `dispatcher/quota.py`'s regexes parse them;
    check with `.venv/bin/python -c "from dispatcher.quota import parse_usage_output; print(parse_usage_output('''<result>'''))"`.
  - `num_turns` is 0 and `total_cost_usd` is 0, meaning no model turn.
- Expected failure (hypothesis): `/usage` isn't handled under `-p`. It
  goes to the model as plain text, so every dispatch pays a model turn
  for a probe that then fails open, and proactive cooldown never fires.
  - Decision: drop the probe until item 2's `rate_limit_event` (D2)
    replaces it, or keep it with its cost documented.

### V2 — Vibe Kanban MCP surface (no quota)

`dispatcher/vibe_kanban_client.py` now speaks the surface V2.1-V2.2
verified: stdio over the server's `mcp` subcommand, and the issue
vocabulary (`list_issues`, `create_issue`, `get_issue`, `update_issue`,
`delete_issue`) keyed on a server-assigned `issue_id`. V2.3-V2.6 were
re-run on 2026-09-25 and are closed: **not verifiable, and not worth
verifying again.** `api.vibekanban.com` serves the same SPA shell on
`/api/organizations` and on `/health` as on `/`, so the 401 is what is
left of the service and not a gate credentials would open; `create_issue`
fails on a `project_id` that cannot be obtained, because projects are the
feature PR #3387 retired in 0.1.44, and the 0.1.45 build said to bring
them back was unpublished two hours after release. What follows is kept
as the record of what was measured, not as work waiting on an operator —
`docs/plans/board.md` replaces the dependency with a local client.

Probe the real server from the host venv. It is spawned, not dialled;
there is no URL:

```bash
.venv/bin/python - <<'EOF'
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    server = StdioServerParameters(
        command="npx", args=["-y", "vibe-kanban@0.1.44", "mcp"]
    )
    async with stdio_client(server) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for t in (await s.list_tools()).tools:
                print(t.name, t.inputSchema)

asyncio.run(main())
EOF
```

- **V2.1 Transport.** Settled. `/sse` answers `text/html` — no MCP route is
  served there — and `/mcp` errors; the `mcp` subcommand over stdio
  initializes. The client spawns one subprocess per call. Whatever runs
  that command needs Node, which the dispatcher image doesn't have.
- **V2.2 Tool and argument names.** Settled. No `list_tasks`/`create_task`/
  `update_task` exists, and no "task" vocabulary at all; the client was
  rewritten onto the issue tools. Of the 33 tools, it calls four, so the
  agent-launching ones (`start_workspace`, `create_session`, ...) never
  come up.
- **V2.3 IDs.** Closed, schema only. Every id is `format: "uuid"`, so the
  server assigns them and `create_issue` takes no caller-chosen id. The
  client keeps what comes back, in the task file's `kanban_issue_id`;
  `run-task --kanban-issue-id` attaches an issue that already exists.
  Which field a create reply carries the id in was never observable —
  no create ever succeeded — and the parser still takes several. The uuid
  shape is the one part of this that outlived the dependency:
  `dispatcher/cli.py` rejects a `--kanban-issue-id` that does not parse as
  a uuid, so the local client of `docs/plans/board.md` mints uuid4 rather
  than short handles, and a readable id stays a display concern.
- **V2.4 Status values.** Closed, schema only. `update_issue.status` "must
  match a project status name", and no tool lists a project's names —
  `list_projects` needs an `organization_id` that `list_organizations`
  (401) cannot supply. `status_map` in `config.yaml` is the guess
  (`In Progress`/`In Review`/`Done`) and is now inert, since no run
  reaches a remote board. It stays as the shape of the mapping: the local
  client owns its status names, so the guess becomes a choice.
- **V2.5 Description round-trip.**
  - Question: can an issue's description be read back (`get_issue`)?
  - Closed, not verifiable: no issue ever existed to read, and after the
    2026-09-25 re-run none can be made to exist. `description` is settable
    on `create_issue`, which implies it comes back, but nothing confirms
    it and nothing now will.
  - This never gated stage 1's "agents never see the task" fix —
    `run-task` takes `--description`/`--description-file` as of
    2026-09-19. Reading a description back is still worth having, and is
    verifiable against local storage: Phase 0 requires the round trip as
    one of its tests, which is the check this row could not run.
- **V2.6 Issue creation.** FAIL, and the reason the three above are
  unverifiable: `create_issue` needs a `project_id`, a project sits under
  an organization, and `list_organizations` returns 401. The 2026-09-25
  re-run settled that this is not a login wall — `api.vibekanban.com`
  answers the same SPA shell on every path that would have to be JSON,
  and projects are the feature PR #3387 sunset in 0.1.44.
  - `create_issue` has a real caller — `_file_accepted_debt` opens one
    card per accepted debt declaration, before the auditor writes the row
    that points at it — so until something answers it, `NullKanbanClient`
    is the path every run takes and the entries are filed without cards.
  - This no longer waits on an operator. Item 1's debt cards and item 5's
    epic decomposition now depend on Phase 0 of `docs/plans/board.md`,
    which gives `create_issue` a local implementation. That phase
    shipped as `T-008` on 2026-09-26, so the dependency is satisfied:
    `LocalBoardClient` in `dispatcher/vibe_kanban_client.py` answers
    all four methods. The two things this row wanted to check while a
    scratch issue existed carry over to it:
    that the `[debt] ` title prefix survives a round trip, since
    `create_issue` takes no label argument and the prefix is the label,
    and which field the reply carries the id in, because that id is what
    the index row points at and what `merge-task` closes.

### V3 — End to end on one account (task quota)

The first real `run-task`, with costs capped.

- **Config** (`.data/verify/config.yaml`):
  - `accounts`: `cuenta1` only.
  - `max_revision_rounds: 1`.
  - `escalate_effort_after_round: 0`, so round 1 already passes
    `--effort high`.
  - `phase_timeout_seconds: 1800`.
  - `mapping_enabled` left at its default `false`, so this check sizes
    the four working roles and nothing else. The mapping phase is a
    separate run, against a project with no `docs/README.md`, and it is
    the one phase billed for writing no code.
  - `gates_enabled` left at its default `true`. The gates cost no quota,
    so there is nothing to cap here, but with `max_revision_rounds: 1` a
    red suite ends the task `blocked` without the revisor ever being
    called — which is the point, and is what to expect if the
    implementador leaves the scratch project failing.
  - The learnings inbox needs no key and costs no quota: its directories
    hang off `.hive/`, beside `tasks/`, and the dispatcher opens them
    itself at the start of the cycle. A fresh `.data/` therefore starts
    this check with an empty store, which is the case worth measuring
    first — every role is told to grep the traps before debugging, and an
    empty grep should cost a phase one command, not a detour.
- **Task.** Passed on the command line now that `--description` exists;
  no hand-seeded task file. No card is created alongside it: V2.6 closed
  FAIL, so `NullKanbanClient` is the path this run takes, and the card
  half came back with Phase 0 of `docs/plans/board.md`, shipped as
  `T-008`; a re-run configured against `local_board` would create one.
- **Run:**
  ```bash
  dispatch run-task --task-id T-001 --project scratch --description "Add a
  subtract(a, b) function to sum.js, export it, and cover it with a test in
  sum.test.js. Run node --test before finishing."
  ```
- **While it runs:**
  - `procs agent-cuenta1` shows `timeout --kill-after=30 1800 claude
    --model opus …`. From the implementador on, it also shows
    `--effort high`.
  - `cat .hive/tasks/T-001.md` shows `owner: cuenta1` with a `heartbeat`
    that moves about every 30 s.
  - `cat dispatcher_state/cuenta1.json` shows `BUSY`.
- **Pass:**
  - Each role has its worktree and branch:
    `git -C .data/projects/scratch worktree list`, then `branch -a`.
  - The task file gains `## arquitecto`, `## implementador (round 1)`, and
    `## revisor (round 1)` sections, with the owner and heartbeat cleared
    between phases.
  - With a `vibe_kanban` block configured, the issue moves to the mapped
    status per phase and ends on the `done` or `blocked` one; otherwise
    stderr carries a warning per failed update. With no block, the run
    says nothing about a board at all.
  - The revisor's `## revisor` section opens with `**Status:** … ·
    **Verdict:** APPROVED` (or `CHANGES_REQUESTED`) — the field the
    dispatcher read — and the outcome matches it. A revisor that answered in
    prose instead falls back to its last `VERDICT:` line; note which path the
    run took.
  - The project's `docs/` gains what the duties in each role's prompt
    ask for: an ADR appended to `docs/decisions.md` if the task decided
    anything, `docs/implementations/T-001.md` from the implementador,
    and indexes written by the auditor alone, each row carrying its
    trigger. Record every phase that wrote none. The gates enforce the
    narrow end of this — a contract file moved with no `.md` beside it,
    a pointer under `docs/` that lands nowhere — and the rest stays
    prompt text, so for the ADRs, the implementation doc and the
    indexes this still measures whether the model obeys, not whether
    the dispatcher enforces.
  - The gates ran between the implementador and the revisor. The scratch
    project has no `docs/README.md`, so the test gate is skipped and says
    so in the log (``gates: no `test:` in docs/README.md``);
    `sum.test.js` should satisfy the tests-in-diff gate; and with no
    `docs/` there are no contracts and no pointers to break. So the
    expected result is a task file with **no** dispatcher-gates section
    under the implementador's handoff, and exactly one `claude` call for
    that role. Record it if either comes out otherwise: a gate section
    names which gate fired, and a second implementador call is the one
    `--resume` an `ask` buys. The test gate itself is only exercised
    against a mapped project — see the re-run list below.
  - `.hive/learnings/inbox/` and `.hive/learnings/harness/` exist before
    the first phase runs, opened by the dispatcher and by no model.
    Record which phases wrote an entry, if any: this task is small enough
    that none may, and "nobody filed anything" is the expected result
    rather than a failure. If one did, it is a single Markdown file named
    `T-001-<slug>.md` carrying the error verbatim in its `## Symptom`
    block, and the auditor should have copied what it says into
    `docs/learnings/` on the task branch. Under the default
    `merge_on_done: false` nothing then deletes it — the inbox is only
    emptied by a merge — so after the run the file is still there, stamped
    `carried_by: T-001`.
  - The debt flow leaves its mark in three places, or in none at all.
    A task this small may declare nothing, and "no debt declared" is the
    expected result rather than a miss: the implementador's handoff
    carries an empty `debt` list, and the auditor is told nothing about
    it. If it did declare something, record all three: the revisor's
    handoff carries one ruling per declaration, the dispatcher's log
    names the id it assigned (`T-001-D1`) before the auditor ran, and the
    task branch gains that row in `docs/debt/README.md` plus its entry
    file under `docs/debt/`, with the row's `where` written as a
    condition rather than a topic. With no `vibe_kanban` block the card
    half is a no-op by design: the entries are filed identically, the
    auditor is told "no card (this project has no board)", and nothing on
    stderr mentions a board. The two rulings that change the outcome are
    worth recording verbatim if they happen — `rejected` sends the task
    round again and counts against `max_revision_rounds`, which at 1 ends
    it `blocked`, and `blocks` ends it `blocked` with no further round at
    all.
  - `cuenta1.json` goes back to `IDLE`.
  - Collector events for the run's session IDs are there.
- **Expected failure to record:** "roles don't see each other's code".
  The implementador's change stays uncommitted in its own worktree, and
  the revisor's worktree is a clean `HEAD`. Record what the revisor
  actually did (asked for changes, or went looking in `../implementador`)
  and the verdict it gave.

### V4 — Failure paths (mostly no quota)

- **V4.1 Phase timeout and orphans** (minimal quota).
  - Run: V3 again with `phase_timeout_seconds: 20` and a fresh task ID.
  - Pass:
    - The phase fails with "claude timed out after 20s".
    - The task file shows `blocked` (and the board's issue too, if one is
      configured), the account is `IDLE`, and the lock is released.
    - Once the kill grace has passed, `procs agent-cuenta1` shows no
      leftover `claude`, `node`, or shell children. Children that `setsid`
      out of the process group would survive: record them.
    - Any inbox entry the killed phase had filed is still on disk, with
      its `carried_by` stamp replaced by `orphaned_from: <task-id>`: a
      task that dies does not take what it learned down with it, and the
      next task is handed the entry as a claim nobody has reproduced.
- **V4.2 Ctrl+C** (minimal quota). Start a run and press Ctrl+C during the
  arquitecto.
  - Expected, by design: `cuenta1.json` stays `BUSY`, and `procs` shows
    `claude` still running in the container.
  - Re-run within 120 s:
    - Pass: the run picks another account (or none) and hits the live
      lock.
    - The task goes `blocked` with a "locked by another owner" warning and
      no traceback, and the account it tried goes back to `IDLE`.
  - Re-run after 120 s:
    - The expired lock is reaped and the task proceeds.
    - Meanwhile the orphan may still be working the same worktree (item
      3's race). Record whether it was.
  - Recovery:
    - Kill the orphan: take its container PID from `procs`, then run
      `docker exec agent-cuenta1 kill <pid>`.
    - Delete `dispatcher_state/cuenta1.json` (root-owned, so `sudo`).
- **V4.3 Collector down.**
  - Run: `$DC stop collector`, then repeat V0.5's probe.
  - Pass: exit 0 and a single "collector unreachable" line on stderr.
  - Then, for minimal quota, repeat V1.1: the result is normal and not
    slowed by the hooks.
  - Finish with `$DC start collector`.
- **V4.4 The board's MCP server misbehaves.** Only worth running with a
  `vibe_kanban` block configured. Stopping the compose service proves
  nothing any more — the dispatcher spawns its own server rather than
  dialling one — so the failures to stage are a command that dies and a
  command that never speaks.
  - Run, on the host, with a `command` that exits immediately:
    ```bash
    .venv/bin/python - <<'EOF'
    from dispatcher.config import VibeKanbanConfig
    from dispatcher.dispatcher import _update_task_status
    from dispatcher.vibe_kanban_client import VibeKanbanClient

    client = VibeKanbanClient(VibeKanbanConfig(command=["false"]))
    _update_task_status(client, "0e1d2c3b-4a59-6878-9706-5a4b3c2d1e0f", "blocked")
    EOF
    ```
  - Pass: one `kanban status update failed` warning, exit 0, and it returns
    promptly.
  - Then try a command that starts and never answers (`["sleep", "300"]`).
    It's expected to hang: nothing bounds the MCP handshake, which is item
    3's missing Kanban timeout — now a subprocess to wait on rather than a
    socket.

### V5 — Two accounts (minimal quota)

Only meaningful once V0.4 shows the two accounts really are separate
logins.

- **V5.1 Cross-account `--resume`.**
  - Run:
    ```bash
    docker exec -w /data/projects/scratch agent-cuenta1 \
      claude -p "Remember the word PELICAN. Reply with exactly: OK" --output-format json
    # take session_id from the output
    docker exec -w /data/projects/scratch agent-cuenta2 \
      claude --resume <session_id> -p "Which word did I ask you to remember?" --output-format json
    ```
  - Pass: cuenta2 answers PELICAN with `is_error: false`.
  - Record whether the returned `session_id` is the same or a new one.
    Answered 2026-09-23: **the same**. A resume does not mint a new
    session, so the id the dispatcher stores after a phase stays valid
    for every later resume of it and never has to be re-read out of
    each resume's result.
  - The working directory has to match on both sides: the transcript lives
    under `projects/` in each account's config home, which is one of the
    names the entrypoint symlinks back into the shared `claude_shared`
    volume — so it's visible at the same path on both agents.
  - Answered in two parts, neither of them the literal run above.
    D1 (2026-09-23, Deferred gates below) resumed session
    `85e10326-e62c-48de-be2f-9a7c92741789` on `agent-cuenta2` after
    starting it on `agent-cuenta1`, and the session came back with its
    context — the pass criterion, in a fuller shape than PELICAN. It
    could not answer the id question, because it had pinned the id up
    front with `--session-id`. The 2026-09-23 run of this check
    answered that one, on a same-account resume: cuenta1 was out of
    monthly spend and returned a 429 without starting, so both
    cross-account directions were shut, and legs 2 and 3 ran on
    cuenta2. The transcript cuenta2 wrote was then confirmed visible
    from `agent-cuenta1` at the same path, by both spellings — the
    precondition a cross-account resume rests on is intact; only
    cuenta1's ability to spend is not. That last clause expired on
    2026-09-24: a real sonnet turn on `agent-cuenta1` came back
    `api_error_status: null`, `stop_reason: end_turn`, against 34% of
    the session and 51% of the week, so the 429 was the monthly wall
    and the wall is down. The cross-account leg then ran, on
    2026-09-24, inside V5.2's dispatched failover rather than as a
    standalone turn: T-006's arquitecto started on cuenta1, was refused,
    and the same session continued on cuenta2 in the same worktree with
    its context intact. **Settled.** Evidence:
    `.data/verify/v51-cross-account-resume.txt` and
    `.data/verify/quota-probe-local-only.txt`.
- **V5.2 Dispatcher failover, with a fault injected.** Waiting for a real
  quota exhaustion is too slow and too expensive. Instead, make cuenta1
  report a 429 after doing the real work, and pass `/usage` through
  unchanged.
  ```bash
  docker exec agent-cuenta1 sh -c 'p=$(command -v claude); mv "$p" "$p.real"; cat > "$p" <<"EOF"
  #!/bin/sh
  case "$*" in *"-p /usage"*) exec "$0.real" "$@";; esac
  "$0.real" "$@" | python3 -c "import json,sys; d=json.load(sys.stdin); d.update(is_error=True, api_error_status=429); print(json.dumps(d))"
  EOF
  chmod +x "$p"'
  ```
  - Run: V3's task with a fresh ID, both accounts configured, and both
    state files absent (`IDLE`).
  - Pass:
    - The arquitecto runs on cuenta1. Its result is treated as a rate
      limit: cuenta1 goes `COOLING_DOWN` and the lock is released.
    - The same phase resumes on cuenta2 with `--resume <session_id>` in
      the same worktree path.
    - The task file gets one `## arquitecto` section, not two.
    - The later phases run on cuenta2.
  - Then remove the wrapper:
    - `docker exec agent-cuenta1 sh -c 'p=$(command -v claude); mv "$p.real" "$p"'`
    - Or `$DA up -d --force-recreate --no-deps agent-cuenta1`, which also
      re-checks V0.4's persistence.
  - Ran 2026-09-24 on task `T-006`: **passed on all four**, and the
    wrapper came off with the `mv` line above — `claude` is the original
    symlink again and `claude.real` is gone. The transcript is the whole
    proof of criteria 1 and 2 at once: one session id, the arquitecto
    prompt in it twice (the second with a parent uuid, which is what an
    append-on-resume looks like), under a projects directory whose name
    is the cwd, identical for both accounts. Two notes for whoever runs
    this next. The lock release is proved by cuenta2's acquisition five
    seconds later rather than by a log line, because there is no log
    line — `dispatch_phase` records neither the account it picked nor
    the hand-off, so a real exhaustion would look like a clean run.
    And the resumed phase was handed the *pre-failure* git status block
    verbatim, telling it the worktree was clean while cuenta1's three
    edits sat in it; it checked and did not redo them, but that was the
    phase's judgement, not the harness's. Both were fixed after this run
    and re-verified on T-007, which is V5.3 below. Evidence:
    `.data/verify/v52-failover.txt`.
- **V5.3 Recovery re-check.** What V5.2 cannot reach:
  `_recheck_cooling_accounts` only runs when *no* account is idle, and on
  T-006 cuenta2 was idle before every phase, so cuenta1 was never
  re-probed. The cheap version calls the function by hand.
  1. Leave `cuenta1.json` at `COOLING_DOWN`.
  2. Set `dispatcher_state/cuenta2.json` to
     `{"state": "COOLING_DOWN", "current_task_id": null}` (root-owned, so `sudo`).
  3. Run:
     ```bash
     $DC run --rm --no-deps -v "$PWD/.data/verify/config.yaml:/app/verify.yaml:ro" \
       --entrypoint python dispatcher -c "
     from dispatcher.config import load_config
     from dispatcher.dispatcher import _recheck_cooling_accounts
     print(_recheck_cooling_accounts(load_config('/app/verify.yaml')))"
     ```
  - Pass: both accounts are returned and their files say `IDLE`.
  - Cost: two `/usage` probes (see V1.3 for what those cost).
  - Ran 2026-09-25 on task `T-007`, inside a dispatched run rather than by
    hand, which is strictly more than the recipe above asks: **passed**.
    The fault was V5.2's wrapper made one-shot with a `/tmp/.429-spent`
    marker, and cuenta2 was seeded `COOLING_DOWN`, so the arquitecto's 429
    left no account idle and forced the re-check for the first time in a
    real run: two probes, both accounts back to `IDLE` inside four
    seconds, and the phase resumed on cuenta2 in the same session and
    worktree. One shot is what makes the recovery testable — the account
    is genuinely healthy when it is re-probed seconds later — and it is
    also what saved the run, because a permanently wrapped cuenta1 is
    still first in config order and would have swallowed phases 2-4.
    The same run re-checked the two gaps T-006 opened: the failover now
    writes four lines naming the account it picked, the refusal, each
    recovery and the resumed session id, and the resumed prompt carried
    the stale-context note (+569 bytes) while the shrink retry did not —
    the first two things the resumed phase ran were the
    `git status --short` and `git log --oneline -3` the note names.
    Evidence: `.data/verify/v53-recovery.txt`.
- **V5.4 Real rate-limit output** (opportunistic, no extra quota). When an
  account really hits its limit during normal use:
  - Save the raw JSON and stderr.
  - Check them against `is_rate_limit_error`'s terms (`api_error_status`
    429, "rate limit", "usage limit", "hit your limit").
  - First real sample, 2026-09-23 (cuenta1, during V5.1): classified
    correctly, but only by the status. `api_error_status` was `429`,
    which matches; the text was "You've hit your **monthly spend**
    limit · … · your session limit resets 5:50pm (UTC)", which matches
    none of the four terms — "hit your limit" is broken up by two
    words. The 429 carried it. Two more fields from the same JSON:
    `subtype` was `"success"` on an error result (it describes how the
    turn terminated, not whether it worked — `_exec_succeeded` reads
    `is_error`, and must keep doing so), and `total_cost_usd` was `0`
    with `duration_ms` 759, so probing a quota-blocked account is
    free. Still open: a sample that carries no `api_error_status`, to
    say whether the text fallback needs the wider wording. Evidence:
    `.data/verify/v51-cross-account-resume.txt`.

### Stage 0 exit criteria

- Every V0–V3 check either passed or has a recorded finding and a decision.
- V4 and V5 are done before stage 2 starts.
- README *Known gaps* / *Unverified assumptions* and the spec reflect the
  findings.

## Stage 1 — Fix the known gaps, then accept

1. Apply V0–V2's decisions. Likely candidates, depending on the results:
   - Credential isolation (V0.4 FAIL, 2026-09-17): fixed by giving each
     agent its own `CLAUDE_CONFIG_DIR=/root/.claude-account`, backed by
     that account's `claude_creds_<account>` volume, with the names every
     account shares symlinked back into `claude_shared` by the entrypoint.
     Implemented; V0.4 passed against it on 2026-09-19.
   - The CLI auto-updater (found 2026-09-19 during the V0.4 re-run): in
     each container the CLI runs `npm install -g` on its own, replacing the
     pinned 2.1.273 until the next recreate (2.1.274 on 2026-09-17, 2.1.278
     on 2026-09-19). One update was cut off mid-install when the
     interactive session ended, which left `agent-cuenta1` with no
     `claude` on `PATH`. Fixed: `ENV DISABLE_AUTOUPDATER=1` in the agent
     image (the pinned 2.1.273 honors it), so the pin holds and upgrades go
     through a rebuild. Rebuilt and both agents recreated on 2026-09-19;
     both run 2.1.273 again, and stayed there across an interactive
     session on each (no update ran; closed 2026-09-19).
   - Sysbox for the dind sidecars (confirmed missing, V0.1 FAIL — no
     `sysbox-runc` runtime, which also blocks V0.7): install sysbox, add a
     verification-only privileged-`dind` compose override, or defer both.
   - `safe.directory` and git identity in the image (confirmed, V0.9:
     the dubious-ownership and missing-identity errors both reproduce as
     predicted). Fixed 2026-09-19: the agent image's system gitconfig now
     sets `safe.directory=*` (the literal `*`, since its git 2.39.5 has no
     trailing-`/*` prefix matching) plus a generic unroutable identity that
     the dispatcher overrides per commit. Verified by mounting a host-owned
     repo into a throwaway container off the rebuilt image: `git status`,
     `git worktree add -b agent/implementador/<task-id>` and a commit all
     succeed, and a per-commit `-c user.name=…` wins over the image
     default. Guarded by two tests in
     `tests/integration/test_agent_dockerfile.py`, one of which rejects a
     routable fallback email.
   - The headless permission mode (V1.2).
   - The `/usage` probe (V1.3).
   - Where the Vibe Kanban image comes from (confirmed unpullable, V0.2
     control plane PARTIAL / V0.8 FAIL: `ghcr.io/bloopai/vibe-kanban:latest`
     is private or doesn't exist): build from upstream, run
     `npx vibe-kanban@0.1.44` (confirmed to exist and to expose the MCP
     surface below), or a vetted community image. Answered 2026-09-21 (see
     item 2): the dispatcher spawns the npx server and never reads the
     image, and the service sits behind the `kanban` profile, so an
     `image:` nobody can pull costs an operator the web UI and nothing
     else. Which image to run is now their call, not the harness's.
   - Aligning `VibeKanbanClient` with the real MCP surface (mismatched:
     V2.1–V2.2 live, V2.3–V2.4 schema only, V2.6 login wall; V2.5 not yet
     run). Fixed 2026-09-21 (see item 2): stdio via the `mcp` subcommand
     as a spawned subprocess, the issue vocabulary keyed on the
     server-assigned uuid, and the dispatcher's three statuses mapped onto
     project status names an operator configures. `<role>` is dropped
     rather than carried in a tag or a note: nothing on the board is known
     to take one, and inventing a place for it would be a second guess on
     top of the status names. The client calls four of the 33 tools, so
     the agent-launching ones never come up. V2.3-V2.6 closed on
     2026-09-25 as not verifiable: the remote service is retired, not
     gated, so the real status names (V2.4) and the description round trip
     (V2.5) have no server left to answer them. The client stays; what it
     talks to is what changes.
   - What the board is at all (forced by that re-run: the harness has a
     `KanbanClient` seam, four call sites that use it, and nothing behind
     it). Adopt another product, or implement one. Answered 2026-09-25 in
     `docs/plans/board.md`: implement, in phases, starting with a
     `LocalBoardClient` that satisfies the existing four-method seam
     against local storage — because a generic board cannot model the four
     things this harness actually tracks (the role inside `in_progress`,
     quota as the scarce resource, `depends_on` as a graph, and heartbeat
     TTL locks), and because the seam makes the first phase half a day.
     Phase 0 is queued as `T-008`, to be built by the harness itself.
     Phases 0-3 shipped between 2026-09-26 and 2026-09-28: the local
     board client (`T-008`), the read API (`T-009`), the four
     server-rendered screens that replaced the events dashboard
     (`T-010`), and a live tail over `/api/events`. On 2026-09-28
     `docs/charter.md` C-8 superseded C-7 and named `front/` — a
     TanStack Start app written against these same plans — the console
     this repo ships, so Phases 4-6 are built there rather than in
     Jinja. What that reverses is the toolchain, not this answer:
     implement rather than adopt, in phases, against the four-method
     seam, all still hold, and the board stays as the tie-breaking
     reference until the front serves those screens against the real
     api.
   - Fixing README step 5's host run (confirmed, V0.3: `state_dir`/
     `hive_tasks_dir` are container paths and `vibe-kanban`/`collector`
     only resolve on `ia_harness_net`): drop it, or document a host-side
     config and why `hive_tasks_dir` can't be one.
   - A compose profile for the one-shot `dispatcher` service (confirmed
     unguarded, V0.2 dispatcher service: no profile gates it, so a bare
     `up -d` fires `run-task --task-id CHANGE_ME --project CHANGE_ME`).
     Fixed 2026-09-19: `profiles: ["dispatcher"]` in `docker-compose.yml`
     too, with a test parametrized over both compose files so neither can
     lose it, verified with `docker compose config --services`.
   - dind bind mounts (V0.7), if confirmed.
2. Fix the README's *Known gaps*:
   - **Agents never see the task.** Fixed 2026-09-19 with a
     `--description`/`--description-file` flag on `run-task`, stored in
     the task file's frontmatter (not the body, which accumulates phase
     summaries) and embedded whole in every role's prompt; a task with no
     description anywhere is a usage error instead of four phases of
     quota. Seeding it from a board (V2.5) stays open, and waited on
     Phase 0 of `docs/plans/board.md` rather than on a cloud login: the
     description round trip is one of that phase's tests. Phase 0
     shipped as `T-008`, so what is left is wiring `run-task` to read a
     description off a card, not a board that can hold one.
   - **Roles don't see each other's code.** Fixed 2026-09-19, as
     designed: one branch per task (`agent/task/<task-id>`), one shared
     worktree on it for the writing roles (arquitecto, implementador), a
     dispatcher commit after each of those phases naming the role,
     account, round and session, and detached revisor/auditor checkouts
     rebuilt at that branch's tip every round — git refuses to check one
     branch out in two worktrees, and a reused review checkout is what
     made the revisor read a clean tree. `dispatch_phase` also reads the
     project directory's owner before the phase and `chown -R`s it back in
     a `finally`, since the agents run as root against a uid-1000 bind
     mount. V0.9's prerequisite (git ownership and identity in the image)
     landed the same day. Guarded by 105 unit tests across
     `tests/dispatcher/test_docker_exec.py` and `test_dispatcher.py`,
     including the two hazards that would silently undo it: `git worktree
     add -B`, which resets the branch and drops every commit so far, and
     tolerating an "already exists" error on a reviewer path, which is
     precisely the stale-checkout bug. Still open, and now its own
     Known-gaps bullet: nothing merges the branch or opens a PR when the
     task ends.
   - **Worktrees pile up per task.** Fixed 2026-09-19: `run_task_cycle`
     drops the reviewing checkouts from a `finally`, so every terminal
     exit is covered — `done`, `blocked`, an early return, or a crash —
     and they are rebuilt on demand anyway. Two things the design turns
     on. The reviewers are found by listing `worktrees/<task-id>/` and
     keeping `work`, not by naming roles: `create_worktree` defines a
     reviewer by negation (anything outside `WRITER_ROLES`), so a role
     added later is cleaned up without this code learning its name.
     And a phase that bounced off another owner's `LockHeldError` sets a
     flag that suppresses the cleanup: that other dispatcher is still
     working in those worktrees. The writers' one is kept — it holds what
     a failed phase left uncommitted — and `dispatch cleanup-task
     --task-id <id> --project <slug>` is the deliberate step that takes
     the whole directory, branch intact. Same `chown` restore as the
     phases, since `git worktree prune` writes to `.git/worktrees/` as
     root. 16 new unit tests, 126 across the two dispatcher test files.
   - **A finished task goes nowhere.** Fixed 2026-09-21 as chosen:
     `dispatch merge-task --task-id <id> --project <slug>` always
     available, plus `merge_on_done` in `config.yaml`, default `false`,
     which runs the same merge right after the auditor signs off. `gh pr
     create` was rejected — it needs a remote and a token neither
     container has. The target branch is read, not configured: whatever
     `projects_root/<slug>` is checked out on is what whoever set the
     project up works from, and a merge into a branch nobody looks at
     helps no one. Four refusals leave the repository exactly as found —
     no such branch, detached `HEAD`, the project sitting on the task
     branch itself, uncommitted tracked changes — and a conflicted merge
     is rolled back with `git merge --abort`. Three outcomes, not two:
     `--no-ff` on an already-merged branch exits 0 printing "Already up
     to date.", which is `UP_TO_DATE`, so a re-run does not read as a
     failure or exit non-zero. Two details the design turns on. The dirty
     check passes `--untracked-files=no`, because `worktrees/` lives
     inside the repository and any project that has ever run a task is
     permanently untracked-dirty — treating that as dirty would refuse
     every merge forever, and the case untracked files actually matter in
     (a merge that would write over one) git refuses by itself, which
     surfaces as the merge failing. And no path deletes or rewrites
     `agent/task/<task-id>`: the branch is the record of the work and the
     way back if the merge was wrong. Same `chown` restore as the phases
     and the cleanup, in both the dispatcher hook and the CLI, since the
     merge writes to `.git/` as root. From the dispatcher a refusal is
     logged and the task still ends `done`; from the CLI it prints to
     stderr and exits 1, so a script can tell a refusal from a merge. 20
     new unit tests, 240 in the suite.
   - **A dispatched project's own `git status` never comes back clean.**
     Found while verifying the merge, fixed 2026-09-21: the first time
     `create_worktree` runs, `ensure_worktrees_ignored` appends an
     anchored `/worktrees/` (with a comment naming the harness) to the
     project checkout's `.git/info/exclude`. Three choices behind it.
     `.git/info/exclude` rather than `.gitignore`, because where this
     harness parks its scratch checkouts is a fact about this clone, not
     something the project should commit. Anchored with a leading slash,
     so a project carrying its own `src/worktrees/` goes on seeing it.
     And hooked into `create_worktree` rather than `bootstrap-project`,
     which runs before anyone has cloned the repository and would have
     nothing to write into — the helper is idempotent and best-effort
     (no repository, no `info/`, a failed write: it logs and returns
     `False`, and no phase fails over it), so the entry lands the moment
     the directory it describes does. It also closes the gitlink trap:
     before it, `git add -A` in a project that had run a task would
     commit a worktree as an embedded repository. The merge's dirty
     check keeps `--untracked-files=no` regardless, since an older
     checkout can reach us without the entry. Verified live on a real
     repository inside `agent-cuenta1`: `True` then `False` on a second
     call, both writer and reviewer worktrees created, `git status
     --porcelain` empty with both present, `git add -A` picking up
     nothing, `?? src/` still reported for a nested `worktrees/`, and
     `.git/info/exclude` still owned by 1000:1000 after the append. 12
     new unit tests, 252 in the suite.
   - **Every phase pays for skills nobody chose.** Fixed 2026-09-21:
     `hooks/install_settings.py` now writes `syncClaudeAiSkills: false`
     and `syncClaudeAiPlugins: false` alongside the hooks, so the
     entrypoint turns the claude.ai sync off on every container start.
     That layer is the whole point: the shared `settings.json` lives in
     `claude_shared` and is symlinked into both accounts' config homes,
     so it counts as *user* settings for both — the two keys are read
     from user or managed settings only, never from a project's
     `.claude/settings.json`, and only the literal `false` counts. The
     README's other candidates were checked and dropped.
     `CLAUDE_CODE_SYNC_SKILLS` is an enable gate, not a kill switch.
     Pruning `skills/synced/` at start is undone by the next sync.
     Dropping `skills` from the entrypoint's shared allowlist only stops
     each account from seeing the other's set; each container would
     still pay for its own. The merge is additive (`setdefault`), so
     `settings.json` doubles as the opt-in — an operator who wants
     their skills in the containers sets either key to `true` and the
     entrypoint stops arguing, printing a warning per key on each start
     while the sync is on. What the setting does to what was already
     downloaded took reading the compiled CLI: the prune that logs
     `skills_sync_pruned_for_closed_gate` renames `skills/synced` to
     `skills/.trash`, where `cleanupPeriodDays` deletes it, and both of
     its call sites are gated on `skillsSyncVetoed()` — one of them on
     the MCP-server path, so `claude mcp serve` triggers it locally
     without spending quota, which is how this was verified. Live in
     both agents: `skills/synced` down from 8.5M to 20K with zero
     `SKILL.md` left, 20 of them in `skills/.trash`, and
     `agent-cuenta2` reading the same two keys off the shared file.
     Still open: the actual token saving in the system
     prompt can only be measured by a prompted run, which costs quota
     — the 13,535 bytes of name and description have no source left to
     come from. 5 new unit tests, 257 in the suite.
   - **The board's MCP surface was imagined, and its image can't be
     pulled.** Both fixed 2026-09-21 as chosen: make the board optional
     first, then rewrite the client against the surface V2 actually found.
     `vibe_kanban` is a block in `config.yaml` now (`command`, optional
     `project_id`, optional `status_map`) and having none is the default:
     no block, no board, and `NullKanbanClient` answers every call, so the
     dispatcher's call sites stay unconditional and a boardless run says
     nothing about one. A leftover `vibe_kanban_mcp_url` is rejected at
     load with a message saying the server speaks stdio, rather than
     quietly ignored. The client was rewritten, not patched: it spawns
     `command` and talks MCP over its stdio — one subprocess per call,
     since phases are minutes apart — over `list_issues`, `create_issue`,
     `get_issue` and `update_issue`, keyed on the server-assigned uuid.
     That uuid has to live somewhere, and it lives in the task file's
     frontmatter (`kanban_issue_id`) rather than a dispatcher-side map:
     the task file is what survives a restart, and nothing can derive the
     uuid from `T-001`. `run-task --kanban-issue-id` attaches an issue
     that already exists; with a board configured and no id, the first
     phase opens one. The three dispatcher statuses translate through
     `status_map` (default `In Progress`/`In Review`/`Done`); `<role>` is
     dropped, because `update_issue` takes a project status name and
     offers nowhere else to put it, and an unmapped status is skipped
     with a warning instead of sent to be rejected. The image question
     then dissolves: nothing on the dispatcher's path to the board reads
     it, so the service moved behind a `kanban` profile as the web UI an
     operator may want, which also unbreaks a plain `up -d` for the other
     three control-plane services — Compose pre-pulls every named image
     before starting any of them. What an operator still has to solve is
     Node: the verified command is `npx vibe-kanban@0.1.44 mcp`, and the
     dispatcher image has none, so running the dispatcher in its
     container needs a `command` that works there. The reply parsers are
     deliberately loose. The request schemas came from the server's own
     tool list and are exact, but no response schema is published
     anywhere and none has ever been seen (V2.6), so `structured_content`,
     JSON inside a text block and bare prose all read; an `is_error`
     reply raises carrying the server's complaint, so a rejected status
     name can't pass for a successful update; and a shape the parser
     can't read is an empty list, not a crash — a looseness that was
     never tested against a real reply and now cannot be: the re-run of
     2026-09-25 closed the real status names (V2.4), the description round
     trip (V2.5) and which field a create reply carries the id in (V2.3)
     as not verifiable, the service being retired rather than gated. The
     loose parsers stay, unexercised, because the seam is what makes a
     local replacement half a day's work. 56 new unit tests, 313 in the
     suite.
   - **A phase can't write, and can't read its handoff.** Fixed
     2026-09-23, closed by measurement 2026-09-24. Two causes, one
     config field apiece, and a three-run A/B on the same task shape to
     separate them. The first run (`T-001`, 2026-09-23) denied 43 of 62
     tool calls and wrote nothing, inside its worktree or out: headless
     `claude -p` defaults to asking, and nobody is there to answer.
     `permission_mode` went into `config.yaml` and onto the phase
     command *and* the shrink and gate retries, which inherit no flags;
     at `acceptEdits` the second run (`T-002`, same day) denied 17 of 54
     and wrote files, but ended `blocked`, because `acceptEdits` covers
     edits and not execution, so the test never ran. `allowed_tools` was
     wired the same three places and the third run (`T-003`,
     2026-09-24) settled it: 12 of 95 denied, every denial a `Bash`
     call, task `done` with the revisor approving, five `node --test`
     runs across three worktrees under four phases and all of them
     green. The handoff half was never separately broken — it only
     looked broken because a phase that writes nothing has nothing to
     hand on: once writing worked, each phase read the accumulated task
     file and the cycle walked arquitecto -> implementador -> revisor ->
     auditor with no reseed. What stays open is the two-account half of
     the acceptance below: cuenta1 is out of monthly spend, so all three
     runs were one account. And the run that closed this bullet opened
     four more, all of them things only a run that could write could
     expose: a reviewing role's worktree is deleted with its edits
     uncommitted, and the auditor — the one phase the prompts make
     responsible for the durable doc indexes — is a reviewing role; the
     `/usage` parser rejects a 0% session reading; nothing retires a
     learning the harness has since disproved; and three of four
     handoffs blew their byte budget. The first two are closed by the
     two bullets below and measured by a fourth run the same day; the
     other two are still in the README's *Known gaps*, and the budget
     one has a third run of data — `T-004` blew three of four again, a
     different three, with the arquitecto under and the implementador
     280 bytes over. No new tests of its own: both config fields landed
     with theirs, leaving the suite at 688 passing and 10 skipped.
     Evidence:
     `.data/verify/v3-end-to-end.txt`,
     `.data/verify/v3-rerun-permissions.txt`,
     `.data/verify/v3-allowlist.txt`,
     `.data/verify/v4-auditor-commit.txt`.
   - **The auditor is the only phase that writes the docs, and its
     writes are deleted.** Fixed 2026-09-24 by moving the auditor into
     `WRITER_ROLES`. That is one edit with three effects, because the
     set is what `_should_commit`, `create_worktree` and the cleanup all
     read: the phase now shares the writers' worktree on
     `agent/task/<task-id>` and gets the per-phase commit, instead of a
     detached checkout `run_task_cycle` deletes with its entries still
     in it. The alternative — a commit for the reviewing roles — was
     rejected, since it would have handed the revisor one too, and what
     the auditor needs is not to review from a shared tree, it is to
     leave something behind. `remove_review_worktrees` needed no change:
     it finds reviewers by negation (list `worktrees/<task-id>/`, keep
     `work`), which is the property the worktree-cleanup bullet above
     was built on. The gates are untouched, since `_run_gates` returns
     early for every role but the implementador. What promoting it costs
     is that the auditor runs last, after the revisor has approved, so
     an ordinary whole-tree commit would land code nobody read. So
     `commit_worktree` took a `paths` argument and
     `project_docs.commit_scope` holds the policy — `auditor -> docs/`,
     exactly the paths its prompt tells it to write, and `None` for
     every other role, because the work itself has no fixed shape to
     hold one to. The policy lives in `project_docs` and the mechanism
     in `docker_exec` because the import runs that way and not the
     other. Two details the code turns on. A pathspec matching nothing
     is a hard error rather than an empty commit — `git add -A -- docs`
     exits 128 on a tree with no `docs/` — and a phase that wrote
     nothing is the ordinary case, so the scope is checked for existence
     before it is staged. And anything the phase changed outside its
     scope is logged by name instead of dropped in silence: dropping a
     role's work quietly is the bug this bullet is about, and it must
     not come back in miniature. That warning is best-effort, so a `git
     status` that will not run cannot stop an otherwise-fine commit. 13
     new unit tests across `test_docker_exec.py`, `test_project_docs.py`
     and `test_dispatcher.py`. Measured 2026-09-24 by a fourth
     dispatched run of the same task shape (`T-004`,
     `.data/verify/v4-auditor-commit.txt`), which is the only thing that
     could close it: the auditor's commit `ef3e53f` is on
     `agent/task/T-004`, ten files and 295 insertions, every path under
     `docs/` — the two indexes, six learning entries, the debt file and
     the implementation note — with no out-of-scope path logged. The
     phase's session cwd moved from `worktrees/T-004/auditor` to
     `worktrees/T-004/work`, and the cleanup line reads `removed review
     worktrees revisor` where T-003's read `auditor, revisor`; the
     revisor is still a reviewer by design, and `git log
     --author=revisor` on the branch is empty. Refusals fell to 4 of 75
     (5%), the best of the four runs, and the auditor's own 30 calls —
     ten `Write`, two `Edit` — were refused none: the phase whose output
     used to be deleted is now the cleanest one. Two things the run
     turned up that this bullet does not close. `learnings.reconcile`
     fired for the first time and promoted three entries, while the one
     entry this fix disproved
     (`T-003-phase-edit-missing-from-next-worktree.md`) stayed in the
     inbox and went to all four phases — a named instance of the
     stale-learning gap, not a new one. And the auditor rewrote the toy
     project's `docs/README.md` worktree path from `work` to `<phase>`,
     reading a `git worktree list` still padded with three earlier
     tasks' reviewer directories: the doc it is responsible for is now
     wrong in the other direction. Fixed 2026-09-24 on the project's
     `master`, and not by reverting the word: the auditor was reporting
     what it saw, so the paragraph now describes both shapes — the
     writers' shared `work`, which outlives the task until `dispatch
     cleanup-task` removes it, and a reviewer's detached checkout named
     after the role, rebuilt per run and deleted when the task ends —
     and says in as many words that a directory it does not name turning
     up in `git worktree list` is the harness working rather than the
     doc going stale. A future auditor reading that list now finds it
     already accounted for. `agent/task/T-004` keeps the wrong line: it
     is this run's artefact, nothing branches from it (every task
     branches from the project's `master`, which was never wrong), and
     `merge_on_done: false` means it reaches nothing on its own.
   - **The quota probe stops reading at 0%.** Fixed 2026-09-24 as
     chosen: the `· resets <when>` clause is now optional per line in
     both of `parse_usage_output`'s regexes, and
     `session_reset`/`week_reset` are `str | None`. The percentages stay
     required — they are what `exceeds_threshold` decides on, and a
     reading that will not parse should still fail loudly rather than be
     guessed at. A missing reset costs nothing downstream: nothing
     schedules off those timestamps, because recovery is a re-probe on
     the next dispatch (`_recheck_cooling_accounts`), and no caller but
     the tests reads the fields. The fixture is the CLI's own output
     from T-003, kept verbatim — `Current session: 0% used` with a
     well-formed week line under it, the case that used to cost the
     harness both percentages on the account that had all of its session
     left. 3 new unit tests, 704 passing and 10 skipped in the suite,
     and one dispatched run to show the fixture was not the only thing
     that parses: T-003's run log carries the parse warning at +4s as
     its line 6, and T-004's line 6 is the learnings INFO instead. The
     four quota-probe sessions are in the collector for both runs, so
     what changed is the reading, not the probe.
   - **Nothing retires a learning the harness has since disproved.**
     Fixed 2026-09-24 with two exits besides deletion, both on
     evidence rather than on a model's opinion. Every entry is stamped,
     when its task ends, with `harness_fingerprint(permission_mode,
     allowed_tools, WRITER_ROLES)` — the permission surface and nothing
     else, because those three settings are precisely what made both
     known-false entries false, and a fingerprint over the whole config
     would mark the inbox stale every time a timeout was tuned. An entry
     written under a different surface is *stale*: still rendered, marked
     in the status column, but dropped from the corroboration map, so it
     can neither confirm nor be confirmed until a task hits it again
     under the current one. Both fingerprints must be present for that,
     so entries predating the stamp read as unknown and the feature does
     not retire the inbox on its first run. The second exit is
     phase-side: an entry carrying `refutes: <ref>` marks its target
     `refuted` on the next `reconcile`, recording the refuting task in
     `refuted_by`; the target leaves every phase's table and stays on
     disk and in `dispatch learnings`. Three guards, each the mirror of
     a promotion rule: a task cannot refute its own claim (that is
     changing its mind mid-run), a promoted entry is only ever retired
     by `dispatch learnings --refute` (a human put it in the shared store
     for every project), and a refuted entry corroborates nothing. The
     asymmetry is why this needs no human where promotion does: a wrong
     refutation costs a later phase a debug it would have paid anyway
     before the entry existed, while a wrong confirmation misleads every
     phase that reads it. Ordering follows: confirmed rows survive the
     40-row cap first, refuted rows fall off it first, stale rows sink
     within their band. 9 new unit tests, 713 passing and 10 skipped in
     the suite. Not yet exercised by a dispatched run: no run has been
     paid for since, so what the phases do with `refutes:` when the
     prompt offers it is unmeasured, and the two entries this harness
     already knows to be false are still in its own inbox.
   - **The handoff budgets were sized before a phase could write
     anything.** Fixed 2026-09-24 — the constants re-derived 2026-09-26,
     at the end of this entry — from the eleven overages four
     dispatched runs logged rather than by raising every limit until the
     warning went quiet. The first finding was that the retry is not
     overhead: every over-budget draft came back materially shorter and
     still complete, and the auditor's landed T-004 handoff is 22
     one-line entries plus one debt object — an editing pass worth
     paying for. So only the two budgets *nothing* has ever met moved,
     the rule being that a budget returns straddle is a budget doing its
     job: revisor 3072 → 4096 (3139, 4010, 4149, 4386 in four runs; it
     carries a verdict and a ruling per declaration, the same argument
     that bought the implementador its room) and auditor 2048 → 3584
     (4602 and 5209; it reports on the whole task and now commits the
     docs it files, and its shortened returns measure about 2600, so
     2048 was never reachable). Arquitecto and implementador stay, each
     having met its budget at least once; cartografo stays because no
     run has exercised it and its number is still a guess. Second, an
     overage inside 10% of the budget, floored at 256 bytes, no longer
     buys a `--resume`: four of the eleven were 46, 67, 170 and 280
     bytes, a line and a half each for the price of a model call.
     Replaying all eleven against the new numbers, the retries drop from
     11 to 4, two returns now fit outright and five are taken as they
     came. Third, the log now describes what landed: the draft size goes
     to INFO, which is where the next tuning reads it, and WARNING is
     reserved for a handoff that reaches the task file over budget —
     the rewrite refused for want of a session, failed, or come back
     long. Across the same four runs that line would have fired zero
     times, which is what makes it worth reading. Fourth, the limit is
     stated to the role in entries as well as bytes (`lines_for`
     derives one from the other, so the two cannot drift): 14 for the
     cartografo, 30 for the arquitecto, 38 for the implementador, 30 for
     the revisor, 26 for the auditor. Nothing can count its own bytes
     while writing, which is the likeliest reason every run so far
     overran one; entries it can count, and an entry is what the fields
     hold. Deliberately not done: `maxItems`/`maxLength` in the JSON
     schema, which would make the CLI enforce it — a hard validation
     failure drops the phase to the prose clamp, a worse outcome than
     a long handoff, and nothing but a dispatched run can tell which
     way the CLI takes it. 11 new unit tests, 724 passing and 10
     skipped. Exercised by T-005 (2026-09-24), which hit all three
     branches in one run: the arquitecto retried and came back inside,
     the implementador missed by 267 bytes and was kept as it was
     without paying for a `--resume`, and the revisor and auditor each
     retried once and stayed over — two WARNINGs in a run, where the
     four runs that motivated the numbers would have produced zero.
     The margin does what it was added for and the WARNING means
     something again. What the run did not settle is whether entries
     read better than bytes: every role still overran, so the counts
     are no more reliable a target than the byte budgets were. One
     number is now suspect rather than confirmed — the arquitecto's
     4096, left alone because its misses were 1%, 4% and 18%, was
     missed by 43% here. That is one data point against the reasoning,
     not yet a reason to move it. T-006 (2026-09-24) made it two: the
     arquitecto overran by 44% again, 5911 against 4096, near enough to
     T-005's 5857 to look like that role's natural length rather than
     noise — the next tuning should raise it. The rest of that run is
     the numbers working: four handoffs, four inside budget, no
     WARNING, against two of four on T-005. Three roles retried once
     each and came back in, and the revisor's 377-byte overage was kept
     as it came — the second time the margin has paid for itself.
     T-007 (2026-09-25) makes it three: four handoffs, four inside, no
     WARNING. It also gives the next tuning its two numbers. The
     arquitecto came in at 5374, a third reading in the same 5.4–5.9k
     band as T-005's 5857 and T-006's 5911, which is that role's length
     and not noise. The implementador has now overrun on all three runs
     and the drafts are growing — 5387, 6051, 6589 against 5120 — so its
     budget is the second one set too low. The revisor, by contrast,
     needed neither a retry nor the margin for the first time.
     T-008 (2026-09-25) is the fourth and it breaks the pattern the
     first three set, because it is the first dispatch against a real
     repo instead of the toy project. Every one of its five phases
     blew the budget on the first attempt — arquitecto 6540 against
     4096, implementador 8905 against 5120, revisor 5226 and then
     6254 against 4096, auditor 4443 against 3584 — and three of the
     five were still over after the one rewrite the dispatcher
     allows: 4732, 6313 and 4280. Five phases failing the same way in
     one run is a sizing problem and not five incidents.
     Re-derived 2026-09-26, from measurement rather than from the
     logged totals. Every accepted return in `.data/verify/` was
     inverted back into the canonical JSON `handoff.measure` counts —
     the render mapping is invertible, and the inverse reproduces all
     five logged T-008 sizes byte for byte — which prices a handoff
     field by field across the 32 phases of eight tasks. Three
     findings. First, the roles hit the target they were told and
     missed the target that was checked: the median phase delivered
     two entries *fewer* than `lines_for` allowed, and eight of the
     thirty-two still went over the bytes. `_LINE_BYTES = 128` was the
     mean density, and a budget divided by a mean is overrun half the
     time by construction; measured net of envelope an entry costs a
     median of 130 bytes, a mean of 132 and a p90 of 161, so the
     constant is now 160, the p90, and a role that spends its entries
     fits its budget about nine times in ten. `_ENVELOPE_BYTES` moves
     the other way, 256 down to 160: with every field empty the
     envelope measures 123 bytes for the roles carrying the common
     fields alone, 142 for the implementador and 162 for the revisor.
     Second, the diagnosis above was wrong about the cause. T-008 is
     busier, not wordier: it ran 144 bytes an entry against the toy
     tasks' 114 to 158, and filed 169 entries against their 60 to
     135. The detail did go to files — some 30 KB of notes under the
     task's scratch dir, with the `paths` counts unchanged — so
     telling the roles again to cite rather than quote would have
     fixed nothing. A real repo has more to report per phase, and the
     budgets are now sized off what each role actually filed on T-008
     at its own p90 density plus the envelope: cartografo 2560,
     arquitecto 5120, implementador 7168, revisor 5120, auditor 4608,
     which turns 18944 bytes of budget into 24576. The entry counts
     follow: 15, 31, 43, 31 and 27. Third, pricing the fields
     separately was measured and rejected. The spread between them is
     real — a flat entry runs 92 bytes, a `paths` pair 151, a debt
     declaration 465, and debt, paths and rulings together are 41% of
     all handoff bytes — but weighting them predicts a handoff no
     better than one number does, because the variance is inside each
     field and not between them: the coefficient of variation per
     handoff stays near 18% under every weighting tried. So the
     prompt keeps one number and names the one exception, that a debt
     declaration carries several fields and counts as three entries.
     Deliberately not done, again: `maxItems`/`maxLength` in the
     schema, for the reason above. One new unit test pins each budget
     to the size its role actually filed on T-008; 784 passing, and
     no existing assertion had to move. Not yet exercised — the next
     dispatch against this repo is the check.
   - **A reviewing phase's edits are silently discarded.** Fixed
     2026-09-25. Measured on T-005 (`.data/verify/t005-acceptance.txt`):
     the revisor said it had closed a `pointers` gate by editing
     `docs/implementations/T-005.md`, its own `grep` in its own worktree
     agreed with it, the branch never saw the file, and APPROVED was
     issued over a fix that did not exist. The cause is deliberate and
     stays: a review worktree is added `--detach` at the task branch's
     tip so the reviewer reads the code as it stands, and a detached
     HEAD leaves the dispatcher no branch to commit onto, so whatever
     the reviewer writes goes out with the checkout at the end of the
     round. The alternative considered and rejected was giving reviewers
     a branch of their own to commit to: that puts the reviewer's own
     change onto the branch it is in the middle of approving, and the
     verdict then covers its own work. So the edit is still lost — what
     changes is that the loss is loud. Every phase outside
     `WRITER_ROLES` is asked for `git status --porcelain` on its own
     checkout the moment it returns (`docker_exec.dirty_paths`, factored
     out of the check `_warn_changes_outside` already ran). A dirty one
     logs a WARNING naming the role, the checkout and the paths, then
     spends one `--resume` telling the phase its edits never reach the
     branch and asking for the change back as a finding — `verdict:
     CHANGES_REQUESTED` for the revisor — with every other field as it
     was, and that corrected return is the one that lands in the task
     file. Both degrade paths keep the first return: a phase that left
     no session to resume, and a resume that errors, are logged and the
     review stands as it came. The revisor's prompt now opens with the
     same fact, so the cheap case is the retry never firing. 8 new unit
     tests, 739 passing and 10 skipped. Not yet exercised by a
     dispatched run: no run has been paid for since, so the WARNING, the
     correction turn and the verdict a phase actually returns to it have
     only been seen against fakes.
   - **The quota gate is blind to the refusal that has actually
     happened.** Fixed 2026-09-25. Measured 2026-09-24 (V5.1,
     `.data/verify/quota-probe-local-only.txt`): `claude -p "/usage"` is
     a *local* slash command — `local_command: "usage"`,
     `duration_api_ms: 0`, `num_turns: 0`, `total_cost_usd: 0`, 641 ms —
     so `check_quota_ok` compared `quota_threshold_pct` against counters
     the container itself had written, and never asked the service
     anything. The one real refusal on record is the opposite shape: a
     429 on arrival with no session started (V5.4, 2026-09-23), which a
     `/usage` beside it would have waved through on comfortable numbers.
     The harness did already learn that truth reactively —
     `is_rate_limit_error` on the phase result parks the account — and
     then threw it away, because `_recheck_cooling_accounts` re-probed
     with the same blind command and flipped the just-refused account
     back to `IDLE` on healthy local numbers. So the refusal is now
     persisted rather than re-derived: `record_rate_limit` writes
     `rate_limited_at` beside the account's state, `set_state` carries it
     across the whole-document rewrite every transition performs — an
     account is refused, parked and re-probed in three separate writes,
     and the mark is only worth anything if it survives to the third —
     and for `quota_cooldown_seconds` (new, default 1800, rejected at
     load unless a positive int) it outranks the probe in both places:
     the gate will not hand the account out, and the recheck does not
     spend a probe on it at all. Inside the floor only a turn the service
     actually served drops the mark (`clear_rate_limit` after a
     successful phase) — a clean `/usage` cannot, being the reading that
     was blind in the first place. Once the floor has passed the probe
     decides again, and the recheck clears the mark as it takes the
     account back, so an expired refusal never holds it a second time.
     The question the gap left open — whether a refused account answers
     `/usage` with numbers or with an error — no longer needs an answer:
     both shapes are covered, because the probe result is run through
     `is_rate_limit_error` before anything parses percentages out of it,
     in the gate and in the recheck alike, and a refused probe is a
     fresh refusal that restarts the floor. The probe stays free, which
     the gap named as worth keeping: nothing here spends a turn to test
     an account. Two things are deliberately unchanged. The gate still
     fails open on any other exception — the phase is the backstop, and
     failing closed would park healthy accounts on a transient docker
     error — but it now logs that it waved one through unverified
     instead of reporting a pass. And the undercount stands: `/usage`
     says in as many words that it covers "local sessions on this
     machine", so an account also used from a phone or claude.ai reads
     low here and nothing local can see it. Rejected: a last-resort
     escape letting the oldest-refused account through when every
     account is cooling. Holding is strictly better — the alternative
     spends the phase on an account known to refuse — and `tried`
     already stops the retry loop without it. 15 new unit tests, 756
     passing and 10 skipped. Not yet exercised by a dispatched run: the
     cool-down, both probe-refusal paths and the fail-open WARNING have
     only been seen against fakes.
   - **An account left `BUSY` by a crashed dispatcher stays `BUSY`
     forever.** Fixed 2026-09-25 with `release-account`, plus `status` as
     the verb that shows you the state you are about to repair
     (`dispatcher/operator.py`, Phase 0 of `docs/plans/balancer.md`). The
     gap was three functions agreeing by omission: `list_idle_accounts`
     returns only `IDLE`, `_recheck_cooling_accounts` skips any state that
     is not `PRE_COOLDOWN`/`COOLING_DOWN`, and `reap_expired_locks`
     releases the card's lock on its heartbeat TTL and never the account
     holding it — and it is called from exactly one place, inside
     `run_task_cycle`, which needs an idle account to be reached at all.
     The 2026-09-25 reboot left `cuenta2` there with `cuenta1` parked over
     threshold, so the pool had nothing dispatchable and no way to say so.
     `release-account` sets the account `IDLE` and clears the lock on
     whatever card it holds — `owner` and `heartbeat` dropped, `status`
     untouched, because whether the task is still in progress is the
     task's business — finding that card by the union of what the state
     file names and what the cards claim as their owner, so a card the
     state file never knew about still comes back. It refuses, non-zero,
     on a heartbeat refreshed inside the TTL (a phase is still running,
     and releasing would put a second one in the same container) and
     inside `quota_cooldown_seconds` of a recorded refusal (handing the
     account out spends the next phase on one known to refuse); `--force`
     overrides both and deliberately *keeps* `rate_limited_at`, so the
     gate still honours it, while `--clear-rate-limit` is the only thing
     that erases it. An expired refusal is not cleared on release either:
     `_recheck_cooling_accounts` only honours a mark inside the window, so
     an old one is history rather than a debt. An already-`IDLE` account
     is a no-op and exits zero, but still gets its orphaned card back,
     that being the other half of the same crash. `status` reads and
     writes nothing at all, which is why it does not reuse
     `check_quota_ok`: the gate's probe parks accounts and records
     refusals as a side effect, and a look at the pool must not change it.
     `--probe` asks each container `/usage` on the same terms — the result
     goes through `is_rate_limit_error` before anything parses percentages
     out of it, so a refused probe is reported and not recorded, and a
     container that is down becomes a note on one account rather than an
     error for the listing. **A second, unplanned fix came out of the
     smoke test:** a hand-edited task card crashed every lock reader with
     `fromisoformat: argument must be str`, because PyYAML resolves an
     unquoted ISO timestamp to a `datetime` while `TaskFile` declares
     `heartbeat: str | None`. Cards this harness writes round-trip fine —
     `yaml.safe_dump` quotes a timestamp-shaped string — so the unit
     tests, which build their fixtures through `write_task_file`, could
     not see it, and a hand-edited card is exactly what somebody is
     holding when they go looking at locks. Coerced once in
     `read_task_file` (`_as_heartbeat_str`: a naive datetime is read as
     UTC, anything that is not a timestamp becomes `None`, on the grounds
     that an unheld card is takeable while a card held by garbage would be
     held forever), which fixes `is_lock_expired`, `reap_expired_locks`
     and `acquire_lock` by the same line; the operator's two heartbeat
     helpers also swallow a bad stamp, because `status` is the command you
     run when the harness is already wrong and must not be killed by one
     unreadable card. 24 new unit tests, 780 passing and 10 skipped. Not
     yet exercised against the real pool: both verbs were smoke-tested
     end to end against a scratch config — the live-lock refusal, the
     `--force` release, the repeat no-op and the unknown-account usage
     error all produced the intended output and exit code — but nothing
     has yet been released in `dispatcher_state/`.
     **The TTL that entry left owing was built 2026-09-28**, with Phase 2
     of `docs/plans/balancer.md`: `reap_stale_busy_accounts`, called from
     `pick_idle_account`, so the stuck account is now reached by the code
     that needs it every time the pool is walked, and not only by an
     operator who noticed. Built as the design said — the card's heartbeat
     first, since a running phase refreshes it and a wall-clock reading
     would expire a phase that is legitimately long; `busy_since`, stamped
     by `set_state` on the way into `BUSY` and dropped on the way out,
     only for an account holding no card, judged there against
     `phase_timeout_seconds`. Two edges the design had not named. A
     `busy_since` newer than the TTL is a floor under the heartbeat test,
     so one dispatcher cannot reap the phase another has just started,
     before its first heartbeat lands. And a card whose `heartbeat` is
     `None` reads as *not* expired through `is_lock_expired` — right for
     the lock, since an unheld card is takeable, but a trap here, where it
     would pin the account forever — so that card takes the `busy_since`
     path too, with a test that asserts the premise before the behaviour.
     `release-account` keeps the case the TTL must not touch: a phase that
     is genuinely alive and has to be stopped anyway (`--force`).
   - **A cycle cannot be resumed from the phase it stopped at.** Fixed
     2026-09-25 with `run-phase`, the first piece of real work in Phase 1
     of `docs/plans/balancer.md`. Found the same day: the reboot that
     stranded `cuenta2` also killed the T-008 run between the
     implementador's second round and the revisor's, and what survived
     was worth resuming — two commits on `agent/task/T-008`, a clean
     worktree, four handoff sections in the task file — but nothing could
     pick any of it up, because `run_task_cycle` always begins at the
     arquitecto and the `--resume` already in the codebase is an internal
     re-prompt (`_with_resume_notes`), not a way in. The obstacle was
     structural rather than missing: everything a phase needs around it —
     the account lease, the card lock, the worktree, the commit, the gates,
     the handoff appended to the task file that the next phase reads —
     lived in closures over `run_task_cycle`'s locals, so a phase was not
     a thing anything could run one of. It is now four module-level pieces
     — `CycleContext`, `open_cycle`, `run_phase`, `close_cycle` — with
     `run_task_cycle` rebuilt on them, signature and behaviour unchanged,
     and `run_single_phase` as the second caller the split was for. What
     the verb deliberately does not do is the cycle's own judgement: it
     reads no verdict, opens no further round and merges nothing — and it
     logs that rather than leaving it to be assumed, `merge-task` still
     being the way to offer the branch back. `--final` buys exactly what
     the cycle gives its last phase: the learnings carried in beforehand,
     the debt the earlier phases declared recorded and filed,
     `status: done` in the task file and on the board, and no orphaning of
     the entries on the way out; without it the task stays `pending` and
     this run's
     entries go back to unowned, which is what every phase before the last
     one should do. `--round` is not cosmetic — it labels the section
     (`## revisor (round 2)`), it is what the phase is told it is on, and
     it decides whether `escalated_effort` applies, so a resumed second
     round that does not say `2` is dispatched as though the first had
     never happened. `--role` is checked against the roles the prompt
     builder has a case for, so a typo costs a usage error rather than a
     full-price run, and a task with no stored description is a usage
     error naming `run-task`: the verb resumes and does not start, and
     there is deliberately no `--description`, because rewriting the ask
     underneath a phase picking up somebody else's work is not a thing to
     make easy. A phase that did not land blocks the card and exits
     non-zero, so a hand-driven cycle stops instead of running the next
     phase over the top of it. 20 new unit tests — 12 on
     `run_single_phase`, 8 on the CLI — 800 passing and 10 skipped.
     Exercised 2026-09-26 on the run it was built for: T-008's revisor
     round 2 and auditor, dispatched one verb at a time, 9m28s and 8m00s,
     both landing on `cuenta2` in a new session, the task closing `done`
     with no lock left behind. The dispatched run showed one thing the
     unit tests could not. The closing log — resolved debt not recorded,
     no debt card filed, no merge attempted — was not only a caveat for
     the operator: the auditor had to re-derive the debt ids it filed
     under, and said so in its own Risks ("if cards do exist, `card` is
     wrong"). So a hand-resumed cycle paid for its last phase with a
     weaker record than the same phase inside `run_task_cycle`, and the
     harness wrote that down itself:
     `T-008-auditor-gets-no-debt-ids-after-a-hand-resume.md`. It was read
     then as the price of resuming at all rather than a defect in the
     verb. That reading was wrong, and **closed 2026-09-28**: the cause
     was not the resume, it was that only the *prose* rendering of a
     phase's handoff ever reached disk, so the structure the debt work
     needs — what a task declared, what a revisor accepted, what a commit
     resolved — existed solely in the memory of the one process that held
     all of it. `run_phase` now also writes the parsed return to
     `<hive>/<task-id>/handoffs/<role>.json`, at the single point every
     phase passes through whichever verb dispatched it, and
     `run-phase --final` reads the implementador's and the revisor's
     answers back from there to run the cycle's own three close-out steps:
     resolved debt recorded, the accepted debt filed as cards, the filing
     note handed to the auditor so it names the ids it actually owns. Only
     the merge is still not attempted. A task whose earlier phases predate
     the storing has no record to read and closes as before; the two cases
     are different log lines, because "no debt filed" is a different fact
     when there was something to file from.
3. Acceptance: **passed 2026-09-24** (T-005, `.data/verify/t005-acceptance.txt`).
   - Re-run V3 with the default config (3 rounds, 2 accounts) and without
     hand-seeding the task file.
   - Pass:
     - The task ends `done`, or `blocked` only after real change requests.
     - The revisor and auditor review the implementador's commits.
     - The task branch holds the change, and `node --test` passes on it.
   - All four met: `done` with the revisor APPROVED in round 1, both
     reviewers citing `84490fe` by SHA, three commits carrying
     `subtract` and its test, `node --test` green on the branch tip.
   - What passing did not cover, because the config was run rather than
     forced: the revisor approved in round 1, so rounds 2 and 3 and
     `escalate_effort_after_round: 2` never opened; and no phase
     failed, so failover never fired and cuenta2 sat IDLE beside a
     working cuenta1. Two accounts were configured and one was used.
     The cross-account `--resume` leg of V5.1 was meant to be folded in
     here and was not — the three resumes this run made were handoff
     shrinks, all on the one account. Item 4 below has since made a
     phase change hands and settled it.
4. V5.2 re-run: **passed 2026-09-24** (T-006,
   `.data/verify/v52-failover.txt`). The run the item above left owing:
   a phase changing hands mid-flight, forced with the injected 429 so it
   did not have to wait on a real exhaustion. All four of V5.2's criteria
   met, and the claim this item was written to test — that commits and
   resumes now interact — held in the sharpest form available: the
   arquitecto did its whole job on cuenta1, was refused, left its edits
   uncommitted in the shared worktree because `_should_commit` declines a
   rate-limited phase, and cuenta2 resumed the same session in the same
   worktree, verified rather than redid, and the dispatcher landed one
   commit under cuenta2's name carrying both accounts' work. One phase,
   two accounts, one commit, one `## arquitecto` section.
   - What passing did not cover: cuenta1 failed on the *first* phase, so
     the failover was never asked to resume a phase whose predecessor had
     already committed, and `_recheck_cooling_accounts` never ran, because
     cuenta2 was idle before every phase — nothing had exercised an
     account coming *back* from `COOLING_DOWN` mid-task.
   - What it opened: the failover writes nothing to the log, and a
     resumed phase is handed the pre-resume git snapshot as if it were
     current. Both were fixed, and item 5 re-ran the failover against the
     fixes.
5. V5.3, and V5.2 re-run against the two fixes: **passed 2026-09-25**
   (T-007, `.data/verify/v53-recovery.txt`). The recovery path item 4
   left owing, forced by making the injected 429 one-shot and seeding
   cuenta2 `COOLING_DOWN`: with no account idle, `_recheck_cooling_accounts`
   ran for the first time in a real task, returned both accounts in four
   seconds, and the arquitecto resumed on cuenta2 — then phases 2-4 ran on
   the recovered cuenta1, which is the point of recovering an account
   rather than parking it for the run. One `## arquitecto` section, one
   commit carrying both accounts' work, both accounts `IDLE` at the end,
   handoff budgets four for four, `node --test` green on the branch tip.
   The two gaps T-006 opened were re-checked in the same run and hold: the
   log now names the hand-over and both recoveries, and the resumed prompt
   carried the stale-context note, 569 bytes of it, which the phase then
   acted on.
   - What passing still does not cover: the 429 fired on the *first* real
     call again, so a failover on a phase whose predecessor had already
     committed remains untested — the one clause of item 4 that T-007
     does not close. It needs a wrapper that lets the first phase through
     and refuses the second. And of the five new log lines, the
     over-threshold park `WARNING` and the exhausted-pool `ERROR` cannot
     be forced without real quota exhaustion, so they stay unit-tested
     only.

### When to re-run checks later

- `CLAUDE_CODE_VERSION` bumped in `docker/agent/Dockerfile`: V1 and V5.1.
- `vibe_kanban.command` changed, or the npx server's version bumped: V2.
- `vibe-kanban` image updated: V0.8 only. Nothing on the dispatcher's path
  to the board reads it.
- Compose or image changes: V0.
- Changes to `dispatch_phase` or `run_task_cycle`: V3 and V4, plus V5.2
  if the change touches the failover branch — `is_rate_limit_error`, the
  `COOLING_DOWN` transition, `release_stale_lock`, `_should_commit`, or
  how `resume_session_id` is carried across the `continue`. V3 and V4
  both run on one account that never gets refused, so they walk straight
  past that branch; V5.2 is the only check that enters it, and it needs
  the injected 429 to do so. Add V5.3 if the change touches the recovery
  branch — `_recheck_cooling_accounts`, the `exclude` set `dispatch_phase`
  builds, or `_with_resume_notes` and the stale-context note it attaches.
- The `test:` command in a project's `docs/README.md` changed, or a
  project gained an index for the first time: V3 again against that
  project. That command is the only gate that runs project code, it runs
  it in the writers' worktree, and nothing between the frontmatter and
  `sh -c` validates it — an entry that needs a dependency the worktree
  has not got is reported as "could not run" and everything keeps going,
  which is the safe failure but also a silent one.
- Changes to `dispatcher/gates.py`: V3, and read the gate section (or its
  absence) in the task file. The unit tests fake `docker exec`, so what
  they cannot cover is whether `git merge-base`, `ls -d` and `grep -r`
  behave the same inside the agent image as they do in the fakes.
- Changes to `dispatcher/learnings.py`: V3, and V4.1 for the release
  path. The unit tests drive the store directly, so what they cannot show
  is whether a phase handed the table actually greps it before debugging;
  and `mark_orphaned` only runs on a cycle that ends without merging,
  which V4.1 is the cheapest way to produce.
- Changes to `dispatcher/debt.py`: V3, and — for the card half — a board
  that answers. V2.6 was that board and closed FAIL, so the check moved
  to Phase 0 of `docs/plans/board.md`, which shipped as `T-008`: run it
  against `local_board` rather than the retired service. The unit tests
  fake both `create_issue` and `docker exec`, so what they cannot show is
  whether a board accepts a `[debt] ` title and answers with an id the
  index row can carry, nor whether the dedupe read finds
  `docs/debt/README.md` in the writers' worktree of a project that
  actually has one — a read that fails there costs a duplicate card and
  says so only in the log.
- `mapping_enabled` turned on for the first time: V3 again, against a
  project with no `docs/README.md`. The mapper is the only phase that
  ignores `default_model`, and the only one with a turn budget, so its
  cost and its cut-off are the two things no earlier check measured.
- `CLAUDE_CODE_VERSION` bumped, if `mapping_enabled` is on: check that
  `claude --max-turns 1` is still accepted before trusting V1. The flag
  works on 2.1.273 but is absent from `--help`, so a bump could drop it
  without a changelog line, and the mapping phase would then run
  unbounded.

## Stage 2 — Prioritized future work

Taken in the README's order. Before starting an item, run the gates it
depends on. A gate that fails reshapes the item before any design work.

| Item (README *Prioritized*) | Run first | Notes |
|---|---|---|
| 1. Project memory | D1, D5 | Debt cards need `create_issue`, which V2.6 closed FAIL — `docs/plans/board.md` Phase 0 supplied it locally and shipped as `T-008`, so that gate is closed; D5 sharpens the test gate rather than blocking it |
| 2. Token economy | V1.1 fields, D2, D3 | D3 only if the caveman wrap is adopted |
| 3. Unattended 24/7 operation | V4.2, V4.4, D2 | V4.2 sizes the orphan-phase race; D2 gives machine-readable reset times |
| 4. Per-role model selection | Item 2's usage records | Data-driven split, not a guess |
| 5. Task profiles | D4, D5, D6 | Epic decomposition needs `create_issue`, which Phase 0 provided as `T-008` |
| 6. Observability and hardening | V0.5, V0.6b | Note: the dispatcher mounts `claude_shared` and `docker.sock` |
| 7. Code-intelligence tooling | D4 (for `--mcp-config`), D7 | Memory headroom for indexers, as in D6; D7 sizes the per-worktree index |
| 8. Mid-phase compaction | — | Only if V3 or stage 1 runs show long phases failing |
| 9. Parallel dispatch | V5 | Low value with 2 accounts |
| 10. Multi-provider containers | V1 and V5.1 per provider | The target CLI needs headless JSON output and session resume |

### Deferred gates

These only matter to the item that needs them, so they run when it
starts, not in stage 0.

- **D1 — Subagent revive after a parent resume** (item 1, minimal quota).
  - Run:
    1. In a `-p` session, spawn a subagent.
    2. Kill the phase mid-subagent (V4.1's timeout).
    3. `--resume` the parent on the same account, then on the other one.
    4. Ask it to revive the subagent by its raw agent ID.
  - Pass: the subagent continues with its context.
  - Otherwise the fallback is to resume the role's previous session.
  - **Run 2026-09-23: PASS on both legs**, so the fallback is not needed.
    The revive is the `SendMessage` tool addressed by the raw agent id;
    the CLI itself announces the orphan on the resume
    (`system/task_notification`, `status: stopped`). The cross-account leg
    works because the subagent's own transcript lives in `claude_shared`:
    the task's `output_file` under container-local `/tmp` is a symlink into
    `/root/.claude-account/projects/…/subagents/agent-<id>.jsonl`, and the
    entrypoint symlinks that directory to `/root/.claude/projects`. The
    harness never has to ask the model for the id — it is in
    `system/task_started`, and on disk in the filename next to a small
    `agent-<id>.meta.json` (`agentType`, `description`, `toolUseId`,
    `spawnDepth`), which is enough to enumerate orphan subagents. Limit
    measured: a subagent keeps its instructions and every *completed* turn;
    a turn the kill interrupts leaves nothing behind, so a single-turn
    subagent redoes its work — still better than a respawn, which also has
    to be re-briefed. Evidence: `.data/verify/d1-subagent-revive.txt`.
- **D2 — `rate_limit_event` on a Pro account** (items 2 and 3, minimal
  quota).
  - Run: `claude -p "Reply OK" --output-format stream-json --verbose`
  - Pass: the stream has a `rate_limit_event` whose `rate_limit_info`
    carries `utilization` and `resetsAt`.
  - If it does: drop the `/usage` probe and read the final `result`
    message from the stream.
- **D3 — The caveman wrap** (item 2, minimal quota).
  - Run: V1.1 and V5.1 through the wrap on one account.
  - Pass: identical JSON shape, `usage` figures present, and `--resume`
    works.
- **D4 — Plugins and MCP config across `--resume`** (items 5 and 7,
  minimal quota).
  - Run:
    1. Start with `--plugin-dir <pack>` and ask for the pack's skill list.
    2. Resume without the flag and ask again.
    3. Resume with the flag and ask again.
  - Pass: you know whether the flag must be re-passed on every resume.
  - Repeat with `--mcp-config` for item 7.
- **D5 — Dependencies in fresh worktrees** (items 1 and 5, no quota).
  - Run: `git worktree add` on a real TS/JS target, then time
    `npm ci` / `pnpm install` inside it, and check its disk use under
    `.data/projects`.
  - Decision: install per worktree, share a cache, or use a warm
    `node_modules` template.
  - No longer blocks item 1's test gate: a command that cannot start
    (exit 127, "cannot find module", "command not found") is reported as
    a note rather than a red suite, since no implementador can install
    dependencies from inside its own session. Until this gate is run and
    decided, that gate is simply inert on JS projects.
- **D6 — Browser verification within limits** (item 5, no quota).
  - Run: a dev server plus headless Chromium (e.g. Playwright) in the agent
    container. Separately, the Playwright image in the dind sidecar
    reaching the agent's dev server over the network.
  - Pass: one of the two fits under the 4 GB `mem_limit` and can reach the
    app.
- **D7 — A code index per worktree** (item 7, no quota). The decision
  already taken is that the *dispatcher* owns index freshness, not the
  role skills: a role that forgets to re-index, or that dies mid-phase,
  leaves the next one querying a stale graph and trusting it. One index
  per worktree, rather than one at the project root, because the
  indexers' default exclude lists have no `worktrees/` entry and would
  index every role's copy of every file — though since 2026-09-21 the
  checkout carries `/worktrees/` in its `.git/info/exclude`, which a
  gitignore-aware walker honours, so measure rather than assume that a
  root index still sees them. What's unmeasured is the cost.
  - Run: on a real target repo, build the index in a fresh worktree and
    time it; repeat with the worktree already indexed to time an
    incremental sync; measure the index's disk use and multiply by the
    worktrees one task holds (one shared writer checkout plus one per
    reviewing role); check the indexer's resident memory against the
    agent container's 4 GB `mem_limit`.
  - Pass: a full build is short enough to sit inside `create_worktree`
    without stalling the phase, an incremental sync is negligible per
    phase, and the disk and memory totals fit.
  - Otherwise: index only the writer worktree and let reviewers query it
    read-only, or drop to on-demand indexing for the roles that ask.

## Results log

Add one row per check run, newest at the bottom. Link longer output
(saved under `.data/verify/`) from the notes.

| Date | Check | Result | CLI / images | Notes and decision |
|---|---|---|---|---|
| 2026-09-16 | V0.1 | FAIL | Docker 29.8.0; Compose v5.5.1; kernel 7.0.0-31-generic | Runtimes listed are `io.containerd.runc.v2`, `nvidia`, `runc` — no `sysbox-runc`; ports 8787/8788/5000/9100 confirmed free on 127.0.0.1. Decision: the dind sidecars and V0.7 wait for the operator (install sysbox, or a local privileged-dind override for verification only, or defer V0.7). See `.data/verify/v0.1-prereqs.txt`. |
| 2026-09-16 | V0.3 | FINDING | — | No run needed (per ROADMAP). README step 5's host run (`python -m dispatcher.cli --config config.yaml`) can't work with the shipped config: `state_dir`/`hive_tasks_dir` are container paths, and `vibe-kanban`/`collector` only resolve on `ia_harness_net`. Decision: stage 1 fix; every check in this run uses the dispatcher container via the `dispatch` helper instead. |
| 2026-09-16 | V0.2 (control plane) | PARTIAL | Claude Code CLI 2.1.273; ia-harness-agent `13e602bda885`; ia-harness-dind-sidecar `372bb55db3e6`; compose-collector `1d0554f2b87f`; compose-dashboard `641a90faa248`; compose-dispatcher `20a8978ed21c`; registry:2 `a3d8aaa63ed8` | `$DC up -d vibe-kanban collector dashboard registry-mirror` failed entirely because Compose pre-pulls every named image before starting any, and `ghcr.io/bloopai/vibe-kanban:latest` was denied on anonymous pull — an image-reference defect (private or nonexistent image; anonymous GHCR pulls work on this host for other images), not a credentials problem, per `docker-compose.yml:10` and `docker-compose.coolify.yml:41`; see V0.8. Retried with `$DC up -d collector dashboard registry-mirror`: 3 of 4 services healthy, Up 14 minutes, RestartCount 0 for all three, StartedAt ~20:46:08Z, clean logs, no crash loop; vibe-kanban never started. No stray `dispatcher` container exists. Leftover `ia-harness-collector`/`ia-harness-dashboard`/`ia-harness-dispatcher` images from 2026-09-15 are stale and unused by compose (not removed). Decision: stage 1 fix — operator chooses a Kanban image source before V0.2 can move beyond PARTIAL and the V2 rows below can run; agents and sidecars remain deferred per the agent checks (V0.2 agents onward) and V0.1. Evidence: `.data/verify/v0.2-build.txt`, `.data/verify/v0.2-control-plane.txt`. |
| 2026-09-16 | V0.2 (dispatcher service) | FINDING | — | `$DC config --profiles` returns empty and `--services` lists `dispatcher` with no profile gate, so `docker-compose.yml:77,81-82` (`restart: "no"`, `command: run-task --task-id CHANGE_ME --project CHANGE_ME`) fires unconditionally on any bare `up -d`; `README.md:157,163` instructs exactly that bare `up -d`. Once an account is logged in, `dispatcher/dispatcher.py:300` `run_task_cycle` would, in order: (1) reap expired locks, (2) set the Kanban task status to `in_progress:arquitecto`, (3) probe `/usage` via `check_quota_ok` (`:247`, itself a Claude exec), (4) mark the account BUSY, (5) `acquire_lock` (`:257`), which creates `.hive/tasks/CHANGE_ME.md`, (6) `create_worktree` for project `CHANGE_ME`, (7) run the arquitecto exec; not reproduced here, per constraints (read-only investigation only). Decision candidates: add `profiles: ["dispatcher"]` as `docker-compose.coolify.yml:99` already does, or name services explicitly in README step 4. |
| 2026-09-16 | V0.6 | PASS | compose-dashboard `641a90faa248` | `curl -si 127.0.0.1:8788/` with no credentials and with a wrong password both returned 401; with the credentials from `.data/verify/dashboard-credentials` returned 200, matching the expected 401/401/200 sequence. Evidence: `.data/verify/v0.6-dashboard-auth.txt`. |
| 2026-09-16 | V0.8 | FAIL | — (image never pulled, no digest obtainable) | vibe-kanban never started: `ghcr.io/bloopai/vibe-kanban:latest` is private or does not exist — anonymous GHCR pulls work on this host for other images, so this is an image-reference defect in `docker-compose.yml:10` and `docker-compose.coolify.yml:41`, not a credentials problem. `$DC pull vibe-kanban` reproduces the same denial (exit 1). `curl 127.0.0.1:9100/` and `/sse` both connection-refused, exit 7 for each; no `vibe-kanban` container exists for `docker logs`. Decision: stage 1 fix, operator chooses a Kanban image source (build from upstream, run via `npx vibe-kanban` — npm package `vibe-kanban@0.1.44` exists — or a vetted community image); since the compose image is unobtainable, the V2 MCP surface was instead probed directly against the pinned npm build `vibe-kanban@0.1.44` in a disposable container (not via compose) — see rows V2.1-V2.6 below; the `dispatch` helper used for the agent checks still needs `$DC run --rm --no-deps` so `depends_on` doesn't try to pull this image (Kanban-dependent steps will then fail separately). Compose file left unchanged per constraints. Evidence: `.data/verify/v0.8-kanban.txt`. |
| 2026-09-16 | V2.1 | FINDING | npm vibe-kanban@0.1.44; ia-harness-agent `13e602bda885` | Probed the npm build (not the compose image, which is unobtainable per V0.8) in a disposable container `ia-harness-verify-vk` (launch command not captured verbatim in evidence, so specific flags are not restated here; cleanup confirmed no leftover container or image). Pass criterion is "the session initializes over SSE" (`docs/ROADMAP.md:316`); NOT met. Transport order tested: (1) SSE `http://127.0.0.1:9100/sse` — fails; verbatim: `httpx2.SSEError: Expected response with content type 'text/event-stream', got 'text/html'.` (`v2-calls.txt:10`); this text/html response confirms no MCP route is served at `/sse`. (2) Streamable HTTP `http://127.0.0.1:9100/mcp` — fails; `mcp.shared.exceptions.MCPError: Server returned an error response` (the HTTP status/content-type of `/mcp` itself was not captured, only this client-side error). (3) stdio via `docker exec -i -e MCP_HOST=127.0.0.1 -e MCP_PORT=9100 -e HOST=127.0.0.1 -e PORT=9100 -e BACKEND_PORT=9100 ia-harness-verify-vk npx -y vibe-kanban@0.1.44 mcp` — succeeds (rmcp logs `client initialized`, `Service initialized as server`); no `--mcp` flag exists, the entry point is the `mcp` subcommand. 33 tools discovered via `list_tools()`, saved to `.data/verify/v2-tools.txt`. This is an operator decision among the ROADMAP's three transport options (`docs/ROADMAP.md:317-320`), not forced here: (a) subprocess from the dispatcher — needs Node plus `vibe-kanban@0.1.44` added to the dispatcher image, and `MCP_HOST`/`MCP_PORT`/`BACKEND_PORT` pointed at the kanban service; (b) a stdio-to-SSE proxy sidecar next to the kanban app; (c) call the REST API directly — V2.6 shows it covers only `repos`/`workspaces` without login. Evidence: `.data/verify/v2-calls.txt`, `.data/verify/v2-tools.txt`. |
| 2026-09-16 | V2.2 | FINDING | npm vibe-kanban@0.1.44; ia-harness-agent `13e602bda885` | Compared the 33-tool surface against `dispatcher/vibe_kanban_client.py`'s assumed tools `list_tasks{project}`, `create_task{project,title,description}`, `update_task{id,status}`: no tool named `list_tasks`/`create_task`/`update_task` exists, and no "task" vocabulary appears anywhere in the surface. The closest analog is "issue": `list_issues`, `create_issue`, `get_issue`, `update_issue`, `delete_issue` (plus tag/assignee/relationship variants). Argument names and shapes also differ: `create_issue` takes `title` (required), `project_id`/`description`/`priority`/`parent_issue_id` (optional), not `project`+`title`+`description`; `update_issue` takes `issue_id` (required) not `id`. Also present but unrelated to task CRUD: `start_workspace`/`create_session`/`run_session_prompt`/`get_execution`/`update_session`/`list_sessions`, which launch a coding-agent executor (`start_workspace`'s schema has an `executor` enum incl. `CLAUDE_CODE`, `AMP`, `GEMINI`, `CODEX`, ...); by policy, agent-launching tools were not called; only their schemas were recorded. Decision: stage 1 fix, `vibe_kanban_client.py` needs a full rewrite of tool/arg names to the issue vocabulary if this MCP surface is kept, not a small patch. Evidence: `.data/verify/v2-tools.txt`. |
| 2026-09-16 | V2.3 | FINDING | npm vibe-kanban@0.1.44; ia-harness-agent `13e602bda885` | Checked the caller-supplied-ID assumption (dispatcher expects to mint IDs like `T-001`): `create_issue` accepts no caller-chosen ID; all id inputs across all 33 schemas are `format: "uuid"`, so IDs are server-assigned (not confirmed live: no issue could ever be created in this container, see V2.6). `list_issues` exposes a `simple_id` *filter* field ("Filter by issue simple ID (case-insensitive exact match)"), a candidate human-readable key, but it is never settable via create/update. Mapping choice, named explicitly per `docs/ROADMAP.md:322-326`'s two options: the task file stores the server-assigned Kanban ID (rather than `run-task` taking it as an argument); `simple_id` is a candidate human-readable key for display/lookup if vibe-kanban populates one, unconfirmed live. Decision: stage 1 fix, drop the `T-001`-style caller-minted-ID assumption; the dispatcher stores whatever opaque ID `create_issue` returns. Evidence: `.data/verify/v2-tools.txt`. |
| 2026-09-16 | V2.4 | FINDING | npm vibe-kanban@0.1.44; ia-harness-agent `13e602bda885` | Checked whether `update_issue.status` accepts arbitrary strings or a fixed set. Schema-level answer: not arbitrary strings — `update_issue.status` is documented as "New status name for the issue (must match a project status name)", i.e. a fixed set of status names defined per project, not free text; no `list_statuses`-type tool exists among the 33 to enumerate the valid names for a given project (live confirmation pending: no issue or project was ever reachable to update, see V2.6 blocker). Decision: stage 1 fix, map the dispatcher's `in_progress:<role>`/`blocked`/`done` convention onto the target project's existing status names (not create new ones — no status/project-creation tool exists), carrying the `<role>` portion separately via `add_issue_tag` (tag on the issue) or a comment/description note; confirm the exact status names and the update-rejection error text live once a project is reachable. Evidence: `.data/verify/v2-tools.txt`. |
| 2026-09-16 | V2.5 | NOT RUN | npm vibe-kanban@0.1.44; ia-harness-agent `13e602bda885` | Could not verify live: no issue existed to read back (same blocker as V2.4/V2.6). Schema-only evidence: `get_issue` (takes `issue_id`) exists, and `description` is a settable field on both `create_issue` and `update_issue`, strongly implying `get_issue` returns it, but this was never confirmed against a real response. Decision: stage 1 fix, re-run live once an issue can be created. Evidence: `.data/verify/v2-tools.txt`, `.data/verify/v2-calls.txt`. |
| 2026-09-16 | V2.6 | FINDING | npm vibe-kanban@0.1.44; ia-harness-agent `13e602bda885` | Attempted issue creation live via stdio: `create_issue{title, description}` (no `project_id`, none obtainable) -> `{"success": false, "error": "project_id is required (not available from workspace context)"}`; `list_issues{}` with no `project_id` fails identically. Root cause, attributed by channel: MCP `list_organizations` -> `{"success": false, "error": "VK API returned error status: 401 Unauthorized"}`; REST `GET /api/organizations` -> HTTP 401 `"Unauthorized. Please sign in again."`; the app log shows `Remote client initialized with URL: https://api.vibekanban.com` — organizations/projects (and therefore issues, nested under a project under an organization) appear to require an operator-provided cloud login, with no local account in this fresh npm build (only `/api/organizations` and this log line directly show the wall). `list_projects` requires `organization_id`, itself unobtainable; no `create_project`/`create_organization` tool exists. The three guessed REST paths (`/api/projects`, `/api/v1/projects`, `/api/issues`) all returned HTTP 200 text/html (SPA fallback shell, same index.html as any unmatched route) — none returned 401; the real REST paths were not found. MCP `list_repos`/`list_workspaces` return empty lists; REST `/api/repos` and `/api/workspaces` separately return HTTP 200 with empty data. Per policy, did not sign up for a vibe-kanban cloud account; no `verify-scratch` project or issue could be created, read, updated, or deleted. Decision: stage 1 fix, real vibe-kanban issue CRUD appears to require an operator-provided `api.vibekanban.com` login before the dispatcher can create or update anything; this is a hard prerequisite the current design (`vibe_kanban_client.py`, which assumes anonymous local project access) does not account for at all. Evidence: `.data/verify/v2-calls.txt`. |
| 2026-09-16 | V0.2 (agents) | PASS | ia-harness-agent `13e602bda885` | `$DA up -d --no-deps agent-cuenta1 agent-cuenta2` (dind sidecars stay down: no sysbox on this host, per V0.1), exit 0; expected "orphan containers" warning for `compose-dashboard-1`/`compose-registry-mirror-1`/`compose-collector-1` (shared compose project name with the control plane), harmless, not acted on. Both containers reached `State: running`, `RestartCount: 0`, no crash loop; logs show only `ia-harness: event hook registered in /root/.claude/settings.json`. `docker exec agent-cuenta{1,2} cat /root/.claude/settings.json` shows `python3 /usr/local/lib/ia-harness/emit_event.py` registered for all 8 hook events (PreToolUse, PostToolUse, UserPromptSubmit, Notification, Stop, SubagentStop, SessionStart, SessionEnd) on both accounts. Deviation from V0.2 as written: `dind-cuenta1`/`dind-cuenta2` intentionally not started. Side effects of `$DA up`, not cleaned up: root-owned empty dir `.hive/` (bind-mount source in docker-compose.agents.yml) and Docker volumes `compose_dind_cuenta1_data`/`compose_dind_cuenta2_data` (compose creates a stack's declared volumes even for services not started). Evidence: `.data/verify/v0.2-agents.txt`. |
| 2026-09-16 | V0.4 (baseline) | NOT RUN | — | Pre-login only; login is the operator's step (V0.4 proper, incl. `/status` and persistence, is deferred). `docker exec agent-cuenta{1,2} sh -c 'ls -la /root/.claude /root/.claude/credentials /root/.claude.json'`: on both accounts `/root/.claude` holds only an empty `credentials/` dir and `settings.json`; `/root/.claude.json` does not exist (`ls` exits 2 on that path, rest of the command still runs). No `.credentials.json` anywhere in `claude_shared`/`claude_creds_cuenta1`/`claude_creds_cuenta2` (credential guard passed before agents were started; names-only file listing for both agents recorded, not just asserted). Baseline for the operator's future re-run: `.data/verify/v0.4-baseline.txt`. |
| 2026-09-16 | V0.5 | PASS | — | Posted a `VerifyProbe` event to each agent's `emit_event.py` over `docker exec -i` (exit 0 both), then `curl 127.0.0.1:8787/events?source_app=agent-cuentaN&limit=5`: agent-cuenta1's probe (event id 1) and agent-cuenta2's probe (event id 2) each listed with the matching `source_app`. Evidence: `.data/verify/v0.5-hooks.txt`. |
| 2026-09-16 | V0.7 | NOT RUN | — | Blocked by V0.1: no sysbox on this host, so `dind-cuenta1`/`dind-cuenta2` were never started (agents brought up with `--no-deps`). No commands run. |
| 2026-09-16 | V0.9 | FINDING | ia-harness-agent `13e602bda885`; compose-dispatcher `20a8978ed21c` | Read `dispatcher/cli.py` first: `bootstrap-project` (`cli.py:33-39`) only does `docker_exec.run_docker_exec(container, "/", ["mkdir", "-p", project_dir])` — no model call. Ran `$DC run --rm --no-deps -v "$PWD/.data/verify/config.yaml:/app/verify.yaml:ro" dispatcher --config /app/verify.yaml bootstrap-project --account cuenta1 --project bootstrap-probe` (plain `up`/`run` would try to pull vibe-kanban via `depends_on`, unobtainable per V0.8): exit 0, `bootstrap-probe` created root-owned under `.data/projects/`. Plain `git status` on the host-owned `.data/projects/scratch` then failed exactly as predicted: `fatal: detected dubious ownership in repository at '/data/projects/scratch'`, exit 128 — so the remaining plain commands (worktree add, commit) would only repeat that error; re-ran them once, diagnostic only, with `git -c safe.directory='*'`: `git status` then reports clean, `git worktree add -b verify/probe .../worktrees/probe/x` succeeds; `git config --global --get user.name` (unrelated to repo ownership) returns nothing, exit 1; the in-worktree commit then failed exactly as predicted: `Author identity unknown ... fatal: unable to auto-detect email address (got 'root@<container-id>.(none)')`, exit 128. Cleanup: the two plain cleanup commands (`git worktree remove --force`, `git branch -D verify/probe`) both failed with the same dubious-ownership error (not "not found"), since run without the diagnostic flag; `docker exec agent-cuenta1 rm -rf /data/projects/bootstrap-probe` and `rm -rf /data/projects/scratch/worktrees` both exit 0. `find .data/projects/scratch -user root` still found the git-internal leftovers from the diagnostic worktree add (`.git/worktrees`, `.git/refs/heads/verify`, `.git/logs/refs/heads/verify`, two new loose objects under `.git/objects/58` and `.git/objects/bd`), each confirmed disjoint (by ownership and by directory) from the host-owned `init`-commit objects/refs; removed each of those 5 paths via `docker exec agent-cuenta1 rm -rf <path>`; `find -user root` then empty. Host checks confirm clean: `git -C .data/projects/scratch status --short` empty, `log --oneline --all` shows only `c903450 init`, current branch `master`. `docker exec agent-cuenta1 git --version` -> `2.39.5` (Debian bookworm apt package). On 2.39, `safe.directory` only matches an exact path or the literal `*`; trailing-`/*` prefix matching needs git >= 2.46, so `/data/projects/*` would not work on this image. Decision: stage 1 needs either `safe.directory=*` in the image's system gitconfig (acceptable since these containers only ever operate on the bind-mounted `/data/projects` tree) or one exact `safe.directory` entry per project written at bootstrap/by the entrypoint, plus a git identity (image default, or `-c user.name=… -c user.email=…` per commit); this is one option consistent with ROADMAP.md:205's candidate fix ("`safe.directory` in the image"), which names no pattern. This run also created, as ordinary compose side effects (not cleaned up): root-owned empty dirs `.hive/` and `dispatcher_state/` in the repo root, and Docker volumes `compose_dind_cuenta1_data`, `compose_dind_cuenta2_data`, `compose_vibe_kanban_data`. Evidence: `.data/verify/v0.9-git.txt`. |
| 2026-09-16 | V0.10 | PASS | — | `docker exec agent-cuenta1 timeout --kill-after=30 5 sleep 100` -> exit 124; `docker exec agent-cuenta1 timeout --kill-after=2 2 sh -c 'trap "" TERM; sleep 100'` -> exit 137. Matches the 124-then-137 pair `exec_claude` maps to "timed out". Evidence: `.data/verify/v0.10-timeout.txt`. |
| 2026-09-16 | V1 (flags) | PASS | Claude Code CLI 2.1.273 | `docker exec agent-cuenta1 claude --version` -> `2.1.273 (Claude Code)`, matches the expected pin. `claude --help` lists `-r, --resume [value]`, `--model <model>`, `--effort <level>`, and `--output-format <format>`. Also lists `--permission-mode <mode>`, `--allowedTools, --allowed-tools <tools...>`, and `--dangerously-skip-permissions` (needed later for V1.2). No model turn made (`--version`/`--help` only). Evidence: `.data/verify/v1-help.txt`. |
| 2026-09-17 | V0.4 | FAIL | Claude Code CLI 2.1.273; ia-harness-agent `13e602bda885` | Operator logged in on `agent-cuenta1` only (`/status`: Claude Pro account; no prompt sent). The OAuth file landed at `/root/.claude/.credentials.json`, inside the `claude_shared` volume, not under `/root/.claude/credentials/` (the per-account `claude_creds_cuenta1` volume, still empty). `agent-cuenta2` sees the identical file (sha256 compared, hashes not recorded), so it is already running as account 1 and a login there would overwrite account 1. `/root/.claude.json` (account and onboarding state) is in no volume, so a recreate loses it; the persistence step was not run. Decision: stage 1 fix before logging in on `agent-cuenta2`; re-run V0.4 after it. Evidence: `.data/verify/v0.4-login.txt`. |
| 2026-09-19 | V0.4 | PASS | Claude Code CLI 2.1.273 (image pin); ia-harness-agent `a497162d6ca0` | Re-run against stage 1's per-account `CLAUDE_CONFIG_DIR`. cuenta1's old shared login was moved into `claude_creds_cuenta1` with the README *Upgrading from the shared-login layout* chain (exit 0), the image rebuilt and both agents recreated with `--no-deps`; the operator then logged in on `agent-cuenta2` (no prompt sent). Both agents: `.credentials.json` and `.claude.json` sit in `$CLAUDE_CONFIG_DIR` with mode 600, none in `claude_shared`, `/root/.claude.json` absent; 12/12 shared names are symlinks to `/root/.claude/<name>`; leftover-warning count 0. `oauthAccount.accountUuid` differs between the two agents (sha256 compared in shell, values not recorded). Persistence: after `$DA up -d --force-recreate --no-deps agent-cuenta1 agent-cuenta2` the operator reported no login prompt and the same accounts, the accountUuid hashes were unchanged, and `hasCompletedOnboarding` is true on both (onboarding itself not reported). `.device-keys.json` was never created, in `claude_shared` or in either config dir. Also per account now: `history.jsonl`, `sessions/`, `cache/`, `backups/`. Stale copies from the old layout remain in `claude_shared` (`cache/`, `history.jsonl`, `sessions/`, `.last-update-result.json`, an empty `credentials/`). Finding: the CLI auto-updater overrides the pin inside the containers (2.1.278 installed at 20:28Z in cuenta1's pre-recreate container), and a later update cut off mid-install left `agent-cuenta1` with no `claude` on `PATH`; `agent-cuenta2` still runs 2.1.273. Decision: V0.4 closed; the auto-updater goes to stage 1 (candidate: `DISABLE_AUTOUPDATER=1` in the agent image). Evidence: `.data/verify/v0.4-rerun.txt`. |
| 2026-09-19 | Stage 1: CLI auto-updater | PARTIAL | Claude Code CLI 2.1.273 (image pin); ia-harness-agent `d113cd6920cc` | Fix for the V0.4 re-run's auto-updater finding: `ENV DISABLE_AUTOUPDATER=1` in `docker/agent/Dockerfile` right after the pinned install, guarded by `test_final_stage_disables_cli_autoupdater`. Grepping the pinned CLI (throwaway container, no model call) shows it checks `DISABLE_UPDATES`, then `DISABLE_AUTOUPDATER` as a truthy string, before the `autoUpdates` setting. Image rebuilt (`a497162d6ca0` -> `d113cd6920cc`, npm layer cached) and both agents recreated with `--no-deps`. Both agents: running, RestartCount 0, `DISABLE_AUTOUPDATER=1` and `CLAUDE_CONFIG_DIR=/root/.claude-account` in the container env, `claude --version` -> `2.1.273 (Claude Code)` (restores `agent-cuenta1`), leftover warnings 0, `.credentials.json`/`.claude.json` mode 600 in `$CLAUDE_CONFIG_DIR`, only `claude-code` under `node_modules/@anthropic-ai/`, no `.claude-*` temp entries in `/usr/local/bin`; accountUuid still differs between the agents (sha256 compared in shell). Not yet observed: that no update runs during a real interactive session (needs a quota-spending run). Baseline mtimes of `.last-update-result.json`/`.update.lock` recorded for that check. Decision: fix kept; close after one interactive session leaves the version and those mtimes unchanged. Evidence: `.data/verify/cli-autoupdate-fix.txt`. |
| 2026-09-19 | Stage 1: CLI auto-updater (interactive session) | PASS | Claude Code CLI 2.1.273 (image pin); ia-harness-agent `d113cd6920cc` | The check the PARTIAL row above left open. One interactive `claude` session per agent under a PTY, about 5.5–6 minutes in the REPL each (the updater check runs while the REPL is up), with the model set to sonnet first via `"model": "sonnet"` in the shared `settings.json`; the dispatcher always passes `--model`, so only interactive runs see that key. A one-word prompt on each, answered. `/doctor` was also sent: in 2.1.273 it is model-driven, not local, and ran read-only shell diagnostics in auto mode until interrupted. It left only `/tmp/jqerr` in cuenta1's container layer and an empty `/x` in cuenta2's, nothing in a volume. Before vs after, on both agents: `claude --version` and `package.json` stay 2.1.273; the `claude-code` package dir mtime is unchanged, as are the `.last-update-result.json`/`.update.lock` mtimes in `$CLAUDE_CONFIG_DIR`; no `.claude-*` temp entries appear under `/usr/local/bin`; `node_modules/@anthropic-ai/` still holds only `claude-code`; there are no native-install paths; 12/12 symlinks. Only session files changed: `.claude.json`, `backups/`, `history.jsonl`, `sessions/`, and a new `shell-snapshots/`. Neither terminal log shows an update notice. Decision: auto-updater fix closed. Evidence: `.data/verify/cli-autoupdate-fix.txt`. |
| 2026-09-23 | D1 | PASS | Claude Code CLI 2.1.273; agent-cuenta1 and agent-cuenta2 | Spawned one backgrounded `general-purpose` subagent in a `-p` session whose id was fixed up front with `--session-id` (so the resume never depends on the killed process having printed anything), killed the phase mid-subagent with the same `timeout` shape V4.1 produces (exit 124 at 25 s), then `--resume`d the same session twice. Leg A, same account: exit 0, 41 s; the CLI opened with `[system/task_notification] {"task_id": …, "status": "stopped", "summary": "Background agent \"slow count\" didn't finish before the previous session ended"}` before the model acted, and the model revived it with `SendMessage{to: <raw agent id>}` -> `{"success":true,"message":"Resuming agent …","resumedAgentId":…}`. The revived subagent produced the task's end marker, which appears nowhere in the revive message: it still held its original prompt. Leg B, the same session resumed on the *other* account's container: exit 0, 8 s, same mechanism, and it remembered the work it had done during leg A. Why the cross-account leg works: `/tmp/claude-0/…/tasks/<id>.output` is only a symlink into `/root/.claude-account/projects/…/subagents/agent-<id>.jsonl`, and `entrypoint.sh` symlinks `/root/.claude-account/projects` -> `/root/.claude/projects`, the `claude_shared` volume both agents mount; the file is byte-identical in both. The subagent's turns are not in the parent transcript (0 `isSidechain` lines there). `--permission-prompts none` is what makes this runnable headless, since no `settings.json` has a `permissions` key and `exec_claude` passes no bypass flag; the probe task was therefore tool-free. Caveat from a second run: the subagent emits its whole answer as one atomic assistant message, and a turn reaches the sidechain file only when it completes — an interrupted turn leaves nothing, so a revive restores instructions plus completed turns, not in-flight text. Decision: item 1 does revive-before-respawn, and the dispatcher harvests agent ids itself (stream or disk) rather than asking the model, which the Agent tool tells it not to surface. Evidence: `.data/verify/d1-subagent-revive.txt`. |
| 2026-09-23 | V5.1 | PASS (same-account) | Claude Code CLI 2.1.273; agent-cuenta1 and agent-cuenta2 | Run for its one open bullet — whether a resumed session answers under the same `session_id` or a new one — since D1 had already proved the cross-account mechanism but pinned the id with `--session-id`. **The id is the same**: leg 2 on `agent-cuenta2` returned `06fce22d-7a42-4d42-8934-bdefebd5d211` (`is_error` false, 1 turn, $0.0409), and leg 3, `--resume` of that id in the same cwd, came back under the identical id and answered PELICAN (`is_error` false, 1 turn, $0.0055). Not run cross-account: leg 1 on `agent-cuenta1` hit a real monthly spend limit (429) and never started, which shuts both directions until it resets. The precondition was checked instead — the transcript cuenta2 wrote is visible from `agent-cuenta1` at `/root/.claude/projects/-data-projects-scratch/06fce22d-….jsonl` and at the `/root/.claude-account/projects/…` spelling of the same file, same size, mode 600. Decision: the dispatcher stores a phase's session id once and reuses it for every resume; re-measure the id cross-account opportunistically when both accounts can spend at once. Evidence: `.data/verify/v51-cross-account-resume.txt`. |
| 2026-09-23 | V5.4 | FINDING | Claude Code CLI 2.1.273; agent-cuenta1 | The first real rate-limit result, caught opportunistically as V5.1's leg 1. `is_rate_limit_error` classifies it correctly, but only on `api_error_status` 429; the wording — "You've hit your monthly spend limit · … · your session limit resets 5:50pm (UTC)" — matches none of the four text terms, because "hit your limit" is broken up by "monthly spend". Also: `subtype` is `"success"` on an error result, so the success check must keep reading `is_error`; and the refused call cost $0 in 759 ms, so probing a blocked account is free. Decision: leave the classifier alone for now — the 429 is the documented key and it fired — and widen the text fallback only if a sample turns up without `api_error_status`. The reset time in the text is the `resetsAt` signal item 2 wants, in prose rather than a field, which is D2's question. Evidence: `.data/verify/v51-cross-account-resume.txt`. |
| 2026-09-23 | V3 | FINDING | Claude Code CLI 2.1.273; agent-cuenta2 | Full cycle on one account, 6m12s wall, exit 0, three phases (arquitecto -> implementador -> revisor). Deviation, authorized: the spec pins `cuenta1`, which was still rate-limited from V5.4, so the run used `cuenta2` with the spec's caps in a fresh config (`max_revision_rounds: 1`, `escalate_effort_after_round: 0`, `escalated_effort: high`, `gates_enabled: true`, no `vibe_kanban`). What passed: per-role worktree and `agent/task/T-001` branch, with the revisor's torn down at the end and `work` left standing; the three handoff sections in the task file with `owner`/`heartbeat` cleared between phases; total silence about any board; the revisor's canonical `**Status:** complete · **Verdict:** CHANGES_REQUESTED` (not the `VERDICT:` prose fallback) with a matching outcome; the gates ran, found nothing on an empty diff with no `docs/`, wrote no section, and cost the implementador exactly one `claude` call (collector `UserPromptSubmit` x1); `.hive/learnings/inbox/` and `harness/` pre-created and empty; no debt filed; `cuenta2.json` back to IDLE. What failed, in order of size: (1) **nothing could be written at all** — `exec_claude` passes no permission flag and `settings.json` has no `permissions` key, so with `--permission-prompts` defaulting to `host` and no host under `-p`, 43 of 62 tool calls were denied (PreToolUse minus PostToolUse: 13/15/15), including writes inside each phase's own worktree; (2) **no phase ever read its handoff** — `_role_prompt` hands the role the *path* `/data/.hive/tasks/T-001.md`, which the file tools cannot reach from the worktree (`test -r` says readable at OS level, `Read` and `cat` both refused, `dangerouslyDisableSandbox` included), so context transfer moved zero bytes and only the task description travelled, because the dispatcher embeds it whole in every prompt; `/data/.hive/learnings/` was equally ungreppable; (3) the expected ``gates: no `test:` in docs/README.md`` line **cannot** appear — nothing anywhere configures logging, the root logger sits at WARNING, and all 12 `logger.info` calls are dropped, which is also why the whole run log is five lines; (4) a blocked task leaves no trace — `_update_task_status` is Kanban-only and returns silently with no board, the frontmatter still reads `status: pending`, and the exit code is 0; (5) `escalated_effort: high` is a no-op — the arquitecto gets no `--effort` flag yet its records already read `"effort":"high"`, because high is the CLI default for opus; (6) two of three handoffs blew their byte budget (4817/4096 and 3139/3072) and each shrink resumed the same session for an extra paid turn, 5 turns for 3 phases; (7) `procs agent-cuenta2` does not work, the agent image has no procps; (8) the auditor never ran, so only 3 of 4 working roles were sized; (9) debt proposed by the arquitecto is structurally dropped, since only the implementador's list is filed. The spec's expected failure ("roles don't see each other's code") was **masked**: nothing was written, so there was no uncommitted work to be invisible — though the revisor flagged the mechanism itself ("Work may sit uncommitted in worktrees/T-001/work, unreadable here") and still reached the right verdict from the branch alone. Decision: (1) and (2) are one stage 1 change to `exec_claude` and need *both* halves — a permission grant alone still leaves the handoff unreadable; (3) and (4) are small and go with it; re-run V3 after, to unmask the visibility failure and to size the auditor. Evidence: `.data/verify/v3-end-to-end.txt`. |
| 2026-09-23 | V1.2 | FAIL | Claude Code CLI 2.1.273; agent-cuenta2 | Answered for free inside V3's authorized spend, so the check never ran on its own. The baseline it was going to measure is confirmed denied: `dispatcher/docker_exec.py` `exec_claude` passes no `--permission-mode`, no `--allowedTools` and no `--dangerously-skip-permissions`, and the agent's `settings.json` has no `permissions` key, so under `-p` with `--permission-prompts` at its `host` default and nobody to answer, every Write, Edit and Bash that would prompt is denied automatically — 43 of 62 calls across three phases, with `permission-not-granted` and "This command requires approval" recorded verbatim in the handoffs. The three fallbacks the spec lists are still untried, and the saved help shows more choices than the spec knew about: `--permission-mode` takes `acceptEdits`, `auto`, `bypassPermissions`, `manual`, `dontAsk` or `plan`, `--permission-prompts` takes `host` or `none`, and `--add-dir <directories...>` is what reaches `/data/.hive`. Decision: stage 1 grants a permission mode *and* adds `/data/.hive` as an allowed directory, since V3's finding (2) is a separate wall that a permission grant does not open; measure which mode is the least generous one that works when it lands. Evidence: `.data/verify/v3-end-to-end.txt`, `.data/verify/v1-help.txt`. |
| 2026-09-23 | V1.3 | PASS | Claude Code CLI 2.1.273; agent-cuenta2 | Answered for free inside V3's authorized spend, and the hypothesis is **refuted**: `/usage` under `-p` is not sent to the model, it is handled as a local command. Three probes, one before each phase at cwd `/data/projects`, each a ~3.3 KB transcript shaped `{"type":"queue-operation","content":"/usage"}`, a `local-command-caveat` meta message, then `{"type":"system","subtype":"local_command","content":"<local-command-stdout>…"}` — no assistant record and no result record. The collector agrees on all three session ids: `SessionStart` and `SessionEnd` only, no `UserPromptSubmit` and no `Stop`. So the probe costs zero model turns. It also parses: `UsageInfo(session_pct=43, session_reset='Sep 23, 7:59pm (UTC)', week_pct=23, week_reset='Sep 25, 1:59pm (UTC)')`, and the dispatcher's own proof that it parsed all three times is the absence of `quota probe failed for account …` from the run log — `check_quota_ok` logs that at WARNING (the one level this run could print) and fails open on any exception. Decision: keep the pre-dispatch probe, it is free; the cost worry the check was opened for does not exist. Evidence: `.data/verify/v3-end-to-end.txt`. |
| 2026-09-23 | V1.2 (mode probe) | PASS | Claude Code CLI 2.1.273; agent-cuenta2 | The measurement V1.2's decision asked for: which permission mode is the least generous one that works. Five `claude -p` calls on `agent-cuenta2`, one per mode, each asking for the same three things — Write a file in cwd, Read a handoff planted *outside* cwd and reached with `--add-dir`, and a Bash `echo > file`. No flag: write denied, bash denied, **read outside granted**. `dontAsk`: identical to no flag, so it grants nothing here. `acceptEdits`: all three. `auto`: all three. `bypassPermissions`: never starts — `--dangerously-skip-permissions cannot be used with root/sudo privileges`, and the agent container runs as root. The read result is the load-bearing one: `--add-dir` opens the handoff with no mode at all, so V3's findings (1) and (2) really are two independent grants and the stage 1 fix needs both. Caveat this row has to carry: the Bash ask was `echo > file`, which is inside the CLI's own safe set, so `ran_bash: true` for `acceptEdits` does **not** mean arbitrary programs run — 9d's N3 and the allowed-tools probe below pin that down. Decision: default to `acceptEdits` (the least generous mode that writes) plus `--add-dir` on the hive root; keep `auto` in reserve for execution, and do not use `bypassPermissions` while the container is root. Evidence: `.data/verify/permission-mode-probe.json`. |
| 2026-09-23 | V3 (re-run) | PARTIAL | Claude Code CLI 2.1.273; agent-cuenta2 | The re-run V3's decision demanded, 20:08:53Z→20:16:06Z (7m13s wall), exit 0, task `T-002` so V3's `T-001` artifacts survive for comparison. The A/B is literally one config line, `permission_mode: acceptEdits` — same account, same caps, same prompt — and the CLI's own hook payloads report the mode it ran under (`default` for V3, `acceptEdits` here), so the measurement is of the flag and nothing else. Headline: tool calls that never completed fell from **43 of 62 (69%)** to **17 of 54 (31%)**, and unlike V3 this run can separate denials from misses — 16 of the 17 are permission denials quoted verbatim by the phases, 1 is a plain miss. By tool, Write 7/0→10/9, Edit 3/0→1/1, Read 17/9→14/12, Bash 30/5→23/9. Fixed: (1) **writes land** — Write+Edit 0 of 10 → 10 of 11, two commits on the task branch (`e045c4d`, `95ebce1`), and the branch ends carrying `subtract()` and its test where V3's ended identical to master; (2) **the handoff travels** — all three phases opened `/data/.hive/tasks/T-002.md` as their *first* tool call and all three succeeded, via `--add-dir`; (3) **the log says things** — 7 dispatcher records against V3's zero, including the exact ``gates: no `test:` in docs/README.md`` line the V3 criterion asked for; (11) **docs duties discharged** — `docs/implementations/T-002.md`, 87 lines, committed, and the arquitecto reasoned explicitly that the change warrants no ADR. New and unmeasurable before: learnings transfer worked first time (3 in `.hive/learnings/inbox/`, and the implementador read the arquitecto's before repeating its mistake, saving four turns); the no-quota gates reached the revisor, which ruled on the `pointers` note and dismissed it with reasoning; and **F8 came unmasked and did not fail** — the revisor saw every file from its own worktree, because the harness commits each phase's edits on its behalf, and reached CHANGES_REQUESTED on the one thing genuinely missing. F9 softened: the arquitecto's debts are still structurally dropped, but the implementador now reads and re-declares them. Not fixed, and this is the finding: **N1 — `node --test` never executed, in either run.** Six attempts across all three phases, every one "This command requires approval", so the task's own acceptance criterion was unreachable and the revisor blocked the task for exactly that, correctly. The dispatcher's no-quota test gate did not compensate, because that project declares no test command — nothing in the whole loop executed the code; three opus phases agreed it was correct by reading it. **N2** a phase cannot unblock itself: writing `Bash(node:*)` into its own worktree's `.claude/settings.local.json` is refused (9d's only Write denial), and so is probing the existing allowlist. **N3** what `acceptEdits` + `--add-dir` actually grants: file tools anywhere under cwd or an add-dir; Bash only from the CLI's safe read-only set (`ls`, `cat`, `grep`, `git status`, `git log`, `git diff`) plus create-only forms (`mkdir -p`, `echo >`); denied are any `node`, `git add`, `git commit`, `git -C <path>` *even when the path is the phase's own cwd*, and anything outside cwd + add-dirs. So the rule is not "edits yes, Bash no"; it is "edits yes; Bash only from the CLI's own safe set, only inside the granted directories". F4 unchanged (a blocked task still leaves `status: pending`, state IDLE, exit 0 — the run log is now its only record). F5 proven a no-op twice (`effort: high` appears in *both* runs' payloads, including V3's arquitecto, which got no `--effort` at all; high is opus's default). **F6 got worse**: all three handoffs overran their byte budget (4142/4096, 6559/5120, 4010/3072) against V3's two of three, costing 6 paid turns for 3 phases — the budgets were sized against a harness that could not write, and productive phases have more to say. F7/F10 unchanged. Decision: N1 is the only thing still blocking a task, and the choice it forces is (a) stay on `acceptEdits` and let the dispatcher's gate be the only thing that executes anything — free, but useless on a project that declares no test command — or (b) move to `auto`, which puts a server-side classifier in the loop and lets a phase run arbitrary programs. The allowed-tools probe below found a third way and is what shipped. Evidence: `.data/verify/v3-rerun-permissions.txt`, `.data/verify/v3-rerun-run.log`. |
| 2026-09-23 | V1.2 (allowed-tools probe) | PASS | Claude Code CLI 2.1.273; agent-cuenta2 | Run straight after the V3 re-run to answer N1 without paying for another cycle: can a harness-side allowlist grant the one thing `acceptEdits` withholds? Five `claude -p` variants on `agent-cuenta2` over a toy project with one passing test, the prompt pinned to `node --test` and told not to rewrite the command. Control (`acceptEdits`, no allowlist): **not executed**, 3 turns, "The command requires your approval". `acceptEdits` + `Bash(node --test*)`, + `Bash(node*)`, + `Bash(node:*)`: **executed**, 2 turns each, "Tests ran and passed (1/1)". And **no mode at all + `Bash(node --test*)`: also executed** — so `allowed_tools` and `permission_mode` are orthogonal grants, not a refinement of one another, and a space-bearing pattern survives the hop as a single argv element. Decision, shipped in `e543188`: wire `allowed_tools` into the config, empty by default, restated on every phase command *and* on the shrink and gate retries, because `--resume` inherits no flags. `auto` is therefore not needed to answer N1, and the repo's own toy project got the `test:` key it was missing so the no-quota gate can cover the same ground for free. What this does **not** settle: the allowlist has never run on a dispatched cycle — it is proven on a toy prompt only. One more V3 re-run, with `allowed_tools` set, is what closes the README's *Known gaps* bullet, and it costs quota. Evidence: `.data/verify/allowed-tools-probe.json`. |
| 2026-09-24 | V3 (re-run 2) | PASS | Claude Code CLI 2.1.273; agent-cuenta2 | The run the row above was waiting on: does the allowlist hold on a real dispatched cycle, not a toy prompt? **Yes.** Task `T-003`, 13:34:33Z->13:46:07Z (11m34s wall), exit 0, one config line added to the re-run's — `allowed_tools: [Bash(node --test*)]` on top of `permission_mode: acceptEdits`. The A/B now runs three deep on the same task shape: T-001 denied 43 of 62 calls and wrote nothing; T-002, with the mode, denied 17 of 54 and ended `blocked` on the test; **T-003 denied 12 of 95 and ended `done`, revisor APPROVED**, four phases (arquitecto -> implementador r1 -> revisor r1 -> auditor). Per tool, from the collector: Bash 31 pre / 19 post / 12 denied, Edit 6/6/0, Read 31/31/0, Write 20/20/0, StructuredOutput 7/7/0 — **only `Bash` was ever denied**. Five `node --test` runs across three worktrees under four phases, every one `# tests 2 # pass 2 # fail 0`, and all five are a phase's own execution: the dispatcher's free test gate calls `run_docker_exec` directly rather than through `claude`, so it fires no hook and emits no event, and the run log carries no "gates: no `test:` in ..." skip line, so the gate ran and was green. The 12 denials are a clean taxonomy, none of them mysterious: shell `for` loops (2), `git -C <absolute path>` even on the phase's own cwd (4), paths above the worktree root (3), `npm test --silent` (2 — left out of the allowlist on purpose, so a refusal would surface as a finding rather than be papered over), and `node --test *.test.js 2>&1; echo "EXIT=$?"` (1). That last one sharpens the pattern: chaining is *not* what breaks the match — another event chains two commands after a pipe and passes — it is `echo "EXIT=$?"` falling outside `Bash(node --test*)`. **What the run opened is four new README gaps**, all of them things only a run that could write could expose: both reviewing roles' worktrees were deleted with their edits uncommitted, and the auditor is the phase the prompts make responsible for the durable doc indexes; nothing retires a learning the harness has since disproved (T-002's "`node` needs approval" was read out of `archive/` by T-003's arquitecto, already false); `parse_usage_output` rejected `Current session: 0% used` for want of a `· resets` clause, so the quota probe went blind on the freshest account (a warning, not a stop — the run still exited 0); and three of four handoffs blew their byte budget — arquitecto 4266/4096, revisor 4386/3072, auditor 5209/2048 — at one paid `--resume` apiece. Still unexercised: the two-account half of Stage 1's acceptance, since cuenta1 is out of monthly spend. Evidence: `.data/verify/v3-allowlist.txt`, `.data/verify/v3-allowlist-run.log`. |
| 2026-09-24 | V4 (auditor writes) | PASS | Claude Code CLI 2.1.273; agent-cuenta2; dispatcher image `sha256:3fb0c9d6c062` | The three fixes the row above opened, run through a dispatched cycle instead of unit tests. Task `T-004`, 17:37:54Z->17:47:51Z (9m57s wall), exit 0, same four phases, same verdict (`done`, revisor APPROVED at round 1). The A/B is one artefact, not one field: `config.yaml` is identical field-by-field to T-003's snapshot, and the dispatcher image — which bakes the source — was rebuilt 73 seconds before START. **(1) The auditor committed.** `ef3e53f` on `agent/task/T-004`, author `auditor (cuenta2)`, ten files and 295 insertions. **(2) The commit is scoped.** Every one of the ten paths is under `docs/`, no out-of-scope path was logged, the phase's session cwd moved from `worktrees/T-004/auditor` to `.../work`, and the cleanup line reads `removed review worktrees revisor` where T-003's read `auditor, revisor` — the revisor is still a reviewer by design and `git log --author=revisor` is empty. **(3) The quota probe parsed.** T-003's log had the `Unexpected /usage output format` warning at +4s as line 6; T-004's line 6 is the learnings INFO instead, with the same four quota-probe sessions in the collector. Refusals: **4 of 75 (5%)**, the best of the four runs (T-001 43/62, T-002 17/54, T-003 12/95), and the auditor's own 30 calls — ten `Write`, two `Edit` — were refused none. The four, verbatim: the arquitecto attempting its own commit (once, not retried), the revisor's `node --test *.test.js 2>&1; echo "EXIT=$?"` reproducing T-003's finding 5 in another phase, and two `ls` calls above the worktree root. No `git -C` and no `npm` was attempted at all. Two things it opened rather than closed: `learnings.reconcile` fired for the first time and promoted three entries to `confirmed`, while the one entry this very fix disproved (`T-003-phase-edit-missing-from-next-worktree.md`) stayed in the inbox and went to all four phases — a named instance of the stale-learning gap; and the auditor rewrote the toy project's `docs/README.md` worktree path from `work` to `<phase>`, off a `git worktree list` still padded with three earlier tasks' reviewer directories, so that doc is now wrong the other way. Handoff budgets blew three of four again, a different three (implementador 5400/5120, revisor 4149/3072, auditor 4602/2048). Still unexercised: the two-account half of Stage 1's acceptance, since cuenta1 is out of monthly spend. Evidence: `.data/verify/v4-auditor-commit.txt`, `.data/verify/v4-auditor-commit-run.log`. |
| 2026-09-24 | V5.1 (quota probe) | FINDING | Claude Code CLI in agent-cuenta1 and agent-cuenta2; `--model sonnet` | Authorized as "a little quota on cuenta1", to find out whether the monthly wall V5.1 hit on 2026-09-23 was still standing. **It is not**: one real sonnet turn on `agent-cuenta1` answered `PROBE-OK` with `api_error_status: null`, `stop_reason: end_turn`, 1 turn, $0.0405, served by `claude-sonnet-5`. Session 33%→34%, week 51%, requests 239→240 across the turn, so the counters track spend and update promptly. Two side results. First, the containers really are two logins — sha256 fingerprints of `oauthAccount` and of `.credentials.json` differ on both axes (`407fc743…`/`a57c095f…` against `b05e3f6b…`/`5d770bbe…`), nothing printed in the clear. Second, and the reason this is a FINDING rather than a note: **`claude -p "/usage"` is a local command**, `local_command: "usage"`, `duration_api_ms: 0`, `num_turns: 0`, `total_cost_usd: 0`. `check_quota_ok` therefore reads counters off the container's disk and never asks the service, so a healthy percentage says nothing about whether the next call is refused — which is exactly the shape of the one refusal on record (V5.4: 429 on arrival, no session started). The output also states it covers "local sessions on this machine" only, so `quota_threshold_pct` is compared against a number that undercounts any use from another device, and `check_quota_ok` fails open on exception on top of that. Decision: record it as a README gap rather than fix it blind — the probe's $0/641ms is worth keeping, and nothing has yet measured whether a refused account answers `/usage` with numbers or an error, which is what decides between reading `/usage` harder and reacting to the first real call. Also stale as of this row: `config.yaml`'s header and its `accounts:` comment. Evidence: `.data/verify/quota-probe-local-only.txt`. |
| 2026-09-24 | Stage 1 acceptance (V3, default config) | PASS | Claude Code CLI 2.1.273; agent-cuenta1 + agent-cuenta2; dispatcher image rebuilt 18:52:54 | Task `T-005`, 18:53:45→19:07:16 (13m31s wall), exit 0, launched with `--description` rather than a hand-seeded task file as the acceptance requires. All four criteria met: `status: done` with the revisor `APPROVED` in round 1; revisor and auditor both cite the implementador's commit `84490fe` by SHA; `agent/task/T-005` carries three commits (`16ee9c9` arquitecto, `84490fe` implementador, `b43b7f6` auditor), 16 files, +407/−4, with `subtract` exported from `sum.js` and a `test('subtract')` whose second assertion pins operand order; `node --test` green on the tip (2 pass, 0 fail). **cuenta1 served real pipeline work for the first time** — BUSY on T-005 for all four phases, after answering 429 on arrival since 2026-09-23. **Handoff budgets hit all three branches in one run**: arquitecto 5857/4096 retried to 3983 (inside), implementador 5387/5120 kept as-is inside the 512-byte margin without paying for a `--resume`, revisor 6060/4096 and auditor 5911/3584 each retried once and stayed 362 and 350 over — two WARNINGs where the four runs that set the numbers would have produced zero. **Finding: a revisor's edits never land.** Review worktrees are detached at the branch tip by design, so there is no branch to commit them onto; the revisor reported the `pointers` gate closed and its own grep agreed, while the same grep on the branch still returned both pointers. The auditor re-ran that verification on its own branch, found the gate open and repaired it in `b43b7f6` — turning the loose pointers into `docs/debt/T-005-D1.md` and `D2` — so the tip is clean. But the APPROVED verdict was issued by a phase that believed in a fix it had not made, and nothing but the auditor's own diligence caught it. This is the mechanism behind `T-003-phase-edit-missing-from-next-worktree`, which had the symptom without the reason. Also unprompted: the arquitecto's Risks list reintroduced the `<phase>`-for-`work` worktree-path error verbatim; the implementador checked it, refused the edit and filed the rule. 4 learnings filed and stamped. Not measured: failover (no phase failed, cuenta2 IDLE throughout), rounds 2–3 and effort escalation (approved in round 1), and V5.1's cross-account `--resume`. Decision: acceptance item 3 closes; the detached-revisor gap goes to the README as a known gap; item 4 (V5.2 re-run) is next and needs quota. See `.data/verify/t005-acceptance.txt` and `t005-acceptance-run.log`. |
| 2026-09-24 | V5.2 (failover, fault injected) | PASS | Claude Code CLI 2.1.273; agent-cuenta1 + agent-cuenta2; dispatcher image `sha256:e4cd2f01739f` | The check the acceptance run could not reach, and the one the pipeline had never been through: a phase changing hands mid-flight. Task `T-006`, 22:32:49Z→22:48:28Z (15m39s wall), exit 0, both state files `IDLE` at the start, launched with `--description`. The fault is a 204-byte wrapper on `agent-cuenta1`'s `claude`: `-p /usage` execs the real binary unchanged, so the quota gate stays honest, and everything else runs for real and then has `is_error`/`api_error_status: 429` written into its JSON. **All four criteria met.** (1) The arquitecto ran on cuenta1 and did the work — edited `sum.js` and `sum.test.js`, ran `node --test`, wrote `docs/implementations/T-006.md` — then came back a 429; cuenta1 ended `COOLING_DOWN` with `current_task_id: null`, and the lock was *released*, not left to expire: cuenta2 acquired it five seconds later, far inside the 120s heartbeat TTL that `acquire_lock` refuses a live foreign owner through. (2) The phase resumed on cuenta2 in the same session, `7b13f2fa`: the arquitecto prompt appears twice in one transcript, at 22:32:52 with `parentUuid: null` and at 22:34:39 with a parent, and the transcript sits under `-data-projects-scratch-worktrees-T-006-work` — the same cwd for both accounts, which is the cross-account `--resume` leg V5.1 left owing. (3) One `## arquitecto` section in the task file, four headings total. (4) All three writer commits are authored `(cuenta2)`; cuenta1 was never re-probed, because `_recheck_cooling_accounts` only fires when no account is idle. **And the point of Stage 1's fourth acceptance item — commits and resumes interacting — lands concretely:** cuenta1's edits stayed uncommitted in the shared writers' worktree (`_should_commit` returns False on a rate limit, by design, so a half-done phase is not put in history twice), cuenta2 inherited them, and the dispatcher committed them once as `2f33664`, 3 files, +33/−2. One phase, two accounts, one commit. Two things the run opened, both new README gaps. First, **the failover is silent**: `dispatch_phase` makes no `logger` call at all — not when it picks an account, not when it reads a 429, not when it hands over — so this run's log is shaped exactly like a clean one and the only trace is the state file and the commit author. Second, **a resumed phase is handed a stale git snapshot**: the session-start context block is re-sent verbatim on `--resume`, so the cuenta2 arquitecto was told `Status: (clean)` while three of its own edits sat modified in that worktree. It ran `git status --short`, found them, did not redo them, and filed the learning the auditor landed as `docs/learnings/session-context-block-is-stale.md` — so the check passed because the phase distrusted its context, not because the harness prevented the misread. Side result: the re-sized handoff budgets went four for four inside budget (arquitecto 5911→3540, implementador 6051→5015, revisor 4473 kept inside the 10% margin, auditor 4622→3527) against two of four on T-005, with no WARNING — though the arquitecto again overran by 44%, a second data point against leaving its 4096 alone. Wrapper removed immediately after the run; `claude` is the original symlink and `claude.real` is gone. Evidence: `.data/verify/v52-failover.txt`, `.data/verify/v52-failover-run.log`. |
| 2026-09-25 | V5.3 (recovery) + V5.2 re-run | PASS | Claude Code CLI 2.1.273; agent-cuenta1 + agent-cuenta2; dispatcher image `sha256:478699bd3c319` | The recovery path nothing had ever entered, plus a re-run of the row above against the two gaps it opened. Task `T-007`, 00:17:17Z→00:36:49Z (19m32s wall, launched 21:17 local on the 24th), exit 0, launched with `--description`. Two changes to V5.2's fault make this a different check: the 429 wrapper is **one-shot** — a `/tmp/.429-spent` marker, so the second call through it is the real binary — and **cuenta2 was seeded `COOLING_DOWN`** before the run. So when the arquitecto's result came back a 429 at 00:20:30 there was no idle account left, `pick_idle_account` returned `None`, and **`_recheck_cooling_accounts` ran for the first time in a real task**: two `/usage` probes, both accounts back to `IDLE` at 00:20:31 and 00:20:34, and the phase resumed on cuenta2 at 00:20:36 in session `f6c2f310` and the same worktree. One shot is what makes the recovery testable — an account re-probed four seconds later is genuinely healthy — and it is also what saved the run: a recovered cuenta1 is first in config order, so phases 2-4 ran on it, which is the whole point of recovering an account rather than parking it. **The two T-006 gaps re-checked in the same run and hold.** The log now carries four lines where T-006 carried none — the account picked, the refusal with the session id it is handed on, and one line per recovery — so the hand-over is legible without reading state files. And the resumed prompt is 9659 bytes against the first call's 9090: the 569-byte difference is `_STALE_CONTEXT_NOTE`, while the 347-byte handoff-shrink retry in the same session correctly carries none of it. The note was acted on, not just delivered: the resumed phase's first two commands, at 00:20:41 and 00:20:42, were the `git status --short` and `git log --oneline -3` the note names; it then re-read both sources, re-ran `node --test`, wrote a mutation check under `.hive/tasks/T-007/mutation/` to confirm the assertions fail on a broken divide, and **never re-edited `sum.js` or `sum.test.js`**. Outcome as on T-006: one `## arquitecto` section, and one commit `66eb8cd` authored `arquitecto (cuenta2)` carrying both accounts' work (5 files, +102/−4); then `863f71c` implementador and `4208960` auditor, both `(cuenta1)`, `node --test` green on the tip (2 pass, 0 fail), both state files `IDLE` at the end. Handoff budgets four for four inside for the third run running (arquitecto 5374→3961, implementador 6589→4614, revisor no line at all, auditor 4203→3032) — though the first two overran by 31% and 29%, a third data point that those two budgets are set below what the roles naturally write. 4 learnings filed and stamped. **Not covered, and now the only clause of acceptance item 4 still open:** the 429 fired on the *first* real call again, so a failover on a phase whose predecessor had already committed remains untested — forcing it needs a wrapper that lets the first phase through and refuses the second. Of the five new log lines the over-threshold park `WARNING` and the exhausted-pool `ERROR` need real quota exhaustion and stay unit-tested only. Wrapper and marker removed immediately after the run; `claude` is the original symlink, `claude.real` is gone, `claude --version` answers 2.1.273. Evidence: `.data/verify/v53-recovery.txt`, `.data/verify/v53-recovery-run.log`. |
| 2026-09-25 | V2.3, V2.4, V2.5 (re-run) | NOT VERIFIABLE | npm vibe-kanban@0.1.44 in a disposable container off the agent image | Re-measured nine days after the 2026-09-16 rows above, to settle whether the block was a missing credential or a retired service. It is the service. `api.vibekanban.com` answers `HTTP 200 ct=text/html` — the same `Vibe Kanban Remote` SPA shell — on `/`, on `/api/organizations` and on `/health`, so there is no API left behind the 401 that `list_organizations` reports. `list_issues{}` fails with `project_id is required (not available from workspace context)`, and no `project_id` is obtainable: `list_projects{}` demands an `organization_id` that `list_organizations` (401) cannot supply, and projects are exactly what PR #3387 ("Sunset project routes to an export-only page", shipped in 0.1.44) retired. So V2.3 has no issue to read an id off, V2.4 has no issue to carry a status, and V2.5 has no created issue to round-trip a description through. The 0.1.45 prerelease said to restore local projects is uninstallable: `npx vibe-kanban@0.1.45` gives `ETARGET`, the registry keeps both 0.1.45 entries in `time` and neither in `versions` — the signature of an unpublish, roughly two hours after publication — and `latest` is still 0.1.44, the build that removed the feature. **Decision: closed as not verifiable and retired as checks.** No credential changes any of it; `docs/plans/board.md` replaces the dependency with a local client rather than waiting on a dead one. Evidence: `.data/verify/v2-rerun-2026-09-25.txt`. |
| 2026-09-25 | V2.6 (re-run) | FAIL | npm vibe-kanban@0.1.44 in a disposable container off the agent image | `create_issue{title, description}` over MCP stdio returned `{"success": false, "error": "project_id is required (not available from workspace context)"}` — the same answer as 2026-09-16, now attributable: the parameter is unobtainable by construction, not withheld from this machine. This is the one V2 check that fails outright rather than going unmeasured, and it fails for everyone. The two calls that do succeed, `list_repos` and `list_workspaces`, are the local half of the surface and return empty-but-successful, which splits the 33 advertised tools cleanly into local (works) and remote (dead); the server registers all 33 unconditionally, so advertising is not capability. **Decision: `create_issue` is not coming back**, so the two prioritized items that waited on it — 1. Project memory and 5. Task profiles — now depend on Phase 0 of `docs/plans/board.md` instead. Evidence: `.data/verify/v2-rerun-2026-09-25.txt`. |
| 2026-09-25 | T-008 (board Phase 0, first dispatch against this repo) | INTERRUPTED | Claude Code CLI 2.1.273; agent-cuenta1 + agent-cuenta2; dispatcher image `8a2da0d13b8c` | The eighth dispatch of the cycle, and the first whose target was this repo rather than the toy project. Launched 20:27:46-03:00 with `--description-file -`; last dispatcher line 21:06:30-03:00, `Error waiting for container: Canceled: grpc: the client connection is closing: context canceled`, exit 125, 38m44s wall. **Not a harness defect: the host shut down under it.** `last -x` records `shutdown system down 21:08` and `reboot system boot 21:11`, `who -b` agrees, and every container reports the same 11-minute uptime afterwards — docker's CLI lost the daemon while waiting on the agent container. What survived: `agent/task/T-008` carries `e3b12f4 agent(arquitecto)` and `22c7301 agent(implementador): T-008 round 1` (12 files, +832/-48), and the implementador's round-2 edits are intact but uncommitted in `worktrees/T-008/work` (5 files, +82/-17), where `python3 -m pytest -q` reports 785 passed, 10 skipped. That diff already answers all three of the revisor's round-1 CHANGES_REQUESTED findings. Missing: the round-2 commit, its handoff, revisor round 2, and the auditor. Two gaps this exposed and nothing else would have: no resume-from-phase, and an account stuck `BUSY` — `cuenta2` was left `{"state": "BUSY", "current_task_id": "T-008"}` with `cuenta1` at `PRE_COOLDOWN`, i.e. no dispatchable account, and no code path that would ever release it. Both are now README known gaps, with the fix planned in `docs/plans/balancer.md`. One pattern worth keeping separately: all three roles blew their handoff budget on the first attempt (6540/4096, 8905/5120, 5226/4096) and two of three were still over after the rewrite — a sizing problem, not three incidents. Do not run `cleanup-task` on T-008 until the round-2 work is committed; it deletes `work` too. **Closed 2026-09-26** by the two gaps' own fixes — `release-account` freed `cuenta2`, `run-phase` ran the two missing phases — see the row below; the round-2 work is committed as `1d4ca62`, so that `cleanup-task` caution is lifted. Evidence: `.data/verify/t008-run.log`, `.data/verify/t008-run.start`, `.data/verify/t008-run.end`. |
| 2026-09-26 | T-008 resumed (`run-phase`: revisor round 2, then auditor `--final`) | PASS | Claude Code CLI 2.1.273; agent-cuenta2; dispatcher image `ece0de49462b`, agent image `21067cb640b4`; harness `cce5d8668069` | The first use of `run-phase` against a real interrupted cycle, and the close of the run above. Both phases were dispatched by the operator from a script in the working tree: the auto-mode classifier refuses a `run-phase` dispatch from the assistant ("Real-World Transactions"), so the assistant composes the command and the operator pays for it — which is the intended shape for a verb that spends quota, but worth recording as the reason there is no assistant-side log. **Routing was deterministic, and only because `cuenta2` had been released first.** `cuenta1` sat at `PRE_COOLDOWN` (88% session, 86% week against a 90% threshold — under it by two points), so had `cuenta2` still been stuck `BUSY`, `_recheck_cooling_accounts` would have flipped `cuenta1` back to `IDLE` and both phases would have landed on the nearly-empty account with no fallback behind them. Revisor round 2: 13:22:50→13:32:18 UTC, 9m28s, verdict **APPROVED**. It did what the `--note` asked — checked the operator-written `## implementador (round 2)` section against `1d4ca62`'s diff rather than taking it at its word — confirmed 785 passed / 10 skipped, closed all three of its round-1 findings, and caught what only a reader of the commit would catch: `1d4ca62`'s message cites a README section that does not exist. Auditor `--final`: 13:46:33→13:54:33 UTC, 8m00s, task `done`. It created `docs/learnings/` and `docs/debt/` with their indexes, filed the three accepted debts as `T-008-D1..D3`, and verified every learning against the code instead of copying it from a handoff. What the run showed that no unit test could: closing a cycle one verb at a time costs record fidelity. The dispatcher logged it — resolved debt not recorded, no debt card filed, no merge attempted, all of which need the implementador's and revisor's handoffs from the same process — and the auditor independently hit the same wall, re-deriving the debt ids it filed under and flagging in its own Risks that "if cards do exist, `card` is wrong". Both phases also blew their handoff budget on the first attempt (6254/4096 and 4443/3584); the revisor was still 184 bytes over after its rewrite, the auditor came back inside. That is now five of five roles across two runs, which settles it as a sizing problem rather than a run of bad luck. Aftermath: no `.lock` files, `cuenta2` back to `IDLE` unaided, `cuenta1` untouched at `PRE_COOLDOWN`, `agent/task/T-008` at `40ae7da` with 24 files and +1297/-59 against master, unmerged (`merge_on_done: false`). Evidence: `.data/verify/t008-resume.log` (the operator's terminal capture; the tee'd logs went with the scripts), and `.hive/tasks/T-008.md` sections `## revisor (round 2)` and `## auditor`, which are the primary record. |
| 2026-09-26 | T-009 read API, by hand against the running stack (the row T-009 could not write) | PASS | `compose-api-1` `9b07e3e39232`, image `compose-api:latest` `8b5ee9044cbd`; collector and dashboard unchanged; harness at `40a1dba` | T-009 declared a ROADMAP row *not done* because "a row there would claim the by-hand `curl` check, which no phase may run against the live stack" (`docs/implementations/T-009.md`, Not done). This is that check, run by the assistant against the merged stack — no quota, no dispatch. **Auth first**: `/api/tasks` with no credentials 401, with a wrong password 401, with the real pair 200, `WWW-Authenticate: Basic realm="ia-harness api"` — the V0.6 sequence, now on 8789. The credential came out of `.data/verify/dashboard-credentials` into a shell variable and was never printed or copied. **Every route, every error shape**: `/api/tasks` 200, `/api/tasks/T-009` 200, `/api/tasks/T-999` 404, `/api/accounts` 200 (`cuenta1` IDLE, `cuenta2` PRE_COOLDOWN), `/api/debt?project=ia-harness` 200, `?project=no-existe` 404, `/api/debt` with no project 400, `/api/tasks?nosuchparam=1` 400, `?limit=0` 400, `?since=abc` 400, `POST /api/tasks` 405. The envelope held everywhere: a 200 is always `{data, warnings}` and no error path 500s. **Three things only a live stack could settle.** (1) **T-009-D4 confirmed, then fixed the same day.** `/api/events` answered `[]` plus the WAL warning while the collector counted 2974 rows in the same 11898880-byte file the api saw over `/events`, with no `-wal`/`-shm` pair between writes — so the `:ro` mount, not a missing or empty database, exactly as the debt predicted. The declared fix was then applied out of cycle (one line in each compose file: `observability_data:/events`, no options), `compose-api-1` recreated, and `/api/events?limit=1` now returns one row with `warnings: []`; `events.db-shm` appears in the volume, created by the api's own `mode=ro` open, and the other two writers over that volume are unaffected — dashboard still 200 on 8788. `observability/api/app.py` was not touched: still `mode=ro`, still no `init_db`, so the mount grants the sidecar and not the rows. (2) **T-009-D3 confirmed and left open**, reproduced over HTTP rather than read off the code: `/api/debt`'s `T-009-D4` row comes back with its `where` cell ending `` …handing any later service `observability_data:/…:ro `` — opening backtick kept, closing one eaten by `_rows`. Still open on purpose: the same unwrap feeds `index_fingerprints`. (3) The first unconfirmed rule in `docs/business.md` is confirmed by the API's own 400/404 text, which enumerates `/data/projects` as `ia-harness, scratch`. **Decision:** the compose invariant was kept rather than loosened — `tests/integration/test_compose_invariants.py` names the one exception in `API_WRITABLE_MOUNTS = {"/events"}` and asserts both halves (that mount carries no options, every other api mount is still `:ro`), so reverting the fix and widening it both fail; the test was renamed `test_the_read_api_gets_no_socket_and_only_the_events_volume_is_writable`. Full suite after the change: **913 passed, 10 skipped**. `compose-api-1` is left running with the events volume read-write. Evidence: `.data/verify/t009-api-verification.txt`; the sidecar behaviour is now confirmed in `docs/learnings/a-read-only-sqlite-open-still-writes.md`. |
| 2026-09-27 | V0.6b | PASS | `compose-board:latest` `96e88f7307c4` and `compose-api:latest` `93edcde07d40`, both built this day; `compose-board-1` on 8790, `compose-api-1` on 8789; harness at `9c815f5` | The first run of this check, by the assistant against the merged stack -- no quota, no dispatch. **Auth and server-side render**: 401 with no credentials, 200 with them, `grep -c '<script'` is `0`. The credential came out of `.data/verify/dashboard-credentials` into a shell variable and was never printed or copied. **The three screens**: `/` lists `cuenta1 IDLE` and `cuenta2 PRE_COOLDOWN` and the tasks table down to `T-010 done`; `/events` 201 rows, all hook rows from `agent-cuenta1` with `cwd` inside `worktrees/T-010/work`; `/debt` 9 rows including `T-010-D1` and `T-010-D2`, so `BOARD_PROJECT` works. The api's `400 project is required` was confirmed separately, as a bearer request to `/api/debt` with no project from inside the board container. **Api stopped**: all three answer 200, each with its stamp (`as of 18:36:42 UTC`), its nav and one `no answer` region -- nothing 500s -- and after `docker start` all 14/201/9 rows come back. **Two things had to be fixed first, and both are now steps `0` and `0b` of the recipe.** The `.env` on this host was written 2026-09-16 and had neither `API_TOKEN` nor `BOARD_PROJECT`, which is the exact risk T-010's auditor recorded. Then a correct token still 401'd, because `compose-api:latest` predated T-010 -- `grep -c API_TOKEN /app/observability/api/app.py` was `0` inside the running container, and `ls /app/observability/` had `dashboard` and no `board`. The recipe said "build the image", singular, so an operator following it verbatim rebuilds only the board and meets a 401 that reads like a credential problem and is not one. One thing worth a later look rather than a fix here: the degraded region prints the whole urllib3 `ConnectionError` after `no answer`, three lines of `HTTPConnectionPool(... NameResolutionError ...)`, which meets the pass criterion and still reads badly on screen. |
