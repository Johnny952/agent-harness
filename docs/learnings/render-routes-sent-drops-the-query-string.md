# `renderRoute`'s `sent` drops the query string; read it off `fetchMock`

**When it applies:** you are asserting, in a `front/` route test through
`front/src/test/render-route.tsx`, which query string a screen asked with
(a `limit`, a `project`).

**Status:** unconfirmed — proposed by T-107's implementador; checked against
the harness by its auditor.

## Symptom

`sent` holds `{ path: "/api/actions" }` for a request to
`/api/actions?limit=20`, so an assertion on the `limit` against `sent` can
never pass.

## Why

`stubApi` in `front/src/test/render-route.tsx` records and keys every request
by `url.pathname` only.

## Rule

Map `fetchMock.mock.calls` to `String(call[0])` and assert the full URL there;
keep `sent` for methods, paths and bodies.

## Evidence

The test "asks for twenty finished records" in
`front/src/routes/-queue.test.tsx`.
