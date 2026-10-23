# `yaml.safe_load` raises `ValueError`, not only `yaml.YAMLError`

**When it applies:** you are narrowing an `except (ValueError, yaml.YAMLError)`
around `yaml.safe_load` to `yaml.YAMLError` alone, or writing a new "never
raises" read of hand-written YAML frontmatter.

**Status:** unconfirmed — reported once, by T-161's auditor.

## Symptom

```
ValueError: day is out of range for month
```

Raised out of `dispatcher/learnings.py:_split` for a frontmatter line
`when: 2026-02-30`, after the T-161 implementador split the old combined
`except (ValueError, yaml.YAMLError)` into a `ValueError` catch on the
delimiter split and a `yaml.YAMLError` catch on the load. `_read_entry` does
not catch it, so one such file would stop `read_inbox` for every entry.

## Why

A plain scalar matching YAML's timestamp pattern (`\d{4}-\d\d?-\d\d?`) is
built with `datetime.date(...)` by the `SafeLoader` constructor; an
impossible date raises the stdlib `ValueError` straight through. It is not
wrapped in a `yaml.YAMLError`.

## What to do

Catch `(ValueError, yaml.YAMLError)` around every `yaml.safe_load` of text a
phase wrote. `_split` as T-161 merged it still catches only `yaml.YAMLError`
on the load: the auditor could not land the widening (its commit is scoped to
`docs/`) and handed it on as debt. A test writing `when: 2026-02-30` through
`read_inbox` is the regression test to add with the fix.

## Evidence

A throwaway pytest outside the tree calling `learnings._split` on
`"---\nproject: p\ntask: t\nwhen: 2026-02-30\n---\nbody\n"`, run with
`python3 -m pytest <file> -q -s --rootdir=.` from the T-161 worktree. With the
catch widened to `(ValueError, yaml.YAMLError)` and that test added, the full
suite passed (4590 passed, 15 skipped).
