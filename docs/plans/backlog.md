---
status: idea
approved: null
built: null
supersedes: []
---

# Backlog — open work that is written down nowhere else

The text below was moved here on 2026-10-13 by T-037 from the *Future work*
section of the root `README.md` as it stood at commit `7cc44ca`. It is copied
word for word, dedented to the left margin, under each item's original number
and title. `[…]` marks text left out because it is built, planned or
superseded: `README.md` *Prioritized* says which and points at where it
lives, and `git show 7cc44ca:README.md` has the words. Paths and links are
written from the repo root, as they were in the README, and "above", "below"
and "item N" mean that README section as it was. Nothing here is designed or
approved: evaluate it when the work is taken on, not before.

## Prioritized

### 0. **Editing `config.yaml` from the console.**

**Status:** designed 2026-10-15 by T-055, not built. The decisions are in
`docs/decisions.md` ADR 75: an overrides file merged by `load_config`, a
`config-writer` service, the key table, and when a change takes effect. The
four build tasks, in order, are in `docs/plans/console-config.md`. The text
below is kept as it was written.

**Built 2026-10-17** by T-064 to T-067, the first three tasks of
`docs/plans/console-config.md`. Its fourth, the credentials and the login,
is the Tokens task of `docs/plans/front.md` and is not built.

Added on 2026-10-15 at the operator's request and placed first by them. It is
numbered 0 so the items below keep the numbers other documents cite. Unlike
the rest of this file it was not moved from the README; like the rest, nothing
in it is designed or approved.

**The want.** Read and change the dispatcher's settings from the front instead
of editing `config.yaml` on the host: the accounts and `primary_account`,
`quota_threshold_pct`, `reserve_pct`, `pace_primary_week`, `fallback_roles`,
`default_model` and `role_model`, `default_effort`, `role_effort`,
`escalated_effort`, `escalate_effort_after_round`,
`escalate_effort_on_repeated_finding`, `max_revision_rounds`, `merge_on_done`,
`gates_enabled`, `mapping_enabled`, and the timeouts.

**Where it stands.** It is on no roadmap. `docs/plans/front.md` names it only
as a deferred decision bound to the tier-3 *Role models write* — "whether the
console may edit `config.yaml` at all" — with an ADR as its kind, and the
tier-2 Role models screen only reads the file. Today every mount of
`config.yaml` is `:ro` and the api writes nothing.

**What has to be decided first, in this order.**

1. *Whether the console may write it at all.* Editing the config is a
   different privilege from enqueueing a dispatcher verb: it changes how much
   of every account's quota the harness may spend (C-4), not one run. The
   ruling decides whether that is an ADR or a charter question.
2. *Where the writer lives.* It is tier 3's open "where the write service
   lives", so this item either opens tier 3 or waits for it. The api stays
   write-free and no docker socket goes into a web process (tier 3's floor).
3. *Which keys are editable.* Some are safe to turn from a form (effort,
   models, `escalate_effort_on_repeated_finding`); some are not, because they
   name paths, credentials or containers (`git_credentials_path`,
   `project_keys_dir`, the account containers). A key not on the list is
   read-only in the console.

**Constraints any design keeps.** The write is validated by the same
`load_config` the dispatcher uses, so the console cannot save a file the
dispatcher refuses; it is atomic (write and rename) with the previous version
kept; it is attributed and logged; and it takes effect on the next dispatcher
invocation, which already re-reads the file because each verb is a fresh
`docker compose run`. A form that edits a copy the dispatcher never reads is
not this item.

**The operator's answers, 2026-10-15.** (1) Yes: the console writes the
settings directly, with no agent in the path, and the values in use today are
the defaults. (2) Through the api is the operator's first thought, with the
format left open: YAML or something else, a database probably being overkill.
The api's write-free property (`docs/decisions.md` ADR 19, `front.md` tier 3)
is what that thought runs into, so the design task weighs it against a
separate writer rather than assuming either. (3) Case by case, by whether a
key is worth editing from the front. Credentials are not excluded on
principle: the GitHub token must be renewable from the console when it
expires, and the operator wants the same for the Claude login — run
`claude auth login` in the account's container, show the URL it prints, and
paste the authorization code back. That is the Tokens task's open question in
`front.md` ("whether a device code is ever rendered in the console"), answered
yes by the operator; both writes reach a container or a credential file, so
they ride tier 3's queue and never a docker socket in a web process.

**The operator's agreement, 2026-10-15**, to three proposals the design task
starts from rather than decides: `config.yaml` stays as written and is the
defaults, and the console writes only a separate overrides file that
`load_config` merges and validates, so a reset is deleting a key and the
comments in `config.yaml` survive; the writer is a small separate service
with its own credential and write access to that file alone, not a write
route on the api; and credentials and the login are a second task (the
Tokens task, through the queue), apart from the settings.

### 11. **Autonomy: the automatic approve, the queue, drafted specs, parallel work.**

**Status:** designed 2026-10-15 by the main thread at the user's word. Its
ten build tasks were built by 2026-10-17 (`docs/plans/autonomy.md`, *Status*).
The design and its ten build tasks, in three priorities, are in
`docs/plans/autonomy.md`; the charter rulings are C-4, C-5 and C-12 as
amended that day. Placed here, after item 0, because its first priority
(tasks A-1 to A-6) removes the person's turn between tasks, which the
measured pace says is the bottleneck. Its priority 2 is items 4 and 2 of
this list; its priority 3 (B-1 to B-4) is parallel work within a project.
Unlike most of this file it was not moved from the README.

### 1. **Project memory: role skills, per-project docs, and a pointer-based handoff.**

[…]

- *Role skills (method).* […]
  - [ECC](https://github.com/affaan-m/ECC), unread as of 2026-09-25 and
    recorded here so the evaluation has a home rather than living in a
    conversation. The test to apply when it is read is the one the
    three above were judged against: a skill describes method, never a
    project's conventions, and only the first half belongs in this
    item. Anything that survives that filter has two possible homes,
    and they are not equivalent. One is `ROLE_SKILLS` in
    `dispatcher/role_skills.py`, where a skill is bound to a role and
    where `cartografo` currently has none at all. The other is the root
    thread, which runs no skills today and is the harder case, because
    a skill loaded there is paid on every turn of a long conversation
    instead of once per phase — the cost argument of item 2 applies to
    it with the multiplier reversed.
- *Documenting is a method, and this repo is now a target too.* The
  bullets above say what the docs are and who writes them; nothing
  says how to write one. `skills/` holds eight vendored skills and all
  eight are about code — plans, TDD, debugging, review, scope — so a
  role told to record an ADR, open a `learnings/` entry or keep an
  index current is improvising the form every time. That half is
  method and belongs in a ninth skill: what an entry contains, how to
  phrase a trigger column, when to supersede instead of rewrite, and
  when a change is too small to document. […]
- *Limits go on what travels, not on what exists.* […]
  A line cap on source files is rejected too, but a signal is not. A
  hard cap produces `utils2.py` and splits coherent modules to satisfy
  a number. The evidence for measuring anyway is good:
  `dispatcher/dispatcher.py` is 1420 lines against 786 for the next
  largest, and it is the module [`docs/plans/balancer.md`](docs/plans/balancer.md)
  independently decided to refactor in its Phase 1, because the
  per-phase bookkeeping is buried inside `run_task_cycle`. A
  measurement would have named the same file with no argument attached.
  So the form is a fifth gate in `dispatcher/gates.py`, `NOTE` level,
  looking only at files the diff touched and reporting that one crossed
  a threshold. 800 lines for Python: today that names `dispatcher.py`
  alone out of seventeen modules, and leaves `learnings.py` at 786 just
  under, a margin thin enough that the number is provisional by
  construction. It blocks nothing, which is that module's stated
  posture — *every gate errs toward saying nothing*.
- *Docs and tests kept current, enforced outside the model.* […]
  - Claude Code hooks in the agent container, for feedback within the
    session only. A `PostToolUse` hook on Edit/Write that runs the
    formatter or typecheck on the touched file is cheap and catches
    errors early. A `Stop` hook that refuses to finish until tests ran
    is not recommended: under `-p` every refusal is another paid turn,
    it needs the `stop_hook_active` guard to avoid looping, and it sees
    only its own session, while the dispatcher gate sees the whole diff
    for free.

  […]
- *Shared learnings that survive a dead phase.* […]
  - Format: […] As the index grows, the dispatcher passes each phase only
    the rows matching the task's profile, labels, or the paths its plan
    names.

  […]

[…]

### 2. **Token economy.**

[…]

- *Cap what a role returns.* […] A role's own subagents return to the
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
  cheap version of item 8. Not set today, and the image is not where it
  would go: the agent image ships no `settings.json` of its own, because
  the `claude_shared` volume mounts over `/root/.claude` and shadows
  anything baked in. `hooks/install_settings.py` is the only writer —
  it runs from `docker/agent/entrypoint.sh` at container start and
  merges in the hook registration plus `syncClaudeAiSkills` and
  `syncClaudeAiPlugins`, so a window belongs in that same merge. The
  merge is additive and never rewrites a key the operator already set,
  which means a default there stays overridable by hand in the volume.
  The window is per-account only in appearance: every account symlinks
  the one shared `settings.json`, so setting it sets it for all of them.
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

[…]

### 3. **Unattended 24/7 operation.**

[…]

- […]
- **Done 2026-10-14 by T-051** as a per-claim lock, not a single-instance
  one: `docs/decisions.md` ADR 71. The original text follows.
  A single-instance guard (e.g. `flock` on `state_dir`) so overlapping
  runs can't claim the same account. `acquire_lock` already refuses a
  live lock on the task, but two runs of different tasks still race on
  account state files.
- […]
- An orphan phase after Ctrl+C: the heartbeat stops with the host
  process, so the task lock expires after `heartbeat_ttl_seconds`
  (120 by default) while that orphan `claude` may run for up to
  `phase_timeout_seconds` (7200). A re-run in that window takes the
  task on another account and can work the same worktree
  concurrently, since checkouts under `projects_root` are shared
  across agent containers. The reaper above could hold the lock (or
  skip the task) while an account is still `BUSY` with that task ID.
- […]
- A short "continue where you left off" prompt when resuming after a
  rate limit, instead of re-sending the full role prompt.

### 4. **Per-role model selection and richer effort escalation.**

**Status:** refinement (1), the per-role model override, is **built**:
`role_model` and `Config.model_for` in `dispatcher/config.py`,
`docs/decisions.md` ADR 65. Its split is the operator's judgment, made ahead
of item 2's usage records rather than from them, and the config schema the
text below calls not designed is the one that ADR records. Refinement (2) is
**built in part**: the repeated complaint is a signal, opt-in under
`escalate_effort_on_repeated_finding` and off by default, that runs the next
implementador round at `escalated_effort` when a review sends a round back for
a finding the previous review sent one back for. It reads no freeform
`result_text`: the revisor's schema gained a `findings` list, a finding that
still holds is copied verbatim, and the comparison is set intersection on
`debt.fingerprint` of those lines and the debt it rejected (`docs/decisions.md`
ADR 73). The other half is still **open**: no signal reads a result text
"suspiciously short", for the reasons that ADR records, and switching model
on a signal is not built. The text is kept as it was moved.

[…] Two
refinements were discussed but not implemented: (1) a per-role model
override, e.g. opus for arquitecto and auditor (planning/judgment
roles) and sonnet for implementador (execution), possibly also for
early revisor rounds, instead of one `default_model` for all four;
(2) escalating effort (or switching model) on signals other than round
count, e.g. the revisor repeating the same `CHANGES_REQUESTED`
complaint, or a role's result text coming back suspiciously short.
Make the split data-driven first, from item 2's per-phase usage
records, which show which roles actually consume the quota. Not
designed: a config schema for per-role model overrides
(`default_model` becoming a fallback vs. a
`models: {arquitecto: opus, ...}` map), and how "the same complaint"
or "suspiciously short" would be detected from freeform `result_text`
without over-engineering a heuristic that never fires as intended.

### 5. **Task profiles: skill packs per kind of task, not new roles.**

Role
skills (item 1) say how a role works, not what the task is about. A
frontend task gains from design and browser-verification skills that a
backend task would pay for and never use: every installed skill's
description is in context on every turn, and a subagent's `skills:`
preload draws from the same installed skills, so hiding a pack behind
a subagent doesn't keep it out of the parent's context. So a pack isn't
installed by default; each phase gets only the one its task needs:
- *Mechanism.* A task carries a profile (`web-frontend`, `e2e`, `3d`,
  …): from a label a human puts on the task or, failing
  that, from the arquitecto, which picks from a short catalog of
  profile names and one-line descriptions, never the skills
  themselves. The dispatcher records the profile in the `.hive` task
  file and adds `--plugin-dir /opt/packs/<profile>/<role>` to that
  phase's `claude -p` in the same account container (the flag is
  repeatable, so packs stack). No orchestrating session needs to know
  the packs exist. Pass the flag again on every `--resume`, a quota
  handoff included: whether a resumed session keeps the original
  call's plugins is unverified. A second image (`agent-web`) is only
  worth it for heavy dependencies; the agent image
  (`node:24-bookworm-slim`) has no Chromium today.
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
    item 1's mapping phase, and a human approves the result.
  - The file stores a hash of the manifests it read. When dependencies
    change, detection reruns and the change is proposed to a human,
    never applied on its own.
  - When in doubt, a pack stays off. A missing pack shows up in that
    task's review; an extra one spends tokens on every turn without
    anyone noticing.
- *`web-frontend` candidates, evaluated.*
  - […]
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
    one), marks inferred choices unconfirmed, and a human approves it.
    UI tasks `depends_on` that approval, not just on the pipeline
    finishing.
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
  - […]
  - Security reviewer: a revisor pack (thermos's correctness/security
    review plus Semgrep, item 7), turned on for tasks that touch auth,
    payments, or secrets.
  - […]
  - Bug fixing (reproduce before fixing), infra/DevOps, performance,
    and data migrations (a human gate before anything irreversible):
    profiles or task types, not roles.

Depends on item 1, whose role skills the packs extend, and on the
known gaps. […] Not designed: the
profile catalog, the `.ia-harness.yaml` schema, the label
convention, and where packs live (baked under `/opt/packs` vs.
mounted).

### 6. **Observability and hardening.**

Acceptable for a single operator on
loopback plus Tailscale, but worth tightening, since agent containers
run arbitrary code from cloned repos on the same network:
- **Done 2026-10-14 by T-052** (`docs/decisions.md` ADR 72): not one
  shared token but two kinds. `GET /events` takes the operator's
  `COLLECTOR_READ_TOKEN`, given to no agent; `POST /events` takes a
  per-agent write token bound to that agent's source_app, which the
  collector stores whatever the body says. The collector refuses to start
  without either. The original text follows.
  The collector's `POST`/`GET /events` have no auth, so anything on
  `ia_harness_net`, agent containers included, can read or forge
  events. A shared token would close that.
- **Done 2026-10-14 by T-054** (`docs/decisions.md` ADR 74):
  `hooks/emit_event.py` redacts the event before the POST with fixed rules
  (the container's secret env values, vendor key shapes, secret-named
  pairs, the content of secret-named files) and fails closed; what stays
  exposed is a secret in a shape no rule knows. The original text follows.
  Hook payloads include tool inputs and outputs (file contents, env
  files, tokens) and land in SQLite as-is. Redact them in
  `hooks/emit_event.py`.
- […] The collector and the read API also run
  Flask's development server rather than a production WSGI server.
- […]
- The dispatcher mounts the host's `/var/run/docker.sock`, which is root
  on the host. A socket proxy limited to `exec` would narrow that.

### 7. **Code-inspection/code-intelligence tooling in agent containers.**

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

### 8. **Mid-phase context-window compaction.**

For a single role's run that
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

### 10. **Multi-provider agent containers.**

Lowest priority: the most work,
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
