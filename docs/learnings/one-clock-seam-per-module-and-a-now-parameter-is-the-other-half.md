# One clock seam per module, and a `now` parameter beside one is dead weight

**When it applies:** you are adding something under `dispatcher/` that depends
on the current time, and choosing between a module-level clock a test can
replace and a `now` parameter the caller passes.

**Status:** unconfirmed — one task. Settled inside T-020: its revisor's F3
dropped a `now` parameter the implementador had put beside a seam, and the
round-2 handoff and the revisor's own both named the rule.

## The two shapes, and why a module picks one

A module gets a **seam** or it gets **parameters**, never both.

- `dispatcher/quota.py` reads no clock at all. `parse_reset(clause, now)` and
  `week_ceiling(week_reset, now, reserve_pct)` take the moment as a required
  argument, so the whole ramp is pure and no quota test touches a clock.
- `dispatcher/dispatcher.py` has `_utc_now()`, a one-line function whose only
  reason to exist is that a test can monkeypatch it. Every dispatcher test that
  cares about a paced ceiling patches it.

The failure is mixing them. `_quota_decision` first shipped as
`(cfg, account, usage, now=None)` with `now or _utc_now()` inside, which no
caller ever passed — `grep -rn _quota_decision tests/` returns nothing. Dead,
but not harmless: the gate (`check_quota_ok`) and the recheck
(`_recheck_cooling_accounts`) have to agree about the ceiling or they loop
(`_threshold_for`'s docstring), and a second injection point is exactly a way
for two readers of the same rule to be handed different days.

## What to do

Inject once per module, at the level the tests already patch, and let the pure
functions below it take `now` as a **required** parameter — required, so a
caller cannot silently fall back to a clock the test did not replace. If you
want a `now` parameter on a function in a module that already has a seam, the
question to answer first is which of the two the module is.

The one exception in the tree, and its reason:
`dispatcher/operator.py:format_status(cfg, accounts, tasks, now=None)`. That
module has no seam, and a listing has to take the clock **once** so every row
on the screen is measured from the same moment; the default is for the callers
that print a listing with no paced row in it.

## Evidence

`dispatcher/dispatcher.py` `_utc_now` and `_quota_decision`'s docstring, which
says why the clock is not a parameter there;
`dispatcher/quota.py:parse_reset`; `dispatcher/operator.py:format_status`.
T-020 round 1 added the parameter, the revisor's F3 asked for it back, round 2
dropped it: `/data/.hive/tasks/T-020/review.md` (working notes, not on a
branch) and `docs/implementations/T-020.md` *Round 2 — what review round 1
changed*.
