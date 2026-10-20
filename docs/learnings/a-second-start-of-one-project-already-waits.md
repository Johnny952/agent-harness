# A second start of one project already waits, as `waiting: project`

**When it applies:** you build `docs/plans/projects.md` step 3 (one cycle per
project), or you draw why a pending `start` has not run.

**Status:** unconfirmed. T-115's architect and implementer both reported it,
but that is one source.

## Why

`start`'s serial key in `dispatcher/actions.py` `KINDS` is
`project:<slug>`. `dispatcher/worker.py` `Worker._consider` writes
`waiting: project` on a pending record whose key is busy. So the worker
already holds back a second start of a project while the first runs, though
the plan lists step 3 as unbuilt. The console shows it as *Queued — project*
until ADR 198, decision 4, names it *Waiting for the project*.

## Rule

Before building step 3, read `_consider` and the `start` serial key: part of
the rule already exists, and step 3 may only need to widen it.

## Evidence

`dispatcher/actions.py` `KINDS` (the `start` row) and `dispatcher/worker.py`
`Worker._consider` at the end of T-115. See also
[a-busy-serial-keys-waiting-reason-is-chosen-by-kind](a-busy-serial-keys-waiting-reason-is-chosen-by-kind.md).
