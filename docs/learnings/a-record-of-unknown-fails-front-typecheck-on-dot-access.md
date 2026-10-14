# A `Record<string, unknown>` fails `front/`'s typecheck on dot access

**When it applies:** you are typing a served value as a loose record in
`front/` so you can guard each field at the render (`isRec(v) && v.role`),
and reach for `Record<string, unknown>`.

**Status:** unconfirmed — reported by T-046's implementador, which hit it
under `bun run typecheck` and fixed it; the error text was not quoted.

## Symptom

`bun run typecheck` fails on every `rec.field` read of a value typed
`Record<string, unknown>`: the property comes from an index signature and
must be accessed as `rec["field"]`. A second failure follows a fix that
makes the keys optional, under `exactOptionalPropertyTypes`.

## Why

`front/tsconfig.json` sets both `noPropertyAccessFromIndexSignature` and
`exactOptionalPropertyTypes`. The first forbids dot access through an index
signature; the second makes `?: T` mean "absent", not "`T | undefined`".

## Rule

Type the record as an optional-`unknown` mapped type over the keys you
read — `{ [K in keyof UsageLine | keyof UsageGroup]?: unknown }` — so dot
access type-checks and every field still has to be guarded.

## Evidence

`Rec` and `isRec` in `front/src/routes/tasks.$taskId.tsx`; the two flags in
`front/tsconfig.json` `compilerOptions`; T-046 implementador handoff,
*Verified* ("after fixing Rec index-signature and exactOptionalPropertyTypes
errors").
