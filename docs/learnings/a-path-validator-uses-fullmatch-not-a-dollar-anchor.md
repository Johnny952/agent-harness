# A path validator uses `fullmatch`, not a `$` anchor

**When it applies:** you write a regex that decides whether a tool argument or
a frontmatter value is valid, and are about to anchor its end with `$`.

**Status:** unconfirmed — proposed by T-178-S1's arquitecto and implementador,
checked against the tree by T-178-S1's auditor.

## What happened

Python's `$` also matches just before a trailing newline, so
`re.match(r"docs/plans/[a-z]+\.md$", "docs/plans/x.md\n")` succeeds and a
value with a newline in it would be written into the task file's frontmatter.
T-178-S1 validates the `plan` field with `_PLAN_PATH.fullmatch` in
`dispatcher/tasks.py` `is_plan_path` (ADR 450), which refuses it.

## Rule

Validate a whole value with `pattern.fullmatch(value)` and no anchors.
