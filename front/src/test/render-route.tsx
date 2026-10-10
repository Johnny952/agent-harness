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

/** What `observability/api/` serves: `{data, warnings}`, keyed by the path asked for. */
export type Served = Record<string, { data: unknown; warnings?: string[] }>;

function stubApi(served: Served) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = new URL(String(input), "http://console.test");
    const body = served[url.pathname];
    const headers = { "content-type": "application/json" };
    if (!body) {
      return new Response(JSON.stringify({ error: `${url.pathname} is not served here` }), {
        status: 404,
        headers,
      });
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
 * `route` is the module's exported `Route`; only its `component` is taken, so the
 * generated tree it is attached to never loads. Resolves once the route has
 * mounted — the screen's first read may still be in flight, so wait on what it
 * draws (`findByText`) rather than on this.
 */
export async function renderRoute(route: AnyRoute, path: string, served: Served) {
  const fetchMock = stubApi(served);
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
    history: createMemoryHistory({ initialEntries: [path] }),
  });

  const result = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  await router.load();
  return { ...result, fetchMock, queryClient, router };
}
