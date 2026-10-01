# A new field on an api row: the value is already on `cfg`, and there is no slot beside `data`

**When it applies:** you are adding a field to a route in
`observability/api/app.py` — a configured value, or a fact about the pool or the
collection rather than about one row.

**Status:** confirmed — T-011 added six fields across two routes and hit both
halves; the envelope half is ADR 5 as widened by ADR 11 and predates it.

## The value: it is already on `cfg`

`create_app` takes a `config_path` and holds one `dispatcher.config.Config` as
`cfg` for the life of the app. Every key an operator writes in `config.yaml` is
already on it, so a configured field needs no new module, no new config key and
no new data source — `account.is_primary`, `cfg.quota_threshold_pct`,
`cfg.reserve_pct` and `cfg.quota_cooldown_seconds` were all one attribute away.

Never call `load_config` a second time inside a view. `_task`'s docstring argues
it for the expiry window and the argument is general: a second load is a second
answer to the same question, and the two can disagree mid-request.

## The shape: the envelope has exactly two keys

The response is `{"data", "warnings"}` and two parametrized tests assert it for
every route — `tests/observability/test_api.py:test_every_route_answers_the_same_envelope`
and its bearer twin `test_every_route_accepts_the_configured_bearer_token`, both
`set(resp.get_json()) == {"data", "warnings"}`. A third top-level key is not a
slot this envelope has.

Nor can `data` become an object with the list inside it.
`observability/board/app.py:_envelope_problem` checks `isinstance(rows, list)`
before it looks at any row, and `index` and `task` both fetch `/api/accounts`
with `many` defaulting to true — an object there blanks the pool table on two of
the four board screens, and `docs/charter.md` C-8 keeps that board the
tie-breaking reference until the console serves parity.

What rows *may* do is grow: the same function checks each row with
`missing = [key for key in keys if key not in row]`, a subset check, so a new key
breaks no board consumer and no board template, which render by name.

So a fact about the pool rather than about one account becomes a column repeated
on every row, or it does not get served at all. That is `docs/decisions.md`
ADR 20, which took the first option for three thresholds and deliberately did
not open the door for a fact that is genuinely not per-row — a count, a queue
depth. That one still needs an ADR to find it a home.

## Where in the row

A configured value goes **before** the `try` that reads the per-row file, with
`name` and `container`. The guard exists for the state file: a state file that
will not parse nulls what the state file says and must not null what the config
says, because an account the operator named primary does not stop being primary
because the record of what it is doing is truncated.
`tests/observability/test_api.py:test_an_unreadable_state_file_keeps_the_config_half_of_the_row`
pins that. A value read out of the file goes inside, together with every line
that *uses* it — `docs/learnings/a-never-500-read-wraps-the-use-not-the-parse.md`.

## Evidence

T-011: `observability/api/app.py`, the `accounts` view and `_task`;
`docs/decisions.md` ADR 20 and ADR 21; `docs/implementations/T-011.md` *Why
outside the `try`, and why inside the other one*. The sibling shape question —
which route a field belongs on at all — is ADR 21 and
[board-md-the-shapes-is-not-the-current-key-list](board-md-the-shapes-is-not-the-current-key-list.md)
for why the plan's key list will not tell you.
