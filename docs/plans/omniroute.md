# OmniRoute — an optional gateway for non-Claude models, switched from the console

**Status:** an idea recorded by the main thread on 2026-10-11 at the
user's ask, not designed in full, not built. **Priority 7, the last** of
the 2026-10-11 list (`server-deployment.md`, *The order across the plans*):
it needs the queue, the worker and sign-in first, and an ADR.

## What it is

OmniRoute (omniroute.online) is a self-hosted gateway under the MIT licence.
It serves an endpoint compatible with Anthropic's API, and OpenAI's and
Gemini's, on `localhost:20128/v1`, and routes each call to one of a few
hundred providers. It translates formats, tracks quota and fails over. It
runs from npm or as a docker image. Claude Code reaches it through
`ANTHROPIC_BASE_URL`.

## Why it fits

The console's model list already offers non-Claude models for some roles
(`front/src/lib/api/mock/ops-fixtures.ts`). Nothing can run them today. A
gateway is what would.

## Why with care

- **It sees everything.** Prompts, code and every provider's key pass
  through it. The guides read on 2026-10-11 say its dashboard password
  defaults to `CHANGEME` unless `INITIAL_PASSWORD` is set.
- **Not for the subscription logins.** The pool's accounts are Claude
  OAuth logins. Routing them through a third-party proxy risks Anthropic's
  terms. The gateway is for providers used with an API key, never a way to
  pool subscriptions.
- **A second balancer.** Its failover would cross the dispatcher's pool and
  cooldowns (`balancer.md`). Two systems would decide where a call goes.
- **Fidelity.** The skills and prompts are tuned for Claude. Translating
  tool use for another model loses some of it, so a role moved to another
  model is measured before it stays there.
- **Sources.** What was read is mostly third-party blogs. The official
  repository, its maintenance and its releases are checked before anything
  is built.

## The shape, if it goes ahead

- **An optional compose service** behind a profile, off by default, on the
  internal network only. Its admin password comes from the environment and
  is never the default.
- **Per role, in `/models`:** *Claude* (the pool, as today) or *Via gateway*
  with a model the gateway offers. The change is a queue action, applied
  from the next phase.
- **The phase** gets `ANTHROPIC_BASE_URL` and the gateway's key in its
  environment only when its role is set to the gateway. The pool's logins
  never reach it.
- **Cost** is read from the gateway's usage API into the usage log, under
  the role and the model.
- **Under an ADR**, after the queue, the worker and sign-in
  (`server-deployment.md`, steps 3 and 4).

## Open questions

- Whether the gateway's failover is turned off, so the dispatcher stays the
  only one deciding.
- Which roles may leave Claude at all. The revisor and the auditor are the
  likely last, since they judge the others.
