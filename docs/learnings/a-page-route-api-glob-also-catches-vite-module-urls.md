# A `**/api/**` route glob also catches the dev server's own module urls

**When it applies:** you are writing a Playwright walk of the front against
`bun run dev` and answer reads with `page.route("**/api/**", …)`.

**Status:** unconfirmed. Proposed by the T-178-S3 implementador; its auditor
read the guard in the walk script and reran the walk green with it.

## What is true

The vite dev server serves the front's sources as modules at their own paths,
so `front/src/lib/api/client.ts` is fetched as `<origin>/src/lib/api/client.ts`. The
glob `**/api/**` matches that url as well as `/api/roadmap`. A handler that
fulfils every match with a JSON fixture hands the browser JSON in place of the
api client module, and the page never boots.

## What to do

In the handler, take the url's pathname and `route.continue()` anything not
starting with `/api/`; fixture only the real reads. The T-178-S3 walk script
does this (`<hive_tasks_dir>/T-178-S3/verify/walk.mjs`, its `page.route`
handler).

## Evidence

T-178-S3: `node <hive_tasks_dir>/T-178-S3/verify/walk.mjs` run by the
implementador, revisor and auditor, four scenarios ok with the guard in place.
