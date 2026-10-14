# The pointers gate has no command a phase can run

**When it applies:** your done-when says "the pointers gate passes" and you
are looking for a command to run it inside a phase, before the dispatcher
does.

**Status:** unconfirmed — T-040's implementador proposed it; the auditor
checked it against `dispatcher/gates.py` `run`.

## Symptom

None: there is no CLI entry to try. `dispatcher/gates.py` has no
`__main__` block and no argument parser.

## Why

The gate is `_broken_pointers`, called only from `dispatcher/gates.py` `run`,
whose first argument is a container name: every check it makes (`ls`, the
grep, `git check-ignore`) is executed inside the agent container by the
dispatcher, between phases. Nothing exposes it to a phase.

## Rule

Check the pointers you added by hand — `ls` each path and grep each heading,
ADR number and symbol you cite — say in the handoff that the gate itself was
not run in-phase, and let the dispatcher's run between phases be the verdict.
For the root `README.md` the gate checks nothing anyway: see
[the-root-readme-is-outside-the-pointers-gate](the-root-readme-is-outside-the-pointers-gate.md).

## Evidence

T-040's implementador and revisor both checked their citations with `ls`
for this reason (`docs/implementations/T-040.md`, *Built*).
