# A filter that lives inside a route component is tested through `renderRoute`

**When it applies:** you want to test something a `front/src/routes/` screen
computes inside its page component and hands to no child as a prop (a search
box's filter, a count, which rows survive), and are about to export a
component for it or to build a router and a `QueryClient` in the test.

**Status:** unconfirmed — T-034's arquitecto and implementador proposed it
and wrote the Board's first route-mounted test on it without running it: no
phase of T-034 had `bun`. The harness itself is older and runs: the Debt,
Learnings and Tail tests have used it since `docs/debt/T-017-D1.md` was
resolved.

## Symptom

None in the error sense. The cost is a plan: the Board's search lives in the
`filtered` memo of `BoardPage` in `front/src/routes/index.tsx`, `TaskCard`
never sees the query, and the only test file beside the Board mounted
`TaskCard` alone, so the search looked untestable without new machinery.

## Why

`front/src/test/render-route.tsx` already is that machinery.
`renderRoute(Route, path, served)` mounts one route in a memory-history
router and stubs `fetch`, answering each request from `served` keyed by the
request's **pathname**, so a query string does not miss. A seeded query
cache was not chosen because the tail polls from an effect. Since T-035 it
takes a fourth, optional argument for a route whose path has a parameter:
`docs/learnings/render-route-opens-a-route-on-its-own-path.md`.
`docs/learnings/a-route-can-export-the-component-its-branch-lives-in.md`
still says in its *When it applies* that the suite has no harness for a
route render; that was true when it was written and stopped being true on
2026-10-10.

## Rule

Mount the route with `renderRoute`, serving every path its queries ask for
(read them off `front/src/lib/api/client.ts`), and drive the control with
`fireEvent`; export a component only for a branch that takes its inputs as
props.

## Evidence

`front/src/routes/-index.test.tsx`, *the Board's search over a task with no
description*, serves `/api/tasks`, `/api/accounts` and `/api/debt`, the
three paths `listTasks`, `listAccounts` and `listDebt` ask for.
`front/README.md` *Writing a test* describes the helper. The Board had not
been mounted this way before T-034: if the dispatcher's test gate is red on
the mount and not on the assertion, read the helper before the screen.
