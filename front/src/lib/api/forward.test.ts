// @vitest-environment node
/**
 * The bearer forward's first tests. This is the only module in `front/` that
 * holds the api token, and the whitelist in it is the whole of what stands
 * between a browser and `observability/api/` — `docs/decisions.md` ADR 24. What
 * a typecheck cannot see here is the shape of the url that leaves: which
 * parameters crossed, whose `?project=` it was, and that an id stayed one
 * segment. So the subject of nearly every test below is
 * `fetch.mock.calls[0]`, and the api is a stub.
 *
 * Server-only, hence the docblock above: `server-env.ts` throws on import when
 * there is a `window`, which is the mechanical half of ADR 15's rule about the
 * token. It is mocked rather than fed an environment, so the base url and the
 * project are fixed values a test can assert against.
 */
import { describe, expect, it, vi } from "vitest";
import { forwardApiRequest } from "./forward";

vi.mock("./server-env", () => ({
  API_BASE_URL: "http://api.test:8789",
  API_TOKEN: "test-token",
  CONSOLE_PROJECT: "ia-harness",
  API_TIMEOUT_MS: 5000,
  CONFIG_WRITER_URL: "http://writer.test:8791",
  CONFIG_WRITER_TOKEN: "writer-token",
  CONFIG_WRITER_TIMEOUT_MS: 5000,
  BOARD_SERVICE_URL: "http://board.test:8792",
  BOARD_SERVICE_TIMEOUT_MS: 5000,
  BOARD_ACTIONS_TOKEN: "actions-token",
}));

const BASE = "http://api.test:8789";
const WRITER = "http://writer.test:8791";
const BOARD = "http://board.test:8792";

function get(path: string, init?: RequestInit): Request {
  return new Request(`http://console.test${path}`, init);
}

/** A fresh `Response` per call: `forwardApiRequest` reads the body, once. */
function stubApi(body = '{"ok":true}', status = 200, contentType = "application/json") {
  const headers = { "content-type": contentType };
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async () => new Response(body, { status, headers }));
}

/** The url the forward asked the api for. */
function target(fetchMock: ReturnType<typeof stubApi>): string {
  expect(fetchMock).toHaveBeenCalledTimes(1);
  return String(fetchMock.mock.calls[0]![0]);
}

describe("requests the forward does not own", () => {
  it("leaves a page to SSR", async () => {
    const fetchMock = stubApi();
    expect(await forwardApiRequest(get("/tasks"))).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("answers a write itself, without asking the api", async () => {
    const fetchMock = stubApi();
    const answer = await forwardApiRequest(get("/api/tasks", { method: "POST" }));
    expect(answer?.status).toBe(405);
    expect(await answer?.json()).toEqual({
      error: "POST is not served here: the console forwards reads only.",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("the whitelist", () => {
  it("forwards a list route", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(get("/api/tasks"));
    expect(target(fetchMock)).toBe(`${BASE}/api/tasks`);
  });

  it("sends its own credential and nothing of the browser's request", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(
      get("/api/accounts", {
        headers: { cookie: "session=whatever", authorization: "Bearer from-the-browser" },
      }),
    );
    expect(fetchMock.mock.calls[0]![1]!.headers).toEqual({
      Authorization: "Bearer test-token",
      Accept: "application/json",
    });
  });

  // `/api/sessions` and not `/api/threads`, which was the example until T-111
  // forwarded it (ADR 184): the sessions draft reads no api route, so this is a
  // path the console refuses until a task gives it one.
  it("404s a path it does not serve, without asking the api", async () => {
    const fetchMock = stubApi();
    const answer = await forwardApiRequest(get("/api/sessions"));
    expect(answer?.status).toBe(404);
    expect(await answer?.json()).toEqual({
      error: "/api/sessions is not one of the twenty-one routes the console forwards.",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("copies the parameters a route names and drops the rest", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(get("/api/events?limit=5&source_app=agent-cuenta1&since=7&bogus=1"));
    const sent = new URL(target(fetchMock)).searchParams;
    expect(sent.get("limit")).toBe("5");
    expect(sent.get("source_app")).toBe("agent-cuenta1");
    expect(sent.get("since")).toBe("7");
    expect(sent.has("bogus")).toBe(false);
  });

  it("sends nothing for an empty parameter, which reads as absent anyway", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(get("/api/phases?task_id="));
    expect(target(fetchMock)).toBe(`${BASE}/api/phases`);
  });

  // The three project-scoped reads: the api takes `?project=` as optional with
  // one checkout and required with several (`docs/decisions.md` ADR 41 part 2).
  // A harness screen sends none and gets the console's own; a `/p/$slug/`
  // screen sends its slug, which crosses when it is shaped like one (ADR 210).
  describe.each(["/api/debt", "/api/learnings", "/api/roadmap"])("%s", (route) => {
    it("sends the console's own ?project= when the browser names none", async () => {
      const fetchMock = stubApi();
      await forwardApiRequest(get(route));
      expect(target(fetchMock)).toBe(`${BASE}${route}?project=ia-harness`);
    });

    it("sends the project the browser names, in place of the console's", async () => {
      const fetchMock = stubApi();
      await forwardApiRequest(get(`${route}?project=scratch`));
      expect(target(fetchMock)).toBe(`${BASE}${route}?project=scratch`);
    });

    it("400s a project that is not a slug, and never falls back to the console's", async () => {
      for (const bad of ["../x", ".hidden", "a/b", "a b"]) {
        const fetchMock = stubApi();
        const answer = await forwardApiRequest(get(`${route}?project=${encodeURIComponent(bad)}`));
        expect(answer?.status).toBe(400);
        expect(fetchMock).not.toHaveBeenCalled();
      }
    });
  });

  // Not a fourth: command requests are read hive-wide and `?project=` only
  // narrows them, so it is the browser's filter, copied by name with `state`
  // (`docs/decisions.md` ADR 123), and nothing is added in its place.
  it("copies project and state on the command-requests route", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(get("/api/command-requests?state=pending&project=other&n=3"));
    expect(target(fetchMock)).toBe(`${BASE}/api/command-requests?project=other&state=pending`);
  });

  it("adds no ?project= of its own on the command-requests route", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(get("/api/command-requests"));
    expect(target(fetchMock)).toBe(`${BASE}/api/command-requests`);
  });
});

describe("the task usage route", () => {
  it("forwards a bare id", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(get("/api/tasks/T-014/usage"));
    expect(target(fetchMock)).toBe(`${BASE}/api/tasks/T-014/usage`);
  });

  it("copies no query parameter: the api takes none", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(get("/api/tasks/T-014/usage?x=1"));
    expect(target(fetchMock)).toBe(`${BASE}/api/tasks/T-014/usage`);
  });

  it("is one segment under the task: a path below it is not this route", async () => {
    const fetchMock = stubApi();
    const answer = await forwardApiRequest(get("/api/tasks/T-014/usage/x"));
    expect(answer?.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("404s an id it cannot read, without asking the api", async () => {
    const fetchMock = stubApi();
    const answer = await forwardApiRequest(get("/api/tasks/%zz/usage"));
    expect(answer?.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("re-encodes an id rather than passing it through", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(get("/api/tasks/..%2f..%2fadmin/usage"));
    expect(target(fetchMock)).toBe(`${BASE}/api/tasks/..%2F..%2Fadmin/usage`);
  });
});

describe("the task diff route", () => {
  it("forwards a bare id with the console's own ?project= when the browser names none", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(get("/api/tasks/T-014/diff"));
    expect(target(fetchMock)).toBe(`${BASE}/api/tasks/T-014/diff?project=ia-harness`);
  });

  it("carries the browser's project slug, and no other parameter", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(get("/api/tasks/T-014/diff?project=scratch&x=1"));
    expect(target(fetchMock)).toBe(`${BASE}/api/tasks/T-014/diff?project=scratch`);
  });

  it("400s a project that is not a slug, without asking the api", async () => {
    const fetchMock = stubApi();
    const answer = await forwardApiRequest(get("/api/tasks/T-014/diff?project=..%2Fx"));
    expect(answer?.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("404s an id it cannot read and a path below it, without asking the api", async () => {
    const fetchMock = stubApi();
    expect((await forwardApiRequest(get("/api/tasks/%zz/diff")))?.status).toBe(404);
    expect((await forwardApiRequest(get("/api/tasks/T-014/diff/x")))?.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("re-encodes an id rather than passing it through", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(get("/api/tasks/..%2f..%2fadmin/diff"));
    expect(target(fetchMock)).toBe(`${BASE}/api/tasks/..%2F..%2Fadmin/diff?project=ia-harness`);
  });
});

describe("the ready order", () => {
  it("is forwarded with no parameter and no project", async () => {
    const fetchMock = stubApi('{"data":[],"warnings":[]}');
    await forwardApiRequest(get("/api/ready?project=other&limit=3"));
    expect(target(fetchMock)).toBe(`${BASE}/api/ready`);
  });
});

describe("the notifications inbox", () => {
  it("is forwarded with no parameter and no project", async () => {
    const fetchMock = stubApi('{"data":[],"warnings":[]}');
    await forwardApiRequest(get("/api/notifications?project=other&limit=3"));
    expect(target(fetchMock)).toBe(`${BASE}/api/notifications`);
  });

  it("is a read only: a write is the console's 405, never the api's", async () => {
    const fetchMock = stubApi();
    const answer = await forwardApiRequest(get("/api/notifications", { method: "POST" }));
    expect(answer?.status).toBe(405);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("the task comments read", () => {
  it("goes to the api, not the board-service, with no parameter", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(get("/api/tasks/T-014/comments?x=1"));
    expect(target(fetchMock)).toBe(`${BASE}/api/tasks/T-014/comments`);
  });

  it("re-encodes an id rather than passing it through", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(get("/api/tasks/..%2f..%2fadmin/comments"));
    expect(target(fetchMock)).toBe(`${BASE}/api/tasks/..%2F..%2Fadmin/comments`);
  });

  it("404s an id it cannot read, without asking the api", async () => {
    const fetchMock = stubApi();
    const answer = await forwardApiRequest(get("/api/tasks/%zz/comments"));
    expect(answer?.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("the action queue read", () => {
  it("forwards /api/actions with limit only, and the api's bearer", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(get("/api/actions?limit=1&project=x&foo=y"));
    expect(target(fetchMock)).toBe(`${BASE}/api/actions?limit=1`);
    expect(fetchMock.mock.calls[0]![1]!.headers).toMatchObject({
      Authorization: "Bearer test-token",
    });
  });

  // One action by its id (ADR 216), what *New project* reads its setup by.
  const ACTION = "a-20261019T120001000000Z-0001";

  it("forwards one action's record with no parameter, and the api's bearer", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(get(`/api/actions/${ACTION}?limit=1&project=x`));
    expect(target(fetchMock)).toBe(`${BASE}/api/actions/${ACTION}`);
    expect(fetchMock.mock.calls[0]![1]!.headers).toMatchObject({
      Authorization: "Bearer test-token",
    });
  });

  it("404s an id the queue does not make, without asking the api", async () => {
    const fetchMock = stubApi();
    for (const bad of ["worker", "a-1", `${ACTION}.json`, "..%2Fworker", "%zz"]) {
      const answer = await forwardApiRequest(get(`/api/actions/${bad}`));
      expect(answer?.status).toBe(404);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards no write of one action's record", async () => {
    const fetchMock = stubApi();
    const answer = await forwardApiRequest(get(`/api/actions/${ACTION}`, { method: "DELETE" }));
    expect(answer?.status).toBe(405);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("the thread reads", () => {
  it("forwards /api/threads bare, with the api's bearer and no project", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(get("/api/threads?project=x&limit=3"));
    expect(target(fetchMock)).toBe(`${BASE}/api/threads`);
    expect(fetchMock.mock.calls[0]![1]!.headers).toMatchObject({
      Authorization: "Bearer test-token",
    });
  });

  it("forwards a thread's events with limit and since, and nothing else", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(get("/api/threads/main/events?since=41&limit=200&project=x&n=1"));
    const sent = new URL(target(fetchMock));
    expect(sent.origin + sent.pathname).toBe(`${BASE}/api/threads/main/events`);
    expect(Object.fromEntries(sent.searchParams)).toEqual({ since: "41", limit: "200" });
  });

  it("forwards a task's events with limit and since, and nothing else", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(get("/api/tasks/T-1/events?since=7&limit=50&project=x&n=1"));
    const sent = new URL(target(fetchMock));
    expect(sent.origin + sent.pathname).toBe(`${BASE}/api/tasks/T-1/events`);
    expect(Object.fromEntries(sent.searchParams)).toEqual({ since: "7", limit: "50" });
  });

  it("404s a task's events whose id does not decode, without asking the api", async () => {
    const fetchMock = stubApi();
    const answer = await forwardApiRequest(get("/api/tasks/%zz/events"));
    expect(answer?.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("drops an empty cursor rather than sending since=", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(get("/api/threads/main/events?since="));
    expect(target(fetchMock)).toBe(`${BASE}/api/threads/main/events`);
  });

  it("re-encodes the id so it stays one segment", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(get("/api/threads/a%3Fb%23c/events"));
    expect(target(fetchMock)).toBe(`${BASE}/api/threads/a%3Fb%23c/events`);
  });

  it("404s an id that does not decode, without asking the api", async () => {
    const fetchMock = stubApi();
    const answer = await forwardApiRequest(get("/api/threads/%zz/events"));
    expect(answer?.status).toBe(404);
    expect(await answer?.json()).toEqual({
      error: "/api/threads/%zz/events is not a readable thread id.",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("404s the paths around the two it serves", async () => {
    const fetchMock = stubApi();
    for (const path of [
      "/api/threads/main",
      "/api/threads/main/events/x",
      "/api/threads//events",
      "/api/threadsx",
    ]) {
      const answer = await forwardApiRequest(get(path));
      expect(answer?.status, path).toBe(404);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("answers a write on a thread path the board pairs do not take", async () => {
    // `POST /api/threads` and `PATCH /api/threads/<id>` are board pairs (ADR 195),
    // tested with them below; every other write here is still the 405.
    const fetchMock = stubApi();
    for (const [method, path] of [
      ["PATCH", "/api/threads"],
      ["PUT", "/api/threads"],
      ["DELETE", "/api/threads/main"],
      ["POST", "/api/threads/main"],
      ["POST", "/api/threads/main/events"],
    ] as const) {
      const answer = await forwardApiRequest(get(path, { method, body: "{}" }));
      expect(answer?.status, `${method} ${path}`).toBe(405);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("the task detail route", () => {
  it("forwards a bare id", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(get("/api/tasks/T-014"));
    expect(target(fetchMock)).toBe(`${BASE}/api/tasks/T-014`);
  });

  it("is one segment: a path under a task is not this route", async () => {
    const fetchMock = stubApi();
    const answer = await forwardApiRequest(get("/api/tasks/T-014/phases"));
    expect(answer?.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("re-encodes an id rather than passing it through", async () => {
    // One segment on the api's side too: a `/` inside an id crosses as `%2F`,
    // which no url parser treats as a separator, so this arrives at the api as a
    // task id to validate -- `_is_bare_task_id` is what refuses it -- and not as
    // a path. This is the division of labour the comment in `forward.ts` calls
    // the second lock.
    const fetchMock = stubApi();
    await forwardApiRequest(get("/api/tasks/..%2f..%2fadmin"));
    expect(target(fetchMock)).toBe(`${BASE}/api/tasks/..%2F..%2Fadmin`);
  });

  it("404s an id it cannot read, without asking the api", async () => {
    const fetchMock = stubApi();
    const answer = await forwardApiRequest(get("/api/tasks/%zz"));
    expect(answer?.status).toBe(404);
    expect(await answer?.json()).toEqual({ error: "/api/tasks/%zz is not a readable task id." });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // The reason the regex in `forward.ts` carries no dot-segment guard, pinned
  // here rather than asserted in prose: a dot segment is resolved away while the
  // request's url is parsed, so this route never receives one. Each case below
  // reaches the forward already rewritten, and lands on the whitelist's 404.
  it.each([
    ["/api/tasks/..", "/api/"],
    ["/api/tasks/%2e%2e", "/api/"],
    ["/api/tasks/.%2e", "/api/"],
    ["/api/tasks/%2E%2E", "/api/"],
    ["/api/tasks/.", "/api/tasks/"],
    ["/api/tasks/%2e", "/api/tasks/"],
  ])("never sees the dot segment in %s, which is already %s", async (asked, arrives) => {
    const fetchMock = stubApi();
    const request = get(asked);
    expect(new URL(request.url).pathname).toBe(arrives);
    const answer = await forwardApiRequest(request);
    expect(answer?.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("the api's answer", () => {
  it("passes a status and a content type through as they came", async () => {
    // Flask answers its own 404 in html, and it stays recognisable as html on
    // the other side, which is what `client.ts`'s guard is for.
    stubApi("<!doctype html><title>404</title>", 404, "text/html; charset=utf-8");
    const answer = await forwardApiRequest(get("/api/tasks/T-999"));
    expect(answer?.status).toBe(404);
    expect(answer?.headers.get("content-type")).toBe("text/html; charset=utf-8");
    expect(await answer?.text()).toContain("<!doctype html>");
  });

  it("turns an unreachable api into a 502 that does not name it", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("fetch failed"));
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});
    const answer = await forwardApiRequest(get("/api/tasks"));
    expect(answer?.status).toBe(502);
    const body = await answer?.text();
    expect(body).toBe('{"error":"The api did not answer: TypeError: fetch failed"}');
    // The address is the server half's own topology: the log gets it, the
    // browser does not.
    expect(body).not.toContain("api.test");
    expect(logged.mock.calls[0]![0]).toContain(BASE);
  });
});

/**
 * The settings forward, `docs/decisions.md` ADR 112: one test or a small group per
 * part of the decision. The subject is again what leaves — the writer's url, the
 * method, the exact headers, the body bytes — and what a request the console
 * would never send gets back without the writer being asked.
 */
describe("the settings forward", () => {
  /** The `init` the forward handed `fetch`, after asserting it was called once. */
  function sent(fetchMock: ReturnType<typeof stubApi>): RequestInit {
    expect(fetchMock).toHaveBeenCalledTimes(1);
    return fetchMock.mock.calls[0]![1]!;
  }

  it("reads the collection from the writer with its own headers and nothing else", async () => {
    const fetchMock = stubApi('{"data":{},"warnings":[]}');
    await forwardApiRequest(
      get("/api/settings?revision=x&project=other", {
        headers: { cookie: "session=whatever", authorization: "Bearer from-the-browser" },
      }),
    );
    // No query parameter crosses: the writer's GET takes none.
    expect(target(fetchMock)).toBe(`${WRITER}/settings`);
    const init = sent(fetchMock);
    expect(init.method).toBe("GET");
    expect(init.headers).toEqual({
      Authorization: "Bearer writer-token",
      "X-Actor": "operator",
      Accept: "application/json",
    });
    expect(init.body).toBeUndefined();
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it("passes a PUT's body bytes through under its own JSON content type", async () => {
    const fetchMock = stubApi();
    const payload = '{"value": 3, "revision": "abc"}';
    await forwardApiRequest(
      get("/api/settings/max_rounds", {
        method: "PUT",
        body: payload,
        headers: { "content-type": "text/plain", "x-forwarded-for": "10.0.0.1" },
      }),
    );
    expect(target(fetchMock)).toBe(`${WRITER}/settings/max_rounds`);
    const init = sent(fetchMock);
    expect(init.method).toBe("PUT");
    expect(init.headers).toEqual({
      Authorization: "Bearer writer-token",
      "X-Actor": "operator",
      Accept: "application/json",
      "Content-Type": "application/json",
    });
    expect(new TextDecoder().decode(init.body as ArrayBuffer)).toBe(payload);
  });

  it("passes a DELETE's body through the same way", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(
      get("/api/settings/max_rounds", { method: "DELETE", body: '{"revision":"abc"}' }),
    );
    const init = sent(fetchMock);
    expect(init.method).toBe("DELETE");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(new TextDecoder().decode(init.body as ArrayBuffer)).toBe('{"revision":"abc"}');
  });

  it("re-encodes a decoded key, so a %2f stays inside one segment", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(get("/api/settings/a%2fb", { method: "PUT", body: "{}" }));
    expect(target(fetchMock)).toBe(`${WRITER}/settings/a%2Fb`);
  });

  it.each([
    ["/api/settings", "POST"],
    ["/api/settings", "PUT"],
    ["/api/settings", "DELETE"],
    ["/api/settings", "OPTIONS"],
    ["/api/settings/max_rounds", "GET"],
    ["/api/settings/max_rounds", "POST"],
    ["/api/settings/max_rounds", "PATCH"],
    ["/api/settings/max_rounds", "OPTIONS"],
  ])("answers %s with %s as a 405 without asking the writer", async (path, method) => {
    const fetchMock = stubApi();
    const answer = await forwardApiRequest(get(path, { method }));
    expect(answer?.status).toBe(405);
    expect(answer?.headers.get("content-type")).toBe("application/json");
    expect((await answer!.json()).error).toContain(`${method} is not served on`);
    // No preflight is ever answered: ADR 112 part 7.
    for (const name of answer!.headers.keys()) {
      expect(name.toLowerCase().startsWith("access-control-")).toBe(false);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    "/api/settings/",
    "/api/settings//max_rounds",
    "/api/settings/max_rounds/",
    "/api/settings/a/b",
    "/api/settings/%zz",
  ])("answers %s as a JSON 404 without asking the writer", async (path) => {
    const fetchMock = stubApi();
    const answer = await forwardApiRequest(get(path, { method: "PUT", body: "{}" }));
    expect(answer?.status).toBe(404);
    expect(answer?.headers.get("content-type")).toBe("application/json");
    expect(typeof (await answer!.json()).error).toBe("string");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("leaves a path that only resembles the prefix to the api's whitelist", async () => {
    const fetchMock = stubApi();
    const answer = await forwardApiRequest(get("/api/settingsx"));
    expect(answer?.status).toBe(404);
    expect(await answer?.json()).toEqual({
      error: "/api/settingsx is not one of the twenty-one routes the console forwards.",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("passes the writer's status, body and content type back as they came", async () => {
    stubApi('{"error":"the settings changed since they were read"}', 409);
    const answer = await forwardApiRequest(
      get("/api/settings/max_rounds", { method: "PUT", body: "{}" }),
    );
    expect(answer?.status).toBe(409);
    expect(answer?.headers.get("content-type")).toBe("application/json");
    expect(await answer?.text()).toBe('{"error":"the settings changed since they were read"}');
  });

  it("turns a timeout into a 504 that names the service and not its address", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(
      new DOMException("The operation timed out.", "TimeoutError"),
    );
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});
    const answer = await forwardApiRequest(get("/api/settings"));
    expect(answer?.status).toBe(504);
    const body = await answer?.text();
    expect(body).toBe('{"error":"The config-writer service did not answer within 5000 ms."}');
    expect(body).not.toContain("writer.test");
    expect(logged.mock.calls[0]![0]).toContain(WRITER);
  });

  it("turns a refused connection into a 503 that names the service and not its address", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(
      new TypeError("fetch failed: connect ECONNREFUSED http://writer.test:8791"),
    );
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});
    const answer = await forwardApiRequest(
      get("/api/settings/max_rounds", { method: "DELETE", body: "{}" }),
    );
    expect(answer?.status).toBe(503);
    const body = await answer?.text();
    expect(body).toBe('{"error":"The config-writer service could not be reached."}');
    expect(body).not.toContain("writer.test");
    expect(logged.mock.calls[0]![0]).toContain(WRITER);
  });
});

describe("the board-service writes", () => {
  /** A write as the console's own page sends it. */
  function write(path: string, method: string, body = '{"text":"hi"}', headers = {}): Request {
    return get(path, {
      method,
      body,
      headers: {
        "content-type": "application/json",
        "sec-fetch-site": "same-origin",
        origin: "http://console.test",
        cookie: "session=whatever",
        authorization: "Bearer from-the-browser",
        ...headers,
      },
    });
  }

  function sent(fetchMock: ReturnType<typeof stubApi>): RequestInit {
    expect(fetchMock).toHaveBeenCalledTimes(1);
    return fetchMock.mock.calls[0]![1]!;
  }

  it.each([
    ["POST", "/api/backlog", `${BOARD}/api/backlog`],
    ["PATCH", "/api/tasks/T-014", `${BOARD}/api/tasks/T-014`],
    ["POST", "/api/tasks/T-014/comments", `${BOARD}/api/tasks/T-014/comments`],
    ["POST", "/api/tasks/T-014/accept", `${BOARD}/api/tasks/T-014/accept`],
    ["POST", "/api/tasks/T-014/verdict", `${BOARD}/api/tasks/T-014/verdict`],
  ])("%s %s reaches the board-service with the body and one header", async (method, path, url) => {
    const fetchMock = stubApi('{"id":"T-014"}');
    const answer = await forwardApiRequest(write(path, method));
    expect(answer?.status).toBe(200);
    expect(target(fetchMock)).toBe(url);
    const init = sent(fetchMock);
    expect(init.method).toBe(method);
    // Not the browser's Origin, cookie or authorization: the board-service
    // refuses any Origin, and it has no credential to check.
    expect(init.headers).toEqual({ "Content-Type": "application/json" });
    expect(new TextDecoder().decode(init.body as ArrayBuffer)).toBe('{"text":"hi"}');
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it("copies no query parameter", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(write("/api/backlog?project=other", "POST"));
    expect(target(fetchMock)).toBe(`${BOARD}/api/backlog`);
  });

  it("re-encodes an id, and 404s one it cannot read", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(write("/api/tasks/..%2fadmin/accept", "POST"));
    expect(target(fetchMock)).toBe(`${BOARD}/api/tasks/..%2Fadmin/accept`);
    const answer = await forwardApiRequest(write("/api/tasks/%zz", "PATCH"));
    expect(answer?.status).toBe(404);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("leaves every other method on these paths as it was", async () => {
    const fetchMock = stubApi();
    expect((await forwardApiRequest(write("/api/tasks", "POST")))?.status).toBe(405);
    expect((await forwardApiRequest(get("/api/backlog")))?.status).toBe(404);
    expect((await forwardApiRequest(get("/api/tasks/T-014/accept")))?.status).toBe(404);
    expect((await forwardApiRequest(get("/api/tasks/T-014/verdict")))?.status).toBe(404);
    expect((await forwardApiRequest(write("/api/tasks/T-014/verdict", "PATCH")))?.status).toBe(405);
    expect((await forwardApiRequest(write("/api/tasks/T-014/usage", "POST")))?.status).toBe(405);
    expect((await forwardApiRequest(write("/api/tasks/T-014", "PUT")))?.status).toBe(405);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("passes the board-service's status, body and content type back", async () => {
    stubApi('{"error":"no such task"}', 404);
    const missing = await forwardApiRequest(write("/api/tasks/T-999/accept", "POST"));
    expect(missing?.status).toBe(404);
    expect(await missing?.text()).toBe('{"error":"no such task"}');
    vi.restoreAllMocks();
    stubApi('{"id":"T-014","comment":{},"comment_count":1}', 201);
    const created = await forwardApiRequest(write("/api/tasks/T-014/comments", "POST"));
    expect(created?.status).toBe(201);
    expect(created?.headers.get("content-type")).toBe("application/json");
  });

  it("allows a write with neither Sec-Fetch-Site nor Origin", async () => {
    const fetchMock = stubApi();
    const request = get("/api/backlog", {
      method: "POST",
      body: "{}",
      headers: { "content-type": "application/json; charset=utf-8" },
    });
    expect((await forwardApiRequest(request))?.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("415s a body that is not declared JSON", async () => {
    const fetchMock = stubApi();
    for (const type of ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data"]) {
      const answer = await forwardApiRequest(
        write("/api/backlog", "POST", "{}", { "content-type": type }),
      );
      expect(answer?.status).toBe(415);
      expect((await answer!.json()).error).toContain("application/json");
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("403s a cross-site or same-site Sec-Fetch-Site", async () => {
    const fetchMock = stubApi();
    for (const site of ["cross-site", "same-site", "none"]) {
      const answer = await forwardApiRequest(
        write("/api/backlog", "POST", "{}", { "sec-fetch-site": site }),
      );
      expect(answer?.status).toBe(403);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("403s a foreign Origin, and does not forward the console's own", async () => {
    const fetchMock = stubApi();
    const answer = await forwardApiRequest(
      write("/api/backlog", "POST", "{}", { origin: "http://evil.test" }),
    );
    expect(answer?.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    await forwardApiRequest(write("/api/backlog", "POST"));
    expect(sent(fetchMock).headers).not.toHaveProperty("origin");
    expect(sent(fetchMock).headers).not.toHaveProperty("Origin");
  });

  it("413s a body over 64 KiB without asking the board-service", async () => {
    const fetchMock = stubApi();
    const big = JSON.stringify({ text: "x".repeat(64 * 1024) });
    const answer = await forwardApiRequest(write("/api/tasks/T-014/comments", "POST", big));
    expect(answer?.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("413s a streamed body over 64 KiB that declares no length", async () => {
    const fetchMock = stubApi();
    const chunk = new Uint8Array(40 * 1024).fill(0x20);
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(chunk);
        controller.enqueue(chunk);
        controller.close();
      },
    });
    const request = new Request("http://console.test/api/backlog", {
      method: "POST",
      body: stream,
      headers: { "content-type": "application/json" },
      duplex: "half",
    } as RequestInit);
    expect((await forwardApiRequest(request))?.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("turns an unreachable board-service into a 502 that names it and not its address", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(
      new TypeError("fetch failed: connect ECONNREFUSED http://board.test:8792"),
    );
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});
    const answer = await forwardApiRequest(write("/api/backlog", "POST"));
    expect(answer?.status).toBe(502);
    const body = await answer?.text();
    expect(body).toBe('{"error":"The board-service could not be reached."}');
    expect(body).not.toContain("board.test");
    expect(logged.mock.calls[0]![0]).toContain(BOARD);
  });

  it("turns a timeout into a 504 that names the board-service", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(
      new DOMException("The operation timed out.", "TimeoutError"),
    );
    vi.spyOn(console, "error").mockImplementation(() => {});
    const answer = await forwardApiRequest(write("/api/tasks/T-014", "PATCH"));
    expect(answer?.status).toBe(504);
    expect(await answer?.text()).toBe(
      '{"error":"The board-service did not answer within 5000 ms."}',
    );
  });
});

/** ADR 157: start and cancel, forwarded with the actions bearer. */
describe("the board-service action routes", () => {
  function post(path: string, body: string, headers = {}, method = "POST"): Request {
    return get(path, {
      method,
      body,
      headers: {
        "content-type": "application/json",
        "sec-fetch-site": "same-origin",
        origin: "http://console.test",
        authorization: "Bearer from-the-browser",
        ...headers,
      },
    });
  }

  function sent(fetchMock: ReturnType<typeof stubApi>): RequestInit {
    expect(fetchMock).toHaveBeenCalledTimes(1);
    return fetchMock.mock.calls[0]![1]!;
  }

  const HEADERS = { "Content-Type": "application/json", Authorization: "Bearer actions-token" };

  it("forwards a start with the bearer and the console's project", async () => {
    const fetchMock = stubApi('{"id":"A-1"}', 201);
    const answer = await forwardApiRequest(post("/api/tasks/T-014/start", '{"note":"go"}'));
    expect(answer?.status).toBe(201);
    expect(target(fetchMock)).toBe(`${BOARD}/api/tasks/T-014/start`);
    const init = sent(fetchMock);
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual(HEADERS);
    expect(init.body).toBe('{"project":"ia-harness","note":"go"}');
  });

  it("sends a null note for an empty object or a null note", async () => {
    for (const body of ["{}", '{"note":null}']) {
      const fetchMock = stubApi();
      await forwardApiRequest(post("/api/tasks/T-014/start", body));
      expect(sent(fetchMock).body).toBe('{"project":"ia-harness","note":null}');
      vi.restoreAllMocks();
    }
  });

  it("passes a start's floor and limit on when present, values for the route to check (ADR 230)", async () => {
    const cases: [string, string][] = [
      ['{"note":"go","limit":5000}', '{"project":"ia-harness","note":"go","limit":5000}'],
      ['{"floor":20,"limit":1}', '{"project":"ia-harness","note":null,"floor":20,"limit":1}'],
      ['{"limit":null}', '{"project":"ia-harness","note":null,"limit":null}'],
      ['{"limit":0}', '{"project":"ia-harness","note":null,"limit":0}'],
    ];
    for (const [body, upstream] of cases) {
      const fetchMock = stubApi('{"id":"A-1"}', 201);
      await forwardApiRequest(post("/api/tasks/T-014/start", body));
      expect(sent(fetchMock).body).toBe(upstream);
      vi.restoreAllMocks();
    }
  });

  it("forwards a cancel with the bearer and an empty object, whatever the browser sent", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(post("/api/actions/A-1/cancel", '{"x":1}'));
    expect(target(fetchMock)).toBe(`${BOARD}/api/actions/A-1/cancel`);
    const init = sent(fetchMock);
    expect(init.headers).toEqual(HEADERS);
    expect(init.body).toBe("{}");
  });

  it("re-encodes an id and 404s one it cannot read, on both", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(post("/api/tasks/..%2fx/start", "{}"));
    expect(target(fetchMock)).toBe(`${BOARD}/api/tasks/..%2Fx/start`);
    vi.restoreAllMocks();
    const second = stubApi();
    await forwardApiRequest(post("/api/actions/..%2fx/cancel", "{}"));
    expect(target(second)).toBe(`${BOARD}/api/actions/..%2Fx/cancel`);
    vi.restoreAllMocks();
    const none = stubApi();
    expect((await forwardApiRequest(post("/api/tasks/%zz/start", "{}")))?.status).toBe(404);
    expect((await forwardApiRequest(post("/api/actions/%zz/cancel", "{}")))?.status).toBe(404);
    expect(none).not.toHaveBeenCalled();
  });

  it.each([
    '{"project":"other"}',
    '{"note":"a","x":1}',
    '{"note":5}',
    "[]",
    '"x"',
    "null",
    "not json",
    "",
  ])("400s a start body of %j, sending nothing", async (body) => {
    const fetchMock = stubApi();
    const answer = await forwardApiRequest(post("/api/tasks/T-014/start", body));
    expect(answer?.status).toBe(400);
    expect(typeof (await answer!.json()).error).toBe("string");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("still answers the CSRF refusals first", async () => {
    const fetchMock = stubApi();
    const type = await forwardApiRequest(
      post("/api/tasks/T-014/start", "{}", { "content-type": "text/plain" }),
    );
    expect(type?.status).toBe(415);
    const site = await forwardApiRequest(
      post("/api/actions/A-1/cancel", "{}", { "sec-fetch-site": "cross-site" }),
    );
    expect(site?.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("leaves every other method on these paths as it was", async () => {
    const fetchMock = stubApi();
    expect((await forwardApiRequest(get("/api/tasks/T-014/start")))?.status).toBe(404);
    expect((await forwardApiRequest(get("/api/actions/A-1/cancel")))?.status).toBe(404);
    for (const method of ["PUT", "PATCH", "DELETE"]) {
      const start = await forwardApiRequest(post("/api/tasks/T-014/start", "{}", {}, method));
      const cancel = await forwardApiRequest(post("/api/actions/A-1/cancel", "{}", {}, method));
      expect([start?.status, cancel?.status]).toEqual([405, 405]);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards Start epic as a start: the bearer, the console's project, the note (ADR 211)", async () => {
    const fetchMock = stubApi('{"epic":"T-1","queued":[]}', 201);
    const answer = await forwardApiRequest(post("/api/epics/T-1/start", '{"note":"go"}'));
    expect(answer?.status).toBe(201);
    expect(target(fetchMock)).toBe(`${BOARD}/api/epics/T-1/start`);
    const init = sent(fetchMock);
    expect(init.headers).toEqual(HEADERS);
    expect(init.body).toBe('{"project":"ia-harness","note":"go"}');
    vi.restoreAllMocks();
    const limited = stubApi('{"epic":"T-1","queued":[]}', 201);
    await forwardApiRequest(post("/api/epics/T-1/start", '{"note":null,"limit":5,"floor":20}'));
    expect(sent(limited).body).toBe('{"project":"ia-harness","note":null,"floor":20,"limit":5}');
  });

  it("forwards Stop epic as a cancel: the bearer and an empty object (ADR 211)", async () => {
    const fetchMock = stubApi('{"epic":"T-1","dropped":[],"left":[]}', 201);
    await forwardApiRequest(post("/api/epics/..%2fx/stop", '{"x":1}'));
    expect(target(fetchMock)).toBe(`${BOARD}/api/epics/..%2Fx/stop`);
    const init = sent(fetchMock);
    expect(init.headers).toEqual(HEADERS);
    expect(init.body).toBe("{}");
    vi.restoreAllMocks();
    const none = stubApi();
    expect((await forwardApiRequest(post("/api/epics/%zz/stop", "{}")))?.status).toBe(404);
    expect((await forwardApiRequest(get("/api/epics/T-1/stop")))?.status).toBe(404);
    expect((await forwardApiRequest(post("/api/epics/T-1/start", "{}", {}, "PUT")))?.status).toBe(
      405,
    );
    expect(none).not.toHaveBeenCalled();
  });

  it.each([
    ["POST", "/api/backlog"],
    ["PATCH", "/api/tasks/T-014"],
    ["POST", "/api/tasks/T-014/comments"],
    ["POST", "/api/tasks/T-014/accept"],
    ["POST", "/api/tasks/T-014/verdict"],
  ])("gives the backlog write %s %s no Authorization", async (method, path) => {
    const fetchMock = stubApi();
    await forwardApiRequest(post(path, "{}", {}, method));
    expect(sent(fetchMock).headers).toEqual({ "Content-Type": "application/json" });
  });

  it("passes a 401 back with its body but without WWW-Authenticate", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async () =>
        new Response('{"error":"bad token"}', {
          status: 401,
          headers: { "content-type": "application/json", "www-authenticate": "Bearer" },
        }),
    );
    const answer = await forwardApiRequest(post("/api/actions/A-1/cancel", "{}"));
    expect(answer?.status).toBe(401);
    expect(await answer?.text()).toBe('{"error":"bad token"}');
    expect(answer?.headers.has("www-authenticate")).toBe(false);
  });

  it("puts the token in no response", async () => {
    const answers: Response[] = [];
    stubApi('{"id":"A-1"}', 201);
    answers.push((await forwardApiRequest(post("/api/tasks/T-014/start", "{}")))!);
    answers.push((await forwardApiRequest(post("/api/tasks/T-014/start", "[]")))!);
    vi.restoreAllMocks();
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("fetch failed"));
    vi.spyOn(console, "error").mockImplementation(() => {});
    answers.push((await forwardApiRequest(post("/api/actions/A-1/cancel", "{}")))!);
    expect(answers.map((a) => a.status)).toEqual([201, 400, 502]);
    for (const answer of answers) {
      for (const [, value] of answer.headers) expect(value).not.toContain("actions-token");
      expect(await answer.text()).not.toContain("actions-token");
    }
  });
});

/** The seventh board pair (ADR 190): a turn for a thread. */
describe("the board-service approve route (ADR 270)", () => {
  function post(path: string, body: string, method = "POST"): Request {
    return get(path, {
      method,
      body: method === "GET" ? null : body,
      headers: {
        "content-type": "application/json",
        "sec-fetch-site": "same-origin",
        origin: "http://console.test",
        authorization: "Bearer from-the-browser",
      },
    });
  }

  const PATH = "/api/tasks/T-014/approve";
  const TIP = "a".repeat(40);
  const HEADERS = { "Content-Type": "application/json", Authorization: "Bearer actions-token" };

  it("forwards {project, commit, text} with the bearer", async () => {
    const fetchMock = stubApi('{"verdict":{},"action":{"id":"A-1"}}', 201);
    const body = JSON.stringify({ project: "other-proj", commit: TIP, text: "Approved." });
    const answer = await forwardApiRequest(post(PATH, body));
    expect(answer?.status).toBe(201);
    expect(target(fetchMock)).toBe(`${BOARD}/api/tasks/T-014/approve`);
    const init = fetchMock.mock.calls[0]![1]!;
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual(HEADERS);
    expect(init.body).toBe(body);
  });

  it("sends CONSOLE_PROJECT when the body names no project", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(post(PATH, JSON.stringify({ commit: TIP, text: "ok" })));
    expect(fetchMock.mock.calls[0]![1]!.body).toBe(
      JSON.stringify({ project: "ia-harness", commit: TIP, text: "ok" }),
    );
  });

  it("passes commit and text as given, for the route to refuse", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(post(PATH, '{"commit":1,"text":" "}'));
    expect(fetchMock.mock.calls[0]![1]!.body).toBe(
      '{"project":"ia-harness","commit":1,"text":" "}',
    );
  });

  it("400s a bad slug, an unknown field or a body that is not an object, sending nothing", async () => {
    const fetchMock = stubApi();
    for (const body of [
      '{"project":"../x","commit":"a","text":"t"}',
      '{"project":3,"commit":"a","text":"t"}',
      '{"commit":"a","text":"t","kind":"approve"}',
      "[]",
      "null",
      "not json",
    ]) {
      const answer = await forwardApiRequest(post(PATH, body));
      expect(answer?.status).toBe(400);
      expect((await answer!.json()).error).toMatch(/approve/i);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("re-encodes the id, and keeps GET and other methods as they were", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(post("/api/tasks/..%2fx/approve", "{}"));
    expect(target(fetchMock)).toBe(`${BOARD}/api/tasks/..%2Fx/approve`);
    vi.restoreAllMocks();
    const none = stubApi();
    expect((await forwardApiRequest(post(PATH, "", "GET")))?.status).toBe(404);
    expect((await forwardApiRequest(post(PATH, "{}", "PATCH")))?.status).toBe(405);
    expect((await forwardApiRequest(post("/api/tasks/%zz/approve", "{}")))?.status).toBe(404);
    expect(none).not.toHaveBeenCalled();
  });
});

describe("the board-service request-changes route (ADR 276)", () => {
  function post(path: string, body: string, method = "POST"): Request {
    return get(path, {
      method,
      body: method === "GET" ? null : body,
      headers: {
        "content-type": "application/json",
        "sec-fetch-site": "same-origin",
        origin: "http://console.test",
        authorization: "Bearer from-the-browser",
      },
    });
  }

  const PATH = "/api/tasks/T-014/request-changes";
  const TIP = "a".repeat(40);
  const HEADERS = { "Content-Type": "application/json", Authorization: "Bearer actions-token" };

  it("forwards {project, commit, text} with the bearer", async () => {
    const fetchMock = stubApi('{"verdict":{},"action":{"id":"A-1"}}', 201);
    const body = JSON.stringify({ project: "other-proj", commit: TIP, text: "Fix the diff." });
    const answer = await forwardApiRequest(post(PATH, body));
    expect(answer?.status).toBe(201);
    expect(target(fetchMock)).toBe(`${BOARD}/api/tasks/T-014/request-changes`);
    const init = fetchMock.mock.calls[0]![1]!;
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual(HEADERS);
    expect(init.body).toBe(body);
  });

  it("sends CONSOLE_PROJECT when the body names no project, and passes commit and text as given", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(post(PATH, JSON.stringify({ commit: TIP, text: "ok" })));
    expect(fetchMock.mock.calls[0]![1]!.body).toBe(
      JSON.stringify({ project: "ia-harness", commit: TIP, text: "ok" }),
    );
    await forwardApiRequest(post(PATH, '{"commit":1,"text":" "}'));
    expect(fetchMock.mock.calls[1]![1]!.body).toBe(
      '{"project":"ia-harness","commit":1,"text":" "}',
    );
  });

  it("400s a bad slug, an unknown field or a body that is not an object, sending nothing", async () => {
    const fetchMock = stubApi();
    for (const body of [
      '{"project":"../x","commit":"a","text":"t"}',
      '{"project":3,"commit":"a","text":"t"}',
      '{"commit":"a","text":"t","kind":"approve"}',
      "[]",
      "null",
      "not json",
    ]) {
      const answer = await forwardApiRequest(post(PATH, body));
      expect(answer?.status).toBe(400);
      expect((await answer!.json()).error).toMatch(/request for changes/i);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("re-encodes the id, and keeps GET and other methods as they were", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(post("/api/tasks/..%2fx/request-changes", "{}"));
    expect(target(fetchMock)).toBe(`${BOARD}/api/tasks/..%2Fx/request-changes`);
    vi.restoreAllMocks();
    const none = stubApi();
    expect((await forwardApiRequest(post(PATH, "", "GET")))?.status).toBe(404);
    expect((await forwardApiRequest(post(PATH, "{}", "PATCH")))?.status).toBe(405);
    expect((await forwardApiRequest(post("/api/tasks/%zz/request-changes", "{}")))?.status).toBe(
      404,
    );
    expect(none).not.toHaveBeenCalled();
  });
});

describe("the board-service resolve-conflict route (ADR 288)", () => {
  function post(path: string, body: string, method = "POST"): Request {
    return get(path, {
      method,
      body: method === "GET" ? null : body,
      headers: {
        "content-type": "application/json",
        "sec-fetch-site": "same-origin",
        origin: "http://console.test",
        authorization: "Bearer from-the-browser",
      },
    });
  }

  const PATH = "/api/tasks/T-014/resolve-conflict";
  const HEADERS = { "Content-Type": "application/json", Authorization: "Bearer actions-token" };

  it("forwards {project} with the bearer", async () => {
    const fetchMock = stubApi('{"id":"A-1"}', 201);
    const answer = await forwardApiRequest(post(PATH, '{"project":"other-proj"}'));
    expect(answer?.status).toBe(201);
    expect(target(fetchMock)).toBe(`${BOARD}/api/tasks/T-014/resolve-conflict`);
    const init = fetchMock.mock.calls[0]![1]!;
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual(HEADERS);
    expect(init.body).toBe('{"project":"other-proj"}');
  });

  it("sends CONSOLE_PROJECT when the body names no project", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(post(PATH, "{}"));
    expect(fetchMock.mock.calls[0]![1]!.body).toBe('{"project":"ia-harness"}');
  });

  it("400s a bad slug, an unknown field or a body that is not an object, sending nothing", async () => {
    const fetchMock = stubApi();
    for (const body of [
      '{"project":"../x"}',
      '{"project":3}',
      '{"commit":"a"}',
      "[]",
      "null",
      "not json",
    ]) {
      const answer = await forwardApiRequest(post(PATH, body));
      expect(answer?.status).toBe(400);
      expect((await answer!.json()).error).toMatch(/resolve conflict/i);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("keeps GET and other methods as they were, and 404s an undecodable id", async () => {
    const none = stubApi();
    expect((await forwardApiRequest(post(PATH, "", "GET")))?.status).toBe(404);
    expect((await forwardApiRequest(post(PATH, "{}", "PATCH")))?.status).toBe(405);
    expect((await forwardApiRequest(post("/api/tasks/%zz/resolve-conflict", "{}")))?.status).toBe(
      404,
    );
    expect(none).not.toHaveBeenCalled();
  });
});

describe("the board-service turn route", () => {
  function post(path: string, body: string, headers = {}, method = "POST"): Request {
    return get(path, {
      method,
      body,
      headers: {
        "content-type": "application/json",
        "sec-fetch-site": "same-origin",
        origin: "http://console.test",
        authorization: "Bearer from-the-browser",
        ...headers,
      },
    });
  }

  const HEADERS = { "Content-Type": "application/json", Authorization: "Bearer actions-token" };
  const ORDINARY = "th-20261019T100000Z-abcdef";

  it.each(["main", ORDINARY])(
    "forwards a turn for %s with the bearer, body unchanged",
    async (id) => {
      const fetchMock = stubApi('{"id":"A-1"}', 201);
      const body = '{"text":"hello"}';
      const answer = await forwardApiRequest(post(`/api/threads/${id}/turns`, body));
      expect(answer?.status).toBe(201);
      expect(await answer!.text()).toBe('{"id":"A-1"}');
      expect(target(fetchMock)).toBe(`${BOARD}/api/threads/${id}/turns`);
      const init = fetchMock.mock.calls[0]![1]!;
      expect(init.method).toBe("POST");
      expect(init.headers).toEqual(HEADERS);
      expect(new TextDecoder().decode(init.body as ArrayBuffer)).toBe(body);
    },
  );

  it.each([400, 404, 409])("passes the route's %s and its message back", async (status) => {
    stubApi('{"error":"nope"}', status);
    const answer = await forwardApiRequest(post("/api/threads/main/turns", '{"text":"x"}'));
    expect(answer?.status).toBe(status);
    expect(await answer!.json()).toEqual({ error: "nope" });
  });

  it("answers the CSRF refusals first", async () => {
    const fetchMock = stubApi();
    const path = "/api/threads/main/turns";
    expect(
      (await forwardApiRequest(post(path, "{}", { "content-type": "text/plain" })))?.status,
    ).toBe(415);
    expect(
      (await forwardApiRequest(post(path, "{}", { "sec-fetch-site": "cross-site" })))?.status,
    ).toBe(403);
    expect(
      (await forwardApiRequest(post(path, "{}", { origin: "http://evil.test" })))?.status,
    ).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(["th-bogus", "main2", "MAIN", "%zz", "a%2Fb", "th-20261019T100000Z-ABCDEF"])(
    "404s the id %s, sending nothing",
    async (id) => {
      const fetchMock = stubApi();
      const answer = await forwardApiRequest(post(`/api/threads/${id}/turns`, '{"text":"x"}'));
      expect(answer?.status).toBe(404);
      expect(typeof (await answer!.json()).error).toBe("string");
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("leaves every other method on the path as it was", async () => {
    const fetchMock = stubApi();
    expect((await forwardApiRequest(get("/api/threads/main/turns")))?.status).toBe(404);
    for (const method of ["PUT", "PATCH", "DELETE"]) {
      const answer = await forwardApiRequest(post("/api/threads/main/turns", "{}", {}, method));
      expect(answer?.status).toBe(405);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the largest text the route takes, and 413s a body over its cap", async () => {
    // 20,000 code points that JSON writes in six bytes each: 120,011 bytes.
    const largest = JSON.stringify({ text: "\u0001".repeat(20000) });
    expect(new TextEncoder().encode(largest).byteLength).toBe(120011);
    const fetchMock = stubApi('{"id":"A-1"}', 201);
    expect((await forwardApiRequest(post("/api/threads/main/turns", largest)))?.status).toBe(201);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    vi.restoreAllMocks();

    const none = stubApi();
    const over = JSON.stringify({ text: "x".repeat(128 * 1024) });
    const answer = await forwardApiRequest(post("/api/threads/main/turns", over));
    expect(answer?.status).toBe(413);
    expect((await answer!.json()).error).toContain("131072");
    expect(none).not.toHaveBeenCalled();
  });

  it("keeps the other pairs at 64 KiB", async () => {
    const fetchMock = stubApi();
    const answer = await forwardApiRequest(
      post("/api/actions/A-1/cancel", JSON.stringify({ x: "y".repeat(70 * 1024) })),
    );
    expect(answer?.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

/** The eighth and ninth board pairs (ADR 195): making and changing a thread record. */
describe("the board-service thread records", () => {
  function write(path: string, body: string, method = "POST", headers = {}): Request {
    return get(path, {
      method,
      body,
      headers: {
        "content-type": "application/json",
        "sec-fetch-site": "same-origin",
        origin: "http://console.test",
        authorization: "Bearer from-the-browser",
        ...headers,
      },
    });
  }

  const ORDINARY = "th-20261019T100000Z-abcdef";

  function sent(fetchMock: ReturnType<typeof stubApi>) {
    const init = fetchMock.mock.calls[0]![1]!;
    return {
      method: init.method,
      headers: init.headers,
      body: new TextDecoder().decode(init.body as ArrayBuffer),
    };
  }

  it("forwards a create with no bearer, body unchanged", async () => {
    const fetchMock = stubApi('{"id":"th-x"}', 201);
    const body = '{"title":"About T-014","task":"T-014"}';
    const answer = await forwardApiRequest(write("/api/threads", body));
    expect(answer?.status).toBe(201);
    expect(await answer!.text()).toBe('{"id":"th-x"}');
    expect(target(fetchMock)).toBe(`${BOARD}/api/threads`);
    expect(sent(fetchMock)).toEqual({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    });
  });

  it.each(["main", ORDINARY])("forwards a PATCH of %s with no bearer", async (id) => {
    const fetchMock = stubApi('{"id":"x"}', 200);
    const body = '{"status":"archived"}';
    const answer = await forwardApiRequest(write(`/api/threads/${id}`, body, "PATCH"));
    expect(answer?.status).toBe(200);
    expect(target(fetchMock)).toBe(`${BOARD}/api/threads/${id}`);
    expect(sent(fetchMock)).toEqual({
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body,
    });
  });

  it.each([400, 404, 409, 503])("passes the route's %s and its message back", async (status) => {
    for (const request of [
      write("/api/threads", '{"title":"x"}'),
      write("/api/threads/main", '{"status":"archived"}', "PATCH"),
    ]) {
      stubApi('{"error":"nope"}', status);
      const answer = await forwardApiRequest(request);
      expect(answer?.status).toBe(status);
      expect(await answer!.json()).toEqual({ error: "nope" });
      vi.restoreAllMocks();
    }
  });

  it.each([
    ["POST", "/api/threads"],
    ["PATCH", "/api/threads/main"],
  ])("answers the CSRF refusals on %s %s first", async (method, path) => {
    const fetchMock = stubApi();
    expect(
      (await forwardApiRequest(write(path, "{}", method, { "content-type": "text/plain" })))
        ?.status,
    ).toBe(415);
    expect(
      (await forwardApiRequest(write(path, "{}", method, { "sec-fetch-site": "cross-site" })))
        ?.status,
    ).toBe(403);
    expect(
      (await forwardApiRequest(write(path, "{}", method, { origin: "http://evil.test" })))?.status,
    ).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(["th-bogus", "main2", "MAIN", "%zz", "a%2Fb", "th-20261019T100000Z-ABCDEF"])(
    "404s a PATCH of the id %s, sending nothing",
    async (id) => {
      const fetchMock = stubApi();
      const answer = await forwardApiRequest(write(`/api/threads/${id}`, "{}", "PATCH"));
      expect(answer?.status).toBe(404);
      expect(typeof (await answer!.json()).error).toBe("string");
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("leaves every other method on the paths as it was", async () => {
    const fetchMock = stubApi();
    expect((await forwardApiRequest(get("/api/threads/main")))?.status).toBe(404);
    for (const [method, path] of [
      ["PUT", "/api/threads/main"],
      ["DELETE", "/api/threads/main"],
      ["POST", "/api/threads/main"],
      ["PATCH", "/api/threads"],
      ["DELETE", "/api/threads"],
    ] as const) {
      const answer = await forwardApiRequest(write(path, "{}", method));
      expect(answer?.status, `${method} ${path}`).toBe(405);
    }
    expect(fetchMock).not.toHaveBeenCalled();
    vi.restoreAllMocks();

    // The list is still the api's read.
    const api = stubApi("[]");
    expect((await forwardApiRequest(get("/api/threads")))?.status).toBe(200);
    expect(target(api)).toBe(`${BASE}/api/threads`);
  });

  it("413s a record body over 64 KiB", async () => {
    const fetchMock = stubApi();
    const answer = await forwardApiRequest(
      write("/api/threads", JSON.stringify({ title: "y".repeat(70 * 1024) })),
    );
    expect(answer?.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

/** The tenth and eleventh board pairs (ADR 203, ADR 206): making and changing a project record. */
describe("the board-service project records", () => {
  function write(path: string, body: string, method = "POST", headers = {}): Request {
    return get(path, {
      method,
      body,
      headers: {
        "content-type": "application/json",
        "sec-fetch-site": "same-origin",
        origin: "http://console.test",
        authorization: "Bearer from-the-browser",
        ...headers,
      },
    });
  }

  const HEADERS = { "Content-Type": "application/json", Authorization: "Bearer actions-token" };

  function sent(fetchMock: ReturnType<typeof stubApi>) {
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const init = fetchMock.mock.calls[0]![1]!;
    return {
      method: init.method,
      headers: init.headers,
      body: new TextDecoder().decode(init.body as ArrayBuffer),
    };
  }

  it("forwards a create with the bearer, not the browser's, body unchanged", async () => {
    const record = '{"slug":"demo","status":"active"}';
    const fetchMock = stubApi(record, 201);
    const body = '{"slug":"demo","title":"Demo","settings":{"merge_on_done":false}}';
    const answer = await forwardApiRequest(write("/api/projects", body));
    expect(answer?.status).toBe(201);
    expect(await answer!.text()).toBe(record);
    expect(target(fetchMock)).toBe(`${BOARD}/api/projects`);
    expect(sent(fetchMock)).toEqual({ method: "POST", headers: HEADERS, body });
  });

  it.each(["demo", "ia-harness", "a.b_c-1", "X9"])(
    "forwards a PATCH of %s with the bearer, body unchanged",
    async (slug) => {
      const fetchMock = stubApi('{"slug":"x"}', 200);
      const body = '{"status":"archived"}';
      const answer = await forwardApiRequest(write(`/api/projects/${slug}`, body, "PATCH"));
      expect(answer?.status).toBe(200);
      expect(target(fetchMock)).toBe(`${BOARD}/api/projects/${slug}`);
      expect(sent(fetchMock)).toEqual({ method: "PATCH", headers: HEADERS, body });
    },
  );

  it.each([".hidden", "-x", "_x", "a%20b", "%zz", "a%2Fb", "..%2Fx", "a%00", "a%3Fb"])(
    "404s a PATCH of the slug %s, sending nothing",
    async (slug) => {
      const fetchMock = stubApi();
      const answer = await forwardApiRequest(write(`/api/projects/${slug}`, "{}", "PATCH"));
      expect(answer?.status).toBe(404);
      expect(typeof (await answer!.json()).error).toBe("string");
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it.each([400, 401, 404, 409, 503])(
    "passes the route's %s and its message back",
    async (status) => {
      for (const request of [
        write("/api/projects", '{"slug":"demo"}'),
        write("/api/projects/demo", '{"title":"x"}', "PATCH"),
      ]) {
        stubApi('{"error":"nope"}', status);
        const answer = await forwardApiRequest(request);
        expect(answer?.status).toBe(status);
        expect(await answer!.json()).toEqual({ error: "nope" });
        vi.restoreAllMocks();
      }
    },
  );

  it.each([
    ["POST", "/api/projects"],
    ["PATCH", "/api/projects/demo"],
  ])("answers the CSRF refusals on %s %s first", async (method, path) => {
    const fetchMock = stubApi();
    expect(
      (await forwardApiRequest(write(path, "{}", method, { "content-type": "text/plain" })))
        ?.status,
    ).toBe(415);
    expect(
      (await forwardApiRequest(write(path, "{}", method, { "sec-fetch-site": "cross-site" })))
        ?.status,
    ).toBe(403);
    expect(
      (await forwardApiRequest(write(path, "{}", method, { origin: "http://evil.test" })))?.status,
    ).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the reads to the api, hive-wide (ADR 209)", async () => {
    let fetchMock = stubApi();
    // No `?project=` added, and none of the browser's parameters copied.
    expect((await forwardApiRequest(get("/api/projects?project=x&status=active")))?.status).toBe(
      200,
    );
    expect(target(fetchMock)).toBe(`${BASE}/api/projects`);
    fetchMock.mockRestore();
    fetchMock = stubApi();
    expect((await forwardApiRequest(get("/api/projects/demo.site")))?.status).toBe(200);
    expect(target(fetchMock)).toBe(`${BASE}/api/projects/demo.site`);
    fetchMock.mockRestore();
    // A slug the harness does not take never crosses.
    fetchMock = stubApi();
    for (const path of ["/api/projects/.x", "/api/projects/a%20b", "/api/projects/%zz"]) {
      expect((await forwardApiRequest(get(path)))?.status, path).toBe(404);
    }
    expect((await forwardApiRequest(get("/api/projects/demo/x")))?.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("leaves every other method on the paths as it was", async () => {
    const fetchMock = stubApi();
    for (const [method, path] of [
      ["PUT", "/api/projects/demo"],
      ["DELETE", "/api/projects/demo"],
      ["POST", "/api/projects/demo"],
      ["PATCH", "/api/projects"],
      ["DELETE", "/api/projects"],
    ] as const) {
      const answer = await forwardApiRequest(write(path, "{}", method));
      expect(answer?.status, `${method} ${path}`).toBe(405);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("413s a record body over 64 KiB", async () => {
    const fetchMock = stubApi();
    const answer = await forwardApiRequest(
      write("/api/projects", JSON.stringify({ slug: "demo", title: "y".repeat(70 * 1024) })),
    );
    expect(answer?.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("puts the token in no response", async () => {
    const answers: Response[] = [];
    stubApi('{"slug":"demo"}', 201);
    answers.push((await forwardApiRequest(write("/api/projects", '{"slug":"demo"}')))!);
    answers.push((await forwardApiRequest(write("/api/projects/.x", "{}", "PATCH")))!);
    vi.restoreAllMocks();
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("fetch failed"));
    vi.spyOn(console, "error").mockImplementation(() => {});
    answers.push((await forwardApiRequest(write("/api/projects/demo", "{}", "PATCH")))!);
    expect(answers.map((a) => a.status)).toEqual([201, 404, 502]);
    for (const answer of answers) {
      for (const [, value] of answer.headers) expect(value).not.toContain("actions-token");
      expect(await answer.text()).not.toContain("actions-token");
    }
  });

  // A new project's setup actions (ADR 212): the same bearer, body unchanged.
  it.each(["clone", "init", "keygen", "check"])(
    "forwards a %s with the bearer, body unchanged",
    async (kind) => {
      const fetchMock = stubApi('{"id":"a-1","kind":"x"}', 201);
      const body = kind === "keygen" ? '{"replace":true}' : "{}";
      const answer = await forwardApiRequest(write(`/api/projects/demo/${kind}`, body));
      expect(answer?.status).toBe(201);
      expect(target(fetchMock)).toBe(`${BOARD}/api/projects/demo/${kind}`);
      expect(sent(fetchMock)).toEqual({ method: "POST", headers: HEADERS, body });
    },
  );

  it.each([".hidden", "%zz", "a%2Fb"])("404s a setup action for the slug %s", async (slug) => {
    const fetchMock = stubApi();
    const answer = await forwardApiRequest(write(`/api/projects/${slug}/clone`, "{}"));
    expect(answer?.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards no other setup path or method", async () => {
    const fetchMock = stubApi();
    expect((await forwardApiRequest(get("/api/projects/demo/keygen")))?.status).toBe(404);
    expect((await forwardApiRequest(write("/api/projects/demo/push", "{}")))?.status).toBe(405);
    expect(
      (await forwardApiRequest(write("/api/projects/demo/clone", "{}", "PATCH")))?.status,
    ).toBe(405);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

/** ADR 157's two 503s, each under a second mock of `server-env.ts`. */
describe("the action routes without their configuration", () => {
  async function configured(overrides: Record<string, unknown>) {
    vi.resetModules();
    vi.doMock("./server-env", () => ({
      API_BASE_URL: "http://api.test:8789",
      API_TOKEN: "test-token",
      CONSOLE_PROJECT: "ia-harness",
      API_TIMEOUT_MS: 5000,
      CONFIG_WRITER_URL: "http://writer.test:8791",
      CONFIG_WRITER_TOKEN: "writer-token",
      CONFIG_WRITER_TIMEOUT_MS: 5000,
      BOARD_SERVICE_URL: "http://board.test:8792",
      BOARD_SERVICE_TIMEOUT_MS: 5000,
      BOARD_ACTIONS_TOKEN: "actions-token",
      ...overrides,
    }));
    const forward = await import("./forward");
    vi.doUnmock("./server-env");
    return forward.forwardApiRequest;
  }

  function post(path: string, headers = {}): Request {
    return get(path, {
      method: "POST",
      body: "{}",
      headers: { "content-type": "application/json", ...headers },
    });
  }

  it("503s start and cancel naming BOARD_ACTIONS_TOKEN, sending nothing", async () => {
    const forward = await configured({ BOARD_ACTIONS_TOKEN: null });
    const fetchMock = stubApi();
    for (const path of [
      "/api/tasks/T-014/start",
      "/api/actions/A-1/cancel",
      "/api/epics/T-1/start",
      "/api/epics/T-1/stop",
    ]) {
      const answer = await forward(post(path));
      expect(answer?.status).toBe(503);
      expect(answer?.headers.get("content-type")).toBe("application/json");
      expect((await answer!.json()).error).toContain("BOARD_ACTIONS_TOKEN is not set");
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("503s an approve without the token, and without any project, but forwards one that names its own", async () => {
    const body = JSON.stringify({ commit: "a".repeat(40), text: "ok" });
    const withProject = JSON.stringify({ project: "other", commit: "a".repeat(40), text: "ok" });
    const request = (b: string) =>
      get("/api/tasks/T-014/approve", {
        method: "POST",
        body: b,
        headers: { "content-type": "application/json" },
      });

    const noToken = await configured({ BOARD_ACTIONS_TOKEN: null });
    const fetchMock = stubApi();
    const refused = await noToken(request(withProject));
    expect(refused?.status).toBe(503);
    expect((await refused!.json()).error).toContain("BOARD_ACTIONS_TOKEN is not set");

    const noProject = await configured({ CONSOLE_PROJECT: null });
    const missing = await noProject(request(body));
    expect(missing?.status).toBe(503);
    expect((await missing!.json()).error).toContain("CONSOLE_PROJECT is not set");
    expect(fetchMock).not.toHaveBeenCalled();

    expect((await noProject(request(withProject)))?.status).toBe(200);
    expect(fetchMock.mock.calls[0]![1]!.body).toBe(
      JSON.stringify({ project: "other", commit: "a".repeat(40), text: "ok" }),
    );
  });

  it("503s a request for changes without the token, and without any project, but forwards one that names its own", async () => {
    const tip = "a".repeat(40);
    const request = (b: string) =>
      get("/api/tasks/T-014/request-changes", {
        method: "POST",
        body: b,
        headers: { "content-type": "application/json" },
      });
    const withProject = JSON.stringify({ project: "other", commit: tip, text: "ok" });
    const noToken = await configured({ BOARD_ACTIONS_TOKEN: null });
    const fetchMock = stubApi();
    const refused = await noToken(request(withProject));
    expect(refused?.status).toBe(503);
    expect((await refused!.json()).error).toContain("BOARD_ACTIONS_TOKEN is not set");
    const noProject = await configured({ CONSOLE_PROJECT: null });
    const missing = await noProject(request(JSON.stringify({ commit: tip, text: "ok" })));
    expect(missing?.status).toBe(503);
    expect((await missing!.json()).error).toContain("a request for changes is for");
    expect(fetchMock).not.toHaveBeenCalled();
    expect((await noProject(request(withProject)))?.status).toBe(200);
  });

  it("503s a resolve conflict without the token, and without any project, but forwards one that names its own", async () => {
    const request = (b: string) =>
      get("/api/tasks/T-014/resolve-conflict", {
        method: "POST",
        body: b,
        headers: { "content-type": "application/json" },
      });
    const noToken = await configured({ BOARD_ACTIONS_TOKEN: null });
    const fetchMock = stubApi();
    const refused = await noToken(request('{"project":"other"}'));
    expect(refused?.status).toBe(503);
    expect((await refused!.json()).error).toContain("BOARD_ACTIONS_TOKEN is not set");
    const noProject = await configured({ CONSOLE_PROJECT: null });
    const missing = await noProject(request("{}"));
    expect(missing?.status).toBe(503);
    expect((await missing!.json()).error).toContain("a resolve conflict is for");
    expect(fetchMock).not.toHaveBeenCalled();
    expect((await noProject(request('{"project":"other"}')))?.status).toBe(200);
  });

  it("still 415s before the token, and still forwards the backlog writes", async () => {
    const forward = await configured({ BOARD_ACTIONS_TOKEN: null });
    const fetchMock = stubApi();
    const answer = await forward(post("/api/tasks/T-014/start", { "content-type": "text/plain" }));
    expect(answer?.status).toBe(415);
    expect((await forward(post("/api/tasks/T-014/accept")))?.status).toBe(200);
    expect(target(fetchMock)).toBe(`${BOARD}/api/tasks/T-014/accept`);
  });

  it("forwards the thread-record writes without the token (ADR 195)", async () => {
    const forward = await configured({ BOARD_ACTIONS_TOKEN: null, CONSOLE_PROJECT: null });
    const fetchMock = stubApi('{"id":"th-x"}', 201);
    expect((await forward(post("/api/threads")))?.status).toBe(201);
    expect(target(fetchMock)).toBe(`${BOARD}/api/threads`);
    vi.restoreAllMocks();
    const patched = stubApi();
    const patch = get("/api/threads/main", {
      method: "PATCH",
      body: "{}",
      headers: { "content-type": "application/json" },
    });
    expect((await forward(patch))?.status).toBe(200);
    expect(target(patched)).toBe(`${BOARD}/api/threads/main`);
  });

  it("503s a turn without the token, and needs no CONSOLE_PROJECT", async () => {
    const without = await configured({ BOARD_ACTIONS_TOKEN: null });
    const fetchMock = stubApi();
    const answer = await without(post("/api/threads/main/turns"));
    expect(answer?.status).toBe(503);
    expect((await answer!.json()).error).toContain("BOARD_ACTIONS_TOKEN is not set");
    expect(fetchMock).not.toHaveBeenCalled();

    const noProject = await configured({ CONSOLE_PROJECT: null });
    expect((await noProject(post("/api/threads/main/turns")))?.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("503s the project-record writes without the token, and needs no CONSOLE_PROJECT", async () => {
    const without = await configured({ BOARD_ACTIONS_TOKEN: null });
    const fetchMock = stubApi();
    const patch = () =>
      get("/api/projects/demo", {
        method: "PATCH",
        body: "{}",
        headers: { "content-type": "application/json" },
      });
    for (const request of [post("/api/projects"), patch()]) {
      const answer = await without(request);
      expect(answer?.status).toBe(503);
      const error = (await answer!.json()).error;
      expect(error).toContain("BOARD_ACTIONS_TOKEN is not set");
      expect(error).toContain("project routes");
    }
    expect(fetchMock).not.toHaveBeenCalled();

    const noProject = await configured({ CONSOLE_PROJECT: null });
    expect((await noProject(post("/api/projects")))?.status).toBe(200);
    expect((await noProject(patch()))?.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("503s the push-subscription writes without the token, but still reads the key", async () => {
    const without = await configured({ BOARD_ACTIONS_TOKEN: null, CONSOLE_PROJECT: null });
    const fetchMock = stubApi('{"publicKey":"BAAA"}');
    for (const method of ["POST", "DELETE"]) {
      const answer = await without(
        get("/api/push/subscriptions", {
          method,
          body: "{}",
          headers: { "content-type": "application/json" },
        }),
      );
      expect(answer?.status).toBe(503);
      const error = (await answer!.json()).error;
      expect(error).toContain("BOARD_ACTIONS_TOKEN is not set");
      expect(error).toContain("push routes");
    }
    expect(fetchMock).not.toHaveBeenCalled();
    expect((await without(get("/api/push/key")))?.status).toBe(200);
    expect(target(fetchMock)).toBe(`${BOARD}/api/push/key`);
  });

  it("503s the settings PUT without the token, but still reads the settings", async () => {
    const without = await configured({ BOARD_ACTIONS_TOKEN: null });
    const fetchMock = stubApi('{"kinds":{},"quiet_hours":null}');
    const answer = await without(
      get("/api/push/settings", {
        method: "PUT",
        body: "{}",
        headers: { "content-type": "application/json" },
      }),
    );
    expect(answer?.status).toBe(503);
    expect((await answer!.json()).error).toContain("push routes");
    expect(fetchMock).not.toHaveBeenCalled();
    expect((await without(get("/api/push/settings")))?.status).toBe(200);
    expect(target(fetchMock)).toBe(`${BOARD}/api/push/settings`);
  });

  it("503s a start without CONSOLE_PROJECT, but still forwards a cancel", async () => {
    const forward = await configured({ CONSOLE_PROJECT: null });
    const fetchMock = stubApi();
    const answer = await forward(post("/api/tasks/T-014/start"));
    expect(answer?.status).toBe(503);
    expect((await answer!.json()).error).toContain("CONSOLE_PROJECT is not set");
    expect(fetchMock).not.toHaveBeenCalled();
    expect((await forward(post("/api/epics/T-1/start")))?.status).toBe(503);
    expect(fetchMock).not.toHaveBeenCalled();
    expect((await forward(post("/api/actions/A-1/cancel")))?.status).toBe(200);
    expect(fetchMock.mock.calls[0]![1]!.body).toBe("{}");
    expect((await forward(post("/api/epics/T-1/stop")))?.status).toBe(200);
  });
});

/**
 * `CONFIG_WRITER_TOKEN` unset. The value is read when `server-env.ts` is imported,
 * so the module graph is reset and re-imported under a second mock rather than
 * mutated (ADR 112 *Consequences*).
 */
describe("the settings forward without CONFIG_WRITER_TOKEN", () => {
  async function unconfigured() {
    vi.resetModules();
    vi.doMock("./server-env", () => ({
      API_BASE_URL: "http://api.test:8789",
      API_TOKEN: "test-token",
      CONSOLE_PROJECT: "ia-harness",
      API_TIMEOUT_MS: 5000,
      CONFIG_WRITER_URL: "http://writer.test:8791",
      CONFIG_WRITER_TOKEN: null,
      CONFIG_WRITER_TIMEOUT_MS: 5000,
      BOARD_SERVICE_URL: "http://board.test:8792",
      BOARD_SERVICE_TIMEOUT_MS: 5000,
      BOARD_ACTIONS_TOKEN: "actions-token",
    }));
    const forward = await import("./forward");
    vi.doUnmock("./server-env");
    return forward.forwardApiRequest;
  }

  it("answers a JSON 503 naming the variable, without asking the writer", async () => {
    const forward = await unconfigured();
    const fetchMock = stubApi();
    for (const request of [
      get("/api/settings"),
      get("/api/settings/max_rounds", { method: "PUT", body: "{}" }),
      get("/api/settings/max_rounds", { method: "DELETE", body: "{}" }),
    ]) {
      const answer = await forward(request);
      expect(answer?.status).toBe(503);
      expect(answer?.headers.get("content-type")).toBe("application/json");
      expect((await answer!.json()).error).toContain("CONFIG_WRITER_TOKEN is not set");
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("still answers a wrong method or shape the same way, before the token", async () => {
    const forward = await unconfigured();
    expect((await forward(get("/api/settings", { method: "POST" })))?.status).toBe(405);
    expect((await forward(get("/api/settings/max_rounds")))?.status).toBe(405);
    expect((await forward(get("/api/settings/a/b", { method: "PUT" })))?.status).toBe(404);
  });

  it("leaves the api's routes working", async () => {
    const forward = await unconfigured();
    const fetchMock = stubApi();
    expect((await forward(get("/api/tasks")))?.status).toBe(200);
    expect(target(fetchMock)).toBe(`${BASE}/api/tasks`);
  });
});

describe("the board-service push routes", () => {
  const SUBSCRIPTION = JSON.stringify({
    endpoint: "https://push.example/abc",
    keys: { p256dh: "p", auth: "a" },
  });

  function write(method: string, body: string, headers = {}): Request {
    return get("/api/push/subscriptions", {
      method,
      body,
      headers: {
        "content-type": "application/json",
        "sec-fetch-site": "same-origin",
        origin: "http://console.test",
        authorization: "Bearer from-the-browser",
        ...headers,
      },
    });
  }

  it("forwards the key read to the board-service, with nothing of the browser's", async () => {
    const fetchMock = stubApi('{"publicKey":"BAAA"}');
    const answer = await forwardApiRequest(
      get("/api/push/key?x=1", { headers: { authorization: "Bearer from-the-browser" } }),
    );
    expect(answer?.status).toBe(200);
    expect(await answer?.json()).toEqual({ publicKey: "BAAA" });
    expect(target(fetchMock)).toBe(`${BOARD}/api/push/key`);
    expect(fetchMock.mock.calls[0]![1]!.headers).toEqual({ Accept: "application/json" });
  });

  it("passes the board-service's 404 back when push is off there", async () => {
    stubApi('{"error":"push is off: there is no VAPID key"}', 404);
    const answer = await forwardApiRequest(get("/api/push/key"));
    expect(answer?.status).toBe(404);
    expect((await answer!.json()).error).toContain("push is off");
  });

  it("502s a key read the board-service does not answer, without its address", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("fetch failed"));
    vi.spyOn(console, "error").mockImplementation(() => {});
    const answer = await forwardApiRequest(get("/api/push/key"));
    expect(answer?.status).toBe(502);
    expect(JSON.stringify(await answer!.json())).not.toContain("board.test");
  });

  it.each(["POST", "DELETE"])(
    "forwards a %s of a subscription with the bearer, the body unchanged",
    async (method) => {
      const fetchMock = stubApi('{"endpoint":"https://push.example/abc"}', 201);
      const answer = await forwardApiRequest(write(method, SUBSCRIPTION));
      expect(answer?.status).toBe(201);
      expect(target(fetchMock)).toBe(`${BOARD}/api/push/subscriptions`);
      const init = fetchMock.mock.calls[0]![1]!;
      expect(init.method).toBe(method);
      expect(init.headers).toEqual({
        "Content-Type": "application/json",
        Authorization: "Bearer actions-token",
      });
      expect(new TextDecoder().decode(init.body as ArrayBuffer)).toBe(SUBSCRIPTION);
    },
  );

  it("refuses a subscription write from another site or as a form, sending nothing", async () => {
    const fetchMock = stubApi();
    const cross = await forwardApiRequest(
      write("POST", SUBSCRIPTION, { "sec-fetch-site": "cross-site" }),
    );
    expect(cross?.status).toBe(403);
    const form = await forwardApiRequest(
      write("DELETE", SUBSCRIPTION, { "content-type": "text/plain" }),
    );
    expect(form?.status).toBe(415);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("keeps the answers the other methods had on the push paths", async () => {
    const fetchMock = stubApi();
    expect((await forwardApiRequest(get("/api/push/key", { method: "POST" })))?.status).toBe(405);
    expect((await forwardApiRequest(get("/api/push/subscriptions")))?.status).toBe(404);
    expect(
      (await forwardApiRequest(get("/api/push/subscriptions", { method: "PUT" })))?.status,
    ).toBe(405);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  describe("the settings", () => {
    const CHANGE = '{"kinds":{"merged":false}}';

    function save(body: string, headers = {}, method = "PUT"): Request {
      return get("/api/push/settings", {
        method,
        body,
        headers: {
          "content-type": "application/json",
          "sec-fetch-site": "same-origin",
          origin: "http://console.test",
          authorization: "Bearer from-the-browser",
          ...headers,
        },
      });
    }

    it("forwards the read to the board-service, with nothing of the browser's", async () => {
      const fetchMock = stubApi('{"kinds":{"merged":true},"quiet_hours":null}');
      const answer = await forwardApiRequest(
        get("/api/push/settings?x=1", { headers: { authorization: "Bearer from-the-browser" } }),
      );
      expect(answer?.status).toBe(200);
      expect(await answer?.json()).toEqual({ kinds: { merged: true }, quiet_hours: null });
      expect(target(fetchMock)).toBe(`${BOARD}/api/push/settings`);
      expect(fetchMock.mock.calls[0]![1]!.headers).toEqual({ Accept: "application/json" });
    });

    it("502s a read the board-service does not answer, without its address", async () => {
      vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("fetch failed"));
      vi.spyOn(console, "error").mockImplementation(() => {});
      const answer = await forwardApiRequest(get("/api/push/settings"));
      expect(answer?.status).toBe(502);
      expect(JSON.stringify(await answer!.json())).not.toContain("board.test");
    });

    it("forwards a PUT with the bearer, the body unchanged, and passes a 400 back", async () => {
      const fetchMock = stubApi('{"error":"quiet_hours start equals its end"}', 400);
      const answer = await forwardApiRequest(save(CHANGE));
      expect(answer?.status).toBe(400);
      expect((await answer!.json()).error).toContain("start equals its end");
      expect(target(fetchMock)).toBe(`${BOARD}/api/push/settings`);
      const init = fetchMock.mock.calls[0]![1]!;
      expect(init.method).toBe("PUT");
      expect(init.headers).toEqual({
        "Content-Type": "application/json",
        Authorization: "Bearer actions-token",
      });
      expect(new TextDecoder().decode(init.body as ArrayBuffer)).toBe(CHANGE);
    });

    it("refuses a PUT from another site or as a form, sending nothing", async () => {
      const fetchMock = stubApi();
      const cross = await forwardApiRequest(save(CHANGE, { "sec-fetch-site": "cross-site" }));
      expect(cross?.status).toBe(403);
      const form = await forwardApiRequest(save(CHANGE, { "content-type": "text/plain" }));
      expect(form?.status).toBe(415);
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it("keeps the answers the other methods had", async () => {
      const fetchMock = stubApi();
      expect((await forwardApiRequest(save(CHANGE, {}, "POST")))?.status).toBe(405);
      expect((await forwardApiRequest(save(CHANGE, {}, "DELETE")))?.status).toBe(405);
      expect(fetchMock).not.toHaveBeenCalled();
    });
  });
});
