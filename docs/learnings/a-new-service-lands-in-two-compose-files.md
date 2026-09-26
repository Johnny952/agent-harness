# A new service is two compose files: `docker-compose.coolify.yml` is a deploy path, not a copy

**When it applies:** you added, renamed or removed a service, a port, a mount or
an environment variable in `docker/compose/docker-compose.yml`.

**Status:** unconfirmed — reported once, by T-009's implementador, as review
finding F3.

## Symptom

No error. `docker/compose/docker-compose.yml` gained the `api` service and the
stack was green; `docker-compose.coolify.yml` — the file the Coolify deployment
actually runs — did not have it, so the read API existed on a developer's host
and nowhere an operator deploys to. Nothing failed, because the compose
invariants were asserted against one file.

## Why

The two files are not a source and a convenience copy: they are two deploy
paths over the same images, differing in what the platform provides (builds,
networks, secrets). A service that exists in one is invisible from the other,
and "the stack is up" is true of a stack that is missing an endpoint.

## What to do

Add the service to both files, and parametrize the invariant that asserts it
over both — `tests/integration/test_compose_invariants.py` takes
`compose_file` as a parameter for exactly this reason. Check which existing
invariants are already parametrized before adding a new unparametrized one: the
ones that are tell you which files a reader is expected to keep in step.

## Evidence

T-009's F3: the `api` service mirrored into `docker-compose.coolify.yml` with
the same build, port and `:ro` mounts, and the read-api invariant parametrized
over both files. `docs/implementations/T-009.md`, "Round 2 — what review
changed".
