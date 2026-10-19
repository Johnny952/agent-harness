# The real action-queue reader is not the `/queue` draft's `actionsQuery`

**When it applies:** you are reading `GET /api/actions` in `front/` (a screen
that shows queued or running starts, a cancel, the `/queue` screen itself) and
`front/src/lib/api/queries.ts` already offers `actionsQuery` and
`front/src/lib/api/client.ts` already offers `listActions`.

**Status:** unconfirmed — proposed by T-105-S2's arquitecto, built that way by
its implementador.

## Symptom

`actionsQuery` looks like the reader you want, but it is the `/queue` draft's:
`listActions` returns a mock `QueuedAction[]`, not the api's envelope
`{pending, claimed, done, done_total, worker}`, and it owns the query key
`["actions"]`.

## Why

The `/queue` screen was drawn before the action queue existed. T-105-S2 left
the draft alone and added a second, real reader beside it: `getActionQueue`
in `client.ts`, `actionQueueQuery` under key `["action-queue"]` in
`queries.ts`, and the `ActionRecord` / `ActionQueue` types in `types.ts`
(`docs/decisions.md` ADR 160). Reusing `["actions"]` for the real shape would
put two shapes under one cache key.

## Rule

Read the queue through `actionQueueQuery` (key `["action-queue"]`) and
invalidate that key after a start or cancel; leave `actionsQuery`/`listActions`
to whichever task replaces the `/queue` draft.

## Evidence

`git diff 908d606 HEAD -- front/src/lib/api` on T-105-S2's branch;
`docs/implementations/T-105-S2.md` *Untouched*.
