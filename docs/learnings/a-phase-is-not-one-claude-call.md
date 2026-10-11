# A phase is up to four `claude` calls, and its final result describes only the last one

**When it applies:** you are deriving anything per phase from what
`dispatch_phase` returns, or from a `ClaudeResult` — a cost, a turn count, a
duration, a session id, a model — or you are reading the per-call usage log and
deciding what one phase's row should be.

**Status:** unconfirmed — read out of `dispatcher/dispatcher.py` by T-024's
arquitecto, implementador and auditor, which is three readings inside one task
and so one source.

## Symptom

No error. A number that is measured, labelled per phase, and three turns short
of the truth.

## Why

Two multiplications, and neither is visible from the return value.

**Failover.** `dispatch_phase` loops over accounts: when one answers 429 it
records the rate limit, parks the account `COOLING_DOWN`, releases the task
lock and `continue`s to the next account with `resume_session_id` carried
forward. Every pass through that loop is a call the service served and an
account paid for, and only the last one's result is returned.

**Retries inside one attempt.** `_run_gates`, `_refuse_review_writes` and
`_shrink_over_budget` may each spend one `--resume`, and each one **replaces**
`result` with the retry's. So the `ClaudeResult` that comes back out of a phase
that took its shrink retry carries the shrink retry's `usage`, `num_turns` and
`duration_ms` — two turns asking for a shorter handoff — and not the phase's.

Worse for a reader, those three helpers record and then *discard* a retry that
failed or came back rate-limited, keeping the first result instead. A retry
that was thrown away as a result still cost a turn, so even "the result that
landed" is not "the calls that were made".

## Rule

Count calls, not phases. `docs/decisions.md` **ADR 53** is built on this: the
usage log carries one line per `claude` call with a `call` field naming which
of the four it was (`phase`, `gate-retry`, `review-retry`, `shrink-retry`), on
every account tried, precisely because no single record can be a phase's cost.

If you need a per-phase number, you are aggregating several lines, and
[`docs/debt/T-024-D2`](../debt/T-024-D2.md) is the precondition: whether a
`--resume` result's `usage` is per call or cumulative over the session was
never probed, so whether that aggregate is a sum or a diff is still unknown.

## Evidence

`dispatcher/dispatcher.py`: the account loop in `dispatch_phase` and its 429
`continue`; the `result = ...` reassignments at the three helper calls; and
inside `_run_gates`, `_refuse_review_writes` and `_shrink_over_budget`, the
`if not _exec_succeeded(retry) or is_rate_limit_error(retry)` branch that
discards one. `grep -rn --include=*.py` for each helper shows exactly one
caller each, all in `dispatch_phase`. The four recorded call sites and the
tests that cover a failover and a discarded retry are in
`docs/implementations/T-024.md` *The change*.
