# `renderRoute` opens a route on its own path, and the location is a second argument

**When it applies:** you are mounting a `front/src/routes/` screen whose path
has a parameter (the task page, `/tasks/$taskId`) through `renderRoute` from
`front/src/test/render-route.tsx`, or calling a `Route` hook (`useParams`,
`useSearch`) in a screen that a route test mounts.

**Status:** unconfirmed — T-035's arquitecto found it by reading, before any
run, and no phase of T-035 could run the front's tests: `bun` was not
granted and `front/node_modules` was absent. The dispatcher's test gate is
the first run of `front/src/routes/-tasks.test.tsx`.

## Symptom

None printed: found by reading. Until T-035 `renderRoute(route, path,
served)` used one string as both the route's path and the memory history's
first entry:

```
const screenRoute = createRoute({ getParentRoute: () => rootRoute, path, component });
history: createMemoryHistory({ initialEntries: [path] }),
```

## Why

With `/tasks/T-101` the harness route has no parameter, so the page's
`Route.useParams()` answers `{}` and the screen asks for
`/api/tasks/undefined`. With `/tasks/$taskId` the location is that literal
text and the id is `$taskId`. Every screen mounted before T-035 had a path
with no parameter, where the two strings are the same.

The task page is also the first mounted screen that calls a `Route` hook at
all: of the files under `front/src/routes/`, only `TaskDetailPage` calls
`Route.useParams()`, and none calls `Route.useSearch()`. The page's `Route`
is the generated tree's, not the route `renderRoute` builds, so the hook
resolving against the harness's match was reasoned from the router's
documented behaviour (no `from` means the nearest match) and not read from
its source. `-tasks.test.tsx` is the only evidence either way.

## Rule

For a route with a parameter, hand `renderRoute` the route's path and the
location apart: `renderRoute(Route, "/tasks/$taskId", served,
"/tasks/T-101")`. The fourth argument, `at`, defaults to `path`. Never pass
a filled-in URL as the route path. If a test of such a route is red at the
mount with an error about a match, and not at an assertion, the fix is in
`render-route.tsx` and not in the page.

## Evidence

`renderRoute` in `front/src/test/render-route.tsx` on the base of T-035
(`408adef`), read in the T-035 worktree; `taskPage` in
`front/src/routes/-tasks.test.tsx` is the call. Not run by a phase.
`docs/implementations/T-035.md` *Decisions*, 1.
