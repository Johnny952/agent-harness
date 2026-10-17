# A value with a `$` is single-quoted for compose and bare for `docker run --env-file`

**When it applies:** you are putting a value that contains `$` — a bcrypt
hash such as `CONSOLE_AUTH_HASH`, a password — into the .env file compose reads,
or into a file you hand to `docker run --env-file`, or onto a phase's Bash
command line.

**Status:** unconfirmed — proposed by T-063's arquitecto and implementador from
documentation they recalled; neither half was run, because `docker` is refused
in a phase.

## Symptom

None at bring-up. Compose expands `$2a`, `$14` and the rest of an unquoted or
double-quoted bcrypt hash as variables, warns that they are unset, and passes
Caddy a shortened string; every Basic auth attempt then fails as a wrong
password. The other way round, a single-quoted value in a `docker run
--env-file` file reaches the container with the quotes as part of the value.

## Why

The two readers parse the file differently. Compose's .env parser takes a
single-quoted value literally and interpolates everything else. `docker run
--env-file` does no interpolation and no unquoting: each line after the first
`=` is the value, byte for byte. And a phase's Bash refuses a `$` on the
command line, so `-e CONSOLE_AUTH_HASH=...` cannot carry the hash at all.

## What to do

For compose: `CONSOLE_AUTH_HASH='$2a$14$...'`, single quotes, as README *TLS*
and `docs/decisions.md` ADR 103 point 5 say. For `docker run --env-file`: the
hash bare, in a file written with the Write tool, never on the command line.

## Evidence

`docs/implementations/T-063.md` *Plan* step 8 and *What was built*; the
compose half is held by
`tests/integration/test_compose_invariants.py::test_compose_passes_a_quoted_bcrypt_hash_through_intact`,
which skips where `docker compose` is absent, as it did in every T-063 phase.
