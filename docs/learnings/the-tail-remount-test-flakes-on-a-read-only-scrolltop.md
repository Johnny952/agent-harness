# The tail screen's remount test can fail the `front/` gate on a tree that did not touch `front/`

**When it applies:** the dispatcher's `cd front && bun run test` gate fails
on `front/src/routes/-tail.test.tsx`, "renders the window from the top of the
remounted scroller", and your task did not touch `front/`.

**Status:** unconfirmed — reported once, by T-031's implementador, from the
shared inbox. The failure was seen once and the same tree then passed twice;
the race below is the suspected cause and was not proved. T-031's auditor did
not re-run the `front/` suite.

## Symptom

```
× renders the window from the top of the remounted scroller 1083ms
Cannot assign to read only property 'scrollTop' of object '[object HTMLDivElement]'
❯ src/routes/-tail.test.tsx  expect(await screen.findByText("resume")).toBeTruthy();
Test Files  1 failed | 8 passed (9)
     Tests  1 failed | 89 passed (90)
```

## Why

The test stubs the scroller's `scrollTop` with `Object.defineProperty(scroller,
"scrollTop", { configurable: true, value: … })` and no `writable: true`, so
the property is read-only. The stick-to-bottom effect in
`front/src/routes/tail.tsx`, on `[filtered.length, paused]`, does
`el.scrollTop = el.scrollHeight` whenever the screen is not paused. If that
effect runs between the stub and the fired `scroll` that pauses the screen —
a poll landing in the gap — the assignment throws and the route's error
boundary renders. A timing race, not a regression: T-031 changed no file
under `front/`.

## Rule

Re-run `cd front && bun run test` once before debugging; if it passes and
`git diff --stat <base> HEAD -- front` is empty, report it as a flake under
`risks` rather than editing `front/`.

## Evidence

T-031's first gate run failed 1 of 90; `cd front && bun run test` twice in
the same worktree at the same commit then printed `Test Files 9 passed (9)`,
`Tests 90 passed (90)`. The likely repair, not made because `front/` was
outside T-031's files: add `writable: true` to the `scrollTop` stub in
`front/src/routes/-tail.test.tsx`.
