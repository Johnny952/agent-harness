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
}));

const BASE = "http://api.test:8789";

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
      error: "/api/threads is not one of the seven routes the console forwards.",
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
