# A key added to the usage line is counted in five places, and one is a test comment

**When it applies:** you are adding, removing or moving a key in
`dispatcher/dispatcher.py` `_usage_record` (the usage log of
`docs/decisions.md` ADR 53), and are about to grep for the old key count to
update the prose that states it.

**Status:** unconfirmed — T-047 hit it once.

## What happens

The line's size is written as words, not derived, in five places:

- `dispatcher/dispatcher.py` `_usage_record` docstring: the total and the size
  of the dispatcher's own half ("the first ten are the dispatcher's own").
- `dispatcher/dispatcher.py` `_UsageRecorder` docstring: how many per-attempt
  facts it holds ("the six things").
- `observability/api/app.py` comment on `MAX_USAGE_CALLS`: scalars plus the
  list-valued keys.
- `tests/observability/test_api.py` `_call` docstring, and the fixture's keys
  themselves, which the api serves verbatim.
- `tests/dispatcher/test_dispatcher.py`
  `test_usage_record_of_an_empty_result_says_it_has_no_usage`: a comment on the
  dispatcher's own half ("The dispatcher's own ten are still right").

T-047 grew the line from seventeen keys to nineteen. Its plan, implementation
and review all grepped for the total ("seventeen keys", "sixteen scalars"),
which finds the first four; the fifth counts the dispatcher's *half*, so it
said "nine" until the auditor read the test. The suite cannot catch it.

## What to do

Grep for both counts — the total and the dispatcher's half — as words, in
`dispatcher/`, `observability/` and `tests/`. ADRs and `docs/implementations/`
keep the count of their own day and are not edited.

## Evidence

T-047: the base's comment "The dispatcher's own nine are still right" in
`tests/dispatcher/test_dispatcher.py`, changed to "ten" by the auditor;
`docs/decisions.md` ADR 68 for the two keys.
