# Naming an absent file as a backticked path in a record you add to is a broken pointer

**When it applies:** you are writing an ADR line, an implementation note or a
learning entry, and a sentence says that some file does not exist, was
deleted or was never written, and you are about to spell that file as a path
in backticks.

**Status:** unconfirmed — T-036's implementador found it by reading its own
cycle's ADR 61 against the gate it had just changed. No phase ran the gate:
`docker` is refused in one.

## Symptom

The pointers gate's own message, with the token left out here on purpose:

```
`docs/decisions.md` points at `<the absent path>` — those paths are not there. Fix the pointer or the path.
```

## Why

`pointer_token` in `dispatcher/gates.py` reads a token, not a sentence. A
backticked path is a citation whether the words around it say "see" or "does
not exist". Nothing tracks the file and nothing ignores it, so neither excuse
applies.

This was true before T-036 and never reported: the present docs filled the
limit and the task's own lines were never reached
([a-clean-pointers-gate-before-t-036-checked-no-task-citation](a-clean-pointers-gate-before-t-036-checked-no-task-citation.md)).
Since `docs/decisions.md` ADR 61 a task's own citations are checked first and
in full, so it is reported now. ADR 61's first draft did it, naming the
architecture doc that `PRESENT_DOCS` lists and this project does not have.

## Rule

Name an absent file in prose ("the architecture doc"), never as a backticked
path. A fenced block does not help if the path keeps its backticks inside it.

The same mechanism with a path that exists outside the repo is
[a-hive-path-cited-bare-is-a-broken-pointer](a-hive-path-cited-bare-is-a-broken-pointer.md).

## Evidence

`docs/implementations/T-036.md`, *The gate's first catch was its own ADR*:
the phrase was reworded in place because the ADR was that cycle's own and
unmerged. Read, not run.
