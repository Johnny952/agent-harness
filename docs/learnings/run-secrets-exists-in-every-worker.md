# `/run/secrets` exists in every worker

**When it applies:** you tell whether a secret under `/run/secrets` is
mounted into the `worker` by whether its parent directory exists, or you read
ADR 462 decision 3's parent rule as current.

**Status:** unconfirmed. T-185's arquitecto read it from the compose files;
not run (no docker in a phase). ADR 480 acts on it.

## Symptom

```
git_token: {"state": "absent"}   # expected "not_mounted" under docker-compose.coolify.yml
```

## Why

ADR 462 decision 3 assumed a worker with no token mount has no `/run/secrets`.
Both compose files' `worker` service mounts
`../../.data/credentials/projects:/run/secrets/projects`, so Docker creates
`/run/secrets` in every worker, and the Coolify worker, which mounts no token
(ADR 91), read `absent`.

## Rule

Do not tell `not_mounted` by the parent directory; `/run/secrets` exists in
every worker. `dispatcher/credentials.py` `git_token_row` now tells it by
`CONTAINER_MARKERS` (ADR 480).

## Evidence

`docker/compose/docker-compose.coolify.yml` service `worker` volumes;
`dispatcher/credentials.py` `git_token_row` before T-185-S1.
