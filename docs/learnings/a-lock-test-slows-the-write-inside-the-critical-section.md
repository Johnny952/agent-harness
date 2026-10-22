# A lock test slows the write inside the critical section

**When it applies:** you write a test that two threads or processes racing
over shared files end with each item claimed once, to prove a lock you added.

**Status:** unconfirmed. Proposed by T-136's implementador.

## Why

Without a delay the first thread often finishes before the second starts,
so the test passes with no lock at all. Starting both on a
`threading.Barrier` and monkeypatching the write inside the critical
section to sleep briefly makes the bad interleaving certain.

## Rule

Barrier the starts, slow the write under the lock, and check once that the
test fails with the lock swapped for `contextlib.nullcontext`.

## Evidence

`tests/dispatcher/test_learnings.py`
`test_two_concurrent_carries_claim_each_entry_once`: T-136's implementador
reports twelve claims for six entries with the lock removed, and it passes
with it.
