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
JSON), `requests` (the observability hook) and `mcp` (the Kanban client's stdio
transport). Nothing renders a template any more, so `markupsafe` is no longer
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
| `dispatcher/cli.py` | The verbs: `run-task`, `bootstrap-project`, `merge-task`, `cleanup-task`, `learnings`, `status`, `release-account`. |
| `dispatcher/config.py` | `config.yaml` into dataclasses. `config.example.yaml` is the commented copy and the place a new key gets explained. |
| `dispatcher/docker_exec.py` | Every command that leaves the dispatcher process. Builds the `claude` argv, wraps it in an in-container `timeout`, and owns the worktree checkouts each role gets. |
| `dispatcher/state_machine.py` | One JSON file per account: `IDLE`/`BUSY`/`PRE_COOLDOWN`/`COOLING_DOWN`, plus `rate_limited_at`, the only record of a refusal the harness can observe, and `last_probe`, the last `/usage` probe and the ceilings it was held to (ADR 49). |
| `dispatcher/quota.py` | The `/usage` probe, the threshold that parks an account before a phase spends into a wall, and the primary's weekly ceiling paced against the reset that probe reports (ADR 48). |
| `dispatcher/operator.py` | What the two read-and-repair verbs do: `status` reports the pool without writing to it, and `release-account` is the only way back from an account left `BUSY` by a crashed dispatcher. |
| `dispatcher/gates.py` | The four checks that run between implementador and revisor with no model in the loop. Only the test gate blocks, and only on a red `test:` entry; installs, `lint:` entries and the rest ride along as notes. |
| `dispatcher/context_transfer.py` | `TaskFile`: the `.hive/tasks/<id>.md` card, its frontmatter, and the status block a resumed phase reads. Also the two records the dispatcher keeps beside a task rather than in it: the per-role handoff envelope `/api/phases` serves (ADR 27), and the append-only log of what every `claude` call cost (`append_usage`, ADR 53), and its tolerant reader (`read_usage`, ADR 54), which skips and counts a line that will not parse. |
| `dispatcher/handoff.py` | What one role leaves the next, as pointers rather than prose, and the budgets that hold it to a size. |
| `dispatcher/project_docs.py` | This contract. Where a project's docs live, what each role owes them, and the frontmatter above. |
| `dispatcher/learnings.py` | The inbox of things a run discovered, and the retirement of ones a later run disproved. |
| `dispatcher/debt.py` | The debt index, one row per deferred thing, with the board card it belongs to. |
| `dispatcher/role_skills.py` | Delivers the vendored `skills/` to a role per call, one `--plugin-dir` each, rather than installing them in the shared config volume. |
| `dispatcher/subagents.py` | The subagent definitions a role is given. |
| `dispatcher/vibe_kanban_client.py` | The `KanbanClient` seam: `list_issues`, `get_issue`, `create_issue`, `set_status`. `NullKanbanClient` is what a harness with no board configured gets, and is what every run has used so far. `LocalBoardClient` is the board that is a directory of JSON cards (`local_board` in config), added by Phase 0 of the board plan. |
| `observability/collector/` | A Flask endpoint and one SQLite table, `events`, fed by the agents' hooks. |
| `observability/api/` | The read API: `GET /api/tasks`, `/api/tasks/<id>`, `/api/accounts`, `/api/events`, `/api/debt`, `/api/phases`, `/api/tasks/<id>/usage`, `/api/learnings`, every one `{"data", "warnings"}`. Writes nothing and parses nothing itself — it reads through the dispatcher's own readers, and `lock_expired`, `stale` and `in_phase_table` are the facts it derives. Phase 1 of the board plan; `docs/decisions.md` ADR 3–5, ADR 10, ADR 41 and ADR 54. |
| `observability/auth.py` | The auth the observability services share: a human's Basic credential, a service's bearer token, one copy of each timing-safe comparison. |
| `front/` | The operations console: a TanStack Start app in TypeScript, twelve screens, with the queue, the learnings table and a chat dock the board never had. Six of them read the real api — board, task detail, pool, debt and the live tail since T-012, through a bearer forward in the console's own server half (`docs/decisions.md` ADR 24), the task detail's phase timeline over a sixth route since T-013 (ADR 27), and the learnings table over a seventh since T-016 (ADR 41, ADR 42); the other six — approvals, tokens, session logs, role models, backlog and queue — have no backend at all and still read mock fixtures. In the repo since 2026-10-01, run with `bun`, not a compose service. It has a `typecheck`, a `lint` and a `test` since T-013, and the test gate runs all three through the frontmatter above since 2026-10-08 (`docs/decisions.md` ADR 39): a red type check or test blocks a round, and so does a red lint since 2026-10-10 (`docs/charter.md` C-11, ADR 50). `docs/charter.md` C-8 makes it the console; `front/README.md` is its own entry point. |
| `hooks/emit_event.py` | The Claude Code hook the agent containers POST from. |
| `skills/` | The vendored role skills, trimmed from three MIT upstreams. `skills/README.md` says which and why. |
| `docker/` | One Dockerfile per image and three compose files. `docker/agent/Dockerfile` is where the agent toolchain is pinned. |

## Docs

| Doc | When to open it |
|---|---|
| `README.md` (repo root) | The operator's manual: what the stack is, how to bring it up, the known gaps and the prioritized work. Long. Read the section you need, not the file. |
| `docs/charter.md` | Before deciding anything the project may already have ruled on: which account is primary, what may spend quota, what a phase owes the docs. The only doc here a human writes and no role may edit — its entries are given, not arguments. |
| `docs/ROADMAP.md` | The verification log. Every check that has been run against the real stack, its result and its evidence file. Open it before claiming something is or is not verified. |
| `docs/plans/board.md` | The phased plan for the console, and the spec for each phase — still the specification of what the phases *are*, whoever builds them. Phases 0–3 were built in Flask and Jinja: the local board client, the read API, the read-only screens that replaced the old events dashboard, and the live tail. **Phases 2–3 have no implementation any more**: the console serves those screens and `observability/board/` was deleted (`docs/decisions.md` ADR 32). Phase 1, the read API, stays. Phases 4–6 are specified and unbuilt, and under `docs/charter.md` C-8 they are built in `front/` rather than in Jinja. |
| `docs/plans/front.md` | Before changing the console in `front/` or a route it reads. Sorts every screen and every read the TanStack app invents into what the api already serves, what it may grow, and what is not coming, in three tiers; names which future task each open decision belongs to. Tier 1 is built on both sides — the api's share by T-011 (`docs/decisions.md` ADR 20 and ADR 21) and the console's five reads and bearer forward by T-012 (ADR 22–26) — and tier 2's two routes, `/api/phases` by T-013 (ADR 27 and ADR 28) and `/api/learnings` by T-016 (ADR 41 and ADR 42). **Tier 1 is closed and the board is gone**: the four screens it had read the real api, so `observability/board/` was deleted (`docs/decisions.md` ADR 32). Tier 2's *Done when* is reached too: the Learnings screen and the detail's learnings region both read the hive's trap entries, and what that region still cannot show — which table a phase was *handed* — is a record nothing writes rather than a route that is missing. The configuration read behind Role models is the one tier-2 piece left, and the Role models task's to answer. |
| `docs/ui.md` | Before building or changing a screen in `front/`: the vocabulary every screen has to agree about — the tone of a state, absent vs empty vs broken, how times and ages render, the one shell and its chords. Binding, and edited in place rather than appended to. A definition true of one screen only is not in here; `docs/plans/front.md` *Where a UI definition lives* is the test that sorts them. |
| `docs/plans/balancer.md` | Before changing how accounts are picked, or how a cycle is driven. The plan for a steppable cycle under a conversational account, the two gaps the 2026-09-25 reboot exposed, which account the conversational thread runs under, and how a front-end reaches the pool. Phase 0 done; 1–3 unstarted. |
| `docs/decisions.md` | Before changing how something here behaves, to find out whether it was decided rather than incidental. One ADR per decision, appended, never rewritten. |
| `docs/implementations/<task-id>.md` | Before changing something a past task built here, to find out why it is the way it is. One file per task, written by that task. |
| `docs/learnings/README.md` | Before you start, and again before you debug: things true of this project that cost an earlier task time. Read the table whole, open the entries whose "when it applies" matches your task. |
| `docs/debt/README.md` | Before you start: work earlier tasks deliberately left undone, one row each with the condition that says whether it bites your task. Also where your own accepted debt is filed. |
| `docs/business.md` | Before changing what the harness *decides* — which accounts are eligible, what counts as a project, what a reader is allowed to treat as absent. Partial by construction: it holds the rules a task inferred from the code and marked unconfirmed, waiting on a human, alongside the confirmed ones it cites. |
| `docs/plans/token-economy.md` | Before changing how a role spends its turns, or when sizing a task: what one Opus window bought on T-016, where the avoidable tokens went, and the proposals that follow — including sizing a task so its whole cycle fits one window. |

`docs/superpowers/` is vendored upstream material, not this project's
documentation. Do not treat it as a contract and do not edit it; the pointer
gate greps all of `docs/` and will cite paths from in there, which is noise.

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
