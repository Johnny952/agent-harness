# A cycle test sets `resolved_debt` through the implementador's handoff

**When it applies:** you write a test that runs the full cycle (the
auto-approve `_aa_run` tests in `tests/dispatcher/test_dispatcher.py`, say)
and needs the task's `resolved_debt` to hold ids when the merge runs.

**Status:** unconfirmed — proposed by T-160-S2's implementador, checked
against the tree by T-160-S2's auditor.

## Symptom

`tasks.set_resolved_debt` called before the run has no effect: the merge
sees an empty `resolved_debt` and drops no card.

## Why

Before the auditor, the cycle calls `tasks.set_resolved_debt` with
`debt.resolved(...)` of the implementador's persisted handoff, which
replaces the list. A fake implementador with no `resolved_debt` in its
payload writes `[]` over what the test set.

## Rule

Pass the ids in the fake implementador's payload — the `implementador`
argument of `_aa_dispatch_phase`, as
`{"status": "complete", "resolved_debt": [...]}` — not as a pre-set task
file.

## Evidence

`test_auto_approve_drops_the_resolved_debt_card_and_notes_the_accepted_one`
on the T-160-S2 branch; the replacing call is in `dispatcher/dispatcher.py`
next to the comment "set_resolved_debt replaces".
