# A relayed 204 needs a null body, not an empty buffer

**When it applies:** a forward in `front/` builds its answer as
`new Response(await upstream.arrayBuffer(), { status: upstream.status })`
and the upstream can answer 204, 205 or 304.

**Status:** unconfirmed — hit by T-193-S2's implementador in the full
suite run and reported again by its revisor.

## Symptom

```
Invalid response status code 204
```

thrown from the `Response` constructor, even though the buffer is empty.

## Why

The Fetch `Response` constructor refuses any non-null body with a
null-body status (204, 205, 304), and an empty `ArrayBuffer` is still a
body.

## Rule

Pass `null` as the body for 204, 205 and 304 when relaying an upstream, as
`front/src/lib/api/forward.ts` `forwardSession` does.

## Evidence

T-193-S2: `bun run test` failed one case, the `DELETE /api/session` 204,
before the null-body branch; 948 passed after it.
