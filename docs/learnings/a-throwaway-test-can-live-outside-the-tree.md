# A throwaway test can live outside the tree and still import the project

**When it applies:** you want to measure or observe something with a
throwaway test
([the-pytest-suite-is-the-whole-gate](the-pytest-suite-is-the-whole-gate.md))
and are about to put the file under `tests/`, where you then have to remove
it before the phase ends.

**Status:** unconfirmed — three phases of one task ran it (T-036's
arquitecto, implementador and revisor). It is about what a phase is allowed
to run, which a harness change can move.

## Symptom

None: the command is allowed and runs.

```
python3 -m pytest /data/.hive/tasks/T-036/test_count.py -s -q
```

## Why

`python3 -m pytest` is the granted command, and the grant does not ask where
the test file is. Run from the worktree root, the working directory is on the
import path, so a test file anywhere can `import dispatcher`. `-s` lets its
`print` reach the output.

## Rule

Keep a throwaway measurement in the task's own directory under the hive
(`<hive_tasks_dir>/<task_id>/`), run it by absolute path from the worktree
root with `-s -q`, and leave the tree clean. Cite its method in the
implementation note; the file itself is not read once the task is done.

## Evidence

T-036's count of the present docs' pointer pairs: the command above, in the
T-036 worktree, printed `unique (doc, token) pairs 400` and `1 passed` in
each of the three phases (`docs/implementations/T-036.md`, *The count*).
