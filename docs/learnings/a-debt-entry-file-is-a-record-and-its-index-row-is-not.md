# A debt entry file is a record and its index row is not

**When it applies:** you are adding lines to a file under `docs/debt/` (a
*How it was resolved* section, a widened fix), resolving a debt entry, or
taking a clean pointers gate as proof that the citations in an existing
entry file still resolve.

**Status:** unconfirmed — T-038's arquitecto, implementador and revisor all
proposed it in the same words, which is one source; the auditor checked it
against `PRESENT_DOCS`, `RECORD_DOCS` and `is_record` in
`dispatcher/project_docs.py` and against the two T-038 tests in
`tests/dispatcher/test_gates.py`.

## Symptom

None: the gate passes a broken path in an entry file's old lines, and reports
one in a line your task added to the same file.

## Why

Since `docs/decisions.md` ADR 62, `PRESENT_DOCS` names `DEBT_INDEX` and
`RECORD_DOCS` names `DEBT_DIR`. `is_record` lets `PRESENT_DOCS` win where the
two overlap, so `docs/debt/README.md` is read whole on every task and every
other file under `docs/debt/`, open or resolved, is a record: the gate reads
only the lines a task added to it, checked as the task's own and uncapped
(ADR 61). The class is the path's alone. Nothing reads whether an entry is
resolved, so a row and a file that disagree about it change nothing.

An entry file the auditor files is written after the gate runs and is not
read at all, the same as a learning.

## Rule

A line you add to a debt entry file is checked as your own; an old line is
not. When you resolve an entry, open its file and `ls` the paths it cites
yourself: the row's `Detail:` link and the row's own cells are what the gate
still checks.

## Evidence

`tests/dispatcher/test_gates.py`
`test_the_debt_index_is_read_whole_and_the_entry_files_are_not` and
`test_a_citation_a_task_added_to_a_debt_entry_file_is_still_checked`, both
failing on T-038's base and passing on its branch; the count before and after
is in `docs/implementations/T-038.md` *The count*.
