# Roadmap stage 0, no-quota checks — execution plan

**Spec:** `docs/ROADMAP.md` (stage 0, checks V0–V2) and
`docs/superpowers/specs/2026-09-13-ia-harness-design.md`.

**Goal:** run every stage 0 check that needs neither Claude quota, `sudo`,
nor an interactive login, and record each result in the ROADMAP results
log. Checks this plan does NOT run, because they need a decision from the
operator first: V0.4 (interactive `/login`), V0.7 (needs `sysbox-runc`,
which this host lacks), V1.1–V1.3, V3, V4 and V5 (quota).

**Nature of the work:** verification, not feature code. The "diff" of a
task is its results-log rows (plus the few file changes a task names), and
the evidence is the files it saves under `.data/verify/`.

## Global Constraints

1. **No Claude quota, ever.** Never run `claude -p`, an interactive
   `claude` session, `dispatch run-task`, or anything that makes a model
   turn. The agent containers have no login (the `claude_creds_*` and
   `claude_shared` volumes were empty on 2026-09-16), so nothing can spend
   quota; if a `.credentials.json` shows up anywhere under `/root/.claude`,
   stop and report BLOCKED.
2. **Never run `docker compose ... up -d` without naming the services.**
   `docker/compose/docker-compose.yml` gives the one-shot `dispatcher`
   service no profile, so a plain `up -d` starts
   `run-task --task-id CHANGE_ME --project CHANGE_ME`. The agents' file
   needs `--no-deps` (its dind sidecars can't start without sysbox).
3. **Don't touch anything outside ia-harness.** The host runs unrelated
   containers (`nestjs-app-e2e`, `nextjs-app-e2e`, `postgres-db-e2e`,
   `redis-cache-e2e`, `nestjs-app-dev`, `postgres-db`, `redis-cache`).
   No `sudo`, no host package installs, no dockerd restart, no
   `docker system prune`, no `docker volume rm`, no stopping or removing
   containers, networks or volumes that ia-harness's compose files don't
   define.
4. **Git:** no commits, no `git stash`, `reset`, `checkout` or `restore` of
   tracked files. The working tree carries uncommitted changes to
   `docs/ROADMAP.md`, `README.md` and the spec; they must survive intact.
5. **No dispatcher, hook or image code changes.** Fixes are stage 1.
   The only tracked files a task may edit are the ones it names.
6. **Throwaway target only:** `.data/projects/scratch`. Kanban writes go to
   a scratch project only.
7. **Recording format.** One row per check in `docs/ROADMAP.md`'s
   *Results log* table, appended at the bottom, replacing the empty
   placeholder row `| | | | | |` the first time:
   `| 2026-09-16 | <ID> | PASS / FAIL / PARTIAL / FINDING / NOT RUN | <CLI version and/or image digest when relevant, else —> | <what was observed; the decision it forces; link to .data/verify/<file>> |`
   Save any output longer than a few lines to `.data/verify/<id>-<slug>.txt`
   and link it by relative path. Keep cells single-line (use `;` and
   `<br>` sparingly, no raw `|`).
8. **Language:** `README.md`, `docs/ROADMAP.md` and this plan are English.
   The spec is Spanish, written with *tuteo* (never *voseo*).
9. **Shell helpers** (from the ROADMAP *Shared setup*), run from the repo
   root `/home/johnny/Escritorio/proyectos/ia-harness`:
   ```bash
   DC="docker compose -f docker/compose/docker-compose.yml"
   DA="docker compose -f docker/compose/docker-compose.agents.yml"
   dispatch() {
     $DC run --rm -v "$PWD/.data/verify/config.yaml:/app/verify.yaml:ro" \
       dispatcher --config /app/verify.yaml "$@"
   }
   ```

## Task 1: Local setup, V0.1 and V0.3

**Checks:** V0.1, V0.3 (both are recorded findings, see below).

**Steps:**
1. Record `docker info --format '{{json .Runtimes}}'`,
   `docker compose version`, `docker version --format '{{.Server.Version}}'`,
   `uname -r` into `.data/verify/v0.1-prereqs.txt`. Also confirm ports
   8787, 8788, 5000 and 9100 are free on 127.0.0.1 (`ss -ltn`).
2. `config.yaml`: `cp config.example.yaml config.yaml` (defaults as
   shipped). Validate it loads:
   `.venv/bin/python -c "from dispatcher.config import load_config; print(load_config('config.yaml'))"`.
3. `docker/compose/.env`:
   - `DASHBOARD_USERNAME=admin`.
   - Generate a random password
     (`python3 -c "import secrets; print(secrets.token_urlsafe(18))"`),
     never print it into reports or the ROADMAP.
   - `DASHBOARD_PASSWORD_HASH` is the plain SHA-256 hex digest of the
     password, exactly as `scripts/configure.sh`'s `hash_password` computes
     it (`printf '%s' "$pw" | sha256sum | cut -d' ' -f1`).
   - Save `username:password` to `.data/verify/dashboard-credentials`,
     mode 600, so later checks and the operator can use it.
4. `.gitignore`: `config.yaml` and `docker/compose/.env` are not ignored
   today, and `.env` holds a credential hash. Append two lines under the
   existing entries: `/config.yaml` and `docker/compose/.env`. Confirm with
   `git check-ignore -v config.yaml docker/compose/.env`.
5. `scripts/setup_volumes.sh cuenta1 cuenta2` (idempotent; the volumes
   already exist).
6. Scratch target repo and verify config, exactly as the ROADMAP *Shared
   setup* shows: `.data/projects/scratch` with `sum.js`, `sum.test.js`,
   one `init` commit; `mkdir -p .data/verify && cp config.yaml
   .data/verify/config.yaml`. Run `node --test` once in the scratch repo
   (host node) and note that it passes.
7. Results log rows:
   - **V0.1** — FAIL: `sysbox-runc` is not among the runtimes (list them),
     with Docker, Compose and kernel versions. Decision: the dind sidecars
     and V0.7 wait for the operator (install sysbox, or a local
     privileged-dind override for verification only, or defer V0.7).
   - **V0.3** — FINDING, no run needed (the ROADMAP says so): README step
     5's host run can't work with the shipped config; stage 1 fix. Every
     check uses the dispatcher container.

**Done when:** `config.yaml`, `docker/compose/.env`,
`.data/verify/{config.yaml,dashboard-credentials,v0.1-prereqs.txt}` and the
scratch repo exist; `.gitignore` has the two lines; two rows are logged.

## Task 2: Build images, start the control plane, V0.2 (control plane), V0.6, V0.8

**Steps:**
1. Build, and save the build tails to `.data/verify/v0.2-build.txt`:
   - `docker build -f docker/agent/Dockerfile -t ia-harness-agent .`
   - `docker build -f docker/dind-sidecar/Dockerfile -t ia-harness-dind-sidecar docker/dind-sidecar`
   - `$DC build dispatcher collector dashboard`
   The existing `ia-harness-agent` image predates the CLI pin, so the
   rebuild is required. Then record the CLI version without starting an
   agent: `docker run --rm --entrypoint claude ia-harness-agent --version`
   (expected `2.1.273`; `--version` makes no model turn).
2. Start only the persistent control plane:
   `$DC up -d vibe-kanban collector dashboard registry-mirror`.
   Never a bare `$DC up -d` (Global Constraint 2).
3. **V0.2 (control plane part):** after ~60 s, `$DC ps` shows the four
   services running; `docker inspect -f '{{.RestartCount}}'` is 0 for
   each; `docker logs` shows no crash loop. Record image IDs.
4. **V0.2 finding, `dispatcher` has no profile:** show it with
   `$DC config --profiles` / `$DC config --services` (read-only; do not
   `up`). README step 4's `docker compose -f docker/compose/docker-compose.yml up -d`
   therefore also starts `run-task --task-id CHANGE_ME --project CHANGE_ME`
   once. Cite `docker/compose/docker-compose.yml` lines and
   `dispatcher/dispatcher.py`'s `run_task_cycle` to state what that run
   would do once an account is logged in (reap locks, set Kanban status,
   lock a `CHANGE_ME` task file, probe `/usage`, run an arquitecto phase).
   Don't reproduce it. Decision candidates: a `profiles: [dispatcher]`
   entry like the Coolify file uses, or naming services in step 4.
5. **V0.6 Dashboard auth:** `curl -si 127.0.0.1:8788/ | head -1` without
   credentials, with a wrong password, and with the credentials from
   `.data/verify/dashboard-credentials`. Pass: 401, 401, 200.
6. **V0.8 Vibe Kanban reachable:** the ROADMAP's three commands
   (`curl -si 127.0.0.1:9100/ | head -1`,
   `curl -si -N --max-time 3 127.0.0.1:9100/sse | head -5`,
   `docker logs vibe-kanban | tail`). Save output to
   `.data/verify/v0.8-kanban.txt`. Record the image digest
   (`docker image inspect --format '{{index .RepoDigests 0}}' ghcr.io/bloopai/vibe-kanban:latest`).
   If 9100 doesn't answer, find what the image actually listens on
   (`docker logs`, `docker exec vibe-kanban sh -c 'cat /proc/net/tcp*'`,
   the image's `Env`/`ExposedPorts`) and record it; don't change the
   compose file.
7. Results log rows: V0.2 (PARTIAL: control plane only, agents in Task 4,
   sidecars blocked by V0.1), V0.2 dispatcher-profile FINDING, V0.6, V0.8.

**Done when:** the four services run, the three images and the three
compose-built images exist, and the rows are logged with the CLI version
and Kanban digest.

## Task 3: V2 — Vibe Kanban MCP surface

**Prerequisite:** Task 2 left `vibe-kanban` running.

**Steps:**
1. Read `dispatcher/vibe_kanban_client.py` for the assumed contract
   (SSE at `vibe_kanban_mcp_url`; tools `list_tasks {project}`,
   `create_task {project, title, description}` returning `id`,
   `update_task {id, status}`; IDs like `T-001`; free-form statuses
   `in_progress:<role>`, `blocked`, `done`).
2. Run the ROADMAP's probe script from the host venv against
   `http://127.0.0.1:9100/sse`; save every tool's name, description and
   input schema to `.data/verify/v2-tools.txt`.
   - If SSE fails, try, in order, and record each attempt: the MCP
     Streamable HTTP client (`mcp.client.streamable_http`) at
     `http://127.0.0.1:9100/mcp`; the image's documented MCP entry point
     (`docker inspect` the image, `docker exec vibe-kanban` to look for an
     MCP binary or a `--mcp` flag, the logs). A stdio-only server can be
     probed with `mcp.client.stdio` via `docker exec -i vibe-kanban <cmd>`.
3. Answer V2.1–V2.5 from the schemas plus, where a schema can't answer,
   calls against scratch data:
   - Create (or pick, if the server requires one to exist and there is no
     create tool) a project named `verify-scratch`. Never touch other
     projects.
   - V2.3: does creating a task take a caller-chosen ID like `T-001`, or
     return a server ID?
   - V2.4: does `update_task` (or its real equivalent) accept
     `in_progress:arquitecto`? Try it, then a documented value. Record
     exact error text.
   - V2.5: can the description be read back (`get_task` or similar)?
   - V2.6: does task creation exist and return an ID? Delete the scratch
     task afterwards if a delete tool exists; otherwise record that it
     remains in `verify-scratch`.
   Save the calls and raw responses to `.data/verify/v2-calls.txt`.
4. Results log rows V2.1 … V2.6, each with the Kanban image digest and the
   decision it forces for stage 1 (e.g. "map `in_progress:<role>` to status
   `inprogress` plus …", "run-task takes the Kanban UUID").

**Done when:** six rows are logged, backed by the two files.

## Task 4: Agents without sidecars — V0.2 (agents), V0.4 baseline, V0.5, V0.9, V0.10, V1 no-quota part

**Prerequisites:** Tasks 1 and 2.

**Steps:**
1. Guard (Global Constraint 1): for each of `claude_shared`,
   `claude_creds_cuenta1`, `claude_creds_cuenta2`, list files with a
   throwaway `docker run --rm -v <vol>:/v alpine find /v -maxdepth 3`.
   Any `.credentials.json` → stop, BLOCKED.
2. `$DA up -d --no-deps agent-cuenta1 agent-cuenta2`. The dind sidecars
   stay down (V0.1). Record this deviation.
3. **V0.2 (agents part):** running, restart count 0, no crash loop;
   `docker exec agent-cuenta1 cat /root/.claude/settings.json` shows the
   `emit_event.py` hook registered (same for cuenta2).
4. **V0.4 baseline (pre-login, no pass/fail):**
   `docker exec agent-cuenta1 sh -c 'ls -la /root/.claude /root/.claude/credentials /root/.claude.json'`
   for both agents, saved to `.data/verify/v0.4-baseline.txt`. Row result
   NOT RUN (login is the operator's), with the baseline linked.
5. **V0.5 Hooks reach the collector:** the ROADMAP's probe for
   agent-cuenta1 and agent-cuenta2 (with the matching `source_app`).
6. **V0.9 Target repo, git, worktrees:** the ROADMAP's command block as
   written (the `dispatch bootstrap-project …` line included; confirm first
   by reading `dispatcher/cli.py` that `bootstrap-project` makes no model
   call). Save all output to `.data/verify/v0.9-git.txt`. For each command,
   record whether it failed and the exact message (dubious ownership,
   missing identity). If `git status` fails with dubious ownership, the
   later commands can't be meaningful: record that, then re-run them once
   with `git -c safe.directory='*'` to learn the next failure (identity),
   and label that run clearly as a diagnostic, not a fix.
   Clean up: the ROADMAP's two cleanup commands (tolerate "not found"),
   then `docker exec agent-cuenta1 rm -rf /data/projects/bootstrap-probe`
   and `docker exec agent-cuenta1 rm -rf /data/projects/scratch/worktrees`.
   Leave `.data/projects/scratch` with only its `init` commit on its
   original branch and a clean status (check from the host with
   `git -C .data/projects/scratch status --short` and `log --oneline --all`).
7. **V0.10 In-container timeout:** the ROADMAP's two commands. Pass: 124
   then 137.
8. **V1 no-quota part:** `docker exec agent-cuenta1 claude --version`
   (expected `2.1.273`) and `docker exec agent-cuenta1 claude --help`
   saved to `.data/verify/v1-help.txt`; check `--resume`, `--model`,
   `--effort`, `--output-format` are listed. Also note whether
   `--permission-mode`, `--allowedTools` and `--dangerously-skip-permissions`
   are listed (V1.2 needs them later). Row ID: `V1 (flags)`.
9. Results log rows: V0.2 (agents), V0.4 (baseline, NOT RUN), V0.5, V0.9,
   V0.10, V1 (flags). Also one row **V0.7 — NOT RUN**: blocked by V0.1.

**Done when:** the rows are logged with evidence files, the scratch repo is
clean, and `bootstrap-probe` is gone.

## Task 5: Carry the findings into README, ROADMAP stage 1 and spec

**Prerequisites:** Tasks 1–4 logged.

**Steps:**
1. Read the results log rows dated 2026-09-16.
2. `README.md`:
   - *Known gaps (fix first)*: add each confirmed defect that isn't there
     yet (for example the unprofiled `dispatcher` service in step 4; the
     `.gitignore` gap only if Task 1 didn't already close it — it did, so
     don't add it; git ownership/identity if V0.9 confirmed them; the V2
     contract mismatches). Match the existing bullets' style and length.
   - *Unverified assumptions*: drop what passed; for what failed, point to
     the Known-gaps bullet instead. Keep the `(V…)` tags accurate.
   - Don't rewrite unrelated sections, don't fix commands in the setup
     steps (stage 1).
3. `docs/ROADMAP.md` stage 1, step 1's candidate list: add new decisions
   the findings forced (e.g. dispatcher profile, V2 mapping) in the same
   bullet style; mark V0.3 / V0.9 / V2 bullets with what was confirmed.
   Don't touch the results log rows.
4. Spec (`docs/superpowers/specs/2026-09-13-ia-harness-design.md`): only if a
   finding changes a design statement (the Vibe Kanban contract is the
   likely one). Spanish, *tuteo*. Otherwise leave it.
5. Consistency pass: every README Known-gaps bullet you added maps to a
   log row; no claim in README contradicts the log.

**Done when:** README, ROADMAP stage 1 and (if needed) the spec reflect the
logged findings, with no other edits.
