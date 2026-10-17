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
 * **It is a whitelist and not a proxy.** Eight console paths map one-to-one onto
 * the eight api routes; anything else under `/api/` is a JSON 404 from the console
 * and never reaches the api. Query parameters are copied by name per route — the
 * same posture `observability/api/app.py:_reject_unknown_parameters` takes on the
 * other side, so a parameter the console did not mean to send cannot come back as
 * a 400 it did not expect. `?project=` is added from the console's own
 * configuration and never from the browser.
 *
 * Every path through this function ends in a JSON body, including the ones where
 * the api never answered, which is what lets `client.ts` have one error shape.
 *
 * **One prefix is not the api's.** `/api/settings` and everything under
 * `/api/settings/` go to the config writer instead (`docs/decisions.md` ADR 112):
 * another upstream, another token, another timeout, and the only paths here that
 * accept a write. They are dispatched before the api's read-only check and are
 * deliberately not entries of `FORWARDED`, whose eight routes, count and
 * rejection message describe the api alone.
 */
import {
  API_BASE_URL,
  API_TIMEOUT_MS,
  API_TOKEN,
  CONFIG_WRITER_TIMEOUT_MS,
  CONFIG_WRITER_TOKEN,
  CONFIG_WRITER_URL,
  CONSOLE_PROJECT,
} from "./server-env";

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
  // `project` is absent here too, and for the same reason as `/api/debt`'s: the
  // slug is the console's own configuration and the search box on the Learnings
  // screen filters rows the api already sent (`docs/decisions.md` ADR 41, ADR 42).
  "/api/learnings": [],
};

/**
 * The routes whose `?project=` comes from `CONSOLE_PROJECT` and never from the
 * browser. Both are project-scoped reads on an api that may hold several
 * checkouts, where the parameter is optional for one and required for two —
 * `docs/decisions.md` ADR 24 for why it is set here, ADR 41 part 2 for the rule
 * the api applies to it.
 */
const PROJECT_SCOPED = new Set(["/api/debt", "/api/learnings"]);

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

/**
 * The usage route of one task: its own match, tested before `TASK_DETAIL` and
 * with the same one-segment id. It takes no query parameter, so none is copied
 * (`docs/decisions.md` ADR 54); `/api/tasks/a/usage/b` and `/api/tasks/a/phases`
 * are not this route.
 */
const TASK_USAGE = /^\/api\/tasks\/([^/]+)\/usage$/;

/**
 * The settings collection: `GET` only, onto the writer's `GET /settings`
 * (`docs/decisions.md` ADR 112 part 1 and part 4).
 */
const SETTINGS = "/api/settings";

/**
 * One setting: `PUT` and `DELETE` only, exactly one segment, `TASK_DETAIL`'s
 * shape and for `TASK_DETAIL`'s reasons — decoded, then re-encoded into the
 * writer's path, so a `%2f` crosses as part of one key for the writer to reject
 * as unknown rather than as a second segment. `/api/settings/` (an empty
 * segment) and `/api/settings/a/b` miss it and are the prefix's 404.
 */
const SETTINGS_KEY = /^\/api\/settings\/([^/]+)$/;

/** The words every failure body uses for the writer: the service, never its address. */
const WRITER = "the config-writer service";

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

  // Before the api's read-only check below, because this prefix is the one place
  // a `PUT` or a `DELETE` is legal. Everything under it is this branch's answer,
  // so a settings path can never fall through to the api's whitelist.
  if (url.pathname === SETTINGS || url.pathname.startsWith(`${SETTINGS}/`)) {
    return forwardSettings(request, url);
  }

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

/**
 * The config writer's answer to a settings request (`docs/decisions.md` ADR 112).
 *
 * The order is the ADR's: shape, then method, then token. A request the console
 * would never send — a second segment, a `POST` — is the same 404 or 405 on a
 * console with no `CONFIG_WRITER_TOKEN` as on one with it, so a test or an
 * operator reading the answer learns about the request and not about the
 * deployment.
 *
 * On CSRF (ADR 112 part 7, answering ADR 24's *Consequences*): the writes are
 * `PUT` and `DELETE`, which a cross-site page can only send after a preflight
 * `OPTIONS`. That preflight is a 405 here with no `Access-Control-*` header, so
 * the browser refuses the write before sending it; `POST`, which a form can send
 * cross-site, is a 405 on both paths. `front/src/start.ts`'s middleware stays as
 * it is. Accepting `POST` here, or answering a preflight, re-opens that part.
 */
async function forwardSettings(request: Request, url: URL): Promise<Response> {
  const path = url.pathname;
  const method = request.method;
  let target: string;
  let carriesBody = false;

  if (path === SETTINGS) {
    if (method !== "GET") {
      return jsonResponse(
        { error: `${method} is not served on ${SETTINGS}: the settings are read with GET.` },
        405,
      );
    }
    target = `${CONFIG_WRITER_URL}/settings`;
  } else {
    const match = SETTINGS_KEY.exec(path);
    if (!match) {
      return jsonResponse({ error: `${path} is not a settings route the console forwards.` }, 404);
    }
    let key: string;
    try {
      key = decodeURIComponent(match[1]!);
    } catch {
      return jsonResponse({ error: `${path} is not a readable settings key.` }, 404);
    }
    if (method !== "PUT" && method !== "DELETE") {
      return jsonResponse(
        {
          error: `${method} is not served on a setting: one is saved with PUT and reset with DELETE.`,
        },
        405,
      );
    }
    target = `${CONFIG_WRITER_URL}/settings/${encodeURIComponent(key)}`;
    carriesBody = true;
  }

  if (!CONFIG_WRITER_TOKEN) {
    // Not a refusal to boot, unlike `API_TOKEN`: every other screen reads the api
    // and keeps working (ADR 112 part 3). The variable is named because setting it
    // is the whole fix.
    return jsonResponse(
      {
        error:
          `CONFIG_WRITER_TOKEN is not set, so the console cannot authenticate to ${WRITER}. ` +
          "Set it to the writer's token and restart the console.",
      },
      503,
    );
  }

  // ADR 112 part 5. `X-Actor` is fixed because the console has no signed-in
  // person yet (ADR 75 part 3), and the writer refuses a write without one. The
  // body's bytes cross unchanged but under this side's own content type, since
  // Flask reads a body only under a JSON one and the browser's header is not
  // something that crosses. No query string, cookie or other header does either.
  const headers: Record<string, string> = {
    Authorization: `Bearer ${CONFIG_WRITER_TOKEN}`,
    "X-Actor": "operator",
    Accept: "application/json",
  };
  let body: ArrayBuffer | undefined;
  if (carriesBody) {
    headers["Content-Type"] = "application/json";
    body = await request.arrayBuffer();
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method,
      headers,
      ...(body === undefined ? {} : { body }),
      signal: AbortSignal.timeout(CONFIG_WRITER_TIMEOUT_MS),
    });
  } catch (cause) {
    // ADR 112 part 6: 504 for the timeout, 503 for everything else, and the
    // address only in the server log, for the reason the api's branch gives. No
    // part of the cause reaches the body either, since a runtime's connection
    // error can carry the url it failed on.
    const why = describe(cause);
    console.error(`[forward] ${CONFIG_WRITER_URL} did not answer: ${why}`);
    if (isTimeout(cause)) {
      return jsonResponse(
        { error: `${capitalise(WRITER)} did not answer within ${CONFIG_WRITER_TIMEOUT_MS} ms.` },
        504,
      );
    }
    return jsonResponse({ error: `${capitalise(WRITER)} could not be reached.` }, 503);
  }

  // The writer's status, body and content type, as the api's are passed back.
  const answer = await upstream.arrayBuffer();
  const contentType = upstream.headers.get("content-type");
  return new Response(answer, {
    status: upstream.status,
    headers: contentType ? { "content-type": contentType } : {},
  });
}

/** `AbortSignal.timeout` rejects with a `TimeoutError`; an abort of any other kind is not one. */
function isTimeout(cause: unknown): boolean {
  return typeof cause === "object" && cause !== null && "name" in cause
    ? (cause as { name: unknown }).name === "TimeoutError"
    : false;
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function resolveTarget(url: URL): Resolution {
  const path = url.pathname;

  const usage = TASK_USAGE.exec(path);
  if (usage) {
    let id: string;
    try {
      id = decodeURIComponent(usage[1]!);
    } catch {
      return { kind: "reject", status: 404, error: `${path} is not a readable task id.` };
    }
    return {
      kind: "forward",
      target: `${API_BASE_URL}/api/tasks/${encodeURIComponent(id)}/usage`,
    };
  }

  const detail = TASK_DETAIL.exec(path);
  if (detail) {
    const raw = detail[1]!;
    let id: string;
    try {
      id = decodeURIComponent(raw);
    } catch {
      // `%zz` and friends. A path the console cannot read is not one of the eight.
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
      error: `${path} is not one of the eight routes the console forwards.`,
    };
  }

  const target = new URL(`${API_BASE_URL}${path}`);
  for (const name of allowed) {
    const value = url.searchParams.get(name);
    // An empty value reads as absent on the api's side too, so sending it is
    // legal and sending nothing is clearer.
    if (value) target.searchParams.set(name, value);
  }
  if (PROJECT_SCOPED.has(path) && CONSOLE_PROJECT) {
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
