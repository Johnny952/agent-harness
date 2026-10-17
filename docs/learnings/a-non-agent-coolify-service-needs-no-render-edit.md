# A non-agent service above the agent pairs needs no `configure.sh` heredoc

**When it applies:** you are adding, changing or removing a service in
`docker/compose/docker-compose.coolify.yml` that is not an `agent-*`/`dind-*`
pair, and are budgeting an edit to `scripts/configure.sh` so the byte-for-byte
render test stays green.

**Status:** unconfirmed — proposed by T-061's arquitecto and implementador, and
checked by its auditor against `render_coolify_compose`.

## Symptom

No error. The worry is the opposite one to
[an-agent-block-edit-is-also-a-configure-sh-heredoc-edit](an-agent-block-edit-is-also-a-configure-sh-heredoc-edit.md):
a phase reads that entry and plans a heredoc edit for a new service that the
render never builds from a heredoc.

## Why

`scripts/configure.sh` `render_coolify_compose` copies the Coolify file line by
line with an awk program that stops at the first line starting `  agent-` and
rewrites only the collector's `COLLECTOR_WRITE_TOKENS` line on the way. Every
service written above the first agent pair — `collector`, `api`, `console`,
`registry-mirror`, `dispatcher` — is rendered exactly as it stands in the file.

## What to do

Write the service above the agent pairs and change no heredoc;
`tests/scripts/test_configure.py` holds the rendered head byte for byte, so it
stays green with no render change. Update the function's own comment listing
the head's services. A service written *below* the agents would be dropped by
the render, so never put one there.

## Evidence

T-061 placed `console` after `api` in the Coolify file; the only
`configure.sh` change was the comment, and `python3 -m pytest` stayed green
(2104 passed, 10 skipped). `docs/decisions.md` ADR 97, decision 5.
