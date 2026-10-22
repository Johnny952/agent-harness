# A `rate_limit_event` only comes on `--output-format stream-json --verbose`

**When it applies:** you want a stream-only event — `rate_limit_event`, a tool
call, an assistant turn — out of a `claude -p` call made through
`dispatcher/docker_exec.py` `exec_claude`, and are about to read it off
`ClaudeResult.raw`.

**Status:** unconfirmed — read out of `exec_claude` by T-143's arquitecto and
implementador and checked by its auditor, one source.

## Symptom

No error. `ClaudeResult.raw` is the CLI's `result` object alone, and the event
the D2 run showed (`docs/ROADMAP.md` *Deferred gates*, D2) is simply not
there.

## Why

Before T-143 `exec_claude` always passed `--output-format json`, which prints
only the final result object. The `rate_limit_event` is a line of the
`stream-json` transcript, printed before the `result` line, and the CLI emits
that format only with `--verbose`.

## Rule

Pass `exec_claude(stream=True)`: it adds `--output-format stream-json
--verbose`, and `_parse_stream` keeps the last `type == "result"` line as `raw`
and the last `rate_limit_event` as `ClaudeResult.rate_limit_event`, unparsed;
`dispatcher/quota.py` `usage_from_rate_limit_event` reads it
(`docs/decisions.md` ADR 312). Every phase call and its resumes stream since
T-143; the `/usage` probe in `_read_usage` and the recheck does not, so it
never carries an event.

## Evidence

`git diff main HEAD -- dispatcher/docker_exec.py` on T-143's branch: the
`command += ["-p", prompt, "--output-format", ...]` line and the new
`stream` parameter; the D2 sample event is in `tests/dispatcher/test_quota.py`
`D2_EVENT`.
