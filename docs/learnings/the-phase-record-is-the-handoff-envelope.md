# The phase record is the handoff envelope, and a usage line's model is the call's argument

**When it applies:** a task asks for a fact to appear in "the phase record" (or
on `/api/phases`) or in the usage records, and you are looking for which writer
to change.

**Status:** unconfirmed — proposed by T-175's arquitecto and implementador (one
source), checked by T-175's auditor against the code.

## Symptom

T-175's *Done when* asked that "the usage and phase records name the model it
actually ran on". Neither name is a symbol in the code.

## Why

The phase record is the JSON envelope `dispatcher/context_transfer.py`
`save_handoff` writes under the hive's `handoffs/`, which `/api/phases`
(`observability/api/app.py` `phases`) serves (ADR 432 calls it that). The
per-call usage line is built by `dispatcher/dispatcher.py` `_usage_record`,
whose `model` is the `model` argument `dispatch_phase` was called with, not
anything read back from the CLI's result.

## Rule

Put a phase-level fact in `save_handoff`'s envelope (written only when given),
and make a usage line name a model by passing it to `dispatch_phase`: compute it
once in `run_phase` and hand the same value to both, as `chosen_model` does.

## Evidence

T-175: `run_phase` computes `chosen_model` and passes it to `dispatch_phase` and
`save_handoff` (ADR 432); tests in `tests/dispatcher/test_dispatcher.py` and
`tests/dispatcher/test_context_transfer.py` assert both records.
