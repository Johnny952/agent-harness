# An agent block of a shipped compose file is also a heredoc in `scripts/configure.sh`

**When it applies:** you are editing an `agent-*` block (or its `dind-*`
sidecar) of `docker/compose/docker-compose.coolify.yml` or
`docker/compose/docker-compose.agents.yml`, comments included.

**Status:** unconfirmed — reported once, by T-059's arquitecto, read from the
tests rather than run red.

## Symptom

```
AssertionError: render_coolify_compose cuenta1 cuenta2 no longer reproduces docker-compose.coolify.yml
```

(the failure `tests/scripts/test_configure.py`
`test_the_default_pool_renders_the_shipped_coolify_file` would raise; T-059
edited both sides together, so it never went red.)

## Why

`scripts/configure.sh` renders the agent pairs of both compose files from
heredocs (`render_agents_compose` writes the whole agents file,
`render_coolify_compose` the Coolify agents). The two tests are not alike:
`test_the_default_pool_renders_the_shipped_coolify_file` holds the
`cuenta1`/`cuenta2` render **byte-equal**, but
`test_the_default_pool_renders_the_shipped_agents_file` compares
`yaml.safe_load` of both sides, so a **comment** edited in the agents file and
not in its heredoc passes every test — and the next `configure.sh` run writes
the old comment back. T-059 did exactly that: its corrected `git-credentials`
comment is in `docker-compose.agents.yml` while `render_agents_compose` still
says the token "needs no write scope".

Only the head of the Coolify file — header through `dispatcher` — is copied
from the shipped file, so an edit there needs no renderer change. The first
agent's heredoc and the later agents' heredoc differ (the later one says "See
agent-$first above"), so the edit is two heredocs, not one.

## What to do

Make the same edit, character for character, in the matching heredoc of
`render_coolify_compose` (or the agents renderer), then run `python3 -m pytest`.
A new invariant you write in `tests/integration/test_compose_invariants.py` is
also worth calling from `test_a_rendered_coolify_pool_holds_the_agents_file_invariants`,
so pools other than the default are held to it. See also
[a-new-service-lands-in-two-compose-files](a-new-service-lands-in-two-compose-files.md).

## Evidence

T-059: the deploy-key mount added to both Coolify agents and to both heredocs
in `render_coolify_compose`; `docs/implementations/T-059.md` *What was built*.
