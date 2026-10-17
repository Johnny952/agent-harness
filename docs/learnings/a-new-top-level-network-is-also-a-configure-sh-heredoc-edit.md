# A new top-level network or volume in the Coolify file is also a `configure.sh` heredoc edit

**When it applies:** you are adding, renaming or removing an entry under the
top-level `networks:` or `volumes:` of `docker/compose/docker-compose.coolify.yml`.

**Status:** unconfirmed — proposed by T-065's arquitecto and implementador,
checked by its auditor against `scripts/configure.sh` `render_coolify_compose`.

## Symptom

`tests/scripts/test_configure.py`'s byte-for-byte render test fails, and the
next `configure.sh` run would drop the new network from the Coolify file.

## Why

`render_coolify_compose` copies the file verbatim only down to the first
`  agent-` line ([a-non-agent-coolify-service-needs-no-render-edit](a-non-agent-coolify-service-needs-no-render-edit.md)).
Everything after the agent pairs, the top-level `networks:` and `volumes:`
included, is written from a heredoc in the script. So a service above the
agents needs no script edit, but a network that service joins does.

## What to do

Make the same edit in the heredoc in `render_coolify_compose` as in the file.
T-065 added `console_net:` in both (`docs/decisions.md` ADR 109,
*Consequences*).

## Evidence

T-065, `git diff` of `scripts/configure.sh`: the one `console_net:` line in
the heredoc beside the compose file's; `python3 -m pytest` 2186 passed.
