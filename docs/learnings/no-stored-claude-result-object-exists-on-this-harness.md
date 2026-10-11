# Nothing on this harness has ever stored a `claude` result object — don't go looking, read `raw_keys` instead

**When it applies:** you need to know which keys the `claude` CLI's
`--output-format json` object actually carries — `usage`, `num_turns`,
`total_cost_usd`, `permission_denials`, `stop_reason` — and are about to go
looking for a stored one, or to assume a documented key exists.

**Status:** unconfirmed — established by T-024's arquitecto and implementador,
each of which searched independently, and confirmed by its auditor.

## Symptom

No error. Several turns spent reading task scratch directories, ADRs and the
verification log for a result object that is not anywhere.

## Why

Every place that looks like it should hold one holds something else:

- `<hive_tasks_dir>/<task_id>/handoffs/<role>.json` is ADR 27's four-key
  envelope — `role`, `round`, `saved_at`, `handoff` — and the `handoff` is the
  model's structured return, not the CLI's envelope around it.
- ADR 49's probe record, in the account's state file, holds **parsed
  percentages** off `/usage` free text.
- `.data/verify/`, where `docs/ROADMAP.md` keeps its evidence files, is outside
  the directories a phase may read.
- `docs/ROADMAP.md` **V1.1 JSON result shape** is the check whose *Record* step
  would have written the key set, the `usage` breakdown, `total_cost_usd`,
  `num_turns`, `duration_ms` and `permission_denials` down. It has **no Results
  row**: it was never run. Item 2's per-phase usage records were specified to
  build on it.

`dispatcher/docker_exec.py:exec_claude` parses the object into
`ClaudeResult.raw`, three readers ask it three questions (`is_rate_limit_error`
reads `is_error` and `api_error_status`, `_exec_succeeded` reads `is_error` and
emptiness, `handoff.parse` reads `structured_output`), and the rest went out of
scope with the local — until T-024.

## Rule

Do not search, and do not depend on a key existing. Read every field with
`.get`, record a value that is not the type you expected as `null` rather than
coercing it, and carry the **key names** so the next real run answers the
question for you.

That is what `docs/decisions.md` **ADR 53** does: every line of
`<hive_tasks_dir>/<task_id>/usage/calls.jsonl` carries `raw_keys`, the sorted
top-level key names of the object with values excluded, and `measured`, false
exactly when nothing parsed. So **the answer to this question now arrives on
its own**, from the first dispatched phase after that branch merged — a renamed
key shows up as a column that went all-null beside a `raw_keys` that says why,
instead of as a number quietly meaning something else.

The spellings assumed in the meantime, and their sources: `docs/ROADMAP.md`
V1.1's *Record* step; its 2026-09-24 V5.1 Results row, which is CLI 2.1.273 and
names `num_turns`, `total_cost_usd`, `api_error_status` and `stop_reason`; and
the root `README.md` item 2, which names `usage` with input, output, cache read
and cache creation. None of those is an observation of a live result.

## Evidence

T-024's arquitecto and implementador each searched `/data/.hive/tasks/` across
T-011…T-020 and found only role notes and `handoffs/<role>.json`; its auditor
re-ran `grep -rl "num_turns\|total_cost_usd" /data/.hive/`, whose only hits are
T-024's own notes about this absence. `docs/ROADMAP.md` V1.1 has no row in the
Results log. T-024's card forbade a live probe, so the branch was written to
cost nothing if the assumed spellings are wrong —
`docs/implementations/T-024.md` *What no stored `raw` could tell us* is the long
form.
