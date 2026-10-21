# `renderRoute` answers a write to a bare-served path with the read's answer

**When it applies:** a route test through `front/src/test/render-route.tsx`
serves a path under its bare key (`"/api/push/settings"`) and the screen also
writes to that path (`PUT`, `POST`, `DELETE`).

**Status:** unconfirmed — reported by T-120's arquitecto and implementador.

## Symptom

The write "succeeds" with the GET's body, so a test of the write's answer or
its refusal passes or fails for the wrong reason; nothing says the write was
unserved.

## Why

`stubApi` tries the key `"<METHOD> <path>"` first and falls back to the bare
path for any method (the header comment's ADR 114 paragraph), so a bare key
answers every method.

## Rule

Serve every write the test exercises under its own `"PUT <path>"` key, and
read `sent[]` to check the body the screen sent.

## Evidence

`render-route.tsx`'s `stubApi`: `const key = named in served ? named :
url.pathname;`. T-120's settings tests in
`front/src/routes/-notifications.test.tsx` serve `"PUT /api/push/settings"`
explicitly.
