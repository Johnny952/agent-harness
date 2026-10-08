/**
 * The bearer forward: the console's server half, standing in front of
 * `observability/api/` so browser code never holds a credential.
 *
 * `docs/decisions.md` ADR 15 decides that the console authenticates as a service
 * from its server half; ADR 24 decides that this is where it lives — an
 * interception in `front/src/server.ts`, which is the handler every request
 * already passes through, rather than a file-based route group (that needs an
 * entry in the generated `routeTree.gen.ts`, which no phase here can regenerate)
 * or a server function.
 *
 * **It is a whitelist and not a proxy.** Six console paths map one-to-one onto
 * the six api routes; anything else under `/api/` is a JSON 404 from the console
 * and never reaches the api. Query parameters are copied by name per route — the
 * same posture `observability/api/app.py:_reject_unknown_parameters` takes on the
 * other side, so a parameter the console did not mean to send cannot come back as
 * a 400 it did not expect. `?project=` is added from the console's own
 * configuration and never from the browser.
 *
 * Every path through this function ends in a JSON body, including the ones where
 * the api never answered, which is what lets `client.ts` have one error shape.
 */
import { API_BASE_URL, API_TIMEOUT_MS, API_TOKEN, CONSOLE_PROJECT } from "./server-env";

/** The list routes, and the browser parameters each one may carry. */
const FORWARDED: Record<string, readonly string[]> = {
  "/api/tasks": [],
  "/api/accounts": [],
  "/api/events": ["limit", "source_app", "since"],
  // `project` is deliberately absent: it comes from CONSOLE_PROJECT below.
  "/api/debt": [],
  // `task_id` is copied by name like `since`, and an empty value reads as absent
  // on both sides. The api takes it as optional and the console always sends it:
  // nothing here polls the unfiltered form (`docs/decisions.md` ADR 27, ADR 28).
  "/api/phases": ["task_id"],
};

/**
 * The detail route. No second segment: `/api/tasks/a/b` is not this route.
 *
 * And no dot-segment guard either, though the segment looks like somewhere one
 * belongs. A url parser resolves `.` and `..` away while it reads the path, in
 * every spelling the standard counts as a dot segment (`.`, `%2e`, `..`,
 * `.%2e`, `%2e.`, `%2e%2e`, upper or lower case), so by the time a request
 * reaches this module `/api/tasks/..` is already `/api/` and `/api/tasks/.` is
 * already `/api/tasks/`: both miss this regex and land on the whitelist's 404
 * below. A `%2f` inside the segment is a different matter -- it survives
 * parsing, and is re-encoded rather than resolved, so it crosses as part of one
 * id for the api to reject. A test per spelling is in `forward.test.ts`, since
 * what makes a guard here dead code is a fact about the parser that nothing in
 * this file would otherwise record.
 */
const TASK_DETAIL = /^\/api\/tasks\/([^/]+)$/;

type Resolution =
  { kind: "forward"; target: string } | { kind: "reject"; status: number; error: string };

/**
 * The api's answer to a request the console forwards, or `null` for a request
 * that is not this function's business — in which case `server.ts` lets SSR have
 * it, which is every page the console renders.
 */
export async function forwardApiRequest(request: Request): Promise<Response | null> {
  const url = new URL(request.url);
  if (!url.pathname.startsWith("/api/")) return null;

  if (request.method !== "GET") {
    // The api serves reads only: every route there is a `GET` and the service
    // writes nothing anywhere. A write that ever goes this way re-opens ADR 24's
    // last paragraph about the CSRF middleware rather than inheriting it.
    return jsonResponse(
      { error: `${request.method} is not served here: the console forwards reads only.` },
      405,
    );
  }

  const resolved = resolveTarget(url);
  if (resolved.kind === "reject") {
    return jsonResponse({ error: resolved.error }, resolved.status);
  }

  let upstream: Response;
  try {
    // Nothing of the browser's own request crosses: no cookies, no
    // `Authorization` it may have sent, no encoding negotiation. Only ADR 7's
    // header, and a timeout, because an unbounded read is how a console with no
    // push surface becomes a page that never arrives.
    upstream = await fetch(resolved.target, {
      headers: { Authorization: `Bearer ${API_TOKEN}`, Accept: "application/json" },
      signal: AbortSignal.timeout(API_TIMEOUT_MS),
    });
  } catch (cause) {
    // The service, not the address. This body is read by a browser, and
    // `API_BASE_URL` is the server half's own topology — a host and a port the
    // console has no reason to publish. What the screens need is the difference
    // between "the harness is quiet" and "the console cannot reach it", and
    // naming the api says that; the address goes to the server log, where
    // whoever is debugging an unreachable api is already looking.
    const why = describe(cause);
    console.error(`[forward] ${API_BASE_URL} did not answer: ${why}`);
    return jsonResponse({ error: `The api did not answer: ${why}` }, 502);
  }

  // The api's status and body, with the api's own content type. Flask answers its
  // own 404 and 405 in HTML and that stays recognisable as HTML on the other
  // side, which is exactly what `client.ts`'s guard is for. No other header
  // crosses in either direction.
  const body = await upstream.arrayBuffer();
  const contentType = upstream.headers.get("content-type");
  return new Response(body, {
    status: upstream.status,
    headers: contentType ? { "content-type": contentType } : {},
  });
}

function resolveTarget(url: URL): Resolution {
  const path = url.pathname;

  const detail = TASK_DETAIL.exec(path);
  if (detail) {
    const raw = detail[1]!;
    let id: string;
    try {
      id = decodeURIComponent(raw);
    } catch {
      // `%zz` and friends. A path the console cannot read is not one of the six.
      return { kind: "reject", status: 404, error: `${path} is not a readable task id.` };
    }
    // Re-encoded rather than passed through. `_is_bare_task_id` on the api
    // validates the id again; this is the second lock, not the first.
    return { kind: "forward", target: `${API_BASE_URL}/api/tasks/${encodeURIComponent(id)}` };
  }

  const allowed = Object.prototype.hasOwnProperty.call(FORWARDED, path)
    ? FORWARDED[path]
    : undefined;
  if (!allowed) {
    return {
      kind: "reject",
      status: 404,
      error: `${path} is not one of the six routes the console forwards.`,
    };
  }

  const target = new URL(`${API_BASE_URL}${path}`);
  for (const name of allowed) {
    const value = url.searchParams.get(name);
    // An empty value reads as absent on the api's side too, so sending it is
    // legal and sending nothing is clearer.
    if (value) target.searchParams.set(name, value);
  }
  if (path === "/api/debt" && CONSOLE_PROJECT) {
    target.searchParams.set("project", CONSOLE_PROJECT);
  }
  return { kind: "forward", target: target.toString() };
}

function jsonResponse(body: { error: string }, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function describe(cause: unknown): string {
  if (cause instanceof Error) return `${cause.name}: ${cause.message}`;
  return String(cause);
}
