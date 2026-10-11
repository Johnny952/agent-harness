# "Tokens" names a provider login here, not a token count — spend has no screen and no card

**When it applies:** you are about to cite the console's "Tokens" screen or
`docs/plans/front.md`'s "Tokens task" as the place a token count, a cost or a
quota number would be shown.

**Status:** unconfirmed — found by T-024's revisor as that round's one blocking
finding, after three docs and a debt entry had already been written against the
wrong reading.

## Symptom

No error. A doc written in this project said:

```
no console screen reads it — the tokens screen is fixture-backed and its own
card, which `front/`'s plan puts outside this task
```

while `front/src/routes/tokens.tsx` reads:

```
title="Session tokens"
subtitle="Provider login of each container. Expired or revoked tokens make the account unusable until re-authenticated."
```

## Why

"Tokens" names two unrelated objects in this repo, and the console's is the
credential. The Tokens screen's row is
`front/src/lib/api/ops-types.ts:ContainerToken` — `state`, `expires_at`,
`device_code` — and all three mentions of the Tokens task in
`docs/plans/front.md` are credential too: a tier-3 privileged read, gated on a
docker socket and on a charter ruling about a secret leaving a container.

Token *spend* has no screen and no card anywhere in `docs/`. Grep `cost` and
`num_turns` over `front/src` and the only hits are comments. So a pointer at
the Tokens card sends the next task to a privileged read and a human decision
that have nothing to do with what a phase cost.

Quota *percentages* are a third thing again, and they do have a home: the Pool
screen's usage gauge, served by `/api/accounts` (`docs/decisions.md` ADR 20,
ADR 48, ADR 49).

## Rule

Write "no card owns this yet" rather than naming the Tokens card. If you need
the distinction in prose, name the collision explicitly — ADR 53
*Consequences*, `docs/plans/token-economy.md` **P5** and
`docs/debt/T-024-D1.md` all do, so that the next reader does not re-make the
inference from the word alone.

## Evidence

T-024's revisor, round 1: `grep -rniE "token|cost|usage" docs/plans/front.md`
and `grep -rniE "cost|num_turns" front/src`, in
`/data/projects/ia-harness/worktrees/T-024/revisor` (a review worktree, since
discarded). Re-checked first-hand by T-024's implementador in round 2 against
`front/src/routes/tokens.tsx` and `front/src/lib/api/ops-types.ts`, which is
how the four wrong pointers were corrected before the branch landed.
