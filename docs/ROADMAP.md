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
- Record the CLI version and the `vibe-kanban` image digest with each
  result: the image is pinned only to `:latest`.

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
  - `vibe-kanban` and `collector` only resolve on `ia_harness_net`.
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
     `docker exec agent-cuenta1 sh -c 'ls -la /root/.claude /root/.claude/credentials /root/.claude.json'`.
  3. In each container, check which account `/status` reports (an
     interactive session; no model turn).
- Pass:
  - The OAuth credentials file sits under `/root/.claude/credentials/`
    (the per-account volume).
  - `/status` reports a different account in each container.
- Expected failure (hypothesis): Claude Code writes
  `/root/.claude/.credentials.json`, which is inside the `claude_shared`
  volume. Then the second login overwrites the first.
  - Candidate fixes: have the entrypoint symlink that file into
    `credentials/`, or give each account its own config dir.
  - Either fix is part of stage 1, and V0.4 has to be re-run after it.
- Persistence:
  1. Run `$DA up -d --force-recreate`.
  2. Repeat the `/status` step.
  3. Pass: no re-login and no onboarding prompt.
  - This matters because `/root/.claude.json` is not in any volume.

**V0.5 Hooks reach the collector.**
- Run:
  ```bash
  echo '{"hook_event_name":"VerifyProbe","session_id":"verify"}' \
    | docker exec -i agent-cuenta1 python3 /usr/local/lib/ia-harness/emit_event.py
  curl -s '127.0.0.1:8787/events?source_app=agent-cuenta1&limit=5'
  ```
- Pass: the probe event is listed with `source_app` `agent-cuenta1`.
- Events from a real session are checked in V1.1.

**V0.6 Dashboard auth.**
- Run:
  1. `curl -si 127.0.0.1:8788/ | head -1`
  2. The same request with `-u <user>:<password>`.
- Pass: 401 without credentials, 200 with them.

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

**V0.8 Vibe Kanban is reachable.**
- Run:
  - `curl -si 127.0.0.1:9100/ | head -1`
  - `curl -si -N --max-time 3 127.0.0.1:9100/sse | head -5`
  - `$DC logs vibe-kanban | tail`
- Pass:
  - The UI answers on 9100.
  - `/sse` returns `content-type: text/event-stream`.
- If the image listens on another port, or has no `/sse`, V2 decides the
  transport.

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

`dispatcher/vibe_kanban_client.py` assumes the following, none of it
checked:
- SSE transport at `vibe_kanban_mcp_url`.
- Tools `list_tasks {project}`, `create_task {project, title, description}`
  (returns `id`), and `update_task {id, status}`.
- Task IDs equal to `.hive` file names (`T-001`).
- Free-form status values: `in_progress:<role>`, `blocked`, `done`.

Probe the real server from the host venv:

```bash
.venv/bin/python - <<'EOF'
import asyncio
from mcp import ClientSession
from mcp.client.sse import sse_client

async def main():
    async with sse_client("http://127.0.0.1:9100/sse") as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for t in (await s.list_tools()).tools:
                print(t.name, t.inputSchema)

asyncio.run(main())
EOF
```

- **V2.1 Transport.**
  - Pass: the session initializes over SSE.
  - If the server's MCP is stdio-only, pick one of:
    - Run it as a subprocess from the dispatcher image (which has no Node).
    - Proxy stdio to SSE.
    - Call Vibe Kanban's HTTP API directly.
- **V2.2 Tool and argument names.** Compare with the three tools above.
- **V2.3 IDs.**
  - Question: does the server take a caller-chosen ID like `T-001`, or
    assign its own (e.g. a UUID)?
  - If it assigns its own: map Kanban ID to `.hive` ID. Either `run-task`
    takes the Kanban ID, or the task file stores it.
- **V2.4 Status values.**
  - Question: does `update_task` accept arbitrary strings, or a fixed set?
  - If a fixed set: map `in_progress:<role>` to a status plus a label or
    comment.
- **V2.5 Task description.**
  - Question: can a task's description be read back (`get_task` or
    similar)?
  - This is where stage 1's fix for "agents never see the task" gets the
    body. If it can't, use a `--description` flag on `run-task`.
- **V2.6 Task creation.**
  - Question: does `create_task` exist and return an ID?
  - This gates item 1's debt cards and item 5's epic decomposition.
  - Create one task in a scratch project and delete it afterwards.

### V3 — End to end on one account (task quota)

The first real `run-task`, with costs capped.

- **Config** (`.data/verify/config.yaml`):
  - `accounts`: `cuenta1` only.
  - `max_revision_rounds: 1`.
  - `escalate_effort_after_round: 0`, so round 1 already passes
    `--effort high`.
  - `phase_timeout_seconds: 1800`.
- **Task.** "Agents never see the task" is already confirmed by reading
  the code. Don't spend quota proving it: seed the task file by hand as a
  stand-in for that fix (`acquire_lock` keeps an existing body).
  ```bash
  mkdir -p .hive/tasks && cat > .hive/tasks/T-001.md <<'EOF'
  ---
  task_id: T-001
  status: pending
  owner: null
  depends_on: []
  heartbeat: null
  ---

  ## Task

  Add a `subtract(a, b)` function to `sum.js`, export it, and cover it with a
  test in `sum.test.js`. Run `node --test` before finishing.
  EOF
  ```
  If V2.3 showed Kanban assigns its own IDs, create the card there too and
  record both IDs.
- **Run:** `dispatch run-task --task-id T-001 --project scratch`
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
  - Vibe Kanban shows `in_progress:<role>` per phase and ends in `done` or
    `blocked`. Otherwise stderr has a warning per failed update.
  - The revisor's last line is a `VERDICT:` line, and the outcome matches
    it.
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
    - Kanban shows `blocked`, the account is `IDLE`, and the lock is
      released.
    - Once the kill grace has passed, `procs agent-cuenta1` shows no
      leftover `claude`, `node`, or shell children. Children that `setsid`
      out of the process group would survive: record them.
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
- **V4.4 Vibe Kanban down.**
  - Run:
    ```bash
    $DC stop vibe-kanban
    $DC run --rm --no-deps --entrypoint python dispatcher -c "
    from dispatcher.dispatcher import _update_task_status
    from dispatcher.vibe_kanban_client import VibeKanbanClient
    _update_task_status(VibeKanbanClient('http://vibe-kanban:9100/sse'), 'T-001', 'blocked')"
    $DC start vibe-kanban
    ```
  - Pass: a warning, exit 0, and it returns promptly.
  - Optional: point the client at a socket that accepts and never answers.
    It's expected to hang, which documents item 3's missing Kanban
    timeout.

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
  - The working directory has to match on both sides: the transcript lives
    in `claude_shared` under a folder derived from it.
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
    - Or `$DA up -d --force-recreate agent-cuenta1`, which also
      re-checks V0.4's persistence.
- **V5.3 Recovery re-check.**
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
- **V5.4 Real rate-limit output** (opportunistic, no extra quota). When an
  account really hits its limit during normal use:
  - Save the raw JSON and stderr.
  - Check them against `is_rate_limit_error`'s terms (`api_error_status`
    429, "rate limit", "usage limit", "hit your limit").

### Stage 0 exit criteria

- Every V0–V3 check either passed or has a recorded finding and a decision.
- V4 and V5 are done before stage 2 starts.
- README *Known gaps* / *Unverified assumptions* and the spec reflect the
  findings.

## Stage 1 — Fix the known gaps, then accept

1. Apply V0–V2's decisions. Likely candidates, depending on the results:
   - Credential isolation (V0.4).
   - Sysbox for the dind sidecars (confirmed missing, V0.1 FAIL — no
     `sysbox-runc` runtime, which also blocks V0.7): install sysbox, add a
     verification-only privileged-`dind` compose override, or defer both.
   - `safe.directory` and git identity in the image (confirmed, V0.9:
     the dubious-ownership and missing-identity errors both reproduce as
     predicted; the image's git 2.39.5 needs `safe.directory=*` in the
     system gitconfig or exact per-project entries written at bootstrap
     or by the entrypoint — a trailing `/*` pattern needs git ≥2.46 — plus
     a git identity).
   - The headless permission mode (V1.2).
   - The `/usage` probe (V1.3).
   - Where the Vibe Kanban image comes from (confirmed unpullable, V0.2
     control plane PARTIAL / V0.8 FAIL: `ghcr.io/bloopai/vibe-kanban:latest`
     is private or doesn't exist): build from upstream, run
     `npx vibe-kanban@0.1.44` (confirmed to exist and to expose the MCP
     surface below), or a vetted community image.
   - Aligning `VibeKanbanClient` with the real MCP surface (mismatched:
     V2.1–V2.2 live, V2.3–V2.4 schema only, V2.6 login wall; V2.5 not yet
     run): switch to stdio via the `mcp`
     subcommand, not an SSE client; rewrite tool/arg names to the issue
     vocabulary (`list_issues`/`create_issue`/`get_issue`/`update_issue`/
     `delete_issue`, keyed on `issue_id`); store the server-assigned UUID
     rather than minting an ID (`simple_id` is a candidate human-readable
     key, unconfirmed live); map `in_progress:<role>`/`blocked`/`done`
     onto each project's existing fixed status names, carrying `<role>`
     via a tag or note; pick a transport (subprocess from the dispatcher,
     a stdio-to-SSE proxy sidecar, or the REST API directly); allowlist
     tools so the agent-launching ones (`start_workspace`, `create_session`,
     ...) are never called; and re-run V2.4's live status-name check and
     V2.5 (description round-trip) once a project is reachable past the
     `api.vibekanban.com` cloud-login wall (V2.6).
   - Fixing README step 5's host run (confirmed, V0.3: `state_dir`/
     `hive_tasks_dir` are container paths and `vibe-kanban`/`collector`
     only resolve on `ia_harness_net`): drop it, or document a host-side
     config and why `hive_tasks_dir` can't be one.
   - A compose profile for the one-shot `dispatcher` service (confirmed
     unguarded, V0.2 dispatcher service: no profile gates it, so a bare
     `up -d` fires `run-task --task-id CHANGE_ME --project CHANGE_ME`):
     add `profiles: ["dispatcher"]` (as `docker-compose.coolify.yml`
     already does), or name services explicitly in README step 4.
   - dind bind mounts (V0.7), if confirmed.
2. Fix the README's *Known gaps*:
   - **Agents never see the task:** seed the task body from Vibe Kanban
     (V2.5) or a `--description` flag.
   - **Roles don't see each other's code:** one branch per task, a
     dispatcher commit after each implementador phase (needs V0.9's
     identity), and revisor/auditor worktrees rebuilt at that tip every
     round.
3. Acceptance:
   - Re-run V3 with the default config (3 rounds, 2 accounts) and without
     hand-seeding the task file.
   - Pass:
     - The task ends `done`, or `blocked` only after real change requests.
     - The revisor and auditor review the implementador's commits.
     - The task branch holds the change, and `node --test` passes on it.
4. Re-run V5.2 against the fixed pipeline. Commits and resumes now
   interact.

### When to re-run checks later

- `CLAUDE_CODE_VERSION` bumped in `docker/agent/Dockerfile`: V1 and V5.1.
- `vibe-kanban` image updated: V2.
- Compose or image changes: V0.
- Changes to `dispatch_phase` or `run_task_cycle`: V3 and V4.

## Stage 2 — Prioritized future work

Taken in the README's order. Before starting an item, run the gates it
depends on. A gate that fails reshapes the item before any design work.

| Item (README *Prioritized*) | Run first | Notes |
|---|---|---|
| 1. Project memory | D1, D5, V2.6 | Debt cards need `create_task` |
| 2. Token economy | V1.1 fields, D2, D3 | D3 only if the caveman wrap is adopted |
| 3. Unattended 24/7 operation | V4.2, V4.4, D2 | V4.2 sizes the orphan-phase race; D2 gives machine-readable reset times |
| 4. Per-role model selection | Item 2's usage records | Data-driven split, not a guess |
| 5. Task profiles | D4, D5, D6, V2.6 | Epic decomposition needs `create_task` |
| 6. Observability and hardening | V0.5, V0.6 | Note: the dispatcher mounts `claude_shared` and `docker.sock` |
| 7. Code-intelligence tooling | D4 (for `--mcp-config`) | Memory headroom for indexers, as in D6 |
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
- **D6 — Browser verification within limits** (item 5, no quota).
  - Run: a dev server plus headless Chromium (e.g. Playwright) in the agent
    container. Separately, the Playwright image in the dind sidecar
    reaching the agent's dev server over the network.
  - Pass: one of the two fits under the 4 GB `mem_limit` and can reach the
    app.

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
