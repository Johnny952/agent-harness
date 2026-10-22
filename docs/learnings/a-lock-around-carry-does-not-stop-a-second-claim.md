# A lock around `carry` does not stop a second claim

**When it applies:** you change how `dispatcher/learnings.py` `carry` picks
the entries it stamps, change which task-card states count as a live
carrier, or wonder why a cycle's auditor was handed none of the inbox's
open entries.

**Status:** unconfirmed. Proposed by T-136's arquitecto and implementador
(one source).

## Why

Before T-136, `carry` overwrote any `carried_by` naming another task, on
purpose, so a dead carrier's entries pass to the next task. Serialising two
carries therefore only ordered them: the second still took the first's
entries and its auditor filed them again. ADR 294 adds the other half:
an entry stays with its carrier while that task's card has an `owner`, or
is `awaiting_review` or `conflict` (`_carrier_is_live`,
`_LIVE_CARRIER_STATUSES`). This holds for serial cycles too.

## Rule

Keep both halves: the `_carry_lock` and the live-carrier check. A carrier
with no owner in any other state is dead and loses its entries.

## Evidence

`tests/dispatcher/test_learnings.py`
`test_carry_leaves_a_live_carriers_entries` and
`test_carry_takes_over_from_a_carrier_with_no_owner`, green in
`python3 -m pytest -q` at T-136's audit.
