# A secret-bearing write in `front/` is a plain async handler, not `useMutation`

**When it applies:** you are writing a `front/` control that sends a token, a
login code or another secret to the api, and are about to reach for React
Query's `useMutation` like the screen's other writes.

**Status:** unconfirmed — proposed by T-186-S3's arquitecto, implementador and
revisor as one source; ruled in `docs/decisions.md` ADR 492 decision 4.

## Symptom

No error. The write works, and the secret stays readable afterwards: a settled
mutation keeps its `variables` in the `MutationCache`, where devtools or any
`useMutationState` reader can see it until garbage collection.

## Why

React Query stores each mutation's state, variables included, after it
settles. Clearing the input field does not clear that copy.

## Rule

Send the secret from a plain `async` handler holding the value only in a local
and clear the field in `finally`, as `front/src/routes/tokens.tsx` does for
the GitHub token and the login code; writes without a secret keep
`useMutation`. A reviewer expecting uniformity is answered by ADR 492.

## Evidence

T-186-S3: `front/src/routes/tokens.tsx` comments citing ADR 492 decision 4;
`front/src/routes/-tokens.test.tsx` cleared-field tests; auditor ran
`bun run test` in `front/` (38 files, 935 tests passed).
