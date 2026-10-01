# A new doc needs a row in two places; an appended ADR needs none

**When it applies:** you added a file under `docs/`, or appended an ADR, and are
working out what else has to change so a later task can find it.

**Status:** confirmed — T-011's auditor, which found one file that had been
missing its row since it was created.

## The three answers

**An ADR: nothing else.** `docs/decisions.md` has no index, no trigger table and
no table of contents — its header says append and never rewrite, and that is the
whole protocol. A new entry is one appended `## ADR n — …` section. Do not go
looking for a list to extend; there is not one, and the file's own numbering is
the index.

**An entry under `docs/learnings/` or `docs/debt/`: the file and a row in that
directory's `README.md`.** The row is what phases actually read — the tables are
loaded as triggers and the files are opened only when a trigger matches — so a
file with no row is invisible, and this is the same two-edit rule
[correcting-an-index-entry-is-two-edits](correcting-an-index-entry-is-two-edits.md)
states for a correction.

**Anything else under `docs/`: a row in `docs/README.md`'s *Docs* table.** That
table is "when to open it", one line per doc, and it is the map a role reads
before it reads anything else. `docs/plans/front.md` was created without one and
stayed uncited through two tasks that built against it; T-011's auditor added the
row. `docs/implementations/<task-id>.md` is the one exception, covered by a
wildcard row that is already there.

## Why the asymmetry

`docs/decisions.md` is read front to back by number, from a citation somewhere
else: an ADR is always arrived at by its number, so a list of numbers would add
nothing. Everything else in `docs/` is arrived at by a trigger, and a trigger
only exists if somebody wrote the row.

## Evidence

T-011's auditor: `docs/decisions.md` header and 21 entries with no index table;
`docs/learnings/README.md` and `docs/debt/README.md` headers, which both say the
table is the index; `docs/README.md` *Docs*, where the `docs/plans/front.md` row
was added, having been absent since the file was committed in
`docs(plans): front.md says where a UI decision lives, and defers the rest`.
