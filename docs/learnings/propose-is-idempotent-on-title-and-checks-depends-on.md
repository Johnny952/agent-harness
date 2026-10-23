# `tasks.propose` is idempotent on its title and checks `depends_on`

**When it applies:** you are writing code that files a backlog item more than
once (a cycle that may re-run) and are about to invent an id field to keep it
from doubling — or you are writing a test that calls `tasks.propose` with a
`depends_on`.

**Status:** unconfirmed — proposed by T-164-S1's arquitecto and implementador
(one source), checked against `dispatcher/tasks.py` by T-164-S1's auditor.

## What happened

T-164-S1 files each revisor-ruled `follow_up` through `dispatcher/tasks.py`
`propose` and relies on its title idempotency (`docs/decisions.md` ADR 394):
`_find_titled` finds a task outside any epic with the same title, any status,
and `_propose_again` answers `created: false` when the description and
`depends_on` match and raises `BoardWriteError` when either differs. Before
anything is written, `_depends_problems` reads each `depends_on` task's file,
so a dependency with no file in the hive refuses the proposal. The dispatcher
cycle test in `tests/dispatcher/test_dispatcher.py` had to write the declaring
task's file itself for that reason.

## Rule

Reuse `propose`'s title idempotency (put the declaring task's id in the title
so two tasks do not collide) instead of a new id field, and in a test write
every `depends_on` task's file into the hive before proposing. A reworded
title on a re-run files a second item.
