# The worker==dispatcher volumes test runs over both compose files

**When it applies:** you add or remove a volume on `worker` or `dispatcher`
in only one of `docker/compose/docker-compose.yml` and
`docker/compose/docker-compose.coolify.yml`.

**Status:** unconfirmed. Proposed by T-185-S2's arquitecto and implementador
(one source); checked against the test by the auditor.

## Symptom

`test_the_worker_is_the_dispatchers_mounts_behind_its_own_profile` fails on
`assert worker["volumes"] == dispatcher["volumes"]` for one parameter only.

## Why

The test is `@pytest.mark.parametrize("compose_file", BOTH_FILES)` in
`tests/integration/test_compose_invariants.py`, so one assertion covers both
files (ADR 166 decision 1). Since ADR 486 the plain file's worker carries the
git token the dispatcher does not, and the Coolify file mounts none (ADR 91).

## Rule

Branch the assertion on `compose_file`, as the test now does for the git
token, rather than adding the mount to the other service or the other file.

## Evidence

`tests/integration/test_compose_invariants.py`
`test_the_worker_is_the_dispatchers_mounts_behind_its_own_profile` and
`BOTH_FILES`; `docs/decisions.md` ADR 486 decision 5 (T-185-S2).
