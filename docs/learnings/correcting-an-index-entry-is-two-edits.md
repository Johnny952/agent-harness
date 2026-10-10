# Correcting an entry under `docs/learnings/` or `docs/debt/` is two edits: the file and its row

**When it applies:** your change falsified a claim inside an entry under
`docs/learnings/` or `docs/debt/`, or you are writing an entry whose wording
describes a case a *later* task is scheduled to create.

**Status:** unconfirmed — reported once, by T-009's revisor.

## Symptom

No error. `the-kanban-seam-is-a-closed-surface.md` was rewritten to say a
local-only public method passes the parity test when `LOCAL_BOARD_ONLY` names
it, while `docs/learnings/README.md`'s row for that same file still read:

```
| [the-kanban-seam-is-a-closed-surface](...) | ... a public method on one client only fails a parity test, ... | confirmed |
```

## Why

These indexes exist so a phase scans the rows and opens only the entries whose
trigger matches its own work. The row is therefore read far more often than the
file, and by agents that never open the file at all — so a correction landing
only in the body leaves the wrong claim exactly where it is read. The same trap
one step earlier: an entry that names the case a later task will create ("a
Phase 1 reader method fails this test") goes stale the moment that task lands,
and reads as authoritative while wrong. Write the rule, and the condition under
which an exception is allowed, not the prediction.

## What to do

Grep the index for the sentence you just deleted before calling the fix done —
`grep -rn "<the deleted phrase>" docs/`. A `status` cell does not change on a
correction: `confirmed` stays confirmed, because the trap was reproduced even
though its wording was wrong.

Read the prose around the table too, not only the rows. Any sentence that
*counts* them — `docs/debt/README.md`'s "`card` is `none` on all three" — is
false the moment the next task files a row, and the task that files it is the
only one positioned to notice.

The same holds for prose that says *why* an entry stands or *how much* of it
is left. Marking a row resolved falsifies the index's sentence explaining why
it was left standing, and closing a step falsifies a *Fix* lead-in that counts
the steps still open: both are present-tense claims about the entry, read by
a phase that never opens the body, and neither is in a row.

## Evidence

`grep -rn "one client only" --include="*.md" .` after round 2 of T-009: one hit
in the rewritten learning, one in the untouched index row; fixed as F8 in round
3. Inbox entry `T-009-a-corrected-learning-leaves-its-index-row-behind.md`.

T-019 resolved `T-018-D1` and found two more of the same kind. Correcting the
`T-015-D1` paragraph falsified the index's closing paragraph on `T-018-D1`,
which said that paragraph was left standing because the other half of the
claim sat in an append-only ADR; and `T-013-D1`'s *Fix* lead-in said one half
of step 3 "is still open" while both halves carried a `Done` block. Proposed
by its arquitecto and implementador, and separately by its revisor;
`docs/implementations/T-019.md` *What the index now says about the row*.
