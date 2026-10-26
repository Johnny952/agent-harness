# A re-spied console.error reads only the last spy

**When it applies:** a vitest test calls `vi.restoreAllMocks()` and then
`vi.spyOn(console, "error")` again partway through, and ends by asserting on
`vi.mocked(console.error).mock.calls`.

**Status:** unconfirmed — found by T-186-S1's auditor.

## Symptom

The assertion passes whatever was logged. Adding
`expect(logs.length).toBeGreaterThan(0)` before it shows:

```
AssertionError: expected 0 to be greater than 0
```

## Why

Each `spyOn` makes a fresh mock with an empty `mock.calls`, and
`vi.mocked(console.error)` is whichever spy is installed now. In
`forward.test.ts` "never echoes a token or a code in an answer or a log" the
loop re-spies after its last forward, so the "no SECRET in the logs" check
reads a spy that saw no call.

## Rule

Collect calls into one array across spies (or spy once, and stub `fetch` with
`mockRejectedValueOnce` instead of restoring), and assert the call count is
non-zero before asserting what the calls do not contain.

## Evidence

`cd front && bun run test` in the T-186-S1 worktree at the auditor phase, with
the count assertion added to that test and then removed.
