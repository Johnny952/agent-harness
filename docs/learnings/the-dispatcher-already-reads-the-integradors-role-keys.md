# The dispatcher already reads the integrador's role keys

**When it applies:** a task asks you to make the dispatcher or `load_config`
accept or read `role_model.integrador` or `role_effort.integrador`, or to give
the integrador a per-role setting, and you are about to change
`dispatcher/config.py`.

**Status:** unconfirmed — reported by T-191's arquitecto and implementador
from reading the code; checked by its auditor against `dispatcher/config.py`.

## Symptom

The Settings screen at `/models` had no `integrador` row under `role_model`
or `role_effort`, which reads as if the dispatcher had no such key.

## Why

`dispatcher/config.py` `_load_role_model` and `_load_role_effort` have
accepted `INTEGRADOR_ROLE` since ADR 277, `Config.model_for` falls back to
the implementador's model for it, and `resolve_conflict` in
`dispatcher/dispatcher.py` starts the integrador with `cfg.model_for` /
`cfg.effort_for` of it. The only gap was `config_writer/app.py`'s
`GET /settings` `roles` block, derived from `PHASE_ROLES`, which the
integrador is deliberately not in.

## Rule

Look for the gap in the writer's served role lists before the loader: a role
outside `PHASE_ROLES` (the integrador, `arquitecto-draft`) is added to the
`roles` block by name (`docs/decisions.md` ADR 507), never by widening
`PHASE_ROLES`.

## Evidence

T-191, 2026-10-27: `grep -n INTEGRADOR_ROLE dispatcher/config.py` shows it in
the accepted sets of both loaders; `tests/dispatcher/test_integrador.py`
`test_the_integrador_reads_its_own_model_and_effort_keys` passes.
