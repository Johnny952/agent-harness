# `dispatch_phase`'s handover state is loop locals; the usage log's call name is the durable record

**When it applies:** you are changing what a failover attempt of
`dispatch_phase` sends or does based on how the previous attempt ended (a rate
limit, a login failure, a refused resume), or you want to find out afterwards
which prompt or which call a handed-on phase ran.

**Status:** unconfirmed — read out of `dispatcher/dispatcher.py` by T-148's
arquitecto and implementador, one source.

## Symptom

No error. After the fact nothing on disk says whether a resumed attempt got
the short continue prompt or the full briefing, or why.

## Why

The decision of ADR 330 rests on three locals of the `while True` account loop
in `dispatch_phase` — `handed_on_by_limit`, `session_briefed`,
`prior_auth_handover` — set in the rate-limit and auth branches before their
`continue`. They die with the call. Two traces outlive it: the INFO line
`task … goes to account …, resuming session <id> with the short continue
prompt` / `with the full prompt (<reason>)`, and the usage log's `call` field
(ADR 53, printed verbatim by the console per ADR 67), which since T-148 has a
fifth name, `phase-restart`, for the full-prompt call after a refused resume.

## Rule

Add new handover state as a loop local beside those three, reset it in every
branch that `continue`s, and if anything later needs to know which call ran,
give that call its own `recorder.record` name instead of storing the local.

## Evidence

`dispatcher/dispatcher.py` `dispatch_phase`: the locals' initialisation above
the loop, the `short_prompt` / `how` block before the "goes to account" line,
the `phase-restart` branch after `recorder.record("phase", result)`.
See also [a-phase-is-not-one-claude-call](a-phase-is-not-one-claude-call.md).
