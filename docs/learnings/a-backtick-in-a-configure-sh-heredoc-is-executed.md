# A backtick in a `configure.sh` heredoc is executed, comments included

**When it applies:** you are writing a YAML comment (or any text) into one of
the heredocs in `scripts/configure.sh` — `render_coolify_compose`,
`render_agents_compose` — and the matching compose file's comment uses
backticks.

**Status:** unconfirmed — hit once by T-176's implementador; its auditor
checked that every `cat <<EOF` in the script is unquoted.

## Symptom

`tests/scripts/test_configure.py`'s byte-for-byte Coolify render test fails:
the rendered comment is missing the backticked word, or carries the output
(or error) of running it as a command.

## Why

Every heredoc in `scripts/configure.sh` is written `cat <<EOF`, unquoted, so
the shell expands `$` and runs command substitution inside it — and a YAML
`#` comment is just text to the shell. `` `docker-proxy` `` in a comment is a
command.

## What to do

Write the heredoc's comment without backticks (T-176's `docker_proxy_net`
comment does), and the same comment in the compose file so the byte-for-byte
render holds; or escape them as `` \` ``. Never quote the delimiter: the
heredocs rely on `$` expansion for the account names.

## Evidence

T-176, `docs/implementations/T-176.md` *What was done*; `grep -n "<<"
scripts/configure.sh` lists nine `cat <<EOF`, none quoted.
