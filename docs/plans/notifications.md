---
status: building
approved: 2026-10-11
built: null
supersedes: []
---

# Notifications — the console tells the person when it needs them

**Status:** building, accepted in outline by the user on 2026-10-11. The
record and its writers are built (ADR 221), and so is the read route; the
inbox, the push and the settings are not.
It records a gap the user named that day: push notifications for an awaited
approval, a finished development and the like. It fills a weakness the main
thread's analysis of the same day found: in a near-automatic workflow, the
person's response time is the bottleneck. It builds on
`docs/plans/board-service.md`, `docs/plans/review.md`,
`docs/plans/command-requests.md` and `docs/plans/main-thread.md`, which
produce the states it reports.

## The goal

When something waits on the person, or something they care about happens,
they learn of it without the console open, and one tap takes them to it.

## What exists today

Nothing pushes. The console polls the api every `POLL_MS`, so it shows a state
change only while it is open. The states that matter are written to the hive
and the dispatcher's state, or will be once the other plans are built, but
nothing turns a change into a message.

## Decisions

### 1. A fixed list of kinds, each one a state change already written

| Kind | Source | Group | Default |
|---|---|---|---|
| Awaiting review | `review.md` | needs the person | on |
| Conflict | `review.md`, *Decisions* 6 | needs the person | on |
| Command request | `command-requests.md` | needs the person | on |
| Blocked: a phase needs a decision | handoff status | needs the person | on |
| Merged, and epic closed | `merged` block | informs | on |
| Pool dry, and an account available again | dispatcher state | informs | on |
| A phase failed or timed out | dispatcher | informs | off |
| A thread's turn finished | `main-thread.md` | informs | off |

The person turns each kind on or off. A kind is added to the list, never
inferred from events.

### 2. One record, written where the state changes

Whoever writes the state change also appends a line to `notifications.jsonl`
in the hive. That is the board module, which writes every task file, and the
dispatcher, for its own state. Each line holds:

- `id`, `kind` and `at`;
- the `task`, `thread` or `account` it is about;
- a short `title`;
- the console `url` that opens the item.

The same record feeds the console's inbox (a bell with an unread count) and
the pushes. A push is one delivery of a record, so the inbox works without
pushes. Once the person opens an item in the console, it is marked read.

### 3. Delivered by Web Push, from the console itself

The console stays the only entry point, so the pushes come from it:

- **The console** becomes installable (a PWA) and registers a Web Push
  subscription from a service worker.
- **A notifier** holds the subscriptions and the VAPID private key. It reads
  `notifications.jsonl` and sends each record. It needs no docker socket.
  Whether it runs inside the board service or as its own process is decided
  when it is built.
- **HTTPS** is required. It comes with the server deployment
  (`server-deployment.md`, *Decisions*, 1); `localhost` works in
  development.
- **On iOS**, Web Push works only for a console installed to the home screen.

Other channels (ntfy, Telegram, email) would be further deliveries of the
same record. None is planned now.

### 4. A push carries little

A push says the kind, the task or thread id, a short title and the link. It
never carries a diff, a command's text or a handoff's content: lock screens
show the push, and the push services see it in transit. Tapping it opens the
console at the item.

### 5. No verdict from a notification

A push could carry *approve* buttons, but it does not. Under C-12 a verdict
authorizes what it starts, so it is given over what it authorizes: the diff,
or the exact command and target. The notification takes the person there.

### 6. Not noisy

- Each task gets its own push `tag`, so a newer state replaces the older one
  on the device.
- Records of the same kind within a few minutes are grouped into one push,
  such as "3 tasks awaiting review".
- Quiet hours are an option, off by default. During them, pushes wait and the
  inbox fills.

## How it would be built

Each step is a single surface:

1. The record, and its writers in the board module and the dispatcher.
   **Built 2026-10-20** (ADR 221): `dispatcher/notifications.py` appends to
   `<hive root>/notifications.jsonl`, written by `tasks` (conflict, blocked,
   command request, phase failed, merged, epic closed), the dispatcher
   (awaiting review at a cycle's end, an account available again) and
   `epic_runs` (a run paused at its limit, as blocked). Turn finished has
   no writer yet: see the ADR. Pool dry is written since 2026-10-20 (ADR
   224), by the park that takes the last configured account out of the pool.
2. `GET /api/notifications`, and the inbox in the console, polled. It is
   useful on its own, before any push. **Partly built 2026-10-20** (ADR
   221): the route serves the newest 500 records, newest first; the inbox,
   and read state with it, are not built.
3. The notifier, and the endpoint that stores a subscription (a write, so in
   the board service). The server's Web Push library is its own ask, and the
   VAPID key's location gets an ADR, like the git credential.
4. The service worker and the installable console. A front task, behind its
   own ask for a build.
5. The per-kind settings and quiet hours.

## Open questions

- **More than one person.** There is one user today. Subscriptions and read
  state per person come with the users store and roles in
  `server-deployment.md` (*Decisions*, 1 and 2).
- **Where the notifier runs.** In the board service or on its own
  (*Decisions*, 3).
- **Whether "informs" kinds default on.** Set after the first weeks of use.

## What it is not

Not a log of events: the collector is that. Not a way to act: it links to
where the action is taken.
