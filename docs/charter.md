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

### C-7 — The board is Flask and Jinja, not Next.js

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
