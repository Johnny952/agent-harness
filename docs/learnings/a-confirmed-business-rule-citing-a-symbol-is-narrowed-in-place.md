# A Confirmed rule in `docs/business.md` citing a dispatcher symbol is narrowed in place, and stays Confirmed

**When it applies:** your change moved what a `dispatcher/` function decides,
and a row under **Confirmed** in `docs/business.md` states the old rule and
cites that function as why it holds.

**Status:** unconfirmed — one task. T-020's revisor raised it as the one
blocking finding of round 1 (F1) and round 2 closed it; the shape of the edit
is the revisor's.

## Why it is a revision round and not a note

`docs/business.md` is in `PRESENT_DOCS` in `dispatcher/project_docs.py` — this
project's own ruling, in code, on which stale document is live prose a task
owns and which is a record left as written
([a-stale-count-in-a-dated-paragraph-is-not-a-finding](a-stale-count-in-a-dated-paragraph-is-not-a-finding.md)).
A false **Confirmed** rule is the worst case of the four that entry sorts: it
is the file a later task reads to find out what the system is supposed to do,
and its rows are the ones marked as *already checked by a human*.

This is why T-020 spent a round on one clause in `business.md` and deliberately
left `docs/plans/balancer.md`, which repeats the retired mechanism three times:
`PLANS_DIR` is `RECORD_DOCS`.

## The edit

One clause, appended to the sentence that is now too broad, naming the ADR that
narrowed it and what the old rule still governs. Nothing rewritten, no sentence
deleted, and **the row does not move out of Confirmed** — it was confirmed and
still is, just narrower. The shape to copy is the ADR 3 clause already sitting
on the card row in the same file.

T-020's instance: the quota-ceiling row said the ceiling an account answers to
is `reserve_pct` for the primary, citing
`dispatcher/dispatcher.py:_threshold_for`. ADR 48 made the primary answer to
two ceilings, so the row gained a clause saying `reserve_pct` now governs the
session and is the week's fallback, that the paced value is per probe and
served nowhere, and that reading the served row as a pair is therefore still
the best a consumer can do.

## Not the auditor's business.md duty

Different job, same file: an auditor files rules it **inferred from the code**
rather than read somewhere, under *Unconfirmed*, for a human to confirm or
kill. A Confirmed row your own diff falsified is not that — it is a finding for
the round that changed the code, and waiting for the audit leaves the false
sentence on the branch through the review.

`grep -n <the symbol> docs/business.md` is how to find out whether you have one
and how many: in T-020 it was a single line, which is what made the finding
cheap.

## Evidence

`docs/business.md`, the Confirmed bullet opening "The quota ceiling an account
is held to"; `dispatcher/project_docs.py` `PRESENT_DOCS` and `RECORD_DOCS`;
`docs/implementations/T-020.md` *Round 2 — what review round 1 changed*, F1.
The review itself is `/data/.hive/tasks/T-020/review.md` (working notes, not on
a branch).
