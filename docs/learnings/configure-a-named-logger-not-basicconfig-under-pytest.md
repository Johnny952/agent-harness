# Configure a named logger, not `logging.basicConfig`, when a test pins the output

**When it applies:** you add logging setup to a service entry point and a test
must assert that a library's log line reaches stderr.

**Status:** unconfirmed — proposed by T-172's arquitecto and implementador
(one source: the implementador applied the arquitecto's plan).

## Symptom

`logging.basicConfig(...)` inside the entry point configures nothing under
pytest, so a `capsys` assertion on the library's line fails although the same
code logs in production.

## Why

pytest's logging plugin puts its capture handlers on the root logger for every
test, and `basicConfig` returns without doing anything when the root logger
already has a handler. In production it would also change every importer's
logging, not just the library's.

## Rule

Configure the library's own logger (`logging.getLogger("waitress")`: level,
plus a `StreamHandler()` if it has none, plus `propagate = False`) from the
entry point, never at import,
and restore that logger after each test with an autouse fixture. The handler
binds `sys.stderr` when it is built, so a handler left over from one test
writes to a capture stream that is closed by the next. `tests/conftest.py`
`_restore_the_waitress_logger` and each service's `_log_waitress`
(`docs/decisions.md` ADR 420) are the example.

Corrected by T-173: without `propagate = False` the line printed twice in
production, because `waitress.serve()` runs `basicConfig()` itself and root's
handler printed the record a second time (`docs/decisions.md` ADR 423). To make
that visible in a test, see
`a-root-stand-in-for-basicconfig-needs-basic-format.md`.

## Evidence

T-172: `tests/config_writer/test_app.py`
`test_run_sends_waitress_start_line_and_queue_warnings_to_stderr` and its three
siblings in `tests/board_service/test_app.py` and `tests/observability/`.
