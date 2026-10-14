# The usage region's two subtractions count lines, not figures

**When it applies:** you are changing the task detail's usage region
(`UsageRegion`, `UsageGroupRow` in `front/src/routes/tasks.$taskId.tsx`), or
reviewing it against `docs/plans/front.md` *The usage task*'s rule that the
console computes nothing, and you find a `-` in it.

**Status:** unconfirmed — proposed by T-046's arquitecto and implementador
(one source, the plan the second followed) and checked against the code by
its auditor.

## Symptom

None printed. A reviewer reading "the console computes nothing: no sum, no
difference, nothing across groups" finds two subtractions in
`UsageGroupRow` and reads them as a breach — or, the other way, takes them
as precedent for subtracting a token or cost figure.

## Why

The card itself asks for two counts the api does not serve: how many
retries the *first call* label leaves out (`call_count - 1`), and how many
lines carry a `measured` that is neither true nor false
(`call_count - measured_calls - unmeasured_calls`). Both are counts of log
lines. Neither touches a token, a duration or a dollar figure, and nothing
crosses a group. `total_cost_usd` in particular must never be summed or
differenced: on a resumed call it is the session's running total
(`docs/decisions.md` ADR 64; `a-resumed-calls-cost-is-the-sessions-running-total`).

## Rule

The two line counts are the only arithmetic the region may do. A figure —
tokens, turns, duration, cost — is printed as served through `Figure`, or
not at all; a new total belongs in the api (ADR 54), not here.

## Evidence

`UsageGroupRow`'s `retries` and `neither` in
`front/src/routes/tasks.$taskId.tsx`; the region's banner comment above
`UsageRegion`; `docs/implementations/T-046.md` *The arithmetic the card asks
for*.
