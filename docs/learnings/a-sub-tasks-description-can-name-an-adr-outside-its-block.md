# A sub-task's description can name an ADR outside its reserved block

**When it applies:** your task's description (often an epic's sub-task) says
"ADR N" for the decision you are to write, and N is not in the block your
prompt reserves for this task.

**Status:** unconfirmed. Reported by T-154-S2's arquitecto; T-154-S1 hit the
same (description said 348, block 354 to 356).

## Why

Epic splits name ADR numbers when the epic is cut, before each sub-task's
block is reserved (`_adr_block_note`, ADR 81). By the time the sub-task runs,
other tasks have taken those numbers or the block was assigned elsewhere.

## Rule

Write the ADR in the reserved block and say in its Status line which number
the description named and why it differs; carry the mismatch as a risk, not a
blocker. Every later phase cites the block number.

## Evidence

`docs/decisions.md` ADR 354 and ADR 357: each Status line records the
description's number (348, 349) and the reserved block it was moved into.
