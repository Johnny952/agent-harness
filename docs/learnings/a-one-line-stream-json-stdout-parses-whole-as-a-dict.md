# A one-line `stream-json` stdout parses whole as a dict

**When it applies:** you change how `dispatcher/docker_exec.py` `_parse_stream`
tells a `--output-format json` stdout from a `stream-json` one, or write a test
fake whose stdout is a single JSON line.

**Status:** unconfirmed — found by T-143's implementador while writing
`_parse_stream`; checked against the code by its auditor.

## Symptom

No error. A stream whose only line is, say, a `rate_limit_event` (or a
`system` line) passes `json.loads(stdout)` as one object and would be taken for
the result: `raw` becomes the event, `result_text` is empty and the
`rate_limit_event` is lost.

## Why

The `json` format is one object; a `stream-json` transcript of one line is
also one object. `json.loads` on the whole stdout cannot tell them apart.

## Rule

Take the whole stdout as the result only when its `type` is `result` or absent
(`whole.get("type", "result") == "result"`); anything else typed is a stream
line and goes through the line loop.

## Evidence

`dispatcher/docker_exec.py` `_parse_stream` on T-143's branch, and its tests in
`tests/dispatcher/test_docker_exec.py`; `docs/implementations/T-143.md`.
