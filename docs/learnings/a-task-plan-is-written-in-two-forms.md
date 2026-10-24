# A task's `plan` is written in two forms

**When it applies:** you read a task file's `plan` frontmatter field and
compare it with a plan file name or with another task's `plan`.

**Status:** unconfirmed. Proposed by the T-178-S2 arquitecto and
implementador, checked by its auditor against the code; no phase hit it as a
failure.

## What is true

T-178-S1 writes `plan` as the repo path `docs/plans/x.md`, while the T-072
reader of `GET /api/roadmap` keyed plans by the bare file name `x.md`. Both
forms are on disk. A string compare of the two calls the same plan different,
which is how a sub-task would be warned as "under another plan" than its epic.

## What to do

Normalise before comparing: `observability/api/app.py` `_plan_name` strips the
`project_docs.PLANS_DIR` prefix (ADR 447 part 3). Reuse it, or do the same, in
any new reader of `plan`.

## Evidence

T-178-S2, `docs/decisions.md` ADR 447; `observability/api/app.py`
`_plan_name`; `tests/observability/test_roadmap.py`.
