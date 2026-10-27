# The console's socket peer is its proxy; the client is the last X-Forwarded-For hop

**When it applies:** you write code in `front/` that needs the browser's
address (a rate limit, an audit line), or a board service feature keyed on
`X-Forwarded-For`.

**Status:** unconfirmed — reasoned by T-193-S2's arquitecto from the
Caddyfile and compose, never checked against a live Caddy or Traefik.

## Symptom

None loud: a key built from the socket peer puts every person under one
address (the proxy's), and one built from the first `X-Forwarded-For` hop
is whatever the browser chose to write.

## Why

The console always sits behind a proxy that ends TLS: Caddy 2.10 in the
plain compose, Traefik in Coolify. So its own socket peer is that proxy.
Caddy with no `trusted_proxies` and Traefik with no trusted IPs replace an
incoming `X-Forwarded-For`; a proxy told to trust a further hop appends.
Either way the last hop is the one the nearest proxy wrote.

## Rule

Take the last hop of the incoming `X-Forwarded-For`, check it looks like an
address, and send it as the whole header upstream; never the socket peer,
never the first hop (`docs/decisions.md` ADR 522, 5;
`front/src/lib/api/forward.ts` `clientAddress`).

## Evidence

T-193-S2: `docker/caddy/Caddyfile` has a bare `reverse_proxy` and no
`trusted_proxies`; the compose binds the console to `127.0.0.1:8790`.
