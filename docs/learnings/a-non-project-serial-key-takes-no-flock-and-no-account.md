# A non-`project:` serial key takes no project flock, and a no-quota kind waits on no account

**When it applies:** you add an action kind to `actions.KINDS` whose serial key
is not `project:<…>` (such as `approve`'s `base:<project>`), or whose
`spends_quota` is False, and are about to edit `worker.Worker._consider` to
keep it off the project flock or the account check.

**Status:** unconfirmed. T-125-S1's architect and implementer both reported it,
checked against the code by its auditor; still one task.

## Why

`worker.Worker._consider` takes `ProjectLock` only when the key starts with
`project:`, and runs the pool-wide open-account check only for a kind other
than `turn` whose `Kind.spends_quota` is set. A busy key that is not `turn`'s
waits as `project` (see
[a-busy-serial-keys-waiting-reason-is-chosen-by-kind](a-busy-serial-keys-waiting-reason-is-chosen-by-kind.md),
whose "skips the account check only for `turn`" now also has the
`spends_quota` condition). So `approve` (ADR 258, ADR 264) needed no change to
`_consider` and no new `actions.WAITING` value.

## Rule

Set the key's prefix and `spends_quota` in the `Kind` entry; `_consider` already
reads both, so leave it alone unless the kind needs a new waiting reason.

## Evidence

`dispatcher/worker.py` `Worker._consider` and `dispatcher/actions.py` `KINDS`
(`approve`) at the end of T-125-S1; `tests/dispatcher/test_worker.py` approve
tests assert no account and no project lock is taken.
