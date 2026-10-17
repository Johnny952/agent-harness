# The picker's race tests patch `list_idle_accounts` by name

**When it applies:** you are changing how `pick_idle_account` in
`dispatcher/dispatcher.py` finds its candidates — replacing, renaming or
skipping its call to `state_machine.list_idle_accounts`.

**Status:** unconfirmed — proposed by T-058's arquitecto, implementador and
revisor (one source: the arquitecto's grep), checked against the test helper
by T-058's auditor.

## The trap

`tests/dispatcher/test_account_claim.py` `_barrier_after_first_pick` forces two
dispatchers to race for one account by monkeypatching
`dispatcher_mod.state_machine.list_idle_accounts` with a wrapper that waits on
a barrier. A picker that stops calling that function (one that reads its
candidates through a new helper instead) still passes those tests, but the
barrier never fires, so the race they exist to prove is no longer forced.

## What T-058 did

The picker keeps calling `list_idle_accounts` and adds the accounts with a
free slot from a separate helper, `state_machine.list_shared_accounts`
(`docs/decisions.md` ADR 88).

## Rule

Keep `list_idle_accounts` on the picker's path, or move the barrier in
`_barrier_after_first_pick` to whatever replaces it in the same commit.
