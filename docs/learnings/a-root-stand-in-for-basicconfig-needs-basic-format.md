# A test's stand-in for a library's `basicConfig()` needs `BASIC_FORMAT`

**When it applies:** a test puts its own `StreamHandler` on the root logger to
stand in for a library's `logging.basicConfig()`, and asserts a log line is
printed once by counting a `LEVEL:logger:` prefix in `capsys` output.

**Status:** unconfirmed — proposed by T-173's arquitecto and implementador (one
source), with the format trap found and reproduced by T-173's auditor.

## Symptom

On the code with the bug (`propagate` left `True`), this assertion passed:

```
assert err.count("INFO:waitress:Serving on") == 1  # ADR 423
```

The duplicate line was in `err` as a bare `Serving on http://0.0.0.0:8787`,
without the prefix being counted.

## Why

Under pytest root already holds pytest's capture handlers, so
`logging.basicConfig()` does nothing and root never writes to `capsys`. A test
therefore has to put its own handler on root. A bare `logging.StreamHandler()`
formats with `%(message)s`, while the real `basicConfig()` uses
`logging.BASIC_FORMAT` (`%(levelname)s:%(name)s:%(message)s`). The prefixed
count therefore cannot see the propagated copy.

## Rule

Give the stand-in root handler
`setFormatter(logging.Formatter(logging.BASIC_FORMAT))`, add it inside the fake
`serve` and remove it in a `finally`. Then check that the count fails on the old
code before trusting it.

## Evidence

T-173 auditor: with `logger.propagate = False` removed from
`observability/collector/server.py` `_log_waitress`, `python3 -m pytest -q
tests/observability/test_collector.py -k waitress_start_line` failed only on
`propagate is False`. After `BASIC_FORMAT` was added to the fake handler, it
failed with `assert 2 == 1` on the count.
