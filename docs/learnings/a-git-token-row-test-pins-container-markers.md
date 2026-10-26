# A `git_token_row` test pins `CONTAINER_MARKERS`

**When it applies:** you write or change a test that reaches
`credentials.git_token_row` with a missing token path — directly, through
`credentials.write`, or through the worker's `git_token` handler.

**Status:** unconfirmed. Proposed by T-185-S1's arquitecto, implementador and
revisor (one source); whether the suite runs inside a container was not
checked.

## Symptom

No error on a host; inside a container (where `/.dockerenv` exists) a test
expecting `absent` reads `not_mounted`, or the reverse on a host.

## Why

Since ADR 480 a missing path is `not_mounted` when any of
`credentials.CONTAINER_MARKERS` exists and `absent` otherwise, so the answer
depends on where the suite runs. The module global is read at call time.

## Rule

Monkeypatch `credentials.CONTAINER_MARKERS` to paths under `tmp_path`, as
`tests/dispatcher/test_credentials.py`'s autouse `outside_a_container`
fixture and `test_worker_credentials.py`'s
`test_git_token_fixed_failures_for_the_file` do.

## Evidence

`dispatcher/credentials.py` `_in_container`; ADR 480 *Consequences* (T-185-S1).
