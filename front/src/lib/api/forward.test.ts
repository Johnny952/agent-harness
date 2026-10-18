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
}));

const BASE = "http://api.test:8789";
const WRITER = "http://writer.test:8791";

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

  // `/api/threads` and not `/api/learnings`, which was the example until T-016
  // forwarded it: ADR 19 puts the chat dock's route in no tier at all, so this
  // is a path the console refuses and will go on refusing.
  it("404s a path it does not serve, without asking the api", async () => {
    const fetchMock = stubApi();
    const answer = await forwardApiRequest(get("/api/threads"));
    expect(answer?.status).toBe(404);
    expect(await answer?.json()).toEqual({
      error: "/api/threads is not one of the ten routes the console forwards.",
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

  it("takes ?project= from its own configuration and never from the browser", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(get("/api/debt?project=some-other-project"));
    expect(target(fetchMock)).toBe(`${BASE}/api/debt?project=ia-harness`);
  });

  // Both project-scoped reads, and the slug is the console's own configuration
  // on each: the api takes `?project=` as optional with one checkout and
  // required with several (`docs/decisions.md` ADR 41 part 2).
  it("sends its own ?project= on the learnings route too", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(get("/api/learnings?project=some-other-project"));
    expect(target(fetchMock)).toBe(`${BASE}/api/learnings?project=ia-harness`);
  });

  // The third: the roadmap reads one project's `docs/plans/`, so the slug that
  // picks it is the console's and not the browser's (T-075).
  it("sends its own ?project= on the roadmap route too", async () => {
    const fetchMock = stubApi();
    await forwardApiRequest(get("/api/roadmap?project=some-other-project"));
    expect(target(fetchMock)).toBe(`${BASE}/api/roadmap?project=ia-harness`);
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
      error: "/api/settingsx is not one of the ten routes the console forwards.",
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
