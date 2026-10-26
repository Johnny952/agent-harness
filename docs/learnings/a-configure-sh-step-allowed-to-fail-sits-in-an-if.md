# A `configure.sh` step allowed to fail sits in an `if` condition

**When it applies:** you add a step to `scripts/configure.sh` whose failure
should print a line and let the wizard go on.

**Status:** unconfirmed. Proposed by T-185-S2's arquitecto and implementador
(one source); the auditor checked the script's `set` line.

## Symptom

None seen in this task: a bare failing command would end the wizard with its
own error and a non-zero exit, before the rest of the configuration.

## Why

`scripts/configure.sh` runs `set -euo pipefail`. `set -e` is not tripped by a
command tested in an `if`/`elif` condition or on the left of `&&`/`||`.

## Rule

Put the fallible commands in the `if`/`elif` condition and the message in
its `else`, as the git token block after the `config.d` `mkdir` does
(`elif mkdir -p ... && (umask 077 && : > "$git_token"); then`).

## Evidence

`scripts/configure.sh` `set -euo pipefail` and its `git_token` block;
`docs/decisions.md` ADR 486 decision 4 (T-185-S2).
