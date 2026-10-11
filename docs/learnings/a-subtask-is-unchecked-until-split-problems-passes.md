# A `SubTask` is unchecked until `split_problems` returns empty — `after`'s type is an annotation

**When it applies:** you are writing a caller of `dispatcher/handoff.py`'s
`split_of` — steps 2 to 4 of `docs/plans/task-split.md` section 8 — and are
about to index, map or sort by a `SubTask`'s `after`, or to quote a position.

**Status:** unconfirmed — raised by T-025's revisor as risks against the code
that task built; no caller exists yet, so nothing has tripped on it.

## Symptom

No error today. What a caller would see if it used `after` before validating:
`SubTask.after` is annotated `tuple[int, ...]` and can hold `"1"`, `True`,
`1.0`, `None` or a dict.

## Why

`split_of` reads and never repairs (`docs/implementations/T-025.md`,
*Decisions the design leaves open*, 1 to 5). On a payload the CLI validated
against the schema every `after` item is an integer. On one that came back
through the prose fallback it is whatever was there, kept on purpose: a
dropped `after` is a sub-task started before the work it reads, so the junk is
carried to `split_problems`, which refuses it. Three consequences follow.

- **`after` is trustworthy only after `split_problems(subtasks) == []`.** That
  function is the validation; `split_of` is only the read.
- **A position is a position in the list `split_of` returned**, not in the
  payload's `split` array: an entry that is not an object is skipped and
  shifts the ones after it. `render` and `split_problems` agree because both
  go through `split_of`; a caller that walks `payload["split"]` itself does
  not.
- **`split_of` raises `AttributeError` on a truthy payload that is not a
  dict**, exactly as `_lines` does. Callers pass a dict or `None` today.

Two smaller things in the same code: a junk `after` item stated twice is
reported twice as not an integer, and `_split`'s lines are not clamped to
`_LINE_BYTES`, so a long title lands whole in the parent's task file.

## Rule

Call `split_problems(split_of(payload))` first and touch `after` only when it
returned an empty list; take positions from `split_of`'s list and never from
the raw payload.

## Evidence

`dispatcher/handoff.py`: `SubTask`'s docstring, `split_of`, `split_problems`,
`_split`. `tests/dispatcher/test_handoff.py`: the tests on what `split_of`
keeps for `split_problems` and on an `after` that is not an integer. T-025's
revisor, round 1, *Risks*.
