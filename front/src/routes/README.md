# Routes

TanStack Start uses **file-based routing**. Every `.tsx` file in this directory
defines a route **unless its name starts with `-`**. Do **not** create
`src/pages/`, `src/routes/_app/index.tsx`, or `app/layout.tsx` — those are
Next.js / Remix conventions. The only root layout is `src/routes/__root.tsx`.

## Conventions

| File | URL |
| --- | --- |
| `index.tsx` | `/` |
| `about.tsx` | `/about` |
| `users/index.tsx` | `/users` |
| `users/$id.tsx` | `/users/:id` (dynamic — bare `$`, no curly braces) |
| `posts/{-$category}.tsx` | `/posts/:category?` (optional segment) |
| `files/$.tsx` | `/files/*` (splat — read via `_splat` param, never `*`) |
| `_layout.tsx` | layout route (renders children via `<Outlet />`) |
| `__root.tsx` | app shell — wraps every page; preserve `<Outlet />` |
| `-name.test.tsx` | no route — `routeFileIgnorePrefix` defaults to `-`, so a test sits beside what it tests (`docs/decisions.md` ADR 45) |

`routeTree.gen.ts` is auto-generated. Don't edit it by hand.
