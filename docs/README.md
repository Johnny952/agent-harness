---
install: cd front && bun install --frozen-lockfile
test:
  - python3 -m pytest
  - cd front && bun run typecheck
  - cd front && bun run test
  - cd front && bun run lint
---

# ia-harness

A dispatcher that runs one task through four roles — arquitecto, implementador,
revisor, auditor — as four separate non-interactive `claude -p` sessions, each
in a container holding a different Claude account, so a task outlives any one
account's quota. It is a Python package plus a Docker stack; there is no
service to run and no request to serve. `dispatch run-task` is the entry point
and it returns when the task is `done` or `blocked`.

The harness is now also its own target: this repo is checked out under the
dispatcher's `projects_root` and tasks are dispatched against it, so a role
reading this file may well be about to change the code that put it there. That
is deliberate, and the one thing it asks of you is in **Working on the
dispatcher while it runs you**, below.

## Stack

Python 3.11, standard library except for `pyyaml` (config, task-file
frontmatter, the learnings index), `flask` (the collector and the read API, both
JSON) and `requests` (the observability hook). Nothing renders a template any more, so `markupsafe` is no longer
pinned here — the Jinja board that imported it was retired
(`docs/decisions.md` ADR 32). Tests are `pytest` with no
plugins. Docker Compose runs the stack; the dispatcher reaches the agent
containers with `docker exec` and nothing else.

`test:` above is `python3 -m pytest` rather than a bare `pytest` for two
reasons: `python -m` puts the working directory on `sys.path`, which is what
lets `import dispatcher` work from a worktree that was never `pip install`ed,
and `tests/` is a package (`tests/__init__.py`), so pytest resolves the repo
root as the import root rather than `tests/` itself. `testpaths = ["tests"]`
in `pyproject.toml` keeps collection inside this checkout's own suite, so the
task worktrees the harness parks under `worktrees/<task-id>/` are not
discovered and the suite is not reported several times over — the problem
the `scratch` project's own index has to solve by naming a glob instead.

There is no `build:` key because there is nothing to build: the package is
pure Python and the suite imports it from the checkout.

The other three entries are for `front/`, which the Python suite says nothing
about (`docs/decisions.md` ADR 39). Each key takes one command or a list, run
in order from the worktree root, and the dispatcher's test gate runs them in
the order install, test, lint. `install:` is there because a fresh worktree has
no `node_modules`: it is git-ignored, and bun fills it from a cache on the
projects mount in seconds. A failing install is a note, not a block. A failing
`test:` entry blocks, the type check and `front/`'s lint included. The lint sat
under `lint:` as a note until 2026-10-10 (`docs/charter.md` C-10), while `eslint
.` was red on lines no phase had touched; it exits zero on `main` since
2026-10-08, and with no warnings at all since 2026-10-10 — ten until T-015 made
the board's `rows` referentially stable (`docs/decisions.md` ADR 40), then nine
`react-refresh/only-export-components`, filed as `docs/debt/T-015-D1.md` and
resolved by the operator out of cycle — and C-11 moved it to `test:` that day
(`docs/decisions.md` ADR 50). This project has no `lint:` key now; for any other
project a failing `lint:` is still only a note. No mapper writes `install:`
or `lint:` — `dispatcher/project_docs.py` asks a mapper for `build:` and `test:`
only — so they are added by hand, by whoever decides a project needs them.

That command names the toolchain **the agent container** has, not the one a
human on the host has. On the host, use the `.venv` recipe in the top-level
`README.md`; the container carries the same dependencies installed
system-wide, because it has no venv and its Debian base is PEP 668
externally-managed.

## Modules

| Path | What it is |
|---|---|
| `dispatcher/dispatcher.py` | The role cycle. Owns the phase loop, the revision rounds, failover between accounts, and what a phase is told when it resumes. The largest module and the one to read first. |
| `dispatcher/cli.py` | The verbs: `run-task`, `run-phase`, `project-keygen`, `bootstrap-project`, `merge-task`, `cleanup-task`, `learnings`, `status`, `release-account`, `migrate`, `file-split`. |
| `dispatcher/tasks.py` | The task module of `docs/plans/board-service.md`, the one writer of a task's files. It reads and writes a task's `description.md` on the board beside the hive tasks dir and parses it into sections (`## Goal` and `## Done when` required). It holds every write to the task file — the lock, the heartbeat, the handoff, the resolved debt — behind a writer that refuses a file still carrying a `description` key, and `migrate` moves those out, refusing a task whose lock is live (`docs/implementations/T-026.md`, `docs/implementations/T-027.md`). It also writes the epic format (`file_split`, `mark_merged`, `unmet_dependencies`) and a blocked task (`mark_blocked`) (`docs/implementations/T-029.md`); the dispatcher calls all of those but `file_split` since T-030, with `close_epic` and `epic_problems` beside them (`docs/implementations/T-030.md`), and `file_split` too since T-031 (`docs/decisions.md` ADR 60). |
| `dispatcher/config.py` | `config.yaml` into dataclasses. `config.example.yaml` is the commented copy and the place a new key gets explained. |
| `dispatcher/docker_exec.py` | Every command that leaves the dispatcher process. Builds the `claude` argv, wraps it in an in-container `timeout`, and owns the worktree checkouts each role gets. |
| `dispatcher/state_machine.py` | One JSON file per account: `IDLE`/`BUSY`/`PRE_COOLDOWN`/`COOLING_DOWN`, plus `rate_limited_at`, the only record of a refusal the harness can observe, and `last_probe`, the last `/usage` probe and the ceilings it was held to (ADR 49). Every write holds one `flock` on `.accounts.lock` in the state directory, and `claim` takes an IDLE account under it, so two dispatchers cannot take one account (ADR 71). |
| `dispatcher/quota.py` | The `/usage` probe, the threshold that parks an account before a phase spends into a wall, and the primary's weekly ceiling paced against the reset that probe reports (ADR 48). |
| `dispatcher/operator.py` | What the two read-and-repair verbs do: `status` reports the pool without writing to it, and `release-account` is the only way back from an account left `BUSY` by a crashed dispatcher. |
| `dispatcher/gates.py` | The four checks that run between implementador and revisor with no model in the loop. Only the test gate blocks, and only on a red `test:` entry; installs, `lint:` entries and the rest ride along as notes. The pointers gate checks a task's own citations first and in full, and its limit bounds the present docs only (`docs/decisions.md` ADR 61). |
| `dispatcher/context_transfer.py` | `TaskFile`: the `.hive/tasks/<id>.md` card, its frontmatter, its reader and its serializer, and the status block a resumed phase reads; the writes are `dispatcher/tasks.py`'s. Also the two records the dispatcher keeps beside a task rather than in it: the per-role handoff envelope `/api/phases` serves (ADR 27), and the append-only log of what every `claude` call cost (`append_usage`, ADR 53), and its tolerant reader (`read_usage`, ADR 54), which skips and counts a line that will not parse. |
| `dispatcher/handoff.py` | What one role leaves the next, as pointers rather than prose, and the budgets that hold it to a size. |
| `dispatcher/project_docs.py` | This contract. Where a project's docs live, what each role owes them, and the frontmatter above. |
| `dispatcher/learnings.py` | The inbox of things a run discovered, and the retirement of ones a later run disproved. |
| `dispatcher/debt.py` | The debt index, one row per deferred thing. Its `card` column stays and is written `none`: nothing mirrors a row (`docs/decisions.md` ADR 59). |
| `dispatcher/role_skills.py` | Delivers the vendored `skills/` to a role per call, one `--plugin-dir` each, rather than installing them in the shared config volume. |
| `dispatcher/subagents.py` | The subagent definitions a role is given. |
| `observability/collector/` | A Flask endpoint and one SQLite table, `events`, fed by the agents' hooks. |
| `observability/api/` | The read API: `GET /api/tasks`, `/api/tasks/<id>`, `/api/accounts`, `/api/events`, `/api/debt`, `/api/phases`, `/api/tasks/<id>/usage`, `/api/learnings`, every one `{"data", "warnings"}`. Writes nothing and parses nothing itself — it reads through the dispatcher's own readers, and `lock_expired`, `stale` and `in_phase_table` are the facts it derives. Phase 1 of the board plan; `docs/decisions.md` ADR 3–5, ADR 10, ADR 41 and ADR 54. |
| `observability/auth.py` | The auth the observability services share: a human's Basic credential, a service's bearer token, one copy of each timing-safe comparison. |
| `front/` | The operations console: a TanStack Start app in TypeScript, twelve screens, with the queue, the learnings table and a chat dock the board never had. Six of them read the real api — board, task detail, pool, debt and the live tail since T-012, through a bearer forward in the console's own server half (`docs/decisions.md` ADR 24), the task detail's phase timeline over a sixth route since T-013 (ADR 27), and the learnings table over a seventh since T-016 (ADR 41, ADR 42), and the task detail's usage region over an eighth since T-046 (ADR 54, ADR 67); the other six — approvals, tokens, session logs, role models, backlog and queue — have no backend at all and still read mock fixtures. In the repo since 2026-10-01, run with `bun`, not a compose service. It has a `typecheck`, a `lint` and a `test` since T-013, and the test gate runs all three through the frontmatter above since 2026-10-08 (`docs/decisions.md` ADR 39): a red type check or test blocks a round, and so does a red lint since 2026-10-10 (`docs/charter.md` C-11, ADR 50). `docs/charter.md` C-8 makes it the console; `front/README.md` is its own entry point. |
| `hooks/emit_event.py` | The Claude Code hook the agent containers POST from. |
| `skills/` | The vendored role skills, trimmed from three MIT upstreams. `skills/README.md` says which and why. |
| `docker/` | One Dockerfile per image and three compose files. `docker/agent/Dockerfile` is where the agent toolchain is pinned. |

## Docs

| Doc | When to open it |
|---|---|
| `README.md` (repo root) | The operator's manual: what the stack is, how to bring it up, and in *Future work* the known gaps still open and one status line per prioritized item pointing at where it now lives (since T-037). Long. Read the section you need, not the file. |
| `docs/charter.md` | Before deciding anything the project may already have ruled on: which account is primary, what may spend quota, what a phase owes the docs. The only doc here a human writes and no role may edit — its entries are given, not arguments. |
| `docs/ROADMAP.md` | The verification log. Every check that has been run against the real stack, its result and its evidence file. Open it before claiming something is or is not verified. |
| `docs/plans/board.md` | The phased plan for the console, and the spec for each phase — still the specification of what the phases *are*, whoever builds them. Phases 0–3 were built in Flask and Jinja: the local board client, the read API, the read-only screens that replaced the old events dashboard, and the live tail. **Phases 2–3 have no implementation any more**: the console serves those screens and `observability/board/` was deleted (`docs/decisions.md` ADR 32). Phase 1, the read API, stays. Phases 4–6 are specified and unbuilt, and under `docs/charter.md` C-8 they are built in `front/` rather than in Jinja. |
| `docs/plans/front.md` | Before changing the console in `front/` or a route it reads. Sorts every screen and every read the TanStack app invents into what the api already serves, what it may grow, and what is not coming, in three tiers; names which future task each open decision belongs to. Tier 1 is built on both sides — the api's share by T-011 (`docs/decisions.md` ADR 20 and ADR 21) and the console's five reads and bearer forward by T-012 (ADR 22–26) — and tier 2's two routes, `/api/phases` by T-013 (ADR 27 and ADR 28) and `/api/learnings` by T-016 (ADR 41 and ADR 42). **Tier 1 is closed and the board is gone**: the four screens it had read the real api, so `observability/board/` was deleted (`docs/decisions.md` ADR 32). Tier 2's *Done when* is reached too: the Learnings screen and the detail's learnings region both read the hive's trap entries, and what that region still cannot show — which table a phase was *handed* — is a record nothing writes rather than a route that is missing. The configuration read behind Role models is the one tier-2 route left, and the Role models task's to answer. Tier 2 also carries one screen over a route already built: the task detail's usage region over `GET /api/tasks/<task_id>/usage` (`docs/decisions.md` ADR 54 and ADR 64), carded by T-042 under *The usage task* and built by T-046 (ADR 67). |
| `docs/ui.md` | Before building or changing a screen in `front/`: the vocabulary every screen has to agree about — the tone of a state, absent vs empty vs broken, how times and ages render, the one shell and its chords. Binding, and edited in place rather than appended to. A definition true of one screen only is not in here; `docs/plans/front.md` *Where a UI definition lives* is the test that sorts them. |
| `docs/plans/balancer.md` | Before changing how accounts are picked, or how a cycle is driven. The plan for a steppable cycle under a conversational account, the two gaps the 2026-09-25 reboot exposed, which account the conversational thread runs under, and how a front-end reaches the pool. Phase 0 done; 1–3 unstarted. |
| `docs/decisions.md` | Before changing how something here behaves, to find out whether it was decided rather than incidental. One ADR per decision, appended, never rewritten. |
| `docs/implementations/<task-id>.md` | Before changing something a past task built here, to find out why it is the way it is. One file per task, written by that task. |
| `docs/learnings/README.md` | Before you start, and again before you debug: things true of this project that cost an earlier task time. Read the table whole, open the entries whose "when it applies" matches your task. |
| `docs/debt/README.md` | Before you start: work earlier tasks deliberately left undone, one row each with the condition that says whether it bites your task. Also where your own accepted debt is filed. |
| `docs/business.md` | Before changing what the harness *decides* — which accounts are eligible, what counts as a project, what a reader is allowed to treat as absent. Partial by construction: it holds the rules a task inferred from the code and marked unconfirmed, waiting on a human, alongside the confirmed ones it cites. |
| `docs/plans/token-economy.md` | Before changing how a role spends its turns, or when sizing a task: what one Opus window bought on T-016, where the avoidable tokens went, and the proposals that follow — including sizing a task so its whole cycle fits one window. |
| `docs/plans/task-split.md` | Before building P4's split backstop or an epic by hand: the arquitecto's `split` handoff field, how the dispatcher files `<task>-S<n>` sub-tasks tagged with their epic and stops before the implementador, the `depends_on` gate that waits for a merge, the limits, and a proposed ADR. Approved 2026-10-11 with P4; step 1 of four — the handoff shape, inert — is built by T-025 (`docs/implementations/T-025.md`) and step 2, the epic format, inert, by T-029 (`docs/implementations/T-029.md`), and step 3, the dependency gate and the merged record, by T-030 (`docs/implementations/T-030.md`), and step 4, the switch, by T-031 (`docs/implementations/T-031.md`, `docs/decisions.md` ADR 60): all four are built. |
| `docs/plans/review.md` | Before building anything that merges or pushes from the console: the goal of a pull-request-style review (diff, line comments, approve / request changes / comment) between a passed audit and the merge: the task branch as its pull request, our own screen on a diff library, comments kept in `comments.jsonl`, approve as merge-and-push, task branches pushed to GitHub as a record under amended C-5, request changes back through the arquitecto, conflicts caught by a merge-and-gate check and resolved by a new integrador role, and the console-verdict ruling. Approved in outline 2026-10-11, not built. |
| `docs/plans/board-service.md` | Before changing who writes task files or adding a board tool for agents: the harness's own board with no Vibe Kanban, one module that writes every task file, descriptions and comments in their own files, a service on the internal network that offers it to agents over MCP and to the console over HTTP, the epic and task levels, the backlog (proposed, deferred, dropped; debt enters there) apart from the board (accepted tasks in live per-phase columns), which role gets which tool, why accepting is a person's act and no tool dispatches, and which console screens exist, are mocks or are missing, and when planning and splitting happen; a *Start development* button per task and per epic with a spending limit, added the same day and waiting on a C-12 ruling. Approved 2026-10-11; step 1 is built by T-026 and T-027 and step 2 (the board seam removed) by T-028 and step 3 (the epic format and the blocked writer by T-029, their calls from the dispatcher by T-030, the arquitecto's split filed by T-031), the rest is not. |
| `docs/plans/roadmap-view.md` | Before building a view of the project's history or changing a plan's *Status* line: the idea of a roadmap screen, newest to oldest, derived from the plans, epics, task files, ADRs and git merges, never written by hand; a change of course as a new entry linked to the one it supersedes, narrows or amends, never an edit; the plan as its unit, a small frontmatter on each plan, a one-time backfill of past tasks, and a cheap form that can come first. An idea accepted in outline 2026-10-11, not built. |
| `docs/plans/main-thread.md` | Before building the console's conversation, choosing the primary account, or changing where the main thread runs: the console as the single entry point, with the host session demoted to the operator's development tool (a C-1/C-8 ruling still open); a thread as a hive record whose account can change between turns, shown as one history with separators where its session, account or model changed, the primary assigned from the console as an override of `config.yaml`, a turn as a queue action run by the worker over `stream-json --resume`, and a screen with a *Text* / *Text and commands* switch. Narrows `balancer.md`. A design accepted in outline 2026-10-11, not built. |
| `docs/plans/projects.md` | Before making the board, the console or a credential per project, or running two tasks at once: a project record in the hive with its remote, its settings and its key's fingerprint; a deploy key per project made on the dispatcher's side by `ssh-keygen`, only its public half shown; one store whose cards name their project; a sidebar with the project's screens under `/p/$slug/` and the harness's screens global; parallel work across projects now, one cycle per project, and within a project and fair quota last; slots, one per account, shown where a task is started and where its thread is chosen. Agreed in outline 2026-10-11, not built. |
| `docs/plans/notifications.md` | Before adding a notification, an inbox or push delivery: a fixed list of kinds (awaiting review, conflict, command request, blocked, merged, pool state…), one `notifications.jsonl` record written where the state changes, Web Push from the installable console with a minimal payload and no verdict buttons, and grouping per task and kind. A design accepted in outline 2026-10-11, not built. |
| `docs/plans/command-requests.md` | Before changing phase permissions or letting a phase ask for a command: capturing `permission_denials` first, a `command_requests` handoff field that ends the phase blocked, a target named by kind (agent, dind, service) and resolved by the dispatcher, a person's verdict in Approvals (run once, allow for this role in this project, deny), execution as argv by the worker and a `--resume`, and a denylist no verdict overrides. A design accepted in outline 2026-10-11, not built. |
| `docs/plans/server-deployment.md` | Before exposing the console beyond `localhost`, adding sign-in, or changing how accounts are added, or taking a backup: a real login (users, password and TOTP, cookie sessions, CSRF, HTTPS) replacing Basic auth, owner and reviewer roles, adding and signing in a Claude account from the console through the worker with a fixed container template, account records the dispatcher reloads, which screens must work on a phone, backups of the hive and state with no credential, and the order the plans are built in, with the queue and worker as the critical path. A design accepted in outline 2026-10-11, not built. |
| `docs/plans/models-5-5.md` | Before changing a role's model or effort, or a role prompt: Opus 5.5 and Sonnet 5.5 by ID instead of aliases, effort set per role at `medium` and measured, the guide's premature-stop paragraph for unattended phases with a bounded resume on a missing handoff, and the console's model list. Priority 1 of the 2026-10-11 list, proposed, not built. |
| `docs/plans/research-role.md` | Before letting a phase read the web: an investigador that runs before the arquitecto only when a task asks, with WebSearch and WebFetch and no Bash, writing cited findings that later roles read as tagged external data, never instructions. Priority 3, proposed, not built. |
| `docs/plans/skills-adoption.md` | Before adopting an outside skill: verdicts on run-skill-generator (taken, in mapping), fewer-permission-prompts, batch, loop and skill-doctor, and on replica-skill's roles: an E2E test skill and gate and a design skill taken, clone and marketing skills not. Priorities 2, 4 and 5, proposed, not built. |
| `docs/plans/omniroute.md` | Before routing any role to a non-Claude model: OmniRoute as an optional gateway behind a compose profile, per role from `/models`, for API-key providers only and never the subscription logins, with its risks. Priority 7, an idea, not built. |
| `docs/plans/backlog.md` | Before taking on open work that no plan covers: the prioritized items of the root `README.md` *Future work* that are still open, moved there word for word by T-037 on 2026-10-13, with `[…]` for omitted text. Nothing in it is designed or approved. |

`docs/superpowers/` holds this project's founding design spec and its first two
plans, written before `docs/plans/` existed and kept as records. Do not treat
them as a contract, and edit one only where your task names it, as T-039 did
the spec's section 8 pointers; the pointers gate reads none of them
(`docs/decisions.md` ADR 38, *`docs/superpowers/` stays out*).

## Working on the dispatcher while it runs you

The code under `dispatcher/` is the code executing your own phase, but not the
copy you are editing: the dispatcher runs from its own image and its own
checkout, and your worktree is a bind-mounted clone. So an edit here cannot
break the run in progress, and it also cannot be tested by watching the run
behave differently. Prove a change with the suite, not by observing the
harness.

Two consequences worth stating once:

- **Do not restart, rebuild or `docker exec` into the stack.** The containers
  named in `config.yaml` are the ones executing this task. Touching them is
  not a change to the project, it is a change to the machine running you.
- **The live config is not in this checkout.** `/config.yaml` is gitignored
  — the operator's copy lives on the host and never reaches a clone, so what
  you have is `config.example.yaml`, the commented template. A change that
  needs a new key documents it there and says so in the handoff; it cannot
  verify itself against the running harness's settings, and should not try.
