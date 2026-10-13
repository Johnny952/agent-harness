# A clean pointers gate before T-036 checked none of the task's own citations

**When it applies:** you are trusting a citation in an implementation note,
an ADR or a learning entry written before T-036 merged, on the ground that
the task's pointers gate was clean; or you are reading an old run's gate
report to decide whether a doc was checked.

**Status:** unconfirmed — counted by T-036's arquitecto, implementador and
revisor with the same throwaway test, three runs and one number. No task has
yet met a stale citation that this explains.

## Symptom

Nothing printed. The gate reported no finding, and logged this on every run:

```
gates: stopped after 400 citations, the rest went unchecked
```

## Why

Until T-036, `_broken_pointers` in `dispatcher/gates.py` listed the present
docs' citations first and the task's own last, and stopped at
`_POINTER_LIMIT = 400` unique (doc, token) pairs. At T-036's base (`32d13e9`)
the present docs gave exactly 400. The loop broke on the last present-doc
pair, so no citation from a fresh record or from a line added to a record
was ever asked about.

When the count first reached 400 was not measured. `docs/decisions.md` ADR 35
counted 151, and the debt directory grew by about 12 pairs per task since,
so the gate went blind to a task's own docs some tasks before T-036, not
only at it.

## Rule

Treat a citation in a record written before T-036 merged as unchecked: open
the path before you rely on it. A clean pointers gate says something about a
task's own docs only from T-036 on (`docs/decisions.md` ADR 61).

## Evidence

A throwaway test outside the repo, run from the T-036 worktree root with
`python3 -m pytest`, printed `unique (doc, token) pairs 400`: the gate's own
`grep`, `pointer_token` and `_candidates` over `PRESENT_DOCS`. The method is
in `docs/implementations/T-036.md`, *The count*. The four tests T-036 added
to `tests/dispatcher/test_gates.py` fail on the old loop, two of them with no
finding at all for a broken pointer in the task's own doc.
