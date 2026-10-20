/**
 * Mount one route's screen, whole, with what the api served for it.
 *
 * The component tests beside each route render its empty states with the
 * argument written by hand, so none of them can see which number the screen
 * itself passes — `served.length` and `rows.length` both type-check, and only one
 * of them is right. `docs/debt/T-017-D1.md`. This renders the route's own
 * component, so the argument is the screen's.
 *
 * **The seam is `fetch`, not the query cache.** Seeding a `QueryClient` would
 * cover the two screens that read through `useQuery` and miss the Live tail,
 * which polls `client.ts` from a `useEffect`; intercepting `fetch` covers all
 * three with one stub, and the served body still crosses `readEnvelope` and its
 * adapters — the reverse in `listEvents`, the `task_id` split in `listDebt` — so
 * a test sees the rows the screen does. It is `forward.test.ts`'s idiom: a
 * `vi.spyOn(globalThis, "fetch")` that `restoreMocks` in `vitest.config.ts` undoes
 * after each test. A path the test did not serve answers 404, which the screen
 * renders as its error state, so a missing stub fails loudly instead of waiting.
 *
 * **The router is built here, not taken from `src/router.tsx`**, as
 * `-index.test.tsx` builds its own: the generated tree's root mounts the document
 * shell — `<html>`, `HeadContent`, `Scripts` — which a test has no use for. One
 * root, the one route, and a memory history already on its path. Links the shell
 * draws to other screens still resolve to an `href`; nothing here follows them.
 */
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  type AnyRoute,
} from "@tanstack/react-router";
import { vi } from "vitest";

/**
 * One answer: `{data, warnings}`, or a refusal with its status, or `{bare, status}`
 * — a body answered as it is, for a board-service write that answers its record
 * with no envelope (`POST /api/threads`, ADR 195).
 */
export type Answer =
  | { data: unknown; warnings?: string[] }
  | { status: number; error: string }
  | { bare: unknown; status: number };

/**
 * What `observability/api/` serves: `{data, warnings}`, keyed by the path asked for.
 *
 * Or a refusal: `{status, error}` answers that status with `{"error"}`, the shape
 * every non-200 from the api, the config writer and the forward itself takes
 * (`docs/decisions.md` ADR 110 part 6, ADR 112). The Settings screen's
 * *unreachable* state is the forward's 503, and a 404 from an unserved path would
 * pass that test for the wrong reason.
 *
 * Two additions for the Settings screen's writes (ADR 114). A key may name its
 * method — `"PUT /api/settings/max_revision_rounds"` — and is then tried before
 * the bare path, so a write and the read beside it answer differently. And a
 * value may be a list, answered one entry per request with the last repeated, so
 * the read a write's refetch makes can serve what the write left behind.
 *
 * A list is counted per query string as well as per key: `/api/actions?limit=1`
 * (the sidebar's waiting count, ADR NNN) and `/api/actions?limit=20` (a screen's
 * own read of the queue) each walk the list from its first entry, so the
 * sidebar's poll does not take a turn the screen's sequence was written for.
 */
export type Served = Record<string, Answer | Answer[]>;

/** One request the screen made: its method, path and parsed JSON body, if any. */
export interface Sent {
  method: string;
  path: string;
  body: unknown;
}

function stubApi(served: Served, sent: Sent[]) {
  const asked: Record<string, number> = {};
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://console.test");
    const method = (init?.method ?? "GET").toUpperCase();
    const raw = init?.body;
    sent.push({
      method,
      path: url.pathname,
      body: typeof raw === "string" ? (JSON.parse(raw) as unknown) : undefined,
    });
    const named = `${method} ${url.pathname}`;
    const key = named in served ? named : url.pathname;
    const entry = served[key];
    const turn = `${key}${url.search}`;
    const n = asked[turn] ?? 0;
    asked[turn] = n + 1;
    const body = Array.isArray(entry) ? entry[Math.min(n, entry.length - 1)] : entry;
    const headers = { "content-type": "application/json" };
    if (!body) {
      return new Response(JSON.stringify({ error: `${url.pathname} is not served here` }), {
        status: 404,
        headers,
      });
    }
    if ("bare" in body) {
      return new Response(JSON.stringify(body.bare), { status: body.status, headers });
    }
    if ("status" in body) {
      return new Response(JSON.stringify({ error: body.error }), { status: body.status, headers });
    }
    return new Response(JSON.stringify({ data: body.data, warnings: body.warnings ?? [] }), {
      status: 200,
      headers,
    });
  });
}

/**
 * Render `route`'s component at `path`, with `served` answering its reads.
 *
 * `path` is the route's own path. A route with a parameter is opened on a
 * location that fills it in, which is `at`: `"/tasks/$taskId"` at
 * `"/tasks/T-101"`. Left out, `at` is `path`, which is right for every
 * route with no parameter.
 *
 * `route` is the module's exported `Route`; only its `component` is taken, so the
 * generated tree it is attached to never loads. Resolves once the route has
 * mounted — the screen's first read may still be in flight, so wait on what it
 * draws (`findByText`) rather than on this. `sent` is every request made so far,
 * in order, for a test that asserts what a write carried.
 */
export async function renderRoute(route: AnyRoute, path: string, served: Served, at = path) {
  const sent: Sent[] = [];
  const fetchMock = stubApi(served, sent);
  // `retry: false`: a read the test did not serve should be the error state now,
  // not after the default three retries with backoff.
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  const component = route.options.component;
  if (!component) throw new Error(`${path} has no component to render`);

  const rootRoute = createRootRoute();
  const screenRoute = createRoute({
    getParentRoute: () => rootRoute,
    path,
    component,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([screenRoute]),
    history: createMemoryHistory({ initialEntries: [at] }),
  });

  const result = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  await router.load();
  return { ...result, fetchMock, queryClient, router, sent };
}
