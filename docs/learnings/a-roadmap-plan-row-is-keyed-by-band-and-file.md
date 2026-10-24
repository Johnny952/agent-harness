# A roadmap plan row is keyed by band and file

**When it applies:** you key, look up or toggle a plan row of
`GET /api/roadmap` in the front or any other reader, and are about to use the plan
file name alone.

**Status:** unconfirmed. Proposed by the T-178-S3 arquitecto and implementador
from `docs/decisions.md` ADR 447; checked by its auditor against ADR 447's
consequences and `front/src/routes/roadmap.tsx` `Band`.

## What is true

Since ADR 447 a plan file can be a row in two bands at once: a `building`
plan sits in the now band with its tasks and in the future band with its
backlog epics. Keyed by `file` alone, the two rows collide as React keys and
share one open state.

## What to do

Key a plan row `<band>:<file>`, as `Band` in `front/src/routes/roadmap.tsx`
does (ADR 453 part 3). Epic rows are keyed `epic:<id>`; no-plan keys are
unchanged.

## Evidence

T-178-S3, the walk's `backlog-epic` scenario: the same plan file drawn in the
Future and Now bands, each with its own open state.
