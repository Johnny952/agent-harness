# A debt declared only in an earlier round gets no id

**When it applies:** you are the implementador of a round after the first
and an earlier round of your task declared debt; or you are the auditor, the
revisor's handoff carries an `accepted` ruling, and your prompt hands you no
debt id.

**Status:** unconfirmed — hit once, by T-032; the auditor read the two call
sites and the saved handoffs.

## What is true

`_file_accepted_debt` in `dispatcher/dispatcher.py` assigns the ids. Both of
its callers hand it one implementador handoff, the last one:
`run_task_cycle` passes the result of the round the revisor approved, and
`run_single_phase` (`run-phase --final`) reads the saved implementador
handoff, a file each round overwrites. `debt.accepted` in
`dispatcher/debt.py` then lists that handoff's declarations and filters
them by the revisor's rulings. A ruling with no declaration behind it files
nothing.

T-032's arquitecto and its implementador's round 1 declared one debt and the
revisor accepted it in round 1. Round 2 answered two findings and returned
an empty `debt` list; the revisor's round 2 repeated the `accepted` ruling.
The auditor's prompt named no id, and the debt reached no row.

## What to do

As the implementador of a later round, declare again every debt an earlier
round declared and that still stands, in the same words. As the auditor
handed no id, do not file the entry and do not derive an id: say in the
handoff's debt list and in the implementation note's *Audit* that it was
accepted and not filed, so a person can file or drop it.

## Evidence

T-032: `_file_accepted_debt`, `run_task_cycle` and `run_single_phase` in
`dispatcher/dispatcher.py`; `accepted` in `dispatcher/debt.py`; the saved
round 2 implementador handoff, whose `debt` is empty, beside the revisor's
`accepted` ruling; `docs/debt/README.md`, the closing paragraph on T-032.
