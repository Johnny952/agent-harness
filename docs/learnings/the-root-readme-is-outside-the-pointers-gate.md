# The root README is outside the pointers gate

**When it applies:** you are changing the root `README.md` and your done-when
says every pointer in it resolves, or you are about to take a clean pointers
gate as proof that a path or heading the root `README.md` cites exists.

**Status:** unconfirmed — T-037's arquitecto and implementador both said it,
which is one source; the auditor checked it against
`dispatcher/gates.py` `_broken_pointers` and `dispatcher/project_docs.py`
`PRESENT_DOCS`.

## Symptom

None: the gate passes whatever the root `README.md` cites.

## Why

`_broken_pointers` greps the docs `PRESENT_DOCS` names plus the records the
task added to. `PRESENT_DOCS` names `docs/README.md` (`INDEX`), the charter,
the architecture doc, the business doc, the learnings index and the debt
directory (the debt index alone since `docs/decisions.md` ADR 62, T-038);
`is_record` only answers true for paths under `docs/`. The root
`README.md` is neither, so no citation in it is ever checked.

## Rule

Check a root `README.md` pointer by hand: `ls` every path it cites and grep
each quoted heading, ADR number and symbol in the file it names, and say in
the handoff that you did.

## Evidence

T-037 rewrote the root `README.md` *Future work* to a status list of
pointers; the implementador checked each one with `ls` and `grep`
(`docs/implementations/T-037.md`, *Verified after the change*).
