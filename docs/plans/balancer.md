# Plan: a conversational balancer over the account pool

Status: Phase 0 landed 2026-09-25 — `status` and `release-account` are real
verbs and G2 is closed. Phases 1–3 are unstarted, and still queued behind the
board's Phase 0. Written 2026-09-25, after a host reboot cut the T-008 run
mid-phase and left two gaps in plain sight that this plan closes.

The dispatcher already balances load across accounts. `dispatch_phase` picks an
account, probes `/usage`, parks it over the threshold, transitions its state,
execs the role in that account's container and commits what it wrote. That half
works and is measured: V5.2 and V5.3 both moved a phase between accounts
mid-flight.

What does not exist is a place for a human to stand *between* phases.
`run-task` is all-or-nothing — four roles, then it returns `done` or `blocked`
— so an operator who wants to read the arquitecto's handoff before paying for
an implementador has no way to. This plan makes the cycle steppable and gives
the stepping to a conversational account that also schedules the work.

## The pool is ordered, not partitioned

The first framing of this was wrong and is worth recording so it does not come
back: *the conversational account never joins the worker pool.* With two
accounts that halves capacity and idle-locks the harness the moment the single
secondary parks — the operator sits in front of a thread that can talk about
work and cannot do any.

The fix is priority, not exclusion. Every account gets a rank; the picker walks
workers first and the primary last. "All secondaries are out of quota" then
needs no special case at all — it is just the ordering running off its end.
`list_idle_accounts` (`dispatcher/state_machine.py:112`) returns a list today
and the caller takes the first; this is a sort key on `AccountConfig`, not a
new mechanism.

## The primary keeps a reserve

The conversation and the fallback phases draw on one budget. A fallback phase
that spends the primary to the wall does not just stall the queue — it takes
the console with it, and the operator loses the thread that was supposed to
decide what to do about an exhausted pool. That failure is worse than idleness,
because idleness is recoverable by waiting.

So the primary needs a stricter ceiling than a worker: `quota_threshold_pct`
(90 today) keeps parking workers, and a separate `reserve_pct` — start around
60 — is the line below which the primary will accept fallback work at all.
Above it the primary refuses out loud and says the pool is dry, which is the
answer the operator actually needs.

## Not every phase deserves the fallback

The four roles do not cost the same. Revisor and auditor read a diff and write
a verdict; implementador writes code across up to three revision rounds and is
the phase that blows a handoff budget. Spending the operator's console on a
fresh implementador round is the worst trade available.

Make it configurable rather than hardcoded — `fallback_roles: [revisor,
auditor]` — with the rule that the primary will not *start* an implementador
round on fallback unless the call says so explicitly.

## Parked by a counter and parked by a refusal are different

This is the distinction that decides whether to fall back or simply wait, and
the state machine already carries both halves of it.

An account parked over the local threshold self-heals: `_recheck_cooling_accounts`
re-probes every `PRE_COOLDOWN` account whenever none is `IDLE` and flips it
back on clearing the line. That can come back in minutes and costs nothing to
wait for. An account with `rate_limited_at` set inside `quota_cooldown_seconds`
(1800) will not come back on a probe — `record_rate_limit`'s docstring says why:
`/usage` is a local slash command reading counters this machine wrote, so it
cannot see a refusal, and the refusal outranks it.

The balancer should say which of the two it is *before* falling back. Waiting
out a counter is free. Falling back on a refusal is the real spend, and it is
the only case where the primary's reserve should be touched at all.

## The verbs

| Verb | Why it is needed |
|---|---|
| `run-phase --task-id --role [--round] [--account]` | Runs one phase and returns control. The whole point. |
| `status [--probe]` | Prints the cached state files and the card. Probes `/usage` only when asked, because each probe is itself a `claude -p`. **Built 2026-09-25.** |
| `release-account --name <n>` | The reaper that did not exist. Closes G2 below. **Built 2026-09-25.** |

`run-phase` is the only one with real work behind it: `run_task_cycle`
(`dispatcher/dispatcher.py:1163`) keeps its per-phase bookkeeping — status
block, heartbeat, worktree, commit, learnings, handoff budget — inside the
cycle, and a per-phase verb needs that as a callable unit. The other two are an
afternoon each.

## Gaps this closes

Both found 2026-09-25, neither pre-existing in the README's list:

- **G1 — a cycle cannot be resumed.** `run_task_cycle` always begins at
  arquitecto. The `--resume` in the codebase is an internal re-prompt
  (`_with_resume_notes`, `dispatcher.py:470`), not a CLI verb. A run
  interrupted in the revisor's second round can only be continued by paying
  for all four phases again. `run-phase` is the fix.
- **G2 — an account left `BUSY` by a crashed dispatcher is stuck forever.**
  `list_idle_accounts` returns only `IDLE`; `_recheck_cooling_accounts`
  skips any state that is not `PRE_COOLDOWN`/`COOLING_DOWN`; and
  `reap_expired_locks` (`dispatcher.py:394`) releases the *card* lock by
  heartbeat TTL, never the account. There is no reaper and no verb.
  **Closed 2026-09-25** by `release-account` (`dispatcher/operator.py`). A TTL
  on the account lock, mirroring the card's, is still the better fix and
  belongs with Phase 1, where the per-phase bookkeeping is being moved anyway.
  Building the verb turned up a bug nobody was looking for: a hand-edited card
  whose `heartbeat` is an unquoted YAML timestamp parses as a `datetime`, not
  the `str` `TaskFile` declares, and crashed every reader of that field with
  `fromisoformat: argument must be str`. Cards this harness writes are quoted
  and round-trip fine, so no test built through `write_task_file` could see it.
  Coerced at the source in `read_task_file` — `docs/ROADMAP.md`, Stage 1 item 2.

## Phases

- **Phase 0 — `release-account` and `status`. Done 2026-09-25.** No model in
  the loop, so no quota was spent verifying them: 24 unit tests and an
  end-to-end smoke test against a scratch config. Closes G2. It went first
  because it is the only thing that can unstick the pool after a crash — which
  is the state the pool was in on the day this plan was written.
- **Phase 1 — `run-phase`.** Closes G1. The refactor is the cost, not the verb.
- **Phase 2 — ordering, reserve and `fallback_roles`.** The scheduling policy
  above, on `AccountConfig` and the picker.
- **Phase 3 — the operator's loop.** Largely docs: how a conversational thread
  is meant to drive the three verbs. Worth writing only once 0–2 are real.

## Out of scope

Parallel dispatch — two phases running at once on two accounts. That stays
where it is, in the README's prioritized item 9, and its reasoning is unchanged:
with Pro accounts the limit is quota, not throughput, so concurrency mostly
spends the same budget faster and adds merge conflicts. This plan is about
*sequencing under a human*, not concurrency.

## Which account the conversational thread runs under

Settled 2026-09-25. **The primary is `cuenta1` — the first container defined —
by default, and it is named in `config.yaml`, not in code**, so moving the main
thread to another account is a one-line config change and nothing else:

```yaml
primary_account: cuenta1
```

The key names an existing entry in `accounts`; an unknown name is a config
error, the same way an unknown `--name` is a usage error for `release-account`.
The primary is not a fourth kind of account — it is one of the pool, ranked
last, which is the whole point of *The pool is ordered, not partitioned* above.
That also answers the cost question the open version of this section was
weighing: the primary is one of the accounts that already have a container and
a `claude_creds_<account>` volume, so the fallback needs no third login, and
the reserve (`reserve_pct`) is what keeps the conversation from being eaten by
the worker phases it shares a budget with.

**Today the primary thread is not in a container at all.** It is this Claude
Code session, talking to the operator from the host — an exceptional
arrangement for the development period, and one that ends at server
deployment, when the thread has to live somewhere that survives a laptop
closing. Nothing in the design depends on the exception: the thread reaches
accounts through `docker exec` either way, and the only thing that changes on
deployment is which process holds the session id.

**One account, more than one thread.** The entry point is a single thread of a
single account, but the account is not the limit — several conversational
threads may run in the primary, one per project or per line of work, as the
state of the projects warrants. A thread is a stored session id, not a
container: `claude -p --resume <session-id>` in the primary's container is
what distinguishes two threads, and they share the account's quota, which is
one more reason the reserve is a percentage of the primary and not of a thread.

## How a front reaches all this

The board plan (`docs/plans/board.md`) and this one meet here, so the split is
worth stating once. A front-end has three channels, and they are not the same
channel wearing different hats:

- **Reads — the HTTP API, board Phase 1.** `/api/tasks`, `/api/accounts`,
  `/api/events`, `/api/debt`, served by a small Python process that reads the
  cards, the state files and the collector's SQLite. Not served by `dispatch`:
  `dispatch run-task` is a batch process that exits, and `docs/README.md` says
  so in its first paragraph — *there is no service to run and no request to
  serve*. The read API is the new process; the dispatcher stays a CLI.
- **Actions — the queue, board Phase 4.** A row in a table and a worker that
  shells out to `dispatch`. The decision not to put `/var/run/docker.sock` in a
  web process is already recorded in `board.md` under *Actions go through a
  queue, not a socket*, and it holds for anything the front wants to start:
  `run-task`, `run-phase`, `merge-task`, `release-account`.
- **Conversation — a streaming channel, and only this one talks to the
  primary.** `claude -p --output-format stream-json --resume <session-id>`
  executed in the primary's container over the same `docker_exec` transport the
  phases already use, plus a stored session id so the turn continues a thread
  instead of starting one. This is board Phase 5's transport arriving early;
  the difference between a phase and a conversation is the session id and
  nothing else.

So the answer to *does the front talk to the workflow through the dispatcher to
the primary container* is: **beside it, not through it.** The front does not
proxy work through the conversation — it reads the API and it enqueues actions
directly, and a chat panel is a third connection that happens to reach the same
pool. The reason matters: if every action had to pass through the primary
thread, the board would stop working whenever the thread was mid-turn or out of
quota, and the primary's quota would be spent on clicks.

The same rule applies in the other direction, and it is the piece that is easy
to get wrong. When the conversational agent wants to dispatch work, **it does
not get the docker socket either** — that is the same root-on-the-host
objection with a different client. It gets an MCP tool that writes to the same
actions queue the front writes to. The queue then has one shape and two
producers, a human and an agent, and the worker that drains it is the only
thing in the system holding the socket.

## The conversational thread does not write code

Decided 2026-09-25, and it is a rule about a thread, not about an account. The
primary account writes code all the time under this plan — *Not every phase
deserves the fallback* above is the whole argument for letting an implementador
run in `cuenta1` when the pool is dry. What must not write is the session that
holds the conversation. Two threads in one account, one talking and one
implementing, is the arrangement, so the account is not the unit the rule is
about; writing it as *the primary account does not write* would contradict this
plan's own fallback.

The harness already has the shape for it. `WRITER_ROLES`
(`dispatcher/docker_exec.py:167`) is `{cartografo, arquitecto, implementador,
auditor}` and the revisor sits outside it on purpose, which is why a revisor's
writes are refused out loud instead of being dropped in silence. The
conversational thread is the same kind of participant: one that reads, decides
and dispatches, and whose writes are a bug rather than a shortcut.

So it is enforced where a phase's permissions are enforced and not in a prompt.
The chat channel's `claude -p --resume` is built with `Write`, `Edit` and
`NotebookEdit` denied and `Bash` restricted to an allowlist of reads —
`allowed_tools` in `config.yaml` already carries that exact shape for the
phases. Leaving `Bash` open is what would make the rule decorative, because
`sed -i` writes files. And a rule that lives only in the system prompt is a
request, held by the one participant whose context is periodically discarded;
the *Context compaction* section below is the reason that is not good enough.

The escape hatch is the queue, not a temporary grant. "Edge cases with prior
approval" has two implementations and only one survives compaction: the thread
enqueues a one-step task through the same MCP tool *How a front reaches all
this* gives it, and a phase does the writing. Granting the thread write access
for a turn is state somebody has to remember to revoke. Through the queue there
is one path by which work gets executed, and it is the path that is already
audited.

**Today's exception is this session.** The acting primary is a Claude Code
session on the host and it does write code, by the operator's standing
instruction — the same exception *Which account the conversational thread runs
under* records above. The rule is written for the deployed primary; it does not
govern the thread that is building it.

## Context compaction is the primary thread's problem, not a phase's

Asked whether the agents should get autocompact, the answer splits on the same
line everything else here does. A role phase is a single non-interactive
`claude -p` that returns one JSON blob and exits, so there is no long context
to compact and nothing watching it if there were — `README.md` item 8 records
that the dispatcher never observes context usage mid-call, and gates the
expensive version of that feature on long phases being seen to fail, which no
run has shown. Between phases the handoff already does the job the compactor
would: each role starts cold and reads a bounded envelope. The one knob worth
setting for the phases is the cheap one, `autoCompactWindow`, and the README's
item 2 now says where it has to go, which is `hooks/install_settings.py`'s
merge rather than the image.

The primary thread is the opposite case and it is the one this plan creates. It
is long-lived by definition — it holds the conversation across tasks, which is
why it needs a stored session id at all — so it is the only participant that
will ever approach a context limit, and it is the participant whose context is
most expensive to lose, because unlike a phase it cannot be reconstructed from
a handoff file. Two consequences for whoever builds Phase 5's chat channel.
First, compaction has to be treated as a scheduled act between tasks, never
mid-dispatch: `README.md` states the rule as *compact between tasks, never with
a subagent running*, and a thread that compacts while it is holding subagent
IDs loses exactly the identifiers the *Revive before respawn* rule depends on.
Second, the thread's durable state must not live only in its context. Anything
the primary needs to survive its own compaction — which accounts are parked,
which tasks are in flight, which session ids it holds — belongs on disk in the
state the dispatcher already writes, so a compacted thread rereads it instead
of remembering it. That is the same discipline the phases get for free from the
handoff, applied by hand to the one thread that has no handoff.
