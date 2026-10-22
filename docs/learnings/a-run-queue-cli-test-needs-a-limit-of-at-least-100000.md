# A `run-queue` CLI test needs a `--limit` of at least 100000

**When it applies:** you write or change a test that drives
`run-queue` through the CLI (`_cli(..., "run-queue", ...)` in
`tests/dispatcher/test_queue.py`, `test_drafts.py` or
`test_parallel_queue.py`) with a `--limit`.

**Status:** unconfirmed — proposed by T-151's arquitecto and implementador; no
second task has hit it.

## Symptom

The command exits 2 before any phase, with
`--limit 500: the limit is in measured tokens (input + output + cache, ADR 79), not tasks; at least 100000, since one phase alone measures more`.

## Why

`dispatcher/cli.py` `_run_queue` refuses a limit below `MIN_QUEUE_LIMIT`
(ADR 333) before `queue.plan`. Tests that call `queue.plan` directly are not
bound by it. Fake phase spends in those tests are in the same measured-token
units as the limit.

## Rule

Pass `--limit` at or above `cli.MIN_QUEUE_LIMIT` in CLI-driven tests and scale
any fake phase spend compared against it to match (T-151 used 500000 and
`phases: [600_000]`).

## Evidence

`test_cli_refuses_a_limit_that_is_not_measured_tokens` and
`test_cli_limit_and_push_refused_exit_codes` in `tests/dispatcher/test_queue.py`;
112 passed over the four queue/CLI test files in the T-151 worktree.
