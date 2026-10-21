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
 * **It is a whitelist and not a proxy.** Twenty-one console paths map one-to-one
 * onto twenty-one api routes; anything else under `/api/` is a JSON 404 from the
 * console and never reaches the api. Query parameters are copied by name per route — the
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
 * deliberately not entries of `FORWARDED`, whose twenty-one routes, count and
 * rejection message describe the api alone.
 *
 * **Fifteen method and path pairs go to the board-service** (`docs/decisions.md`
 * ADR 136, ADR 157, ADR 190, ADR 195 for the two thread records, ADR 206
 * for the two project records, ADR 211 for the two epic actions, ADR 222
 * for the review verdict, and ADR 270 for the approve):
 * `POST /api/backlog`, `PATCH /api/tasks/<id>`, `POST /api/tasks/<id>/comments`,
 * `POST /api/tasks/<id>/accept`, `POST /api/tasks/<id>/verdict`,
 * `POST /api/tasks/<id>/approve`, `POST /api/threads` and
 * `PATCH /api/threads/<id>`, the three action routes
 * `POST /api/tasks/<id>/start`, `POST /api/actions/<id>/cancel` and
 * `POST /api/threads/<id>/turns`, and the two project-record writes
 * `POST /api/projects` and `PATCH /api/projects/<slug>`, and the two epic actions
 * `POST /api/epics/<id>/start` and `POST /api/epics/<id>/stop`, onto the same paths
 * there; the last eight carry a bearer. Four more, a new project's setup actions
 * `POST /api/projects/<slug>/{clone,init,keygen,check}` (ADR 212), go there
 * too with the same bearer (see `forwardBoard`). They are matched by method *and* path before the read-only
 * check, so every other method on those paths keeps the answer it had: a `GET`
 * of `/api/tasks/<id>` and of `/api/tasks/<id>/comments` is still the api's, a
 * `GET /api/threads` is still the api's list, a
 * `POST /api/tasks` is still the 405, a `GET /api/backlog` still the 404. They
 * are the only writes that are not the settings', so they carry the console's
 * CSRF checks, written at `forwardBoard`.
 *
 * **Five more go to the board-service for Web Push** (ADR 229, and ADR 232 for
 * the forward): `POST` and `DELETE /api/push/subscriptions`, which store and
 * drop this browser's subscription, carry the same bearer and the same CSRF
 * checks as the action routes, the body unchanged, and so does
 * `PUT /api/push/settings` (ADR 237, ADR 246), which saves which kinds are
 * pushed and the quiet hours; with the nineteen above they make twenty-two
 * writes. `GET /api/push/key`, the VAPID public key, and
 * `GET /api/push/settings` are reads of the board-service and not of the api:
 * no token, no query parameter, no header of the browser's
 * (`forwardBoardRead`). Every other method on these three paths keeps the
 * answer it had: the 405 for a write, the whitelist's 404 for a read.
 */
import {
  API_BASE_URL,
  API_TIMEOUT_MS,
  API_TOKEN,
  CONFIG_WRITER_TIMEOUT_MS,
  CONFIG_WRITER_TOKEN,
  CONFIG_WRITER_URL,
  CONSOLE_PROJECT,
  BOARD_ACTIONS_TOKEN,
  BOARD_SERVICE_TIMEOUT_MS,
  BOARD_SERVICE_URL,
} from "./server-env";

/** The list routes, and the browser parameters each one may carry. */
const FORWARDED: Record<string, readonly string[]> = {
  "/api/tasks": [],
  "/api/accounts": [],
  "/api/events": ["limit", "source_app", "since"],
  // `project` is not copied by name: it is checked as a slug, and falls back to
  // CONSOLE_PROJECT when absent (`PROJECT_SCOPED` below, ADR 210).
  "/api/debt": [],
  // `task_id` is copied by name like `since`, and an empty value reads as absent
  // on both sides. The api takes it as optional and the console always sends it:
  // nothing here polls the unfiltered form (`docs/decisions.md` ADR 27, ADR 28).
  "/api/phases": ["task_id"],
  // `project` is `/api/debt`'s: checked as a slug, the console's own when absent.
  // The search box on the Learnings screen filters rows the api already sent
  // (`docs/decisions.md` ADR 41, ADR 42, ADR 210).
  "/api/learnings": [],
  // The Roadmap screen's one read. `project` is `/api/debt`'s again: the api
  // resolves the project's `docs/plans/` from the slug
  // (`docs/plans/roadmap-view.md` item 4, T-072, ADR 210).
  "/api/roadmap": [],
  // The Approvals screen's command-request card (`docs/decisions.md` ADR 123).
  // Here `project` *is* the browser's: the api reads requests hive-wide, the way
  // `/api/roadmap` reads its tasks, and `?project=` only narrows them (ADR 122),
  // so it is a filter the screen picks and not the console's scope. `state` is
  // one of the api's four, and anything else is the api's 400.
  "/api/command-requests": ["project", "state"],
  // The Ready column's order, as task ids (`docs/decisions.md` ADR 133). The api
  // takes no parameter here and reads the hive, not a project, so nothing is
  // copied and no `?project=` is added.
  "/api/ready": [],
  // The action queue (`docs/decisions.md` ADR 142, T-103). Hive-wide like
  // `/api/ready`, so no `?project=` is added and none of the browser's is copied.
  "/api/actions": ["limit"],
  // The inbox (`docs/decisions.md` ADR 221, `docs/plans/notifications.md`
  // step 2). Hive-wide like `/api/ready`, and the api takes no parameter, so
  // nothing is copied and no `?project=` is added.
  "/api/notifications": [],
  // The thread list (`docs/decisions.md` ADR 177, ADR 184). Hive-wide like
  // `/api/actions`, and the api takes no parameter here at all, so nothing is
  // copied and no `?project=` is added. Only the `GET`: `POST /api/threads` is
  // the board service's (ADR 170), matched in `boardTarget` before this table
  // is read (ADR 195).
  "/api/threads": [],
  // The project records (ADR 209). Hive-wide: the records are the hive's, not a
  // checkout's, and the api takes no parameter here, so nothing is copied and no
  // `?project=` is added. Only the `GET`: `POST /api/projects` is the board
  // service's (ADR 203, ADR 206), matched in `boardTarget` first.
  "/api/projects": [],
};

/**
 * The project-scoped reads, on an api that may hold several checkouts, where
 * `?project=` is optional with one checkout and required with several —
 * `docs/decisions.md` ADR 24 for why the console sets it, ADR 41 part 2 for the
 * rule the api applies to it. The harness's own screens send none and get
 * `CONSOLE_PROJECT`; a project's screen under `/p/$slug/` sends its slug, which
 * crosses only when it is shaped like one (ADR 210, narrowing ADR 24's "never
 * from the browser" for these three).
 */
const PROJECT_SCOPED = new Set(["/api/debt", "/api/learnings", "/api/roadmap"]);

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
 * One task's comments: `TASK_USAGE`'s shape exactly, one segment and no query
 * parameter. The path is shared (ADR 136): a `GET` is the api's read, and a
 * `POST` is the board-service's `task_comment`, matched in `boardTarget`.
 */
const TASK_COMMENTS = /^\/api\/tasks\/([^/]+)\/comments$/;

/**
 * One thread's conversation log (`docs/decisions.md` ADR 180, ADR 184): one
 * segment, `TASK_USAGE`'s shape, with the two parameters the api takes. The id
 * is re-encoded like a task id and validated again by the api, which answers a
 * 400 for one the harness does not make.
 */
const THREAD_EVENTS = /^\/api\/threads\/([^/]+)\/events$/;

/** The parameters `THREAD_EVENTS` copies: the page size and the cursor. */
const THREAD_EVENT_PARAMETERS = ["limit", "since"] as const;

/**
 * The hook events of one task's phases (`docs/decisions.md` ADR 197), the
 * dock's execution view: `TASK_USAGE`'s shape, tested before `TASK_DETAIL`,
 * with `THREAD_EVENT_PARAMETERS`, the two the api takes.
 */
const TASK_EVENTS = /^\/api\/tasks\/([^/]+)\/events$/;

/**
 * One task branch's diff against its base (ADR 219), the Approvals screen's
 * review: `TASK_USAGE`'s shape, tested before `TASK_DETAIL`. Its `?project=` is
 * `PROJECT_SCOPED`'s: the browser's when shaped like a slug, since a task names
 * the project whose clone holds its branch, else the console's own.
 */
const TASK_DIFF = /^\/api\/tasks\/([^/]+)\/diff$/;

/** Accepting a backlog item: a `POST` to the board-service, never a read. */
const TASK_ACCEPT = /^\/api\/tasks\/([^/]+)\/accept$/;

/**
 * A person's verdict on a task under review (ADR 222, `docs/plans/review.md`
 * step 3): a `POST` to the board-service, never a read, with no bearer, as a
 * comment has none. The api has no route here, so a `GET` is its 404.
 */
const TASK_VERDICT = /^\/api\/tasks\/([^/]+)\/verdict$/;

/** Starting a task (ADR 142, ADR 157): a `POST` to the board-service, with the actions bearer. */
const TASK_START = /^\/api\/tasks\/([^/]+)\/start$/;

/**
 * A person's approve of a task under review (ADR 267, ADR 270): a `POST` to the
 * board-service with the actions bearer and a rebuilt body. A `GET` is the api's 404.
 */
const TASK_APPROVE = /^\/api\/tasks\/([^/]+)\/approve$/;

/** Cancelling an action: a `POST` to the board-service, with the actions bearer. */
const ACTION_CANCEL = /^\/api\/actions\/([^/]+)\/cancel$/;

/**
 * One action record by its id (ADR 216): the api's `GET`, one segment, no query
 * parameter, hive-wide like `/api/actions` so no `?project=` is added. The id is
 * checked against `ACTION_ID` here, so one the queue does not make is the
 * console's 404 and never crosses; `ACTION_CANCEL`'s two segments miss it.
 */
const ACTION_DETAIL = /^\/api\/actions\/([^/]+)$/;

/** The ids the queue makes: `dispatcher/actions.py`'s `ID_RE`, written again here. */
const ACTION_ID = /^a-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{4}$/;

/**
 * *Start epic* and *Stop epic* (ADR 207, ADR 211): `POST`s to the board-service,
 * with the actions bearer. A start's body is rebuilt as a task's is, a stop's is
 * `{}` as a cancel's is.
 */
const EPIC_START = /^\/api\/epics\/([^/]+)\/start$/;
const EPIC_STOP = /^\/api\/epics\/([^/]+)\/stop$/;

/**
 * Sending a turn to a thread (ADR 181, ADR 190): a `POST` to the board-service,
 * with the actions bearer. The path is shared with nothing else.
 */
const THREAD_TURNS = /^\/api\/threads\/([^/]+)\/turns$/;

/**
 * The thread ids the harness makes: `dispatcher/threads.py`'s `MAIN_ID` and
 * `_ORDINARY_ID`, written again here. A turn or a record `PATCH` for any other
 * id is the console's 404 and never crosses (ADR 190 part 1, ADR 195).
 */
const THREAD_ID = /^(?:main|th-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6})$/;

/** Proposing a backlog item: a `POST` to the board-service, never a read. */
const BACKLOG = "/api/backlog";

/**
 * Making a thread record (ADR 170, ADR 195): a `POST` to the board-service with
 * no bearer, since a record spends no quota. The `GET` is the api's list.
 */
const THREADS = "/api/threads";

/**
 * Changing one thread record (ADR 170, ADR 195): a `PATCH` to the
 * board-service with no bearer, the id checked against `THREAD_ID` as a turn's
 * is. No other method on the path is forwarded.
 */
const THREAD_RECORD = /^\/api\/threads\/([^/]+)$/;

/**
 * Making a project record (ADR 203, ADR 206): a `POST` to the board-service
 * with the actions bearer, which the route asks for. A `GET` is the api's list
 * of the records (ADR 209), forwarded from `FORWARDED`; every other method keeps
 * the answer it had.
 */
const PROJECTS = "/api/projects";

/**
 * Changing one project record (ADR 203, ADR 206): a `PATCH` to the
 * board-service with the actions bearer, the slug checked against
 * `PROJECT_SLUG`. A `GET` is the api's read of the one record (ADR 209), the
 * slug checked the same way in `resolveTarget`. No other method is forwarded.
 */
const PROJECT_RECORD = /^\/api\/projects\/([^/]+)$/;

/**
 * A new project's four setup actions (ADR 212): a `POST` to the board-service
 * with the actions bearer, as a record write is, the slug checked against
 * `PROJECT_SLUG` and the body unchanged (the route takes only `replace`, on a
 * keygen). No other method on these paths is forwarded.
 */
const PROJECT_SETUP = /^\/api\/projects\/([^/]+)\/(clone|init|keygen|check)$/;

/**
 * The slugs a project may have: `dispatcher/tasks.py`'s `_PROJECT_SLUG`, written
 * again here. A record `PATCH` for any other slug is the console's 404 and
 * never crosses, as a thread id that is not one the harness makes is.
 */
const PROJECT_SLUG = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

/**
 * Web Push (ADR 229, ADR 232): the VAPID public key, a `GET` of the
 * board-service, and this browser's subscription, a `POST` to store it and a
 * `DELETE` to drop it, both with the actions bearer the route asks for.
 */
const PUSH_KEY = "/api/push/key";
const PUSH_SUBSCRIPTIONS = "/api/push/subscriptions";

/**
 * Which kinds are pushed and the quiet hours (ADR 237, ADR 246): a `GET` of the
 * board-service, and a `PUT` with the actions bearer.
 */
const PUSH_SETTINGS = "/api/push/settings";

/** The words every failure body uses for the board-service: never its address. */
const BOARD = "the board-service";

/**
 * The largest body a board write carries across (ADR 136). The biggest thing the
 * tools take is a proposal's description and its sub-tasks, far under this; a
 * body over it is the console's 413 and never reaches the board-service.
 */
const MAX_BOARD_BODY_BYTES = 64 * 1024;

/**
 * The turn pair's cap (ADR 190 part 2). The route takes up to 20,000 characters
 * (`MAX_TURN_TEXT_CHARS`, code points), and `JSON.stringify` writes one code
 * point in at most six bytes (`\u0001`, a lone surrogate), so the largest text
 * the route accepts is 120,011 bytes with `{"text":""}`: over 64 KiB, under this.
 * The console never answers 413 for a text the route would take.
 */
const MAX_TURN_BODY_BYTES = 128 * 1024;

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
  | {
      kind: "forward";
      target: string;
      action?: "start" | "cancel" | "turn" | "project" | "push" | "approve";
    }
  | { kind: "reject"; status: number; error: string };

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

  // The push key is a read, but the board-service's and not the api's (ADR 232).
  if (request.method === "GET" && url.pathname === PUSH_KEY) return forwardBoardRead(PUSH_KEY);
  if (request.method === "GET" && url.pathname === PUSH_SETTINGS) {
    return forwardBoardRead(PUSH_SETTINGS);
  }

  // Also before the read-only check, and only for the twenty-two exact pairs: a method
  // or a path that is not one of them falls through to the answer it had.
  const board = boardTarget(request.method, url.pathname);
  if (board) return forwardBoard(request, url, board);

  if (request.method !== "GET") {
    // The api serves reads only: every route there is a `GET` and the service
    // writes nothing anywhere. A write that ever goes this way re-opens ADR 24's
    // last paragraph about the CSRF middleware rather than inheriting it. The
    // message still says "reads only" because it describes the api's paths: the
    // writes the console does forward are the settings' and the board's, both
    // dispatched above, so none of them reaches this line.
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

/**
 * Where on the board-service one of the twenty-two writes goes, or `null` for any
 * other method and path, which then gets the answer it had before ADR 136.
 * The id is decoded and re-encoded as `TASK_DETAIL`'s is, so a `%2f` stays one
 * segment for the board-service to refuse; an undecodable one is a 404 here.
 * The action routes, the two epic ones too, are `POST` only and carry `action` (ADR 157, ADR 190).
 * A thread id that is not one the harness makes is a 404 here too, for a turn
 * and for a record's `PATCH`, and so is a project slug the harness would not
 * take. The two project-record writes carry `action` "project" (ADR 206). The
 * two epic actions carry `action` "start" and "cancel", since their bodies are
 * a task start's and a cancel's (ADR 211). The two push-subscription writes
 * and the settings `PUT` carry `action` "push" (ADR 232, ADR 246).
 */
function boardTarget(method: string, path: string): Resolution | null {
  if (method === "PUT" && path === PUSH_SETTINGS) {
    return { kind: "forward", target: `${BOARD_SERVICE_URL}${PUSH_SETTINGS}`, action: "push" };
  }
  if ((method === "POST" || method === "DELETE") && path === PUSH_SUBSCRIPTIONS) {
    return { kind: "forward", target: `${BOARD_SERVICE_URL}${PUSH_SUBSCRIPTIONS}`, action: "push" };
  }
  if (method === "POST" && path === BACKLOG) {
    return { kind: "forward", target: `${BOARD_SERVICE_URL}${BACKLOG}` };
  }
  if (method === "POST" && path === THREADS) {
    return { kind: "forward", target: `${BOARD_SERVICE_URL}${THREADS}` };
  }
  if (method === "POST" && path === PROJECTS) {
    return { kind: "forward", target: `${BOARD_SERVICE_URL}${PROJECTS}`, action: "project" };
  }
  if (method === "PATCH") {
    const project = PROJECT_RECORD.exec(path);
    if (project) {
      const slug = projectSlug(path, project[1]!);
      if (typeof slug !== "string") return slug;
      return {
        kind: "forward",
        target: `${BOARD_SERVICE_URL}${PROJECTS}/${encodeURIComponent(slug)}`,
        action: "project",
      };
    }
    const record = THREAD_RECORD.exec(path);
    if (record) {
      const id = threadId(path, record[1]!);
      if (typeof id !== "string") return id;
      return {
        kind: "forward",
        target: `${BOARD_SERVICE_URL}${THREADS}/${encodeURIComponent(id)}`,
      };
    }
  }
  if (method === "POST") {
    const setup = PROJECT_SETUP.exec(path);
    if (setup) {
      const slug = projectSlug(path, setup[1]!);
      if (typeof slug !== "string") return slug;
      return {
        kind: "forward",
        target: `${BOARD_SERVICE_URL}${PROJECTS}/${encodeURIComponent(slug)}/${setup[2]!}`,
        action: "project",
      };
    }
    const turns = THREAD_TURNS.exec(path);
    if (turns) {
      const id = threadId(path, turns[1]!);
      if (typeof id !== "string") return id;
      return {
        kind: "forward",
        target: `${BOARD_SERVICE_URL}/api/threads/${encodeURIComponent(id)}/turns`,
        action: "turn",
      };
    }
    const actionRoutes = [
      { pattern: TASK_START, route: "tasks", tail: "start", what: "task", action: "start" },
      {
        pattern: TASK_APPROVE,
        route: "tasks",
        tail: "approve",
        what: "task",
        action: "approve",
      },
      {
        pattern: ACTION_CANCEL,
        route: "actions",
        tail: "cancel",
        what: "action",
        action: "cancel",
      },
      { pattern: EPIC_START, route: "epics", tail: "start", what: "epic", action: "start" },
      { pattern: EPIC_STOP, route: "epics", tail: "stop", what: "epic", action: "cancel" },
    ] as const;
    for (const { pattern, route, tail, what, action } of actionRoutes) {
      const hit = pattern.exec(path);
      if (!hit) continue;
      let id: string;
      try {
        id = decodeURIComponent(hit[1]!);
      } catch {
        return { kind: "reject", status: 404, error: `${path} is not a readable ${what} id.` };
      }
      return {
        kind: "forward",
        target: `${BOARD_SERVICE_URL}/api/${route}/${encodeURIComponent(id)}/${tail}`,
        action,
      };
    }
  }
  const pattern =
    method === "PATCH"
      ? TASK_DETAIL
      : method === "POST"
        ? TASK_COMMENTS.test(path)
          ? TASK_COMMENTS
          : TASK_ACCEPT.test(path)
            ? TASK_ACCEPT
            : TASK_VERDICT.test(path)
              ? TASK_VERDICT
              : null
        : null;
  const match = pattern?.exec(path);
  if (!match) return null;
  let id: string;
  try {
    id = decodeURIComponent(match[1]!);
  } catch {
    return { kind: "reject", status: 404, error: `${path} is not a readable task id.` };
  }
  const tail =
    pattern === TASK_COMMENTS
      ? "/comments"
      : pattern === TASK_ACCEPT
        ? "/accept"
        : pattern === TASK_VERDICT
          ? "/verdict"
          : "";
  return {
    kind: "forward",
    target: `${BOARD_SERVICE_URL}/api/tasks/${encodeURIComponent(id)}${tail}`,
  };
}

/**
 * A thread id from a path segment, decoded and checked against `THREAD_ID`, or
 * the 404 that names the path: one undecodable, or not an id the harness makes.
 */
function threadId(path: string, raw: string): string | Resolution {
  let id: string;
  try {
    id = decodeURIComponent(raw);
  } catch {
    return { kind: "reject", status: 404, error: `${path} is not a readable thread id.` };
  }
  if (!THREAD_ID.test(id)) {
    return { kind: "reject", status: 404, error: `${path} names no thread the harness makes.` };
  }
  return id;
}

/**
 * A project slug from a path segment, decoded and checked against
 * `PROJECT_SLUG`, or the 404 that names the path: one undecodable, or not a
 * slug the harness takes.
 */
function projectSlug(path: string, raw: string): string | Resolution {
  let slug: string;
  try {
    slug = decodeURIComponent(raw);
  } catch {
    return { kind: "reject", status: 404, error: `${path} is not a readable project slug.` };
  }
  if (!PROJECT_SLUG.test(slug)) {
    return {
      kind: "reject",
      status: 404,
      error: `${path} names no project slug the harness takes.`,
    };
  }
  return slug;
}

/**
 * The board-service's answer to one of its twenty-two writes (`docs/decisions.md`
 * ADR 136).
 *
 * On CSRF. These are the first writes here a cross-site page could send without
 * a preflight being refused first, since `POST` is one a form can make, so the
 * console checks the request itself, in this order:
 * - `Content-Type` must be `application/json` (415). A form can only send three
 *   other types, and a cross-site `fetch` with this one is preflighted, and the
 *   preflight is refused: no `OPTIONS` is answered with an `Access-Control-*`.
 * - `Sec-Fetch-Site`, when the browser sends it, must be `same-origin` (403).
 * - `Origin`, when it is sent, must be this request's own origin (403).
 * The two headers are checked only when present, because a browser that sends
 * neither is one the content type already holds; a header that is there and
 * says another site is the refusal.
 *
 * Nothing of the browser's request crosses but the body, bounded to
 * `MAX_BOARD_BODY_BYTES` (413; `MAX_TURN_BODY_BYTES` for a turn), under the console's own
 * `Content-Type: application/json`. `Origin` above all: the board-service
 * refuses any request that carries one (its guard against a browser), and the
 * console's server half is not a browser. No query string: the board-service
 * refuses one.
 *
 * The four backlog writes and the two thread-record writes carry no token: the
 * board-service takes none there (ADR 170, 7). A record's body crosses
 * unchanged, as a turn's does: the route refuses an unknown field or a wrong
 * type itself.
 * The three action routes (ADR 157, on ADR 151; the turn is ADR 190) carry `Authorization: Bearer
 * BOARD_ACTIONS_TOKEN`, and after the CSRF checks above, in this order: 503 if
 * the token is unset, 503 if a start has no `CONSOLE_PROJECT`, then 413. A start
 * body may hold only `note`, `floor` and `limit` (else 400, `project` included:
 * the project is the console's, set here); the upstream body is rebuilt from
 * them, the route checking the floor's and the limit's values. A cancel sends `{}`.
 * An approve (ADR 270) takes the token's 503, then the 413 cap, then its body: only
 * `project`, `commit` and `text` (else 400), `project` shaped like a slug (400) and
 * absent meaning `CONSOLE_PROJECT`, with neither a 503 naming it.
 * *Start epic* and *Stop epic* (ADR 211) are a start and a cancel here: the same
 * checks, the same rebuilt body and the same `{}`.
 * A turn's body crosses unchanged: the route validates `text` itself.
 * The two project-record writes (ADR 203, ADR 206) carry the same bearer, since
 * the route asks for it, and nothing else of the action routes: no
 * `CONSOLE_PROJECT` (the record names its own slug), the 64 KiB cap, and the
 * body unchanged, the route refusing an unknown field or a wrong type itself.
 * The two push-subscription writes (ADR 232) are the project writes' twins:
 * the bearer, the 64 KiB cap and the body unchanged, the route checking the
 * subscription whole. The settings `PUT` (ADR 246) is their twin: the route
 * validates the kinds and the quiet hours itself and answers a 400 `{error}`.
 * The token is in no response and no log.
 */
async function forwardBoard(request: Request, url: URL, resolved: Resolution): Promise<Response> {
  if (resolved.kind === "reject") {
    return jsonResponse({ error: resolved.error }, resolved.status);
  }

  const mediaType = (request.headers.get("content-type") ?? "").split(";")[0]!.trim().toLowerCase();
  if (mediaType !== "application/json") {
    return jsonResponse({ error: `A write to ${BOARD} must be sent as application/json.` }, 415);
  }
  const site = request.headers.get("sec-fetch-site");
  if (site !== null && site !== "same-origin") {
    return jsonResponse(
      { error: `A write to ${BOARD} must come from the console's own pages, not ${site}.` },
      403,
    );
  }
  const origin = request.headers.get("origin");
  if (origin !== null && origin !== url.origin) {
    return jsonResponse(
      { error: `A write to ${BOARD} must come from the console's own origin.` },
      403,
    );
  }

  const action = resolved.action;
  if (action && !BOARD_ACTIONS_TOKEN) {
    return jsonResponse(
      {
        error:
          `BOARD_ACTIONS_TOKEN is not set, so the console cannot authenticate to ${BOARD}'s ${action === "project" || action === "push" ? action : "action"} routes. ` +
          "Set it to the board-service's token and restart the console.",
      },
      503,
    );
  }
  if (action === "start" && !CONSOLE_PROJECT) {
    return jsonResponse(
      {
        error:
          "CONSOLE_PROJECT is not set, so the console cannot say which project a start is for. " +
          "Set it and restart the console.",
      },
      503,
    );
  }

  const cap = action === "turn" ? MAX_TURN_BODY_BYTES : MAX_BOARD_BODY_BYTES;
  const read = await readBounded(request, cap);
  if (read === null) {
    return jsonResponse({ error: `A write to ${BOARD} takes at most ${cap} bytes.` }, 413);
  }

  let body: ArrayBuffer | string = read;
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (action) {
    headers["Authorization"] = `Bearer ${BOARD_ACTIONS_TOKEN}`;
    if (action === "cancel") {
      body = "{}";
    } else if (action === "start") {
      const start = startBody(read);
      if ("error" in start) return jsonResponse(start, 400);
      body = JSON.stringify({ project: CONSOLE_PROJECT, ...start });
    }
  }

  if (action === "approve") {
    const approve = approveBody(read);
    if ("error" in approve) return jsonResponse(approve, 400);
    const project = approve.project ?? CONSOLE_PROJECT;
    if (!project) {
      return jsonResponse(
        {
          error:
            "CONSOLE_PROJECT is not set, so the console cannot say which project an approve is for. " +
            "Set it and restart the console.",
        },
        503,
      );
    }
    body = JSON.stringify({ project, commit: approve.commit, text: approve.text });
  }

  let upstream: Response;
  try {
    upstream = await fetch(resolved.target, {
      method: request.method,
      headers,
      body,
      signal: AbortSignal.timeout(BOARD_SERVICE_TIMEOUT_MS),
    });
  } catch (cause) {
    // The address only in the server log, as for the api and the writer.
    // 504 for the timeout, 502 for everything else: the console is the gateway
    // and the board-service is what did not answer.
    const why = describe(cause);
    console.error(`[forward] ${BOARD_SERVICE_URL} did not answer: ${why}`);
    if (isTimeout(cause)) {
      return jsonResponse(
        { error: `${capitalise(BOARD)} did not answer within ${BOARD_SERVICE_TIMEOUT_MS} ms.` },
        504,
      );
    }
    return jsonResponse({ error: `${capitalise(BOARD)} could not be reached.` }, 502);
  }

  // The board-service's status (201 for something made), body and content type,
  // as the api's are passed back.
  const answer = await upstream.arrayBuffer();
  const contentType = upstream.headers.get("content-type");
  return new Response(answer, {
    status: upstream.status,
    headers: contentType ? { "content-type": contentType } : {},
  });
}

/**
 * The board-service's answer to `GET /api/push/key` (ADR 229, ADR 232): its
 * VAPID public key, or the 404 that says push is off there; and to
 * `GET /api/push/settings` (ADR 237, ADR 246): the kinds and the quiet hours.
 * Nothing of the browser's request crosses, not even its query string, which
 * the routes refuse; both are open reads, so no token goes either. A failure to
 * reach the board-service is its 502 or 504, as for the writes.
 */
async function forwardBoardRead(path: string): Promise<Response> {
  let upstream: Response;
  try {
    upstream = await fetch(`${BOARD_SERVICE_URL}${path}`, {
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(BOARD_SERVICE_TIMEOUT_MS),
    });
  } catch (cause) {
    const why = describe(cause);
    console.error(`[forward] ${BOARD_SERVICE_URL} did not answer: ${why}`);
    if (isTimeout(cause)) {
      return jsonResponse(
        { error: `${capitalise(BOARD)} did not answer within ${BOARD_SERVICE_TIMEOUT_MS} ms.` },
        504,
      );
    }
    return jsonResponse({ error: `${capitalise(BOARD)} could not be reached.` }, 502);
  }
  const answer = await upstream.arrayBuffer();
  const contentType = upstream.headers.get("content-type");
  return new Response(answer, {
    status: upstream.status,
    headers: contentType ? { "content-type": contentType } : {},
  });
}

/**
 * A start's fields from the browser's body, or `{ error }` for a 400: `note`, a
 * string or null (absent is null), and `floor` and `limit` (ADR 227, ADR 230),
 * passed on only when present, their values checked by the board-service's
 * route. Nothing else may be present; `project` is the console's (ADR 157
 * part 1), so a browser one is refused rather than ignored.
 */
const START_FIELDS = new Set(["note", "floor", "limit"]);

type StartFields = { note: string | null; floor?: unknown; limit?: unknown };

function startBody(bytes: ArrayBuffer): StartFields | { error: string } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    return { error: "A start's body must be a JSON object." };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return { error: "A start's body must be a JSON object." };
  }
  const fields = parsed as Record<string, unknown>;
  for (const key of Object.keys(fields)) {
    if (START_FIELDS.has(key)) continue;
    return {
      error:
        key === "project"
          ? "A start's body may not name a project: the project is the console's."
          : `A start's body may hold only a note, a floor and a limit, not ${JSON.stringify(key)}.`,
    };
  }
  const note = fields["note"];
  if (note !== undefined && note !== null && typeof note !== "string") {
    return { error: "A start's note must be a string." };
  }
  const start: StartFields = { note: note ?? null };
  if (fields["floor"] !== undefined) start.floor = fields["floor"];
  if (fields["limit"] !== undefined) start.limit = fields["limit"];
  return start;
}

/**
 * An approve's fields from the browser's body, or `{ error }` for a 400 (ADR
 * 270): `project`, `commit` and `text` and nothing else. `project`, when
 * present, must be a string shaped like a slug; `commit` and `text` cross as
 * given, the route checking them.
 */
const APPROVE_FIELDS = new Set(["project", "commit", "text"]);

type ApproveFields = { project?: string; commit?: unknown; text?: unknown };

function approveBody(bytes: ArrayBuffer): ApproveFields | { error: string } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    return { error: "An approve's body must be a JSON object." };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return { error: "An approve's body must be a JSON object." };
  }
  const fields = parsed as Record<string, unknown>;
  for (const key of Object.keys(fields)) {
    if (APPROVE_FIELDS.has(key)) continue;
    return {
      error: `An approve's body may hold only a project, a commit and a text, not ${JSON.stringify(key)}.`,
    };
  }
  const approve: ApproveFields = {};
  const project = fields["project"];
  if (project !== undefined) {
    if (typeof project !== "string" || !PROJECT_SLUG.test(project)) {
      return { error: "An approve's project must be a slug the harness takes." };
    }
    approve.project = project;
  }
  if (fields["commit"] !== undefined) approve.commit = fields["commit"];
  if (fields["text"] !== undefined) approve.text = fields["text"];
  return approve;
}

/**
 * The request's body, or `null` once it passes `limit` bytes. Read as a stream
 * and stopped at the limit, so a large body is not buffered whole to be refused.
 * A declared `Content-Length` over the limit is refused before any of it is read.
 */
async function readBounded(request: Request, limit: number): Promise<ArrayBuffer | null> {
  const declared = Number(request.headers.get("content-length"));
  if (Number.isFinite(declared) && declared > limit) return null;
  if (!request.body) return new ArrayBuffer(0);
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > limit) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const out = new ArrayBuffer(size);
  const view = new Uint8Array(out);
  let at = 0;
  for (const chunk of chunks) {
    view.set(chunk, at);
    at += chunk.byteLength;
  }
  return out;
}

function resolveTarget(url: URL): Resolution {
  const path = url.pathname;

  const comments = TASK_COMMENTS.exec(path);
  if (comments) {
    let id: string;
    try {
      id = decodeURIComponent(comments[1]!);
    } catch {
      return { kind: "reject", status: 404, error: `${path} is not a readable task id.` };
    }
    return {
      kind: "forward",
      target: `${API_BASE_URL}/api/tasks/${encodeURIComponent(id)}/comments`,
    };
  }

  const events = THREAD_EVENTS.exec(path);
  if (events) {
    let id: string;
    try {
      id = decodeURIComponent(events[1]!);
    } catch {
      return { kind: "reject", status: 404, error: `${path} is not a readable thread id.` };
    }
    const target = new URL(`${API_BASE_URL}/api/threads/${encodeURIComponent(id)}/events`);
    for (const name of THREAD_EVENT_PARAMETERS) {
      const value = url.searchParams.get(name);
      if (value) target.searchParams.set(name, value);
    }
    return { kind: "forward", target: target.toString() };
  }

  const taskEvents = TASK_EVENTS.exec(path);
  if (taskEvents) {
    let id: string;
    try {
      id = decodeURIComponent(taskEvents[1]!);
    } catch {
      return { kind: "reject", status: 404, error: `${path} is not a readable task id.` };
    }
    const target = new URL(`${API_BASE_URL}/api/tasks/${encodeURIComponent(id)}/events`);
    for (const name of THREAD_EVENT_PARAMETERS) {
      const value = url.searchParams.get(name);
      if (value) target.searchParams.set(name, value);
    }
    return { kind: "forward", target: target.toString() };
  }

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

  const diff = TASK_DIFF.exec(path);
  if (diff) {
    let id: string;
    try {
      id = decodeURIComponent(diff[1]!);
    } catch {
      return { kind: "reject", status: 404, error: `${path} is not a readable task id.` };
    }
    const target = new URL(`${API_BASE_URL}/api/tasks/${encodeURIComponent(id)}/diff`);
    const scoped = scopeProject(url, target);
    if (scoped) return scoped;
    return { kind: "forward", target: target.toString() };
  }

  // One project record (ADR 209): the slug checked as a `PATCH`'s is, so one the
  // harness does not take is the console's 404 and never crosses.
  const record = PROJECT_RECORD.exec(path);
  if (record) {
    const slug = projectSlug(path, record[1]!);
    if (typeof slug !== "string") return slug;
    return { kind: "forward", target: `${API_BASE_URL}${PROJECTS}/${encodeURIComponent(slug)}` };
  }

  // One action record (ADR 216): an id the queue does not make is the console's
  // 404, as a thread id is, and nothing of the browser's query crosses.
  const action = ACTION_DETAIL.exec(path);
  if (action) {
    let id: string;
    try {
      id = decodeURIComponent(action[1]!);
    } catch {
      return { kind: "reject", status: 404, error: `${path} is not a readable action id.` };
    }
    if (!ACTION_ID.test(id)) {
      return { kind: "reject", status: 404, error: `${path} names no action id the queue makes.` };
    }
    return { kind: "forward", target: `${API_BASE_URL}/api/actions/${id}` };
  }

  const detail = TASK_DETAIL.exec(path);
  if (detail) {
    const raw = detail[1]!;
    let id: string;
    try {
      id = decodeURIComponent(raw);
    } catch {
      // `%zz` and friends. A path the console cannot read is not one of the twenty-one.
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
      error: `${path} is not one of the twenty-one routes the console forwards.`,
    };
  }

  const target = new URL(`${API_BASE_URL}${path}`);
  for (const name of allowed) {
    const value = url.searchParams.get(name);
    // An empty value reads as absent on the api's side too, so sending it is
    // legal and sending nothing is clearer.
    if (value) target.searchParams.set(name, value);
  }
  if (PROJECT_SCOPED.has(path)) {
    // A screen under `/p/$slug/` names its project; every other screen names
    // none and reads the console's own. A slug that is not one the harness
    // makes never crosses: it is the console's 400, not a fall-back to
    // `CONSOLE_PROJECT`, which would answer another project's rows under this
    // one's name. Whether the slug has a clone is the api's 404. ADR 210.
    const scoped = scopeProject(url, target);
    if (scoped) return scoped;
  }
  return { kind: "forward", target: target.toString() };
}

/**
 * Sets a project-scoped read's `?project=` on `target`: the browser's when it is
 * shaped like a slug, the console's own when absent. A refusal for one that is
 * not, or `null` once it is set.
 */
function scopeProject(url: URL, target: URL): Resolution | null {
  const asked = url.searchParams.get("project");
  if (asked) {
    if (!PROJECT_SLUG.test(asked)) {
      return { kind: "reject", status: 400, error: `${asked} is not a project slug.` };
    }
    target.searchParams.set("project", asked);
  } else if (CONSOLE_PROJECT) {
    target.searchParams.set("project", CONSOLE_PROJECT);
  }
  return null;
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
