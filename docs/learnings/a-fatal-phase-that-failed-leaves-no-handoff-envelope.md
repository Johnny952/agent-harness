# A fatal phase that did not finish leaves no handoff envelope, so `/api/phases` lists finished phases only

**When it applies:** you are reasoning about which phases have a record — a row
in `/api/phases`, a file under `handoffs/`, a `read_handoff` that came back
`None` — or you are about to put a new per-phase fact on the envelope
`save_handoff` writes.

**Status:** unconfirmed — read out of `dispatcher/dispatcher.py:run_phase` by
T-024's arquitecto and implementador, and re-checked by its auditor.

## Symptom

No error. A reader that treats the absence of a row as "this phase never ran",
or a design that expects to find a failed phase's record where the successful
ones are.

## Why

`run_phase` returns **before** it reaches `context_transfer.handoff` and
`context_transfer.save_handoff`. The order in that function is: dispatch, then
`if not result.success and fatal` → log the diagnosis, set the card `blocked`,
`return None`. The prose rendering into the task file and the JSON envelope
both sit after that return.

So a phase that crashed, timed out, or ran out of accounts leaves its
diagnosis in the dispatcher's log and nothing on disk under `handoffs/`. Every
file there — and therefore every row `/api/phases` serves, since that route
walks `list_handoff_roles` over `handoffs/*.json` — is a phase that finished.
`docs/decisions.md` ADR 26, ADR 27 and ADR 28 rest on that.

**One exception, and it is the only one.** `fatal=False` is passed at exactly
one call site: the optional mapper (`project_docs.MAPPER_ROLE`), run when
`_needs_mapping` is true. A mapper that failed only warns and then falls
through to both writes, so it *does* leave an envelope — one whose `handoff`
payload may be `null`, which `save_handoff` writes deliberately rather than
leaving the previous round's file standing.

## Rule

Read a missing envelope as "did not finish", not as "did not run", and expect
the one `fatal=False` role to break the pattern in the other direction.

This is also why a per-phase cost cannot live on that envelope: the phases
whose cost matters most are the ones that never reach it. ADR 53 gives that
argument in full and is the worked example of designing around this.

## Evidence

`dispatcher/dispatcher.py:run_phase` — the `if not result.success:` block, its
`if fatal: … return None`, and the `context_transfer.handoff` /
`context_transfer.save_handoff` calls below it; `grep -n "fatal" ` over that
file finds the parameter, the branch and the single `fatal=False`, which is the
`run_phase` call guarded by `_needs_mapping`.
`dispatcher/context_transfer.py:save_handoff`'s docstring states the
written-even-when-`None` rule and why. `observability/api/app.py`'s
`/api/phases` route is the reader.
