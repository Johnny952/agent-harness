# A fake `Popen` must not have attributes the real one lacks

**When it applies:** you write or fake a `subprocess.Popen` in `dispatcher/`
(for example `docker_exec.open_docker_exec` and the `FakeProc` in
`tests/dispatcher/test_worker.py`), and the code reads `proc.stdout` or
`proc.stderr`.

**Status:** unconfirmed. T-110's auditor found it once.

## Symptom

There is no test failure. On a real exec, a turn with no `result` event
ends `failed` with this error:

```
AttributeError: 'NoneType' object has no attribute 'seek'
```

claude's stderr does not appear in the error.

## Why

`Popen` sets `.stdout`/`.stderr` only for a stream given as
`subprocess.PIPE`. When it is given a file object, such as
`tempfile.TemporaryFile()`, the attribute is `None`. The caller has to keep
its own reference to the file. T-110's `FakeProc` set
`stderr = io.StringIO(...)`, so the suite exercised a path that production
cannot reach.

## Rule

Keep your own handle on any file you pass as `stdout`/`stderr`. Give a fake
proc only the attributes the real call leaves non-`None`.

## Evidence

T-110 auditor, 2026-10-19: a throwaway pytest outside the tree,
`Popen(..., stderr=tempfile.TemporaryFile(mode="w+"))`, asserted
`proc.stderr is None`, and it passed. The code that reads it is
`dispatcher/worker.py` `_turn` (`proc.stderr.seek(0)`).
