# `_harness` writes only the config keys it is passed, and gives you one account

**When it applies:** you are pinning a config-driven value through a route, or
need a second account, in `tests/observability/test_api.py`.

**Status:** confirmed — T-011 added four kwargs and hit all three traps below in
one task.

## What the fixture does

`_harness` builds the mounts, writes a `config.yaml` and returns a test client
over it. A config key is written into that file **only when its kwarg is
passed**: `heartbeat_ttl_seconds`, `primary_account`, `quota_threshold_pct`,
`reserve_pct` and `quota_cooldown_seconds` are all absent by default. That is
deliberate and worth keeping — every pre-existing case goes on exercising
`dispatcher/config.py`'s own defaults, so a change to a default shows up here.

Three things follow.

**A new configured field needs a kwarg *and* a case off the default.** The route
is reading `cfg`, but a route that hardcoded the literal would pass every case
that takes the default, and the defaults are guessable numbers: 90, 60, 1800.
T-011's `test_the_thresholds_on_a_row_are_the_configured_ones` sets 95/70/900 for
exactly this reason. A field only ever asserted at its default is not tested —
`primary_account` absent marks nothing, so `is_primary` came back `False` on
every row of every case until one wrote the key.

**`reserve_pct` cannot be pinned alone.** `dispatcher/config.py:_load_reserve_pct`
defaults it to `min(60, quota_threshold_pct)` and raises when an explicit
`reserve_pct` exceeds `quota_threshold_pct`. Set it above 60 without raising the
threshold in the same `_harness` call and `load_config` raises inside the
fixture, where the failure reads like a fixture bug rather than the config rule
it is. T-011 pins 95/70/900: reserve under threshold, both away from their
defaults.

**`accounts` defaults to one account.** A case about which account is marked,
ordered or picked has to pass `accounts=("cuenta1", "cuenta2")` or it asserts
nothing about "the rest".

## Keep going through the dispatcher's writers

The fixtures write task files and state files through
`dispatcher/context_transfer.py` and `dispatcher/state_machine.py` rather than by
hand — the module docstring says why. Keep it: a change to one of those formats
then shows up here as a failure instead of as two files that quietly disagree.

## Evidence

T-011: `tests/observability/test_api.py:_harness`,
`test_the_thresholds_on_a_row_are_the_configured_ones`,
`test_the_primary_account_is_marked_and_the_rest_are_not`, against
`dispatcher/config.py:_load_reserve_pct` and `_load_primary_account`.
