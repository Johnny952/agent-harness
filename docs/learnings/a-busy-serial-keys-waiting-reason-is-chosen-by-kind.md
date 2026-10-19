# A busy serial key's `waiting` reason is chosen by kind in `_consider`

**When it applies:** you add an action kind to `actions.KINDS` with a serial
key that is not `project:<…>`, or you change `worker.Worker._consider`.

**Status:** unconfirmed. T-110's architect and implementer both reported it,
but that is one source.

## Why

Before T-110, `_consider` wrote `waiting: project` for any busy serial key.
A `thread:<id>` key needs `waiting: thread`, which is in `actions.WAITING`.
T-110 chose the reason by `kind == "turn"`. A new kind gets `project` unless
`_consider` names it. `_consider` also skips the pool-wide open-account check
for `turn` only (ADR 173, 5).

## Rule

When you add a kind, check both branches of `_consider` for it: the waiting
reason, and whether the pool-wide account check applies.

## Evidence

`dispatcher/worker.py` `_consider` at the end of T-110.
