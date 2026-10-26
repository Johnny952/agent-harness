# The Tokens screen's states, wording, tones and malformed rule are already decided in ADR 489

**When it applies:** you are changing `front/src/routes/tokens.tsx` — adding
its controls, a new credential row, or a new state — and are about to pick a
banner, a pill tone, a region title or what a bad row draws.

**Status:** unconfirmed — proposed by T-186-S2's arquitecto, who wrote the
ADR; no later task has built on it yet.

## Symptom

No error. ADR 456 decision 7 names the Regions and States in outline only, so
a task touching the screen can read it as open and decide the words again.

## Why

`docs/decisions.md` ADR 489 fixes, per decision, the two reads and their join
with `/api/accounts`, the broken, no-snapshot, warnings and stale banners, the
three regions (GitHub token, Deploy keys, Claude logins), each state's tone
(`accountStateTone` in `front/src/lib/format.ts`, shared with the Pool
screen), and that a row of the wrong shape draws `Malformed` in its place
while the others still draw. `front/src/routes/-tokens.test.tsx` pins one
test per state.

## Rule

Add into ADR 489's rows — a control goes in the region the ADR already draws —
and write a new ADR only for what it leaves open; the empty-region
`EmptyState` bodies are the one thing it does not fix (titles only).

## Evidence

T-186-S2: arquitecto handoff *Proposed learnings*; implementation note
`docs/implementations/T-186-S2.md`; auditor ran `bun run test` in `front/`
(38 files, 906 tests passed).
