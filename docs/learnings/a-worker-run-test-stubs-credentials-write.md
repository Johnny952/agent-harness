# A `Worker.run()` test stubs `credentials.write`

**When it applies:** you write a test that drives `Worker.run()` in a file
other than `tests/dispatcher/test_worker.py`, or you remove or narrow that
file's autouse `snapshots` fixture.

**Status:** unconfirmed. T-181's arquitecto named the risk; the implementador
added the stub.

## Symptom

No error you would notice: the first loop pass starts the
`credentials-snapshot` thread, which runs `docker exec` into each configured
account's container (up to `STAT_TIMEOUT_SECONDS`, 15 s, each) and writes
`credentials.json` into the test's state dir.

## Why

Since T-181, `Worker.run()` calls `_maybe_snapshot` before every `tick`, and
the first call always starts a snapshot (ADR 462 decision 7).

## Rule

Monkeypatch `worker_mod.credentials.write` in any test that runs `run()`, as
`tests/dispatcher/test_worker.py`'s autouse `snapshots` fixture does.

## Evidence

`tests/dispatcher/test_worker.py` `snapshots` fixture and `SnapshotRecorder`
(T-181).
