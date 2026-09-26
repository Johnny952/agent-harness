# `mode=ro` is not a write-free open: a WAL database needs its sidecars

**When it applies:** you are reading `observability/collector/`'s `events.db`
from anywhere other than the collector — `db.connect_read_only`, or a new
service handed `observability_data:/…:ro`.

**Status:** confirmed 2026-09-26 against the running stack. The sidecar half
was measured from inside the phase; the `:ro` half, which no phase could
reproduce, was reproduced by hand on `compose-api-1` and then fixed — see
*Evidence*.

## Symptom

A `file:<path>?mode=ro` open of a cleanly-closed events database creates two
files beside it:

```
AssertionError: after_write=['events.db'] during_read=['events.db', 'events.db-shm', 'events.db-wal'] after_read=['events.db', 'events.db-shm', 'events.db-wal'] rows=(1,) journal_mode=wal
```

On a `:ro` mount those creates cannot happen and sqlite refuses the whole open
with `unable to open database file` — a message that says nothing about
sidecars and reads exactly like a missing file.

## Why

`db.init_db` sets `PRAGMA journal_mode=WAL`, which is stored *in the database
file*, and the collector opens and closes a connection per insert, so the
`-wal`/`-shm` pair is usually checkpointed away. A WAL reader needs the
shared-memory file and creates it when it is absent, `mode=ro` or not.
`mode=ro` stops sqlite creating the *database*; it does not make the open free
of writes.

## What to do

Do not read a plain `sqlite3.connect` over a path on a `:ro` mount either — it
creates the file and then raises. Use `db.connect_read_only`, never call
`init_db` from a reader, and treat an unopenable database as `[]` plus a warning
naming the path. If the endpoint has to return rows in production, the events
volume has to be mounted read-write for that reader — which is what
[T-009-D4](../debt/T-009-D4.md) ended up doing, the alternatives having been
ruled out. `immutable=1` is not one of them — the collector is a live writer and
that flag tells sqlite the file cannot change. Read-write for the reader is not
read-write for the *rows*: keep `mode=ro` in the URI and never call `init_db`,
so what the mount grants is the sidecar and a stray write still raises.

## Evidence

A throwaway `tests/observability/` probe in
`/data/projects/ia-harness/worktrees/T-009/work`, deleted after measuring; the
output above is verbatim. Inbox entry
`T-009-a-read-only-sqlite-open-creates-wal-sidecars.md`;
`docs/implementations/T-009.md`, "The events volume is `:ro` and the collector
writes WAL".

For the `:ro` half, `.data/verify/t009-api-verification.txt`: with the volume
mounted `:ro` the collector counted 2974 rows in the same 11898880-byte file the
api saw over `/events`, no `-wal`/`-shm` pair existed between the collector's
writes, and `/api/events` answered `[]` plus the warning — so the failure was the
mount and not a missing, empty or mispathed database. With the mount read-write
and nothing else changed, `/api/events?limit=1` returns one row with
`warnings: []` and `events.db-shm` appears in the volume, created by the api's
own `mode=ro` open.
