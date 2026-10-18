---
status: design
approved: null
built: null
supersedes: []
---

# Research role — an investigador that reads the web and cites it, before the arquitecto

**Status:** proposed by the main thread on 2026-10-11 at the user's ask, not
built. **Priority 3** of the 2026-10-11 list (`server-deployment.md`, *The
order across the plans*). The idea comes from the recon skill of
replica-skill (`skills-adoption.md`, *What replica-skill offers*), widened
from "map an app to clone" to "find out what a task needs to know". The
harness builds projects in general, not only itself, and many tasks turn on
facts no repo holds: a library's current version, a third-party API, a
choice between alternatives.

## The goal

When a task needs outside knowledge, a phase gathers it before anyone
designs, writes it down with a source for every claim, and hands it to the
arquitecto as data. No other role searches the web.

## Decisions

### 1. It runs only when asked

- A task or an epic carries `research: true`, or a short list of questions.
  A person sets it when accepting the task, or the arquitecto asks for it
  in its handoff and the task waits for that phase.
- It runs once, before the arquitecto, and its output stays with the task.
  A later round reuses it.

### 2. What it may use, and what it writes

- **Tools:** WebSearch, WebFetch, and reading the repo. No Bash, no edits to
  code, no git.
- **Output:** one file, `docs/research/<task>-<topic>.md`, plus its handoff.
  Each claim carries its source URL and the date it was read. What the
  sources disagree on is said, not settled.
- **Model:** Opus 5.5 at `medium` (`models-5-5.md`). Sonnet 5.5 is the
  candidate once a few runs are measured.

### 3. Web content is data, never instructions

This is the role's main risk. A page can carry text aimed at the model.

- The role prompt says what it fetches is data, and it reports any
  instruction it finds rather than following it.
- The arquitecto receives the research file marked as external content,
  inside tags like the guide's `<pasted_content>`, never as instructions.
- The revisor may check the citations: that each URL exists and says what
  the file claims.
- The research file is never a source of permissions, commands or targets
  (`command-requests.md`).

### 4. Its spend is counted like any phase

Searches and fetched pages cost tokens. The phase has its own
`max_turns`, it counts against *Start development*'s spending limit
(`board-service.md`), and its cost shows in the usage log under its role.

## To verify before building

- That the agent containers can reach the internet, and through which
  proxy, if any.
- That WebSearch and WebFetch work under `-p` with the subscription logins
  the pool uses, in the CLI version the image ships.

## How it would be built

1. The `research` field on the task and the epic, and the phase ordering
   before the arquitecto. No quota.
2. The role's skill, prompt and tool list, with its handoff schema.
3. The arquitecto's prompt reading the research file as tagged external
   content.
4. The revisor's citation check.
5. One real task with `research: true`, measured. Spends quota, so it waits
   for the user's go.

## Open questions

- **Who sets the flag.** A person only, or also the arquitecto asking for
  it. The second costs a phase boundary; decided at step 1.
- **Where the file lives.** In the target project's repo under
  `docs/research/`, or in the hive beside the task. The repo keeps it with
  the code it explains; the hive keeps outside text out of the repo.
