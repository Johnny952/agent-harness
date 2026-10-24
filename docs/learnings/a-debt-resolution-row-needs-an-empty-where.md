# A debt resolution row needs an empty **where**, or it counts as a repeated id

**When it applies:** you append a resolution row for a debt id to
`docs/debt/README.md` and `test_this_repositorys_own_index_has_no_repeated_entry`
fails on that id.

**Status:** unconfirmed — T-172's auditor, from the implementador's round 1.

## Symptom

`tests/dispatcher/test_debt_index_merge.py`
`test_this_repositorys_own_index_has_no_repeated_entry` fails naming the id
whose resolution row was just appended.

## Why

`dispatcher/debt.py` `_is_resolution` takes a row as a resolution only when its
**what** opens with `**Resolved` and its **where** is empty. A resolution row
that keeps the entry's **where** is read as a second open row, so
`duplicate_ids` reports it. Rewriting the open row in place makes the test pass
but breaks ADR 297's rule that a row is never edited; T-172's round 1 did that
and the auditor restored the row.

## Rule

Leave the open row untouched and append `| <id> | **Resolved <date> by <task>.** |  | <fix> | none | none |`
with nothing in **where**, as the intro of `docs/debt/README.md` says.

## Evidence

T-172 implementador, `python3 -m pytest -q` in the T-172 worktree; the
auditor's appended row in that shape passes `tests/dispatcher/test_debt_index_merge.py`.
