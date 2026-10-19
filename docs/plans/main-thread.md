---
status: design
approved: 2026-10-11
built: null
supersedes: []
---

# Main thread — the conversation lives in the console, on an account the person assigns

**Status:** design, accepted in outline by the user on 2026-10-11, not built
as a whole; step 1 is built: the thread records and the model list by T-109
(2026-10-19), the primary override the same day (ADR 176); step 2 is built by
T-110 (2026-10-19, ADR 173).
It records the user's correction to the main thread's first analysis of that
day, which had a read-only mirror of the host session as its first step. It
builds on `docs/plans/balancer.md` (*Which account the conversational thread
runs under*, *How a front reaches all this*, *The conversational thread does
not write code*, *Context compaction*) and narrows its first section: the
primary is still named in `config.yaml`, but as a default the console can
override. The charter ruling in *Decisions*, 1 that the console needed
before it could send a message was given the same day (C-1, C-8 and C-12,
*Amended*). Amended the same day, on the user's second round: one
history per thread with separators where its session, account or model
changed (*Decisions*, 2 and 5), and the next task started by a button on the
board rather than by the thread (`board-service.md`, *Starting development*).
Amended again the same day, on the user's third round: a task that runs gets
an execution thread beside the main one, chosen from a selector, and kept
until the task closes (*Decisions*, 8; `projects.md`).

## The goal

In the user's words, translated: the end state is the harness on a server,
with the console as the single entry point. The conversation that drives the
workflow lives there. The session that builds the harness today need not be
the main thread, nor run this model or this account: from the console, the
person chooses which account is the primary and assigns a thread to it.

## What exists today

- **The channels are designed.** `balancer.md` gives the console three
  channels: reads through the api, actions through a queue, and a
  conversation that runs `claude -p --output-format stream-json --resume
  <session-id>` in an account's container. The thread dispatches through an
  MCP tool that writes to the same queue, never through the docker socket,
  and it writes no code.
- **The primary is a config key.** `primary_account: cuenta1` in
  `config.yaml`, read when the dispatcher starts.
- **The main thread is a host session.** It is a Claude Code session on the
  operator's machine, an exception `balancer.md` records for the development
  period.
- **A session is not tied to an account.** Both agent containers mount
  `/root/.claude/projects` from the shared `claude_shared` volume, and
  `docs/ROADMAP.md` check D1 (2026-09-23) resumed one session on the other
  account's container, which remembered its work.
- **Nothing in the console shows a conversation.** `front.md` defers the chat
  dock, and `docs/charter.md` C-8 leaves open whether it becomes a second
  interactive session. The queue (board Phase 4) and the live stream-json
  timeline (board Phase 5) are unbuilt; `/queue` is a mock.
- **The sessions screen is a draft.** `front/src/routes/sessions.tsx` shows
  sessions and subagents with their log lines, but over mock data:
  `listSessions` and `listSessionLines` in `front/src/lib/api/client.ts`
  return mocks, and no api route reads a session log (checked 2026-10-11).
  Its components are what this screen reuses.

## Decisions

### 1. The console is the entry point, and the host session stops being the main thread

Once the console holds a thread, that thread is the conversation C-1 speaks
of. The host session becomes what it is underneath: the operator's tool for
developing the harness, which drives nothing once deployed. So C-1 still
holds, since there is one conversation: it moves to the console rather than
doubling.

That is a reading of C-1 and C-8, and the user's to give. It is more than a
reading of C-12, which says the opposite in so many words: C-1 "still makes
the conversation the one place a model is talked to, and the console a place
a person decides, not a session". So the ruling amends C-1 and C-12. **Ruled
2026-10-11:** the user gave it, and C-1, C-8 and C-12 carry it as
amendments. Before that ruling the console could show threads but not send
to them (*How it would be built*, 4).

There is no mirror of the host session first. Its transcript sits in
`~/.claude` with everything the operator's session has read, and the api
must not mount it. The console gets its own thread instead.

### 2. A thread is a record, not an account

Each thread is a file in the hive, `threads/<id>.json`, with these fields:

- `id`, `title` and `project`;
- `account`, `model` and `effort`, chosen in the console;
- `segments`, the stretches of the thread, oldest first. Each one holds
  `session_id`, `account`, `model`, `effort` and `from` (the time it began),
  and a new one starts whenever any of them changes;
- `created` and `status`;
- `task`, set only on a thread opened to talk about one task (*Decisions*,
  8), and empty on the main thread.

Because sessions live on the shared volume, a thread's account can change
between turns: the next turn runs on the account the record names, under the
same session, and the model remembers everything. It never changes mid-turn.
A new session (*Decisions*, 7) is also a new segment, and that one the model
does not remember across.

There is one main thread, the one the console opens on. Others may be opened
beside it, one per project or line of work, as `balancer.md` already allows,
but the main thread is the console's centre. A task's execution thread is not
one of these records unless the person talks to it (*Decisions*, 8).

### 3. The primary is assigned from the console

**Ruled 2026-10-11 (C-3, *Amended*):** a harness starts with no accounts, so
the default primary is the first account added from the console, and
`primary_account` in `config.yaml` names it on a harness configured by hand.
Changing it from the console comes later. When it does, the console writes an
override through the board service, because choosing which account's quota
holds the conversation is a person's act, like accepting a task. Its meaning
is `balancer.md`'s: the primary is last in the pool and keeps its reserve.

- A change applies to the next pick. A phase already running is not moved.
- A new thread defaults to the primary's account, and the person may pin it
  to another.
- When a thread's account is cooling, the console says so and offers to move
  the thread. It never moves the thread by itself, because spending another
  account's quota on conversation is the person's call.

### 4. A turn goes through the queue, and only the worker holds the socket

The api and the board service never get the docker socket (`board.md`,
*Actions go through a queue, not a socket*). So a message from the console
goes through the queue:

1. The board service writes an action `{kind: "turn", thread, text}` to the
   queue.
2. The worker runs `claude -p --output-format stream-json --resume
   <session-id>` in the thread's account container, with `balancer.md`'s
   no-code permission set.
3. It appends each stream event as a line to `conversations/<thread>.jsonl`
   in the hive.
4. The api serves that file read-only and live, by polling or by SSE as the
   live tail does.

One turn runs at a time per thread. A message sent during a turn queues as
the next one, and *stop* is an action that kills the running exec. This is the
transport of board Phase 5, so it is built once, for both screens.

### 5. The screen: one history, with separators, and a switch

The thread's whole history is one scroll, from its first turn to its last,
across every segment. Where one segment ends and the next begins, the screen
draws a separator that says what changed and why:

- **Account**, such as "cuenta1 → cuenta2: cuenta1 cooling". The session is
  the same, so the model still remembers what is above.
- **Model or effort.** The same session too.
- **New session**, after a compaction or a fresh start. This one is drawn
  heavier and says plainly that the model does not remember what is above it,
  only the durable state it was given at the start. Without it a single
  history would mislead: it looks like one continuous memory, and it is not.

The screen has three parts:

- the selector: the main thread first, then each running task's execution
  thread (*Decisions*, 8), then any other thread, each showing its current
  account and model;
- the history;
- the composer, which waits for *Decisions*, 1.

The header shows the thread's context use and cost, read from each turn's
`result` event.

The switch has two positions:

- **Text**, the default: the person's messages and the assistant's `text`
  blocks.
- **Text and commands**: also each `tool_use`, shown as one collapsed line
  with the tool and a short argument, its `tool_result` expandable beneath it.
  Permission denials are marked. A dispatch through the MCP tool links to the
  queued action and its task.

The switch's position is remembered per viewer in browser storage. The
screen reuses the sessions draft's components; the sessions screen itself
stays what it is, a view of every session including the phases'.

### 6. The conversation log is a record

`conversations/<thread>.jsonl` is kept, like `comments.jsonl`: never
summarised or deleted by the harness, and one file for the whole thread,
across its segments. It holds what the stream gives, tool results included,
so it exposes what the handoffs already expose.

The worker writes it from the stream it already reads, rather than the api
reading the CLI's own session files. Those live in the shared
`claude_shared` volume with every phase's transcript, and the api does not
mount it. The separators need nothing extra: they are drawn from the
segments. It sits
behind the console's authentication, and the thread holds no secrets in its
environment.

### 7. Compaction is the person's action, between tasks

As `balancer.md` says, compaction never happens mid-dispatch. The console
shows the thread's context use. When it runs high, the person starts a new
session in the same thread, seeded from the durable state: the dispatcher's
state, the board and the thread's records. It is a new segment, so the
history goes on with a separator rather than in a new thread. A thread never
compacts by itself.

### 8. Each running task has an execution thread, chosen from the selector

When a task is started (`board-service.md`, *Starting development*), the
selector gains an entry for it, and loses it when the task is merged or
dropped. The user settled its shape on 2026-10-11: the task is already
written and designed when it starts, so its thread follows the workflow
rather than drives it, and talking to it is optional.

- **It is a view first.** The dispatcher drives the cycle, one `claude -p`
  per phase, so the execution thread shows that cycle in one scroll: the
  person's note from the start, each phase as a segment with its role,
  account and model, the phase's stream events under the same switch as the
  main thread's, its handoff, and each gate's result. It spends no quota of
  its own. The separators are drawn from the phases, as the main thread's are
  from its segments.
- **The note is its first line.** The person's note from *Start development*
  heads the thread, as it heads the task's comments.
- **Talking to it is a button.** *Talk about this task* opens a thread record
  with `task` set, on the primary by default, seeded with the task's
  description, its handoffs so far and the note. From then on the execution
  thread shows that conversation's turns between the phases. It does not stop
  or steer the cycle: that is still the board's buttons.
- **It is temporary.** When the task closes, the entry leaves the selector
  and is reachable from the task's view. Its events stay on disk as every
  phase's do; a `conversations/<thread>.jsonl` opened for it is kept like any
  other (*Decisions*, 6).
- **The selector shows the limit.** Above the execution threads it shows the
  slots of `projects.md` *Decisions*, 6: one per account that may take a
  phase, what is on each, and which started tasks wait and why. A task that is
  started but waiting has an entry too, marked with the reason, so the person
  sees that approving a third task with two accounts queues it.

## How it would be built

Each step is a single surface. Steps 2 onward need `board-service.md`'s queue
and worker.

1. The thread records and the primary override: written by the board
   service, read by the dispatcher's picker. Built by T-109, narrowed on
   2026-10-19: the records (`dispatcher/threads.py`, `POST /api/threads`,
   `PATCH /api/threads/<id>`, ADR 170) and the model list (ADR 169). The
   primary override is built the same day (ADR 176): the console saves
   `primary_account` through the config writer, the settings service that
   already owns `config.d/overrides.yaml` (ADR 75), and `pick_idle_account`
   reads it on each pick, so a change needs no restart; an override naming
   no configured account falls back to `config.yaml`'s with a warning.
2. The worker's `turn` action and the stream written to
   `conversations/*.jsonl`. It shares board Phase 5's transport. Built by
   T-110 (2026-10-19): `worker.HANDLERS["turn"]`, read-only by flags, the log
   between `turn_start` and `turn_end` lines (ADR 173).
3. The api routes `GET /api/threads` and `GET /api/threads/<id>/events`.
   `GET /api/threads` is built (2026-10-19, ADR 177): every record, the main
   thread first and then by `created`, archived ones with their status.
   `GET /api/threads/<id>/events` is built (2026-10-19, ADR 180): the log's
   lines as `{"n", "event"}` rows, followed with `?since=<last n>`.
4. The screen, read-only: threads and conversations with the switch. A front
   task, behind its own ask for a build.
5. The composer and *stop*, now that *Decisions*, 1 is ruled.
6. The thread's MCP dispatch tool, writing to the queue (`balancer.md`).
7. The execution threads and the selector's slots (*Decisions*, 8). They read
   what the phases already write, so they need board Phase 5's transport and
   `projects.md`'s slots, not the composer. *Talk about this task* comes with
   step 5.

## Open questions

- **The ruling.** Settled on 2026-10-11: C-1's one conversation moves to the
  console, and the host session is the operator's development tool
  (*Decisions*, 1; C-1, *Amended*).
- **Who starts a phase with no per-task word?** Settled in outline the same
  day: a button on the board, per task or per epic
  (`board-service.md`, *Starting development*), so it is a person's act and
  the thread does not need to dispatch. Its charter ruling was given the same
day (C-12, *Amended*).
- **What seeds a new session.** Which durable state a fresh segment is given
  at its start, and how big it may be (`token-economy.md`), is decided at
  step 5.
- **Model per thread.** Settled on 2026-10-19 (ADR 169): the list is config,
  `thread_models` with an optional `models` per account, never probed.
- **A message mid-turn.** Queued as the next turn is the default (*Decisions*,
  4). Whether one can also interrupt the current turn is decided at step 5.

## What it is not

Not a second interactive session beside the host's: it replaces the host
session as the main thread. Not a place that writes code: the thread
dispatches. Not a mirror of `~/.claude`.
