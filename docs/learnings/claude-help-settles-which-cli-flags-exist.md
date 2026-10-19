# `claude --help` in a phase settles which CLI flags exist

**When it applies:** you are building or reviewing a `claude` argv in
`dispatcher/docker_exec.py` (`exec_claude`, `turn_command`) and need to know
whether a flag exists, such as `--permission-prompts`, `--disallowed-tools`
or `--fork-session`.

**Status:** unconfirmed. T-110's architect ran it once.

## Why

The CLI is installed in the phase container, and `claude --help` runs
there. It lists the flags of that container's version, which may not be the
agent image's version.

## Rule

Run `claude --help` before you design around a flag, and record in risks
that the version you checked is the phase container's.

## Evidence

T-110 architect, 2026-10-19. The output listed `--disallowed-tools`,
`--permission-prompts none|host`, `--verbose` and `--fork-session`. The
decision it informed is `docs/decisions.md` ADR 173.
