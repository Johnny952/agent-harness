# The reaper tests age an account through its top-level `busy_since`

**When it applies:** you are changing the shape of an account's state file in
`dispatcher/state_machine.py` — moving, renaming or nesting `busy_since` or
`current_task_id` — or adding a reader of the BUSY stamp.

**Status:** unconfirmed — proposed by T-057's arquitecto and implementador,
checked against the test helper by T-057's auditor.

## The trap

`tests/dispatcher/test_dispatcher.py` `_age_busy_since` backdates a phase by
opening the account's JSON file and rewriting the **top-level** `busy_since`
key, because `set_state` and `claim` always stamp `time.time()`. The reaper
tests that call it sit outside any state-machine surface, so a shape change
that moves the stamp (into a holders list, say) leaves those tests reading a
key nothing honours, and they fail in a file your diff never touched.

## What T-057 did

ADR 85 point 3: the state file keeps a list of holders, each with its own
`busy_since`, and still writes a top-level `current_task_id` / `busy_since`
pair mirroring the first holder. On read, that top-level pair **wins** over
`holders[0]` (`state_machine.py` `_holders_of`), so an edit to the top-level
key ages the first holder. The cost is the other direction: a hand edit to
`holders[0]` alone is ignored.

## Rule

Keep the top-level `busy_since` meaningful for the first holder, or change
`_age_busy_since` in the same commit and budget the reaper tests' file into
your surface.
