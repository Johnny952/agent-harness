# A console verdict that acts is a new action kind, not a poll of comments

**When it applies:** you design or build something the console starts that
must run on the dispatcher's side (a merge, a push, anything needing the
Docker socket or a git credential), and are choosing how the dispatcher learns
of it.

**Status:** unconfirmed — proposed by T-121's arquitecto and implementador;
checked against `dispatcher/actions.py` and `dispatcher/worker.py` by its
auditor.

## Why

The board service holds no socket and no credential (C-12, ADR 124, ADR 166).
Its one channel to the side that does is the action queue (ADR 142): a route
writes `actions.create`, and the worker drains it through `worker.HANDLERS`,
keyed by `actions.KINDS`. A record in `comments.jsonl` (a verdict line, ADR
222) is the review's record and is read by nothing that decides.

## Rule

Add an `actions.KINDS` entry (serial key, `spends_quota`, recovery) and a
`worker.HANDLERS` handler; never have the dispatcher poll `comments.jsonl` or
listen for a signal from the board service. ADR 258 is the worked example
(the `approve` kind).

## Evidence

`dispatcher/actions.py` `KINDS` and `_ONE_PER_TASK`; `dispatcher/worker.py`
`HANDLERS`; `docs/decisions.md` ADR 258, decisions 1 to 3 and *Ruled out*.
