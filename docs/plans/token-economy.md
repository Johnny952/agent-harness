# Token economy — what one window buys, and sizing tasks to fit it

**Status:** proposal, nothing built. Measured 2026-10-09 by the operator, out of
cycle, on T-016's first round. It feeds the root README's *Prioritized* item 2
(Token economy) and item 5 (Task profiles), and does not replace either.
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

Ordered by cost to build. None is approved. P1–P3 are prompt text, P4 is a
process change, and P5–P7 are code.

**P1 — Batch independent tool calls.** Add one line to every role's prompt
through `dispatcher/project_docs.py:duties`, where the docs rules already
live: *"Independent reads, greps and edits go in the same turn; every extra
turn re-reads your whole context."* Check the effect by measuring tool calls
per turn on the next task.

**P2 — Never read the Results log to learn its format.** Add a rule to the
same duties block: *"To append a Results log row, read its header and the
last row with `tail -n 1 docs/ROADMAP.md | cut -c1-400`; never `sed` or `grep`
whole rows."* Wrapping the existing rows would also work, but it is ruled out
because the log is append-only.

**P3 — Don't diff what you just wrote.** Add to the implementador's duties: use
`git diff --stat` to check scope, and use offset/limit reads for files you only
need a region of. The full diff is the revisor's read, not the implementador's.

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

**P6 — Don't trust a `/usage` snapshot past its own reset.** `/usage` is
local: it reads the counters the CLI stored at the account's last API call.
An account parked by `_recheck_cooling_accounts` makes no calls, so its
snapshot can only move if the CLI zeroes it at the reset. If the CLI
doesn't, the account stays parked forever. That is a deadlock nothing in the
code breaks, because the reset timestamps are parsed but never used. The fix
is to treat a reading as stale once its reset time has passed: mark the
account IDLE and let a real dispatch refresh the counter. Whether the
deadlock actually happens is unverified. It will show at cuenta2's reset at
17:30 UTC today, in `.data/verify/t016-wait.log`.

**P7 — A cycle that stops for lack of an account exits non-zero.** Today it
exits `rc=0`. That happened twice on 2026-10-09, so a script or an operator
reading only the exit code takes a held cycle for a finished one.
