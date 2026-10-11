# A path the harness writes outside the repo is a broken pointer the moment you backtick it

**When it applies:** you are documenting a file the harness writes outside the
repo — under `.hive/`, `dispatcher_state/` or `.data/` — and are about to cite
it in backticks in a doc under `docs/`.

**Status:** unconfirmed — read out of `dispatcher/gates.py` by T-024's
arquitecto while writing `docs/decisions.md` ADR 53, and avoided rather than
hit. No phase of T-024 ran the gates; `docker` is refused in one.

## Symptom

The pointers gate reports the citation as broken, in its own words
(`dispatcher/gates.py:_broken_pointers`):

```
`docs/decisions.md` points at `usage/calls.jsonl` — those paths are not there. Fix the pointer or the path.
```

## Why

`dispatcher/gates.py:pointer_token` accepts any backticked token with a slash
and an extension, and `_candidates` resolves it from the repo root and from
beside the citing doc. A path the dispatcher writes under `.hive/` resolves to
neither, and it never will: `.hive/` lives outside every worktree.

Neither escape saves it. `_ignored` asks `git check-ignore` about the token
**as written**, so `.hive/` being gitignored excuses `.hive/x.json` and not a
token spelled from inside it; `_in_a_subproject` only looks at tracked paths.
A fenced block does not help either — the gate matches backticks inside one.
And `docs/decisions.md` is a `RECORD_DOC`, so only the lines a task adds are
checked, which is exactly the ADR being written.

## Rule

Spell it with the placeholder prefix the harness's own config key gives it —
`<hive_tasks_dir>/<task_id>/usage/calls.jsonl` — because an angle bracket makes
`pointer_token` reject the token outright, before any resolution is attempted.
ADR 53 and `docs/plans/token-economy.md` **P5** use that spelling throughout
for this reason.

The same mechanism with a different token is
[a-docker-image-tag-reads-as-a-path-to-the-pointers-gate](a-docker-image-tag-reads-as-a-path-to-the-pointers-gate.md),
which a task did hit. The complement — a citation shape the gate never checks
at all, so a wrong one passes silently — is
[a-path-ext-symbol-citation-is-never-existence-checked](a-path-ext-symbol-citation-is-never-existence-checked.md).

## Evidence

Read by hand in the T-024 worktree: `dispatcher/gates.py:pointer_token`,
`_candidates`, `_ignored`, `_broken_pointers`, and `.gitignore`, while writing
ADR 53. The symptom text above is `_broken_pointers`' own message with the
offending token substituted, not a run — nothing in T-024 exercised the gates.
