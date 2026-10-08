# A stale number in a dated measurement paragraph is weaker grounds for a revision round than one in live prose

**When it applies:** your change falsifies a number another doc states — a
warning count, a timing, a tally, a file count — and you are deciding whether
that doc is a blocking finding, a debt row, a one-clause amendment, or nothing
at all.

**Status:** unconfirmed — one task. T-015 took `bun run lint` from ten warnings
to nine and found the old count in five places falling into four classes, each
class wanting a different answer.

## Symptom

No error. A grep for your own change's number comes back with more hits than you
expected, in docs your task never opened:

```
docs/README.md        *Stack*                  ... ten warnings it does not fail on
docs/decisions.md     ADR 39                   ... now exits zero with the ten warnings
docs/ROADMAP.md       a "Done 2026-10-08" item ... 124 prettier errors and 10 warnings
docs/debt/T-013-D1.md *How it was resolved*    ... now exits zero on `main` with ten warnings
docs/debt/README.md   the T-013-D1 row         ... so it exits zero with ten warnings
```

## Why

The hits are not the same kind of claim, and only one class is load-bearing. Ask
what the sentence is *for*:

- **Live prose that describes the present** — `docs/README.md` *Stack* here.
  This is the one a reader consults to find out what is true today, and
  `dispatcher/project_docs.py:PRESENT_DOCS` names it for exactly that reason. It
  is the task's own to change, in the same diff as the code.
- **A dated record of a measurement** — `docs/ROADMAP.md`'s "Done 2026-10-08"
  bullet, and the timing paragraph in
  [`docs/debt/T-013-D1.md`](../debt/T-013-D1.md) that reads "the lint 3.2 s (red:
  124 prettier errors, 10 warnings)". Historical by construction: the number was
  true when measured and the sentence says when that was. Leave it. Rewriting it
  destroys the record and gains nothing.
- **An ADR on `main`** — ADR 39's tally. Never edited; a later entry narrows it,
  which is what ADR 40 does. See
  [an-adr-your-own-cycle-appended-is-not-yet-append-only](an-adr-your-own-cycle-appended-is-not-yet-append-only.md)
  for the one case where editing in place *is* right.
- **A present-tense claim inside a record** — `docs/debt/T-013-D1.md` *How it
  was resolved*'s closing "`eslint .` **now** exits zero on `main` with ten
  warnings". The awkward class: it sits among the dated measurements, so it reads
  as history, but the word *now* makes it a claim about today. Its load-bearing
  half — no task starts with a `lint` note — stays true, which is why T-015's
  revisor weighed it and did **not** raise it as a finding.

## What to do

Spend a revision round only on the first class. For a present-tense claim inside
a record, append one clause naming the task and the ADR that narrowed it — no
decision in the clause, no rewrite of the paragraph — and do it where the auditor
is already writing, since it is one line in a directory that phase owns. Two
edits, not one: the entry file *and* its row in the directory's `README.md`,
because the row is what later phases actually read
([correcting-an-index-entry-is-two-edits](correcting-an-index-entry-is-two-edits.md)).

Do not file it as debt instead. T-015's revisor proposed exactly that and its
auditor ruled the other way: a one-clause amendment in a file that phase was
opening anyway is cheaper to make than to track, and a debt row would have
outlived the problem.

## Evidence

T-015. The hits above are the grep its auditor ran over `docs/`. The
revisor's reasoning for not blocking is in `/data/.hive/tasks/T-015/review.md`
*Weighed and deliberately not raised as findings*; the amendment it led to is
the T-015 clause in `docs/debt/T-013-D1.md` *How it was resolved* and in that
entry's row in [`docs/debt/README.md`](../debt/README.md). The narrowing a
reader lands on is [`docs/decisions.md`](../decisions.md) **ADR 40**.
