# A board-service refusal's parsed body is on `ApiError.body`

**When it applies:** a console write through `front/src/lib/api/client.ts`
must tell one refusal from another by a field of the error's JSON body, not
only its message or status.

**Status:** unconfirmed. Reported by T-125-S3's implementador; checked against
the tree by its auditor.

## Why

Since T-125-S3, `readBare` passes the refusal's parsed body as `ApiError`'s
optional third argument, `body: unknown`. The approve uses it: a 409 or 503 whose
body holds `verdict` means the verdict was recorded but nothing queued
(ADR 267, ADR 270). Older callers construct `ApiError(message, status)` and are
unaffected.

## Rule

Narrow `err instanceof ApiError && typeof err.body === "object" && err.body !== null && "<field>" in err.body`
as `Verdicts` in `front/src/routes/approvals.tsx` does; do not add a second error class.

## Evidence

`front/src/lib/api/client.ts` `ApiError` and `readBare`; the approve tests in
`front/src/routes/-approvals.review.test.tsx`, T-125-S3.
