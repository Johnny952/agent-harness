# The auditor writes every debt index row by hand, so a row habit spreads by imitation

**When it applies:** rows in `docs/debt/README.md` share a wording or format
defect and you are looking for the dispatcher code that writes them.

**Status:** unconfirmed — T-174's auditor, from the arquitecto's and the
implementador's handoffs.

## Symptom

25 rows ended their **what** cell with the entry's origin word (` Introduced`
or ` Found`), the first by hand in T-037, the rest by auditors copying it, and
the word reached every backlog title `dispatcher/debt.py` `backlog_item` built.

## Why

No dispatcher code writes an index row. `dispatcher/debt.py` only reads the
index (`index_rows`, `backlog_item`) and builds rows to hand over
(`declared_row`, `filing_note`); the auditor model appends the row with its
own Edit call, guided by `filing_note` and `dispatcher/project_docs.py`
`_AUDITOR`, and by the rows already in the table.

## Rule

Fix a row defect in the auditor's instructions (`filing_note`, `_AUDITOR`) and
absorb it in the reader (`backlog_item`); the old rows stay, since the index is
append-only (`docs/decisions.md` ADR 297, ADR 426).

## Evidence

T-174 arquitecto, `git log -S 'Introduced |' -- docs/debt/README.md`: every
such row came from an auditor commit or a hand edit; T-174 auditor,
`grep -rn DEBT_INDEX dispatcher` shows reads and prompt text only.
