# Token economy — what one window buys, and sizing tasks to fit it

**Status:** P5 is built — T-024, 2026-10-11, under `docs/decisions.md` ADR 53;
the change is `docs/implementations/T-024.md`. Its reader and route —
`GET /api/tasks/<task_id>/usage`, one group per attempt, nothing summed — were
added the same evening by the operator, out of cycle, under ADR 54 (dated
2026-10-10 in local time, where T-024's dates are UTC); the change is
`docs/implementations/T-024-D1.md`, and no screen consumes it yet. P8 is
built — T-020, 2026-10-10, under `docs/decisions.md` ADR 48; the change is
`docs/implementations/T-020.md`. P7 is built — T-023, 2026-10-10, under ADR 52;
the change is `docs/implementations/T-023.md`. P1–P3 are built — T-022,
2026-10-10; the change is `docs/implementations/T-022.md`. P4 was approved by the
user on 2026-10-11, with `docs/plans/task-split.md` as its design, and is not
built; P6 is still a proposal and not approved. Measured 2026-10-09 by the operator,
out of cycle, on T-016's first round. It feeds the root README's *Prioritized*
item 2 (Token economy) and item 5 (Task profiles), and does not replace either.
Evidence: `.data/verify/token-usage-T-016-2026-10-09.txt` (aggregates only).

## Why this was measured

T-016's implementador and revisor round 1 both ran on cuenta2, from 12:37 to
13:08 UTC. Its five-hour window opened at 12:30 UTC, so nothing else was charged
to it. When the revisor finished, `/usage` read 91% session. That put cuenta2
over the 90% PRE_COOLDOWN line, so round 2 could not be dispatched. The
question was whether a cache or a leak inflated the counter, or whether the
spend was real.

## What the transcripts show

| Phase | Turns | Cache read | Cache write | Output | Mean / max context |
|---|---|---|---|---|---|
| implementador | 148 | 19.7M | 224k | 70k | 134k / 224k |
| revisor r1 | 66 | 7.0M | 166k | 44k | 109k / 166k |

- **No cache leak.** 98.6% of all input was a cache read. The only large cache
  write is the opening one, about 21k tokens of system prompt, tools and role
  prompt, and every later turn wrote only what it added. This fixed context
  sits inside the 23–35K the README quotes from another setup. Both phases
  ran on `claude-opus-5` with no subagents.
- **Rereading the context is the cost.** How `/usage` weights token kinds is
  not published. As a proxy, the API's price ratios are used here: cache read
  0.1×, cache write 1.25×, output 5× input. On that proxy, cache reads are
  about 70% of the spend, output about 20% (almost all of it hidden thinking;
  the visible text was 1.5k characters across both phases) and cache writes
  the rest. The implementador alone is about 70% of the two phases.
- **Cache reads grow with the square of a phase's length.** Each turn
  re-reads everything before it. The implementador's context grew about 1.3k
  tokens a turn from a 21k base. Summed over n turns that is
  `n·base + g·n²/2`, which predicts 17.6M for 148 turns against 19.7M
  measured. Past roughly 60 turns, the quadratic term dominates.

### Where the avoidable part went

1. **One tool call per turn.** 143 of the implementador's 148 turns made exactly
   one call, including 43 separate `Edit`s. The revisor made one call in 65 of
   66 turns. At 130–220k of context, each extra turn costs a full re-read.
   Batching independent reads and edits into one turn is the largest single
   saving available, estimated at 20–30% of the implementador's cache reads.
2. **`docs/ROADMAP.md`'s Results log rows are single lines of up to several
   thousand characters**: 15 rows over 2,000 characters, 52k in all. To append
   one row, the implementador ran `sed -n '2047,2070p'` (26k characters back)
   and `grep "V0.6" | head -40` (20k). That is about 12k tokens pulled in to
   read a format, then carried in context to the end of the phase.
3. **Rereading its own work.** The implementador read back `git diff` of the
   tests and `app.py` it had just written (27k characters). It also read whole
   files where it then used a region: `tasks.$taskId.tsx` (20k),
   `client.ts` (17k) and `plan.md` (14k).
4. **The revisor is close to its floor.** It read about 170k characters of diff
   in topic-sized slices, which is its job; on a diff of 21 files and 1,899
   insertions there is little to take out.

## The structural finding: one round did not fit one window

On Opus, T-016's implementador and one revisor round together used 91% of an
account's five-hour window. A full cycle also needs the auditor, and usually
a second round. So by its size, T-016 could not finish inside one window on
one account. The pool rules (no fallback to the primary for the implementador,
and a hold rather than a fallback while an account is PRE_COOLDOWN on a
counter) then parked the cycle for about four and a half hours.

Cost tracked iteration, not output, as the README's item 2 predicted. Round 1's
two blocking findings were both doc-prose claims, so round 2 had to re-enter a
context that was already at its maximum.

## Proposals

Ordered by cost to build; the status line above says which are built or approved. P1–P3 are prompt text, P4 is a
process change, and P5–P7 are code.

**P1 — Batch independent tool calls.** Add one line to every role's prompt
through `dispatcher/project_docs.py:duties`, where the docs rules already
live: *"Independent reads, greps and edits go in the same turn; every extra
turn re-reads your whole context."* Check the effect by measuring tool calls
per turn on the next task.

**Built by T-022 (2026-10-10).** The line is `project_docs._BATCH`, word for
word, appended after `_ANCHORS` by `duties`. So it reaches every role that has
a duties block: arquitecto, implementador, revisor, auditor and the cartografo.
It has not been measured yet.

**P2 — Never read the Results log to learn its format.** Add a rule to the
same duties block: *"To append a Results log row, read its header and the
last row with `tail -n 1 docs/ROADMAP.md | cut -c1-400`; never `sed` or `grep`
whole rows."* Wrapping the existing rows would also work, but it is ruled out
because the log is append-only.

**Built by T-022 (2026-10-10), in `project_docs._IMPLEMENTADOR` only.** No
role's prompt assigns the Results log; a task file does. The implementador is
the role seen appending to it, the revisor's checkout is thrown away, and the
auditor's duty is the indexes. The text says "read only its last row", not
"its header and the last row", because `tail -n 1` returns no header and the
last row already shows the columns. The command is marked "run as written"
because it is a pipe, and `dispatcher._ONE_OPERATION_PER_CALL` tells every
phase to avoid pipes except in a form it is handed.

**P3 — Don't diff what you just wrote.** Add to the implementador's duties: use
`git diff --stat` to check scope, and use offset/limit reads for files you only
need a region of. The full diff is the revisor's read, not the implementador's.

**Built by T-022 (2026-10-10).** It is one sentence in the same closing
paragraph of `project_docs._IMPLEMENTADOR` as P2. No other role's block
carries it: the revisor is the one meant to read the whole diff.

**P4 — Size tasks to fit one cycle in one window.** This is the larger change,
and the one the measurement argues for. Because cost is quadratic in a phase's
length, splitting a task in two cuts more than half the cache reads. Using
the implementador's own curve, two tasks of 74 turns would read about 10.3M
tokens against 17.6M for one task of 148, or about 40% less. Each split pays
some of that back: the 21k fixed context and a few orienting reads per task.

- **The rule.** A task is sized so that implementador, revisor and auditor
  together fit in about 60% of one account's window. That leaves room for one
  more round on the same account.
- **The proxy until P5 exists.** One *surface* per task:
  - one api route with its tests and ADR;
  - or one screen;
  - or one region of a screen.

  The proxy is checked against `git diff --stat` after the fact. T-016 was
  three surfaces: `GET /api/learnings`, the Learnings screen, and the
  task-detail learnings region. It would have been three tasks. The api task
  comes first, because the other two consume it.
- **Who applies it.**
  - The operator, when writing the task file.
  - The arquitecto, as a backstop. If its plan spans more than one surface,
    it returns a split, with sub-tasks and their order, instead of a plan.
    This needs a `split` outcome in the arquitecto's handoff schema
    (`dispatcher/handoff.py`) and a dispatcher path that files the sub-tasks
    and stops the cycle. Those are design decisions for an ADR, not for this
    page.
    Those decisions are drafted, with a proposed ADR, in `docs/plans/task-split.md`.
- **The trade-off.**
  - More tasks means more task files, more reviews and more merges.
  - A cross-surface contract, such as an api row shape that a screen reads,
    is fixed by the first task and only consumed by the next. A later
    disagreement then costs a task instead of a round.

  That is acceptable because the contract usually already exists. The api
  serves it before any screen reads it, which is the order
  `docs/plans/front.md` already sets.

**P5 — Record usage per phase.** This is item 2's first bullet, unchanged.
`ClaudeResult.raw` already carries `usage`, `num_turns` and `duration_ms`.
Sending them to the collector would replace this page's one-off transcript
analysis with a series. It would also give P4 a measured budget instead of a
surface count: "the median ia-harness implementador costs X% of a window".

**Built by T-024 (2026-10-11), as `docs/decisions.md` ADR 53.** Not to the
collector and not on the handoff envelope: one JSON object per line per
`claude` call the dispatcher made, appended to the task's own usage log at
`<hive_tasks_dir>/<task_id>/usage/calls.jsonl`. A phase is up to four calls —
the attempt and the gate, review-write and shrink retries, each of which
*replaces* the result — on as many accounts as a failover tries, and the
envelope keeps one file per role, overwritten per round, which is the series
P5 is for. A line holds the role, round, account and which of the four calls it
was, plus `num_turns`, `duration_ms`, `total_cost_usd` and the four token
counts from `usage`, and `measured: false` when the CLI printed nothing that
parsed. Every field the CLI did not give is `null`, never 0, and nothing is
summed or scaled: a call that crashed records that it has no usage, which is
not the same fact as a call that cost nothing. The measured keys keep the CLI's
own spelling, taken from `docs/ROADMAP.md` V1.1's *Record* step and its V5.1
row because no stored `raw` exists on this harness to read them off and T-024
was refused a live probe — so each line also carries `raw_keys`, and the first
real run says on its own which keys were there. `/api/phases` serves nothing
new: the log is a sibling of `handoffs/`, and nothing reads it yet — no console
screen and no card owns a view of what a phase cost, the Tokens screen and
`docs/plans/front.md`'s Tokens task being a container's provider login rather
than its spend.

**This does not give P4 its budget.** One task's records are not a median, and
nothing here converts a token count into the window percentage `/usage`
reports, whose weighting is unpublished. P4's surface proxy above and P8's
5-point margin below stand as written until enough of these logs exist to
replace them.

**P6 — Don't trust a `/usage` snapshot past its own reset.** `/usage` is
local: it reads the counters the CLI stored at the account's last API call.
An account parked by `_recheck_cooling_accounts` makes no calls, so its
snapshot can only move if the CLI zeroes it at the reset. If the CLI did not,
the account would stay parked forever, and nothing in the code would break
that deadlock, because the reset timestamps are parsed but never used.

**Checked 2026-10-09: the deadlock does not happen.** cuenta2 made no API call
after 13:08 UTC. Its snapshot read 91% at every 15-minute probe up to 17:22
UTC, then 0% at the first probe after its 17:30 UTC reset, at 17:37 UTC. The
dispatcher moved it from PRE_COOLDOWN to IDLE on that probe and dispatched
revisor round 2 to it a second later (`.data/verify/t016-wait.log`,
`.data/verify/t016-rev2.log`). So the CLI applies the reset to a stored
snapshot without a fresh call. The fix is now a guard rather than a repair:
it would rest on the dispatcher's own reading of the reset time instead of
on that CLI behaviour, which is observed on one version (2.1.273), not documented. It
is optional, and comes after P7.

**P7 — A cycle that stops for lack of an account exits non-zero.** Today it
exits `rc=0`. That happened twice on 2026-10-09, so a script or an operator
reading only the exit code takes a held cycle for a finished one.

**Built by T-023 (2026-10-10), as `docs/decisions.md` ADR 52.** A held cycle
exits 75, sysexits.h's `EX_TEMPFAIL`, and a blocked one exits 1, the code every
other refusal in the CLI already uses; a finished one still exits 0. Held gets
its own code because the answer to it is a wait and not a person, and 2 was
argparse's. `run_task_cycle` returns a `CycleOutcome` — `FINISHED`, `HELD` or
`BLOCKED` — and `cli.py` maps it, so nothing raises from inside the cycle. The
signal starts at the one return in `dispatch_phase` that every no-account path
reaches (every account tried, cooling, or held back by `check_quota_ok`), as
`DispatchResult.no_account`; `run_phase` copies it to `CycleContext.held` when
a phase the task depends on stops on it. The optional map that finds no
account is not a hold, since the task runs anyway. `run-phase` has the same
split: `run_single_phase` hands back the unsuccessful result instead of None
when it was held, and the CLI exits 75 on `no_account`. Blocked exiting 1 is a
change of its own: rounds exhausted, a blocked arquitecto, a foreign lock or a
diverged branch all exited 0 before.

**P8 — Pace the primary's weekly spend against its reset.** Proposed by the
operator on 2026-10-09, after cuenta1 was parked at 84% of its week with the
week resetting the next day. This is the next harness change after T-017
closes, before P1–P7.

- **The problem.** The primary is held to a fixed `reserve_pct` (60), and
  `quota.exceeds_threshold` applies it to the session and the week alike. So
  the reserve is too loose at the start of a week, when the harness could
  spend the operator's 60% on day one, and too strict at the end, when about
  40% of the week is lost at the reset unspent.
- **The rule.** The primary's weekly ceiling rises linearly over its week:
  10% just after the reset, 95% from one day before the next one. With *d*
  days left, the ceiling is 10% + 85% × (6 − *d*) / 6 for 1 < *d* ≤ 6, and 95%
  once *d* ≤ 1. Days left 6, 5, 4, 3 and 2 give 10, 24, 38, 53 and 67%, just
  over one day left gives about 81%, and the last day 95%. Spend not used on one day carries to the next, because the
  ceiling is compared with the cumulative `week_pct`. The 5% kept at the end
  is for the operator. The five-hour window keeps its own threshold, so the
  week and the session are checked separately.
- **When it is computed.** At every probe, not once a day, so the ceiling
  never jumps at midnight. `_recheck_cooling_accounts` already probes every
  parked account while none is IDLE, so a primary parked by the ceiling is
  released by the ceiling rising, with no new scheduler.
- **Where the reset comes from.** `/usage` prints it on the week line, for
  example `Current week (all models): 86% used · resets Oct 10, 4:59pm (UTC)`
  (cuenta1, probed 2026-10-09 21:38 UTC). `UsageInfo.week_reset` already
  captures that clause and nothing reads it. The reset is not a fixed
  weekday per account. A week starts at a moment set by the account's own
  history, so the probe must reread it every time, never take it from
  config. One observation, on cuenta2: its week rolled between 13:08 and
  17:37 UTC on 2026-10-09, with no API call in between, and its next reset
  reads `Oct 16, 1:59pm (UTC)`. That fits a fixed seven-day cadence from the
  previous reset rather than a week anchored to the first message after it.
  It is one sample, so it is unverified; the rule does not depend on which
  of the two is true, as long as it reads the reset rather than predicting
  it.
- **Failure mode.** The clause is free text with no year. Parse
  `%b %d, %I:%M%p` and `%b %d, %I%p` with the zone in parentheses, and take
  the year that puts the reset within the next seven days. If parsing fails,
  or the reset is missing (the CLI omits it from a 0% line), fall back to
  today's fixed `reserve_pct` and log it. The probe runs only between phases, so a phase may overshoot
  the ceiling by its own cost, so dispatch stops about 5 points short until
  P5 gives a measured phase cost.
- **Scope.** The primary only. A worker account exists to be spent, and
  pacing it moves work in time without adding any. A config switch could
  enable it per account later.
- **Needs** an ADR (it replaces ADR-level reserve semantics), a change to
  `config.py`, `quota.py` and the parking in `dispatcher.py`, and tests on
  the reset parser with the recorded `/usage` texts. One surface, per P4.

**Built by T-020 (2026-10-10), as `docs/decisions.md` ADR 48.** The ramp is
`quota.weekly_ceiling_pct`, in five named constants rather than one
expression: 10% while more than six days remain, `10 + 85 × (6 − d) / 6`
through the ramp, 95% from one day out, less a 5-point margin for the phase
that runs after the probe. `quota.parse_reset` reads the week line's clause
against the probe's own `now`, and `quota.week_ceiling` is what the primary's
`week_pct` is compared with — recomputed every probe, never cached. The
primary's session keeps `reserve_pct`, which is also what its week falls back
to when no reset can be read; that fallback is the pre-ADR-48 behaviour
exactly, so a parse failure can delay work but never admit a week the old code
refused. `dispatcher._quota_decision` is the single comparison both
`check_quota_ok` and `_recheck_cooling_accounts` go through, which is what
releases a parked primary as the ceiling rises past its `week_pct` and not
before. `pace_primary_week` (default `true`) is the only new key. The rule as
measured: the probe this section was written from, cuenta1 at 86% of a week
resetting in under a day, is inside a 90% ceiling where the flat 60% reserve
parked it.

