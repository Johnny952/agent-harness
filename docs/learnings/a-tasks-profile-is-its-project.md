# No task or project carries a "profile": the README's word means the project

**When it applies:** you are building or reading something that selects by a
task's "profile" — the root `README.md` *Future work* item 1 uses the word
for which learnings rows a phase gets — and are looking for a field to read
it from.

**Status:** unconfirmed — found by T-142's arquitecto, repeated by its
implementador, checked by its auditor with the grep below.

## Symptom

No error. The README asks for the rows matching "the task's profile, labels,
or the paths its plan names", and nothing in the code holds a profile:

```
grep -rn -i "profile" dispatcher/ observability/api/app.py --include=*.py
dispatcher/learnings.py:916:    The README's "profile" is the project, which `eligible` already applies;
observability/api/app.py:2486:    since a stack runs without the `worker` profile (ADR 142, 1). A file that
```

The second hit is a docker compose profile, unrelated.

## Why

A task file carries `tags` and a project slug; a project's config carries no
profile either. The only per-project cut that exists is
`dispatcher/learnings.py` `eligible`, which keeps a project's own rows and the
harness ones.

## Rule

Read "profile" as the project slug, already applied by `eligible`; match on
the card's `tags` and the paths the task names (`docs/decisions.md` ADR 315,
decision 1), and do not invent a profile field.

## Evidence

The grep above, run by T-142's auditor in its worktree; ADR 315 *Context* and
`learnings.TaskContext`'s docstring record the same reading.
