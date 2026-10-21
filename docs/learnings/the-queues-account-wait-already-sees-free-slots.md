# The queue's account wait already sees a busy account's free slots

**When it applies:** you are changing how many phases an account or the pool
can run at once (`max_concurrent_phases`, the `--parallel` cap) and are about
to edit `dispatcher/queue.py` `wait_for_account` or `account_open` to match.

**Status:** unconfirmed — proposed by T-123's arquitecto and implementador,
checked against the code by its auditor.

## Why

`queue.account_open` asks `dispatcher/dispatcher.py` `pick_idle_account`, and
that function adds `state_machine.list_shared_accounts` (busy accounts with a
free slot, `docs/decisions.md` ADR 88) to the idle list. So a run-queue cycle
waiting for an account is let through by a free slot on an account already
running a phase, with no queue code of its own.

## Rule

A slot-count change needs no `dispatcher/queue.py` edit: change the cap
(`dispatcher/cli.py` `_run_queue`, ADR 252) or the capacity
(`dispatcher.py` `_capacity_for`) and let `pick_idle_account` do the rest.

## Evidence

T-123 (2026-10-21): read `queue.account_open` and `pick_idle_account`;
`tests/dispatcher/test_parallel_queue.py`
`test_one_multi_slot_account_runs_two_cycles_at_once` runs two cycles on one
account with `max_concurrent_phases: 2` through the real `pick_idle_account`.
