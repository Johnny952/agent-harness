# A resumed call's tokens are its own; its `total_cost_usd` is the session's so far

**When it applies:** you are adding up, averaging or diffing the lines of the
usage log `docs/decisions.md` **ADR 53** writes — a per-attempt, per-phase or
per-task total, a median, a budget — or serving such a total from the route
**ADR 54** describes.

**Status:** unconfirmed — read by T-041's three phases out of two real
retries, which is one source. `docs/decisions.md` **ADR 64** is the decision
and holds the evidence table and its limits.

## Symptom

No error. A cost total that is too large by roughly the phase's own cost for
every retry an attempt took, or a per-call cost that looks absurd for a
two-turn call.

## Why

On a `--resume` call the CLI reports `usage` (the four token fields) and
`num_turns` for that one call, but `total_cost_usd` as the running total of
the whole session. T-030's revisor `shrink-retry` reported 2 turns and fewer
tokens than its phase on every column, yet `total_cost_usd` 1.5551396 after
the phase's 1.470297.

## Rule

Sum the token fields and `num_turns` over an attempt's lines; take the
attempt's cost from its **last** line's `total_cost_usd`, and one call's cost
as the difference from the line before it in the same session. Never sum the
cost lines. A failover attempt on another account is a new session.

## Evidence

`<hive_tasks_dir>/T-030/usage/calls.jsonl` and
`<hive_tasks_dir>/T-031/usage/calls.jsonl`, read on 2026-10-14; the four lines
are tabulated in ADR 64. Two retries of two kinds under one inferred CLI
version; no `review-retry` and no attempt with two retries has been read.
