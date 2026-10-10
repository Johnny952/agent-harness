# Charter — what the human has ruled

The rulings a role may not re-decide. Everything else in `docs/` is written by
agents; this file is written by the human who owns the project, in
conversation with the main thread, and **no role may edit it**. If a phase's
commit touches this file, the phase is wrong.

Read the table of triggers, open the entries whose trigger matches the task in
front of you, and treat what they say as given — not as an argument to weigh
against the code you are reading.

## When an entry is wrong

It happens: a ruling made before the code existed can turn out to be
impossible, or to contradict another one. **Do not edit the entry, and do not
quietly work around it.** Say so where a human will see it:

- In the phase's `risks`, naming the entry by number — the cheapest route, and
  the right one when the task can still be finished.
- As an unconfirmed row in [`business.md`](business.md), when what you found
  is a rule the code follows and the charter denies.
- As a `blocks` ruling, when the task cannot be finished either way. A charter
  entry that forbids the only route is not debt; it is a decision the task was
  given and cannot satisfy.

The human amends the charter. An agent reports.

## Where a rule belongs

| File | Who decided | Who may change it |
|---|---|---|
| `docs/charter.md` | a human ruled | the human, in conversation with the main thread |
| [`docs/decisions.md`](decisions.md) | an agent decided, doing the work | a later ADR, by superseding — never by rewriting |
| [`docs/business.md`](business.md) | an agent inferred from the code, nobody ruled | a human confirms or kills the row; the auditor adds rows |

The direction is the point. `business.md` runs code → human: it is an inbox of
rules nobody has ruled on. The charter runs human → code. They are separate
files because every file an agent is told to write, an agent will write.

## Entries

Numbered, dated, appended. A ruling that is replaced keeps its number and its
heading is struck through, pointing at the number that supersedes it —
the same convention as `decisions.md`, so that a role that learned one has
learned both.

---

### C-1 — One human drives many accounts from one conversation

**Ruled:** 2026-09-25.
**Trigger:** you are changing who starts a phase, or adding something that
runs a phase with no human in the loop.

The harness exists so that a single conversational thread — a human talking to
one main model — can spend several Claude accounts' quota without the human
holding several sessions open. The roles are non-interactive sessions in other
accounts' containers; the conversation is the only interactive one.

What follows: the main thread is not a worker, and a worker is not a place to
ask a question. A phase that needs a decision the task never gave it ends
blocked and says so; it does not invent the decision, and it does not wait.

---

### C-2 — The primary account is in the worker pool

**Ruled:** 2026-09-25, overruling the first draft of
[`docs/plans/balancer.md`](plans/balancer.md), which had the conversational
account never taking worker phases.
**Trigger:** you are changing account eligibility, ordering, or what happens
when the pool is dry.

With two accounts, an account reserved for conversation is half the harness
idle. The primary is ranked last, not excluded: workers first, the primary
last, and "all secondaries are out of quota" means the primary takes the work
rather than the queue stalling.

It keeps a reserve — see *The primary keeps a reserve* in the balancer plan —
because a fallback that spends the conversation to the wall takes away the
thread that would notice. A reserve is a ceiling, not a partition.

---

### C-3 — Which account is primary is configuration, not a constant

**Ruled:** 2026-09-25. Recorded in
[`docs/plans/balancer.md`](plans/balancer.md), *Which account the
conversational thread runs under*.
**Trigger:** you are writing the account-selection code, or tempted to hard-code
`cuenta1` anywhere.

`cuenta1` — the first container defined — is the **default** primary. The
default belongs in config (`primary_account:`), so that a harness with
different accounts, or a human who moves the conversation, changes one line
rather than the code.

---

### C-4 — Nothing spends an account's quota without a human authorizing that run

**Ruled:** standing, restated throughout 2026-09; written down 2026-09-26.
**Trigger:** you are adding anything that could dispatch a phase on its own — a
scheduler, a watcher, a retry that re-dispatches, a queue worker, a health
check that talks to a model.

A model round-trip costs quota that the human, not the harness, is paying for.
Every dispatch is authorized by a human who was told its shape first: which
role, which account, how many turns. Automation is allowed to *prepare* a run
and to report that one is ready; it is not allowed to start one.

This is why `dispatch run-task` is a command a human types, and why Phase 4 of
[`docs/plans/board.md`](plans/board.md) puts actions behind a queue a human
releases rather than behind a button that runs them.

---

### C-5 — No role pushes

**Ruled:** standing; written down 2026-09-26.
**Trigger:** you are writing anything that would run `git push`, open a pull
request, publish a package, or otherwise put this repo's contents somewhere
outside this machine.

Phases commit to their own branch and stop there. Merging is a human's call and
pushing is a separate one. The dispatcher contains no `git push` today and that
is not an omission to fix.

---

### C-6 — A task is done when the docs say why, not when the code works

**Ruled:** standing — *"documenta todo lo necesario"*; written down 2026-09-26.
**Trigger:** you are deciding whether your phase is `complete`, or whether a
doc edit is in scope for a task that did not name it.

Docs are part of the work, not a report on it. A phase that changed behaviour
and left no ADR, no implementation note and no learning has finished half its
job, and the half it skipped is the half the next task needs. Writing them is
never out of scope, and "the task did not ask for docs" is not a reason.

The counterpart is that a doc nobody will open is worse than no doc: what gets
written is what a later task, reading a trigger, would open.

---

### ~~C-7 — The board is Flask and Jinja, not Next.js~~ — superseded by C-8

**Ruled:** 2026-09-26, answering the open question in *The toolchain is the one
question this spec does not answer* in
[`docs/plans/board.md`](plans/board.md).
**Trigger:** you are building, extending or containerising the board, or you
are about to add a `package.json`, a lockfile or a Node build step to this
repo.

This repo is Python and Docker. A Node lockfile, a build step and a CVE
surface are paid for when a screen needs them — a live execution timeline,
Phase 5 — and not before. Phase 2 is four tables and a strip of accounts over
five read-only endpoints; it is half a day of Jinja against the same
`python:3.11-slim` base the api already uses.

The phases plan says Node pays for itself at Phase 4. Having written both
specs, the real trigger is Phase 5: Phase 4's actions are a form post and a
queue, which Jinja serves. Either way the trigger is after Phase 3, and
nothing after Phase 3 starts before Phase 3 has run against a real dispatch.

What follows for whoever builds it: the board is an HTTP client of the api and
nothing else — no volumes, no `.hive/`, no events database, no socket. That is
the property that makes this ruling cheap to reverse. If phases 4–6 are
approved, the replacement is written against the same specification; the
screens, the states, the warnings and the exclusions do not change, and only
*Where it runs* and *Configuration* name a toolchain at all.

---

### C-8 — The console is the front in `front/`, not the Flask board

**Ruled:** 2026-09-28, superseding C-7.
**Trigger:** you are building, extending or containerising the console, or you
are about to add a `package.json`, a lockfile or a Node build step to this
repo.

C-7 deferred Node until a screen needed it, and priced that screen at Phase 5.
The price has already been paid: `front/` is a TanStack Start app, written
against these plans — the five roles, the four account states, the four gate
names, the handoff envelope and its byte budgets, the six dispatcher verbs, and
a queue with an operator and an agent as its two producers. It covers Phases 2
through 6 and the conversational channel that Phase 5 was going to need. The
question C-7 answered was whether to *build* a Node console; the question now is
whether to *discard* one, and the answer is no.

So Node enters this repo: a lockfile, a build step, a CVE surface and a fourth
service on a Node base image. That cost is accepted, not discovered later.

What does **not** change, because it was never about the toolchain:

- The console is an HTTP client of the api and nothing else. No volumes, no
  `.hive/`, no events database, no Docker socket. C-7's closing paragraph holds
  word for word, and it is what makes the console replaceable a second time.
- Actions reach the dispatcher through a queue drained by a worker. A web
  process that can reach the socket is root on the host for anyone who reaches
  the page, and no deadline changes that.
- Nothing after Phase 3 is *implemented* before Phase 3 has run against a real
  dispatch. Writing the specs, the ADRs and the endpoint contracts is not
  implementation and does not wait; building Phase 4's queue is and does.

`observability/board/` stays until the front serves the four screens it
duplicates — tasks, task detail, debt and tail — against the real api, and is
retired in the same task that closes the last of them. Until that task, it is
the reference: where the two disagree about what a screen should say, the Flask
board is right, because it has run.

Two things this ruling does not decide, and no role may read into it: whether
the front's chat dock becomes a second interactive session — C-1 says the
conversation is the only one, and reconciling them is a later ruling, not an
implementation detail — and whether `front/` keeps its Lovable round-trip.

---

### C-9 — `front/` lives in this repo, and the Lovable round-trip stays live

**Ruled:** 2026-10-01, closing the second of the two things C-8 left open.
**Trigger:** you are about to add, move or remove a file under `front/`, or you
are about to rewrite history on `main`.

`front/` is in this repo, whole, in one commit: the code, `bun.lock` and
`.lovable/project.json`. It is not a vendor directory and not a submodule. A
change to a screen is a change to this repo, and it goes through the same role
cycle as everything else.

The round-trip stays live: `front/` can still be edited in Lovable, and what is
published on the connected branch syncs back into the editor. One thing follows
from that, and it is operational rather than cosmetic — **no force-push, and no
rebase, amend or squash of a commit that is already pushed.** It holds on
`main`, not only on a side branch, because `main` is the branch that is
connected. `front/AGENTS.md` says it from Lovable's side; here it is a rule of
the project. C-5 already keeps every role away from `origin`, so what this
binds is the operator in the conversation, which is where every push so far has
come from. A commit that turns out wrong is corrected by a commit on top of it.

Closing the round-trip — treating the generation as a one-time import, after
which the editor is no longer a producer — is a later ruling and needs an entry
that supersedes this one. It does not close by habit, and it does not close
because a task finds the history constraint inconvenient.

---

### ~~C-10 — a red `front/` lint is a note; a red `front/` typecheck blocks~~ — superseded by C-11

**Ruled:** 2026-10-04, when `front/` got a `typecheck`, a `lint` and a `test`
script and the question of what a gate should do with each one came up.
**Trigger:** you are about to teach a dispatcher gate to run `bun run lint`,
`bun run typecheck` or `bun run test` under `front/`.

A red `bun run typecheck` blocks the phase. A red `bun run lint` does not: its
finding rides along as a note. `bun run test` needs no ruling here — a failing
suite is what the test gate already blocks on for Python, and the console's
suite is the same kind of thing.

The asymmetry between the first two is measured, not aesthetic. `tsc --noEmit`
is clean on the tree as it stands, so a red typecheck means the phase in front
of you broke it, which is what a blocking gate is for. `eslint .` reports 125
errors on that same tree — every one of them `prettier/prettier`, and every
one of them already on `main` before the `lint` script existed. A lint gate
that blocked would put every phase from today in red over lines nobody in the
cycle touched, and a gate that is red before the work starts is a gate the
roles learn to route around. That costs more than the formatting it was meant
to buy.

This is not a ruling that the 125 are acceptable. Clearing them is a task of
its own, and once the tree is clean, promoting the lint to blocking is a later
entry here — not a judgement call made inside a phase.

---

### C-11 — a red `front/` lint blocks, like its typecheck

**Ruled:** 2026-10-10, when `eslint .` reached zero problems on `main` — no
errors since 2026-10-08, no warnings since T-015-D1 closed — which is the
condition C-10 set for promoting it.
**Trigger:** you are changing which of `front/`'s commands a dispatcher gate
runs, or at what level a red one lands.

A red `bun run lint` under `front/` now blocks the phase, the same as a red
`bun run typecheck` or `bun run test`. C-10's reason for keeping it a note was
that the tree was already red before any phase touched it; that reason is
gone. On a clean tree a red lint means the phase in front of you introduced
it, which is the same argument that made the typecheck blocking.

This promotes `front/`'s lint only. The dispatcher's `lint:` key stays what
it is for every other project — reported, not enforced — and `front/`'s
command moves to the index's `test:` list, which is where a blocking check
already lives. A rule a phase cannot satisfy without silencing it (an
`eslint-disable` with no reason, a rule turned off in the config) is not a
fix: say so in the phase's `risks`, as with any charter entry.
