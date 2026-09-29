# A spec written before a toolchain ruling can name a state the ruled toolchain cannot enter

**When it applies:** you are building a section of `docs/plans/*.md` that was
written before an entry in `docs/charter.md` ruled the toolchain it would be
built on, and the spec fixes a list "verbatim".

**Status:** unconfirmed — reported once, by T-010's arquitecto, on the Loading
state in `docs/plans/board.md` "Phase 2 — a read-only board".

## Symptom

No error. `docs/plans/board.md` fixes four states per region — Loading, Empty,
Error, and a partial — and calls them verbatim. `docs/charter.md` C-7, written
later by a human, rules the board Flask and Jinja rendered on the server. A
Jinja template renders after every call the request made has returned or
failed, so there is no instant at which a region has an unresolved fetch and a
reader to see it. The Loading state is unreachable, and a phase that tries to
satisfy the verbatim list ships a skeleton nothing ever displays.

## Why

The two documents were written for different toolchains and neither is wrong
about its own. The spec's four states are a client-side fetch's four states; the
charter replaced the client-side fetch. A "verbatim" list is a contract about
*wording*, and it silently carries the architecture the wording assumed.

## What to do

The charter wins — it is a human's ruling and no role re-decides it (C-7 says
so in as many words). Do not edit the spec section to match: rewriting it
deletes the reasoning a reversal of the ruling would need. Write an ADR that
names the state, says which ruling makes it unreachable, and says what stands in
its place; `docs/decisions.md` ADR 6 is that ADR for the Loading state. Then
touch only the plan's **Status** paragraph, which is the part a later reader
uses to decide whether the phase exists — leaving it stale is the defect
`docs/debt/T-008-D3.md` already records.

## Evidence

T-010: `docs/decisions.md` ADR 6, `docs/charter.md` C-7, and
`docs/implementations/T-010.md` "What was ruled out" → "A Loading state and a
skeleton". The plan's Phase 2 section is unedited apart from its Status
paragraph.

2026-09-28, C-8: the ruling was reversed the day after T-010 shipped, and the
advice paid. The unedited Phase 2 section is what the front work is now being
planned from, and ADR 6 was there to be read instead of the missing Loading row
being re-litigated as a gap; `docs/decisions.md` ADR 14 records where that row
went. The trap itself has not recurred, so the status is unchanged — this is
the advice being vindicated, not the symptom being reproduced a second time.
