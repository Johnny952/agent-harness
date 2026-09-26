# One durability story: copy `state_machine.py`'s atomic write, fsync included (it has none)

**When it applies:** you are adding code that writes a JSON document some other
process reads — a card, a state file, an index — anywhere under `dispatcher/`.

**Status:** confirmed — T-008's `LocalBoardClient._write_card` was built against
it and the revisor checked the two side by side.

## The pattern

`dispatcher/state_machine.py:_write_state` writes a temp file in the *target
directory* and `os.replace`s it over the destination: same filesystem, so the
rename is atomic and a reader never sees half a document. There is no
`fsync` — a crash mid-write can lose the last change, and the project accepts
that.

Copy it exactly, including the absence of the fsync. Matching it is what keeps
one durability story for the whole repo instead of two, and a reader who has
read one writer knows what every other one guarantees. `dispatcher/learnings.py:_write`
is the third copy; it adds an explicit `os.chmod` because the umask differs
between the dispatcher and the agent containers.

## The part that is easy to miss

Temp files must be invisible to your own reader. `LocalBoardClient._cards`
requires a `.json` suffix and skips dotted names, which is what keeps a leaked
`.card-*.tmp` out of `list_issues` — if you name temp files something your
listing matches, a crash leaves a phantom row.

## Evidence

T-008: `dispatcher/vibe_kanban_client.py:LocalBoardClient._write_card` against
`dispatcher/state_machine.py:_write_state`; the leaked-temp check is in the
revisor's round-1 notes and in
`tests/dispatcher/test_vibe_kanban_client.py`.
