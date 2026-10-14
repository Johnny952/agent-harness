# A usage slot is read while the calls disclosure is closed

**When it applies:** you are writing a test in
`front/src/routes/-tasks.test.tsx` that asserts what one labelled slot of
the task detail's usage region prints (`model`, `models used`, a figure),
and `getByText(label)` would match the same label on every disclosed line
too.

**Status:** unconfirmed — proposed by T-049's arquitecto and implementador
(one source, the plan the second followed) and checked against the passing
suite by its auditor.

## Symptom

None printed. A test that opens the `calls (N)` disclosure first and then
calls `getByText("model")` throws on multiple matches, because the label now
sits in the *first call* block and on each line.

## Why

`Collapsible` draws nothing while closed, so before the click only the
*first call* block (`FirstCall`, fed `phase_call`) is in the DOM and each
label appears once. The T-049 test counts prove it: a name seen twice before
the click is seen four times after it.

## Rule

Read a single slot as `getByText(label, { exact: true }).parentElement`'s
`textContent` before opening the disclosure; open it only to assert on the
lines, and count with `getAllByText` there.

## Evidence

`describe("the usage region's models")` and its `slot` helper in
`front/src/routes/-tasks.test.tsx`; `bun run test` in `front/`, 116 passed,
on the T-049 branch.
