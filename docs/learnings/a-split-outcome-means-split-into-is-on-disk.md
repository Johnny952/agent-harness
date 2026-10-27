# A `SPLIT` outcome means the parent's `split_into` is already on disk

**When it applies:** you write code that reacts to a cycle ending
`CycleOutcome.SPLIT` and needs the sub-task ids.

**Status:** unconfirmed — proposed by T-188's arquitecto and implementador;
checked by its auditor against `dispatcher/dispatcher.py` `_file_split`.

## Symptom

No error. Code that waits for, or re-derives, the sub-task ids after a split
does work the task file already holds.

## Why

`dispatcher/dispatcher.py` `_file_split` returns `CycleOutcome.SPLIT` only
after `tasks.file_split` wrote the sub-tasks and the parent's `split_into`;
any problem returns `BLOCKED` with nothing written.

## Rule

Read the sub-task ids from the task file's `split_into` right after the cycle
settles, as `dispatcher/queue.py` `_absorb_split` does (ADR 495); treat a
missing or empty list as unreadable, not as "not yet written".

## Evidence

T-188: read `_file_split` (the `SPLIT` return follows `tasks.file_split`).
