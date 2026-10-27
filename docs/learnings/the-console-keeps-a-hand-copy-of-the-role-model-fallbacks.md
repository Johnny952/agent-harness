# The console keeps a hand copy of the role_model fallbacks

**When it applies:** a task adds, removes or changes a role's model fallback
in `dispatcher/config.py` `Config.model_for` (today `arquitecto-draft` to the
arquitecto's model, `integrador` to the implementador's), or adds a role to
the writer's served `roles.role_model` list that falls back somewhere other
than `default_model`.

**Status:** unconfirmed — read from the code by T-198's arquitecto,
implementador and auditor.

## Symptom

None loud: the Settings screen at `/models` keeps telling the operator what
an unset role runs on, and a stale map makes that sentence wrong without any
test going red.

## Why

`GET /settings` serves the role list, not the fallbacks, so
`front/src/routes/models.tsx` `ROLE_MODEL_FALLBACK` repeats
`Config.model_for`'s two fallbacks by hand and `roleModelHint` builds the
`role_model` hint from it (`docs/decisions.md` ADR 513). Nothing ties the two
copies (ADR 513 *Consequences*).

## Rule

Change `ROLE_MODEL_FALLBACK` in the same task as the fallback in
`Config.model_for`, and extend the hint tests in
`front/src/routes/-models.test.tsx` for the role.

## Evidence

T-198, 2026-10-27: `grep -n -A14 "def model_for" dispatcher/config.py`
shows the `DRAFT_ROLE` and `INTEGRADOR_ROLE` fallbacks; `git diff main HEAD
-- front/src/routes/models.tsx` shows the map copying them.
