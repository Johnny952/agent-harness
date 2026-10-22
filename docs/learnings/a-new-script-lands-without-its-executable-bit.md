# A new shell script lands without its executable bit: `chmod` is refused in a phase

**When it applies:** your task adds a new file under `scripts/` (or anywhere)
that an operator or a crontab line is meant to run directly.

**Status:** unconfirmed — reported once, by T-147's implementador.

## Symptom

The implementador's `chmod +x scripts/backup.sh scripts/restore.sh` was
refused in the phase, so both scripts were written mode 100644 and a direct
`scripts/backup.sh` would fail with permission denied.

## Why

`chmod` is not among the commands a phase is granted; the Write tool creates
files without the executable bit.

## What to do

File `chmod +x <script>` as a command request in the handoff (the operator ran
it for T-147, and `git ls-files -s` then showed 100755), and document the
script invoked through `bash <script>` so it works either way.

## Evidence

T-147 implementador round 1 handoff, *Command requests*; the revisor's
`git ls-files -s scripts/backup.sh scripts/restore.sh` showing 100755 after
the operator's run.
