# The board service's shared test config points at the real state dir

**When it applies:** you are writing a test under `tests/board_service/` for a
route that writes under `cfg.state_dir` (the action queue, ADR 142, ADR 151),
and are about to reuse the `cfg` fixture or `CONFIG_YAML` from
`tests/board_service/test_app.py` as it stands.

**Status:** unconfirmed — proposed by T-102's arquitecto, implementador and
revisor (one source: the arquitecto's reading), checked against the tree by
T-102's auditor.

## Symptom

No error: the test passes and writes into the host's real
`/data/dispatcher_state`, or fails only where that directory is not writable.

## Why

`CONFIG_YAML` in `tests/board_service/test_app.py` hard-codes
`state_dir: /data/dispatcher_state`, and its `cfg` fixture keeps it; only the
hive is moved under `tmp_path`. No board-service route wrote state before
T-102, so nothing noticed.

## Rule

Build your own config with the state dir under `tmp_path`: format
`CONFIG_YAML` and replace `/data/dispatcher_state` with a `tmp_path` path, as
the `cfg` fixture in `tests/board_service/test_actions_routes.py` does.

## Evidence

`grep -n state_dir tests/board_service/test_app.py` on the T-102 branch prints
the `/data/dispatcher_state` line of `CONFIG_YAML`.
