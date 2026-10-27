# The only dispatcher-written sentinel in a phase session is its `Role:` line

**When it applies:** you write a hook or reader that must tell which role a
session, or a subagent inside it, belongs to, and reach for the subagent's
prompt or `agent_type`.

**Status:** unconfirmed — proposed by T-195's arquitecto and implementador;
checked by its auditor against `dispatcher/dispatcher.py`, the prompt builder.

## Symptom

No error. A role read from a subagent's own prompt or from `agent_type` comes
back empty most of the time, because nothing the dispatcher controls writes
either.

## Why

A role's subagent prompt is written by the role's model, not by the
dispatcher, and `agent_type` is mostly empty (ADR 510 *Context*). The one text
the dispatcher writes into every phase session is the prompt's
`Role: <role>. Task: <id>.` line, built in `dispatcher/dispatcher.py`.

## Rule

Read the role from the parent session's first prompt (`transcript_path`,
`^Role:\s*<name>\.`), as `hooks/cap_subagent_return.py` `role_of` does; treat
a subagent-prompt sentinel as an opt-in nothing writes yet (ADR 510 decision 4).

## Evidence

T-195: `grep -rn "Role: {" dispatcher/dispatcher.py` — both prompt builders
open with `Role: {role}. Task: {task_id}.`.
