# A claimed action's record says `running`, never `claimed`

**When it applies:** you are writing `front/` code (or a test fixture) that
asks whether an action from `GET /api/actions` is open or in progress, and are
about to compare its `status` with `"claimed"`.

**Status:** unconfirmed — proposed by T-186-S3's arquitecto, implementador and
revisor from one grep; checked by its auditor against `dispatcher/actions.py`.

## Symptom

No error. A check such as `a.status === "claimed"` never matches, so a running
action reads as not open: `front/src/routes/projects.new.tsx` `isOpen` and its
progress line test `"claimed"`, and a running setup action there reads as
"is waiting for the worker" and can be queued a second time.

## Why

`claimed` is the record's *directory* state (`dispatcher/actions.py` `CLAIMED`,
and the `state` field), not its `status`. `STATUSES` is `pending`, `running`,
`done`, `failed`, `cancelled`, `interrupted`, and `claim` writes
`status="running"`. The front's `FINISHED` lacked `cancelled` until T-186-S3
(`docs/decisions.md` ADR 492).

## Rule

Open is `pending` or `running`; finished is `done`, `failed`, `cancelled`,
`interrupted` — test `status`, never the directory name, as
`front/src/routes/tokens.tsx` `OPEN` does.

## Evidence

T-186-S3: grep of `status=` in `dispatcher/actions.py` (`claim` sets
`running`); grep of `"claimed"` in `front/src/routes/projects.new.tsx` (two
hits, unfixed — recorded in ADR 492 *Consequences*).
