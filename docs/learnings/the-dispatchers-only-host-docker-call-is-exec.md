# The dispatcher's only call to the host's Docker daemon is `docker exec`

**When it applies:** you are adding a `docker` CLI call to `dispatcher/` or
`scripts/` that talks to the host daemon — anything other than `docker exec`
into an `agent-*` or `dind-*` container — or renaming an account container
away from those prefixes.

**Status:** unconfirmed — inventoried by T-176's arquitecto by grepping every
docker argv under `dispatcher/`, re-checked by its revisor and auditor.

## Symptom

None in the tests. On a deployment, the new call gets a `403` from the
`docker-proxy` service and the phase that made it fails; the proxy's log
names the refused path.

## Why

Since T-176 the dispatcher and the worker mount no socket: they reach Docker
through `DOCKER_HOST=tcp://docker-proxy:2375`, and the proxy admits only the
calls `docker exec` makes (`_ping`, container inspect, exec create, start and
inspect), and only for containers named `agent-…` or `dind-…`. The one
host-daemon argv is built in `dispatcher/docker_exec.py` `_docker_exec_argv`.
`docker ps` in `dispatcher/command_targets.py` `running_services` and
`docker compose exec` in `dispatcher/command_run.py` `_command_for` run
*inside* the account's dind through `run_docker_exec`, against the dind's own
daemon, so the proxy never sees them.

## What to do

Route a new host call through exec into an agent or dind container if you
can. If it truly needs another endpoint, widen the allowlist in both compose
files' `docker-proxy` `command`, extend
`tests/integration/test_compose_invariants.py`
`test_the_docker_proxy_allowlist_admits_exec_and_refuses_the_rest`, and amend
`docs/decisions.md` ADR 435 with a new ADR.

## Evidence

`docs/decisions.md` ADR 435 *Context* (the inventory); T-176's handoffs.
