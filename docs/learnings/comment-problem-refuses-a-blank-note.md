# `commentProblem` refuses blank text, so an optional note checks it only when non-blank

**When it applies:** you are validating an *optional* free-text field in
`front/` (a start note, a reason) with `commentProblem` from
`front/src/lib/comments.ts` to get the comment length limit.

**Status:** unconfirmed — proposed by T-105-S2's arquitecto and implementador;
checked against the function by its auditor.

## Symptom

The empty field reports a problem and the confirm button stays disabled:

```
a comment needs some text
```

## Why

`commentProblem` is the comment rule: its first line returns that message for
any text whose `trim()` is empty, before it checks the length limit.

## Rule

Write `note.trim() ? commentProblem(note) : null`, and send a blank note as
`null` — as `StartControl` in `front/src/routes/index.tsx` does.

## Evidence

`front/src/lib/comments.ts` `commentProblem`; the test "sends a blank note as
null" in `front/src/routes/-index.test.tsx`.
