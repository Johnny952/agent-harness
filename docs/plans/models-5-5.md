# Models 5.5 — pinned model IDs, explicit effort, and prompts fit for unattended phases

**Status:** proposed by the main thread on 2026-10-11 at the user's ask.
Steps 1 to 3 built the same day (ADRs 57 and 58); steps 4 to 6 not built. **Priority 1** of the 2026-10-11 list (`server-deployment.md`, *The
order across the plans*): it touches only the config, the role prompts and
the console's model list, and it changes the cost of every phase after it.
It follows Anthropic's guide to prompting Opus 5.5, read that day.

## The goal

Every role that runs Opus runs Opus 5.5, and every role that runs Sonnet
runs Sonnet 5.5, named by ID so a CLI upgrade cannot change the model under
a run. The effort each role runs at is set on purpose and measured, and the
role prompts follow what the guide says about unattended runs.

## What exists today

- **Models are aliases.** `config.yaml` has `default_model: opus` (line 116)
  and `mapping_model: sonnet` (line 211); the defaults in
  `dispatcher/config.py` are the same (`default_model`, `mapping_model`).
  The dispatcher passes them as `claude --model <model>`
  (`dispatcher/docker_exec.py`). An alias resolves to whatever the CLI
  version in the agent image maps it to (`CLAUDE_CODE_VERSION` in
  `docker/agent/Dockerfile`), so which model ran is not in the config.
- **Effort is mostly implicit.** No effort is passed until a task passes
  `escalate_effort_after_round` (2), and then `escalated_effort: high`.
  Since step 2 (2026-10-11), every phase carries `--effort`:
  `default_effort` (`medium`), or the role's level in `role_effort`, until
  the escalation takes over.
- **A phase must hand off.** Since step 3 (2026-10-11), every role with a
  handoff schema is told the phase runs unattended
  (`handoff.UNATTENDED_PHASE`), and a phase that ends without a readable
  handoff is resumed up to `max_handoff_resumes` (2) times, then failed.
- **The console's model list is a mock.** `front/src/lib/api/mock/ops-fixtures.ts`
  lists `claude-opus-4`, `claude-sonnet-4.5`, `claude-haiku-4.5` and three
  non-Claude models, and its role map uses them. `/models` reads it through
  `modelOptionsQuery` and `roleModelsQuery`.
- **No old thinking instructions.** A search of `skills/` and `dispatcher/`
  on 2026-10-11 found no "think step by step" or "write your reasoning"
  instructions, which the guide says can now trigger a refusal.

## Decisions

### 1. Model IDs, not aliases

`default_model: claude-opus-5-5` and `mapping_model: claude-sonnet-5-5`, in
`config.yaml` and as the defaults in `dispatcher/config.py`. Each phase's
record already carries its model; with an ID it says which model ran.
Before the change, check that the CLI version the agent image ships accepts
both IDs.

### 2. Effort set per role, starting at `medium`

The guide's points that apply:

- Opus 5.5 defaults to `medium`, and at `medium` matches or beats Opus 5 at
  `high`.
- At a given level it thinks more per turn than Opus 5, so a level carried
  over from before costs more than it did.
- `xhigh` and `max` are for a measured gain only, and lowering effort comes
  before adding prompt instructions.
- Changing effort mid-session invalidates the prompt cache.

So:

- A per-role `effort` in the config, `medium` for every role at first,
  passed as `--effort` on every phase rather than left to the CLI.
- The escalation stays: `escalated_effort: high` after round 2.
- A level changes only after a measurement on real tasks
  (`token-economy.md`), recorded with the change.

### 3. Unattended phases: the premature-stop paragraph

The guide warns that in unattended runs Opus 5.5 may end a turn with a
progress report instead of continuing. Under `-p` that ends the phase. So:

- The role system prompt (`dispatcher/role_skills.py`, `system_prompt`)
  gains the guide's paragraph naming the ways a turn ends too early, adapted
  to a phase: the phase ends only when the handoff is written.
- A phase whose result has no valid handoff is a report, not a completion.
  The dispatcher resumes it with `--resume` and one short "continue" line, up
  to `max_handoff_resumes` times (config, 2 by default), before it is a failed
  phase. The resumes are part of the run already authorized and count
  against its spending limit (C-4, *Amended* 2026-10-11).
- **Not in the main thread** (`main-thread.md`): a person answers there, and
  the paragraph would make it ignore them.

### 4. What else the guide changes

- **A time budget line.** `elapsed <s> / <phase_timeout_seconds>` given to a
  phase on a resume, so it can pace itself.
- **Pasted text marked.** What a person pastes in the console's composer
  reaches the thread inside `<pasted_content id=…>` tags (`main-thread.md`,
  step 5).
- **No reasoning-extraction instructions** in any role prompt or skill,
  checked by a test over `skills/` that fails on such phrases.
- **Front design prompts name the styles to avoid** (`skills-adoption.md`,
  the design skill).

### 5. The console's list follows the api

`MODEL_OPTIONS` and `initialRoleModels` in the mock move to the 5.5 IDs.
The real list comes from the api once Role models reads the configuration
(`front.md`, tier 2). The non-Claude entries stay in the mock only as long
as `omniroute.md` is undecided.

## How it would be built

Each step is a single surface:

1. The model IDs in `config.yaml` and `dispatcher/config.py`, after checking
   the image's CLI accepts them. No quota. Built 2026-10-11: CLI 2.1.296.
2. The per-role `effort` key and its `--effort` flag, with tests. No quota.
   Built 2026-10-11: `default_effort` and `role_effort` (ADR 57).
3. The premature-stop paragraph and the bounded resume on a missing handoff,
   with its `max_handoff_resumes` config key, with tests. Built 2026-10-11
   (ADR 58). The paragraph is appended in the dispatcher
   (`_phase_system_prompt`), not in `role_skills.system_prompt`, so the
   cartografo, which has no skills, gets it too.
4. The phrase check over `skills/`.
5. The mock's model list and role map. A front task, behind its own ask for
   a build.
6. One measured task run at `medium` against the last comparable run at the
   old settings (`token-economy.md`). Spends quota, so it waits for the
   user's go.

## Open questions

- **Haiku.** Whether any role, the cartografo first, may run a smaller model
  is decided after step 6.
- **`max_tokens`.** The guide suggests about 128k for long agentic turns.
  Checked at step 2: `claude --help` in 2.1.296 lists no flag for it, under
  `-p` or otherwise, so the harness has nothing to set. It stays the CLI's.
