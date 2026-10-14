# The hook is copied alone into the agent image, so it imports nothing from `hooks/`

**When it applies:** you are adding code that `hooks/emit_event.py` runs —
a helper, a rule table, a dependency — and are about to put it in a sibling
module under `hooks/` or reach for a package the agent image does not have.

**Status:** unconfirmed — proposed by T-054's arquitecto and implementador
(one source), checked against the Dockerfile by T-054's auditor.

## Symptom

None in the suite: `tests/hooks/test_emit_event.py` imports
`hooks.emit_event` from a checkout, where every sibling module is on the
path, so a split passes every test and the hook then fails inside an agent
container with an `ImportError` on every tool call.

## Why

`docker/agent/Dockerfile` copies `hooks/emit_event.py` and
`hooks/install_settings.py` one file at a time into
`/usr/local/lib/ia-harness/`, never the `hooks/` directory, so no other file
of `hooks/` exists beside them in the image.

## Rule

Keep everything the hook runs in `hooks/emit_event.py` itself, on the
standard library plus `requests` (ADR 74 puts every redaction rule in one
block of that file for this reason); a new file needs its own `COPY` line.

## Evidence

The two `COPY hooks/…` lines in `docker/agent/Dockerfile`; `docs/decisions.md`
ADR 74, *Context*.
