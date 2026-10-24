# The integrador takes the implementador's model but never its escalation

**When it applies:** you change `Config.model_for` or `Config.effort_for`, or
expect a round-based escalation (`escalated_model`, `escalated_effort`) to reach
the integrador or the arquitecto's `draft-specs` run.

**Status:** unconfirmed — proposed by T-175's implementador, checked by T-175's
auditor against the code.

## Symptom

With `escalated_model` set and an escalated implementador round, the integrador
of the same task still runs on the implementador's `role_model` (or
`default_model`), not on `escalated_model`.

## Why

`dispatcher/config.py` `Config.model_for` falls `DRAFT_ROLE` back to
`model_for("arquitecto")` and `INTEGRADOR_ROLE` to `model_for("implementador")`
with no `round_num` and no `repeated_finding`, so `_escalates` is false on that
call (ADR 277, ADR 432).

## Rule

Treat the fallback as "the role's own model": if a fallback role should
escalate, pass the round and signal through explicitly and record it in an ADR.

## Evidence

Read of `Config.model_for` and `Config._escalates` at T-175's implementador
commit; ADR 432 limits escalation to the implementador.
