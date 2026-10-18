---
status: building
approved: 2026-10-11
built: null
supersedes: []
---

# Command requests — a phase asks for a command it may not run, aimed at the right container

**Status:** design, accepted in outline by the user on 2026-10-11, not built.
It records a gap the user named that day: an agent should be able to ask for a
command to be run when it lacks the permission to run it. Because the harness
has many containers, the request must reach the right one. It builds on
`docs/plans/board-service.md` (the queue, the worker and Approvals) and
`docs/plans/review.md` (C-12's console verdicts).

## The goal

A phase that needs a command it may not run ends by saying so. A person sees
the exact command and the exact container it would run in, and decides. The
command runs on the dispatcher's side, and the phase continues with its
output. A command approved often enough becomes a standing permission for
that role in that project, never a global one.

## What exists today

- **Permissions.** Phases run under `permission_mode` (default
  `acceptEdits`), with a global `allowed_tools` list passed as
  `--allowed-tools`. There is no `--permission-prompt-tool`
  (`dispatcher/docker_exec.py`). `bypassPermissions` is refused because the
  agent image runs as root, and no phase can grant itself a permission.
- **Denials are silent.** Under `-p` nobody can answer a prompt, so the call
  is denied. The `claude -p` result reports this in `permission_denials`, and
  nothing in `dispatcher/` reads it
  (`docs/learnings/no-stored-claude-result-object-exists-on-this-harness.md`).
  In V3 (`docs/ROADMAP.md`), 43 of 62 tool calls were denied, and the only
  trace was the handoffs' prose.
- **The containers.** Each account has an agent container (`agent-cuenta1`,
  `agent-cuenta2`) and a paired dind (`dind-cuenta1`, `dind-cuenta2`).
  `/data/projects` is mounted in both. The dispatcher, the api, the collector
  and the registry mirror are separate services. `accounts[]` in the config
  names only the agent container.
- **No request path.** A phase that is denied works around the denial or ends
  blocked in prose.

## Decisions

### 1. First, see the denials

The dispatcher reads `permission_denials` from each phase's result and writes
them to the phase's handoff record and to the usage log. The task detail
lists them. This is small, touches only the dispatcher, spends no quota, and
shows which permissions are missing in real runs before any allowlist is
widened.

**Built 2026-10-17** by T-069 (`docs/decisions.md` ADR 116). Every usage line
carries the call's `permission_denials`, reduced and capped, the handoff
envelope carries the final call's list, and the task page's usage region
lists them per call with a count on each attempt.

### 2. A request is a handoff field, and the phase ends blocked

A role that needs a command ends its phase with this field in its handoff:

```json
"command_requests": [
  {"argv": ["pytest", "-q", "tests/test_x.py"],
   "reason": "the gate's failure names this test",
   "target": {"kind": "agent"},
   "cwd": "."}
]
```

The phase does not wait for an answer: under C-1 a worker is not a place to
ask a question. The task moves to *Blocked* with the reason
`command_request`, and the request shows in Approvals. Each phase may file
only a few requests (the number is set when this is built), and `cwd` is
relative to the task's worktree.

### 3. The agent names a kind of target, and the dispatcher resolves the container

The agent never names a container. It names one of three kinds, and the
dispatcher resolves it from the account the phase ran on:

| `kind` | Resolves to | Use |
|---|---|---|
| `agent` | that account's agent container, from `accounts[].container`, in the task's worktree | tests, linters, reading the tree |
| `dind` | that account's dind, from a new `accounts[].dind` key (not guessed from the name) | the project's docker commands |
| `service` | a service of the project's compose inside that dind, checked against what is running there | a command inside the project's database or app |

These are never targets: the dispatcher, the api, the collector, the board
service, the host, another account's containers and another project's
services. A request that resolves to none of the three kinds is refused when
it is filed, not when it is approved.

### 4. A person decides in Approvals

The card shows the exact argv first. Under it come the resolved target (the
container's name and the working directory), then the phase and the task. The
reason comes last, marked as the agent's words, because an agent that has
read repo content writes it and it is not to be trusted.

The person gives one of three verdicts:

- **Run once.**
- **Allow for this role in this project** (*Decisions*, 6).
- **Deny**, with an optional comment the phase gets back.

Under C-12 a verdict authorizes what it starts; the three are in its list
since 2026-10-11 (C-12, *Amended*). Running the command spends no
quota; resuming the phase does, and the verdict authorizes it too.

### 5. The worker runs it, then the phase resumes

The worker runs the command through `docker exec` on the resolved target:

- the argv is executed as given, with no shell;
- it has a timeout and a cap on output;
- the output goes to `commands/<n>.log` under the task, and the exit code is
  recorded.

Then the phase resumes with `--resume <session-id>`, with the exit code and
the output, or the log's path when the output is long. Resuming is cheaper
than a fresh phase because the session keeps its context. It may resume on
another account if the phase's account is cooling: sessions live on the
shared volume (`docs/ROADMAP.md` check D1). In that case the target is
resolved again from the account that resumes.

### 6. A standing permission is per project and per role

*Allow* writes the command to that project's allowlist for that role, as the
`Bash(...)` pattern the CLI takes. The person chooses the exact command or a
prefix. The dispatcher merges the project's role list with the global
`allowed_tools` when it builds a phase. Each entry records who allowed it,
when, and from which request. A request never widens the global list; that
stays a config edit.

### 7. A denylist that no verdict overrides

Some commands are refused when filed and again when run, whatever the
verdict:

- pushes and other remote git operations (C-5: no role pushes);
- anything that reaches a docker socket from the agent side;
- reading credential paths such as `/root/.claude-account` or `.env`;
- `sudo`;
- a `cwd` outside the task's worktree.

The list lives in the dispatcher's code, not in the config, and changes under
an ADR.

### 8. Capturing a request at the moment of denial, to be verified

The CLI has `--permission-prompt-tool`, an MCP tool asked for each permission
decision under `-p`, and recent versions have a permission-request hook.
Either could file the request at the moment of denial instead of at the end
of the phase, with the exact command the model tried. The phase still does
not wait: the tool would deny, file the request and let the role finish.
Whether to use it is decided at step 3, after checking which CLI version the
agent image ships.

## How it would be built

Each step is a single surface:

1. The denials captured into the handoff record and the usage log
   (*Decisions*, 1), and shown in the task detail.
2. The `command_requests` field, the `command_request` block reason, and the
   check on the filed requests.
3. Target resolution and the denylist, with `accounts[].dind`, under an ADR.
4. The verdicts in the board service, and the worker's execution and resume.
   This needs `board-service.md`'s queue and worker.
5. The per-project, per-role allowlist and its merge into a phase's flags.
6. The card in Approvals. A front task, behind its own ask for a build.

A filed request is also a notification of kind *command request*
(`docs/plans/notifications.md`).

## Open questions

- **Which roles may file.** The implementador and the cartografo, surely. The
  revisor and the auditor do not write, so a request from either one must not
  write. Whether that is checked, or just left to the person, is decided at
  step 2.
- **A prefix in the allowlist.** `Bash(pytest:*)` is convenient and broad.
  Whether the screen offers prefixes or only exact commands is decided at
  step 6.
- **When the same command is requested again.** Whether a command that was
  run once recently is approved without asking is left out. *Allow* exists
  for that.

## What it is not

Not a way for a phase to wait for a person. Not a way to widen the global
allowlist. Not a shell: an argv, run in a container the dispatcher chose.
