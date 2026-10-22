# A queue runs an entry only from `pending`

**When it applies:** you are changing `dispatcher/queue.py` so that some entry
not in `pending` — `limit_reached`, `held`, `interrupted`, a failed state —
runs again on a resume or a later pass.

**Status:** unconfirmed — one task. Found by T-152's arquitecto reading
`_next`, `_run_serial` and `_run_parallel`; repeated by its implementador.

## What is true

`_next` picks only `pending` entries, in both the serial and the parallel
runner. Before T-152 no path ever moved a `limit_reached` (`STOPPED`) entry
back, so a plain resume of a queue its limit stopped ran nothing. T-152's
`reopen` is now the one place that does it: while the spend is below the
limit, a `limit_reached` entry goes back to `pending`, keeping its
`usage_offset` (`docs/decisions.md` ADR 336, decision 5).

## Rule

Put a new re-run rule in `reopen`, as a move to `pending`, not in `_next`; and
keep `usage_offset` on the move, since both runners now set it only when it
is `None`, so the entry's tokens keep counting from its first start.

## Evidence

`dispatcher/queue.py` `_next`, `reopen`, and the `usage_offset is None` guard
in `_run_serial` and `_run_parallel`.
