# The agent image has no procps: inspect processes with sh over /proc

**When it applies:** you write a `docker exec` command that looks for a process
inside an agent container and reach for `pgrep`, `ps` or `pkill`.

**Status:** unconfirmed — T-170's auditor, from the arquitecto's and the
implementador's handoffs.

## Symptom

`pgrep`/`ps` are not on the agent image's PATH, so a probe built on them fails
inside the container whatever the process table holds.

## Why

`docker/agent/Dockerfile` builds on `node:24-bookworm-slim` and never installs
`procps`. The image has no `USER` line, so commands run as root and can read
every `/proc/<pid>/cwd`.

## Rule

Walk `/proc/[0-9]*` with `sh` and `readlink`, as `dispatcher/docker_exec.py`
`PROCESS_PROBE_SCRIPT` does (ADR 429), rather than installing or assuming procps.

## Evidence

T-170 arquitecto read `docker/agent/Dockerfile`; the probe script was run only
under the host's `sh` in `tests/dispatcher/test_process_probe.py`, not in a real
agent container.
