<!-- LOVABLE:BEGIN -->
> [!IMPORTANT]
> This project is connected to [Lovable](https://lovable.dev). Avoid rewriting
> published git history — force pushing, or rebasing/amending/squashing commits
> that are already pushed — as it rewrites history on Lovable's side and the
> user will likely lose their project history.
>
> Commits you push to the connected branch sync back to Lovable and show up in
> the editor, so keep the branch in a working state.
<!-- LOVABLE:END -->

## Project rules

- All backend reads/writes go through the typed client in `src/lib/api/client.ts` (one function per endpoint). **Six of them are real:** `listTasks`, `getTask`, `listAccounts`, `listEvents`, `listDebt` and `listPhases` read `observability/api/` through the console's own origin, and resolve to `ApiResult<T>` — the rows *and* the `warnings` the api's envelope carried, because nothing between the fetch and the render may drop them (`docs/decisions.md` ADR 16, ADR 25). Everything else still resolves from `src/lib/api/mock/fixtures.ts` and `src/lib/api/mock/ops-fixtures.ts`, because their routes are tier 2 and tier 3 of `docs/plans/front.md` — `listLearnings` waits for `/api/learnings`; `listActions`, `enqueueAction` and `listThreads` are not coming to this service at all. A region reading one of those says which route it is waiting for, on screen, instead of showing a fixture — and a region waiting on a fact no file records names the record instead, which is the Board's In progress column (ADR 28).
- Query keys and poll intervals live in `src/lib/api/queries.ts`; nothing polls faster than 2s.
- **The console authenticates to the api as a service, from its server half.** `src/lib/api/forward.ts` is a closed, GET-only whitelist of the six routes, intercepted in `src/server.ts` in front of SSR; it presents `Authorization: Bearer <API_TOKEN>` and browser code only ever calls this origin, so the token never reaches a bundle and the api needs no CORS (`docs/decisions.md` ADR 15, ADR 24). Three environment variables, and **none of them may take a `VITE_` prefix** — Vite inlines every `import.meta.env.VITE_*` into the client bundle:

  | Name | Default | When it is missing |
  |---|---|---|
  | `API_BASE_URL` | `http://api:8789`, the api's own compose network name | the default; point it at `127.0.0.1:8789` to run against the api on the host |
  | `API_TOKEN` | none | **the console refuses to start.** `src/lib/api/server-env.ts` throws at import rather than falling through to unauthenticated requests |
  | `CONSOLE_PROJECT` | none | `?project=` is omitted from the debt call and the api picks the project; its own name rather than the retired Jinja board's `BOARD_PROJECT` (`docs/decisions.md` ADR 24 for the name, ADR 32 for the retirement) |

- Every screen renders inside `AppShell` (`src/components/console/app-shell.tsx`), which owns navigation, keyboard shortcuts and the chat dock.
