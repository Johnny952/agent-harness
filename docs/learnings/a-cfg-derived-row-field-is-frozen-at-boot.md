# A row field the api derives from `cfg` follows `config.yaml` at restart, not at the next poll

**When it applies:** you are adding a field to a row in
`observability/api/app.py` that is derived from `cfg` rather than read from a
per-request file, and you are about to write in an ADR, a docstring or an empty
state what an operator sees after editing `config.yaml`.

**Status:** unconfirmed — found by T-016's revisor in round 1 by reading an ADR
against the code it describes, and corrected in
[`docs/decisions.md`](../decisions.md) **ADR 41** *Consequences* in round 2.

## Symptom

No error, and no test can catch it: the code and the ADR disagree about when a
served value follows the config, and the ADR is the half a reader acts on. ADR
41 part 8 computes the harness fingerprint once, above the views:

```
harness = learnings.harness_fingerprint(
    cfg.permission_mode, cfg.allowed_tools, docker_exec.WRITER_ROLES
)
```

and the same entry's Consequences then promised the opposite:

```
`in_phase_table` and `stale` are recomputed on every request, so an operator who
changes `permission_mode` or `allowed_tools` in `config.yaml` sees this screen
move at the next poll.
```

## Why

`create_app` calls `load_config` once and holds that one `Config` for the life
of the app — [`a-new-field-on-an-api-row-has-two-questions`](a-new-field-on-an-api-row-has-two-questions.md)
states it, and forbids a second `load_config` inside a view. So anything derived
from `cfg` above the views is frozen at boot, and a `config.yaml` edit reaches
the screen only when `compose-api-1` restarts.

The sentence is easy to get wrong because half of it is true. The view really
does recompute the row on every request: the file walk, the selectors over it,
and every judgement that compares one file against another. It is only the
`cfg`-derived half that is fixed, and a single field can depend on both — `stale`
compares a per-request directory read against a boot-time fingerprint, so a new
entry moves it at the next poll and a `permission_mode` edit does not.

The dispatcher reads `config.yaml` once per run and there is no daemon loop
(`dispatcher/cli.py:main`), so between an operator's edit and the api's restart
the screen can disagree with what the next phase is actually handed.

## What to do

Say "restart" in the ADR, the docstring and the empty state, and never
"recomputed per request" about the config half. When a field mixes the two,
split the sentence: name what moves at the next poll and what waits for the
restart, as ADR 41's corrected Consequences now does.

If you want a value that really does follow `config.yaml` per request, that is a
contract change and not a line of code — the one `Config` is the rule, not an
accident of this route.

## Evidence

`observability/api/app.py:create_app` and `learnings_index` read against
`docs/decisions.md` **ADR 41** parts 7–8 and its Consequences, in T-016's
worktree at `f2937aa`. Found by reading, not by running: no command reproduces
it, which is how it reached an accepted ADR. The same wrong belief reached
T-016's implementador round-1 handoff as a risk line, and is corrected in
[`docs/implementations/T-016.md`](../implementations/T-016.md). Inbox entry:
`/data/.hive/learnings/inbox/T-016-a-row-field-derived-in-create-app-is-frozen-at-boot.md`.
