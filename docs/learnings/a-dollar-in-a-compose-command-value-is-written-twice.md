# A `$` in a compose `command` value is written `$$`, and a test reading it back unescapes

**When it applies:** you are putting a regex anchor or any literal `$` into a
service's `command`, `environment` or other value in a compose file, or
writing a test that reads such a value out of the parsed YAML.

**Status:** unconfirmed — proposed by T-176's arquitecto and implementador;
the test half is held by the suite, the compose half was not run (`docker` is
refused in a phase).

## Symptom

Compose treats a bare `$` as the start of a variable: `...json)$` loses its
anchor or warns of an unset variable, and the proxy gets a pattern that is not
the one reviewed. In the other direction, a test that compiles the value as
read by `yaml.safe_load` gets `$$`, which in Python `re` is two end anchors and
still matches — so a missing unescape passes silently.

## Why

Compose interpolates every value in the file, `command` list items included;
`$$` is its escape for a literal `$`. YAML parsing does no interpolation, so
the test sees the escaped form.

## What to do

Write `$$` in the compose file; in the test, `.replace("$$", "$")` before
using the value, as `tests/integration/test_compose_invariants.py`
`_proxy_patterns` does (and as the bcrypt test there does in reverse). For
`.env` values see
[a-dollar-in-an-env-value-is-quoted-for-compose-and-bare-for-docker-run](a-dollar-in-an-env-value-is-quoted-for-compose-and-bare-for-docker-run.md).

## Evidence

T-176: the `docker-proxy` `-allowHEAD`/`-allowGET`/`-allowPOST` flags in both
compose files; `docs/decisions.md` ADR 435 decision 2.
