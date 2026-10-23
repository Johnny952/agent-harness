# A proposal's `source` is a kind, not a person

**When it applies:** you are about to record who did something on a task and
reach for its `source` field — to write a person's name there, or to read a
person out of it.

**Status:** unconfirmed — proposed by T-162's arquitecto and implementador
(one source), checked against the tree by T-162's auditor.

## What happened

T-150-D1 asked for `_person()` as a proposal's `source`. `source` is a closed
enum, `dispatcher/tasks.py` `SOURCES` (`operator`, `arquitecto`, `debt`,
and since T-164-S1 `follow_up`): `propose` refuses any other value,
`field_problems` flags it, and the console types and filters it as
`TaskSource`. It names the kind of proposer, so a console proposal still
records `operator` (`docs/decisions.md` ADR 387).

The board service sets `source` to the caller's role (`board_service/app.py`,
the `propose` tool's handler), so a value added to `SOURCES` for the
dispatcher's own filing (`follow_up`, ADR 393) cannot be claimed by an agent
through the board (T-164-S1, checked by its auditor).

## Rule

Never put a person's name in `source`; who proposed a task needs a field of
its own (debt `T-162-D1`). A new `SOURCES` value is not a new caller right.
