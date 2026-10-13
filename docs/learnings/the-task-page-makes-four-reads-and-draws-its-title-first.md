# The task page makes four reads and draws its title before the first lands

**When it applies:** you are writing or editing a test that mounts the task
page (`front/src/routes/tasks.$taskId.tsx`) through `renderRoute`, or adding
a query to `TaskDetailPage` or to a child it renders.

**Status:** unconfirmed — read off the code by all three of T-035's earlier
phases and by its auditor; no phase ran the test that rests on it.

## Symptom

None printed. Two ways a test of this page passes or fails for the wrong
reason:

- a path left out of `served` answers 404, and the page draws that region's
  `ErrorState` ("/api/phases did not answer", "The debt index did not
  answer", "The learnings tree did not answer") while the header is still
  right, so a header assertion passes over a page reporting broken reads;
- `PageHeader` is drawn with the task id before `getTask` answers, with no
  subtitle, so "the header does not say *no description*" is true at once
  for every fixture.

## Why

`TaskDetailPage` makes four `useQuery` calls: `taskQuery` (`getTask`,
`/api/tasks/<id>`), `debtQuery` (`listDebt`, `/api/debt`), `phasesQuery`
(`listPhases`, `/api/phases?task_id=<id>`) and `learningsQuery`
(`listLearnings`, `/api/learnings`). `stubApi` in
`front/src/test/render-route.tsx` keys on the pathname, so the phases read
is served under `/api/phases`. `AppShell` adds no `fetch`: its
`actionBackendQuery` is a fixture, and `ChatPanel`, which asks for
`/api/tasks`, mounts only once the chat is opened.

The title is `taskId` from the route, not from the read, and `subtitle` is
`undefined` while no task has been read.

## Rule

Serve all four paths (`[]` for the three secondary reads), and before
reading the header await a text only the settled state draws: *task body*
for a task that was read, the `EmptyState` title (`<id> exists and could
not be read`) for `data: null`.

## Evidence

`TaskDetailPage` in `front/src/routes/tasks.$taskId.tsx`; `getTask`,
`listDebt`, `listPhases` and `listLearnings` in
`front/src/lib/api/client.ts`; `taskPage` in
`front/src/routes/-tasks.test.tsx`. The table is
`docs/implementations/T-035.md` *Every read the page makes*.
