# `load_config` raises more than `ValueError`

**When it applies:** you are calling `dispatcher/config.py` `load_config` from
code that must answer for a bad file rather than crash or 500 — a route, a
status read, a validator.

**Status:** unconfirmed — proposed by T-065's arquitecto and implementador.

## Symptom

A caller catching only `ValueError` lets a bad `config.yaml` through as a
500: a file with no `accounts` key, or a value of the wrong shape, comes out
of the loader as `KeyError` or `TypeError`, and a file that does not parse as
`yaml.YAMLError`.

## Why

`load_config` words its own refusals as `ValueError`, but it indexes and
iterates the parsed YAML directly, so the shape errors it never anticipated
surface as the builtin exception that hit them.

## What to do

Catch `(OSError, ValueError, KeyError, TypeError, yaml.YAMLError)`, and keep
the `ValueError` message as it is while prefixing the others with the file and
exception name. `config_writer/app.py` `LOAD_ERRORS` is the tuple.

## Evidence

T-065: `tests/config_writer/test_app.py` cases for a `config.yaml` without
`accounts`; `docs/implementations/T-065.md` *Why this way*.
