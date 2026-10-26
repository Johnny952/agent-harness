/**
 * The Tokens screen's states (`docs/decisions.md` ADR 489, on ADR 456 decision 7):
 * loading, a failed read, no snapshot, warnings, stale, the three regions, the
 * join with `/api/accounts`, and a row of the wrong shape. The `-` on the
 * filename is load-bearing, as in `-index.test.tsx`.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { Route } from "./tokens";
import { renderRoute, type Sent, type Served } from "@/test/render-route";

afterEach(cleanup);

const NOW = Date.now();
const iso = (secondsAgo: number) => new Date(NOW - secondsAgo * 1000).toISOString();

function snapshot(over: Record<string, unknown> = {}) {
  return {
    version: 1,
    written_at: iso(30),
    stale: false,
    stale_after_seconds: 900,
    git_token: { path: "/root/.git-credentials", state: "present", modified_at: iso(7200) },
    deploy_keys: [
      { project: "alpha", state: "present", modified_at: iso(60), fingerprint: "SHA256:abc123" },
    ],
    accounts: [
      {
        account: "cuenta1",
        container: "agent-cuenta1",
        state: "present",
        modified_at: iso(120),
        logged_in_at: iso(3600),
      },
    ],
    ...over,
  };
}

const POOL = [
  {
    name: "cuenta1",
    container: "agent-cuenta1",
    is_primary: true,
    quota_threshold_pct: 90,
    reserve_pct: 60,
    quota_cooldown_seconds: 1800,
    state: "BUSY",
    current_task: null,
    rate_limited_at: null,
    last_probe: null,
  },
];

const queue = (over: Record<string, unknown> = {}) => ({
  pending: [],
  claimed: [],
  done: [],
  done_total: 0,
  worker: { heartbeat: null, stale: false, stale_after_seconds: 60 },
  ...over,
});
const QUEUE = queue();

function record(id: string, kind: string, over: Record<string, unknown> = {}) {
  return {
    version: 1,
    id,
    kind,
    project: null,
    task_id: null,
    params: null,
    actor: "operator",
    note: null,
    created_at: iso(10),
    claimed_at: null,
    started_at: null,
    finished_at: null,
    status: "pending",
    waiting: null,
    result: null,
    error: null,
    ...over,
  };
}

function serve(snap: unknown, warnings: string[] = [], over: Served = {}): Served {
  return {
    "/api/credentials": { data: snap, warnings },
    "/api/accounts": { data: POOL },
    "/api/actions": { data: QUEUE },
    ...over,
  };
}

async function page(served: Served) {
  const r = await renderRoute(Route, "/tokens", served);
  return r;
}

const text = () => (document.body.textContent ?? "").replace(/\s+/g, " ");

describe("loading and failure", () => {
  it("says it is reading credentials before the reads answer", async () => {
    await page(serve(snapshot()));
    expect(screen.getByText("Reading credentials…")).toBeTruthy();
  });

  it("a failed /api/credentials is the ErrorState and no region", async () => {
    await page({
      "/api/credentials": { status: 500, error: "boom" },
      "/api/accounts": { data: [] },
    });
    expect(await screen.findByText("Credential status could not be read")).toBeTruthy();
    expect(screen.queryByText("GitHub token")).toBeNull();
  });
});

describe("no snapshot", () => {
  it("quotes the no-snapshot warning and names the worker, with no region", async () => {
    await page(serve(null, ["no credentials snapshot yet"]));
    expect(await screen.findByText("no credentials snapshot yet")).toBeTruthy();
    expect(text()).toContain("Check that the worker is running on the Queue screen.");
    expect(screen.queryByText("GitHub token")).toBeNull();
    expect(screen.queryByText("Deploy keys")).toBeNull();
  });

  it("an unreadable file gets the same banner with its reason", async () => {
    await page(serve(null, ["unreadable credentials snapshot: bad json"]));
    expect(await screen.findByText("unreadable credentials snapshot: bad json")).toBeTruthy();
    expect(text()).toContain("until it has, there is nothing to show");
    expect(screen.queryByText("Claude logins")).toBeNull();
  });
});

describe("a fresh snapshot", () => {
  it("draws the three regions with the scope note, fingerprint and login times", async () => {
    await page(serve(snapshot()));
    expect(await screen.findByText("GitHub token")).toBeTruthy();
    expect(screen.getByText("Deploy keys")).toBeTruthy();
    expect(screen.getByText("Claude logins")).toBeTruthy();
    const t = text();
    expect(t).toContain("/root/.git-credentials");
    expect(t).toContain("ADR 36 asks for a fine-grained token with `contents: read`");
    expect(t).toContain("SHA256:abc123");
    expect(t).toContain("signed in from the console");
    expect(t).toContain("1h 0m ago");
    expect(t).not.toContain("stale");
  });

  it.each([
    ["empty", "the file exists and is empty"],
    ["absent", "no file at this path"],
    ["directory", "Docker made a directory here because the host file"],
    ["not_mounted", "the worker has no such path: this compose mounts no token"],
  ])("gives the git token a hint for %s", async (state, hint) => {
    await page(serve(snapshot({ git_token: { path: "/p", state, modified_at: null } })));
    await screen.findByText("GitHub token");
    expect(text()).toContain(hint);
    expect(text()).toContain("never written");
  });

  it("joins the pool state by account name, and says when there is none", async () => {
    await page(
      serve(
        snapshot({
          accounts: [
            {
              account: "cuenta1",
              container: "c1",
              state: "present",
              modified_at: null,
              logged_in_at: null,
            },
            {
              account: "ghost",
              container: "c9",
              state: "absent",
              modified_at: null,
              logged_in_at: null,
            },
          ],
        }),
      ),
    );
    await screen.findByText("Claude logins");
    expect(screen.getByText("BUSY")).toBeTruthy();
    expect(screen.getByText("not in the pool")).toBeTruthy();
    expect(screen.getAllByText("never from the console")).toHaveLength(2);
  });
});

describe("warnings, stale, and a broken join", () => {
  it("shows the warnings of both reads", async () => {
    await page(
      serve(snapshot(), ["credentials warning one"], {
        "/api/accounts": { data: POOL, warnings: ["accounts warning two"] },
      }),
    );
    expect(await screen.findByText("credentials warning one")).toBeTruthy();
    expect(screen.getByText("accounts warning two")).toBeTruthy();
    expect(screen.getByText("GitHub token")).toBeTruthy();
  });

  it("a stale snapshot is a banner with its age, and the rows stay", async () => {
    await page(serve(snapshot({ stale: true, written_at: iso(1200) })));
    await screen.findByText("GitHub token");
    expect(text()).toMatch(/The credentials snapshot is 20m \d+s old, written \d{4}-/);
    expect(text()).toContain("stale past 15 minutes");
    expect(screen.getByText("Deploy keys")).toBeTruthy();
  });

  it("an unreadable written_at is of unknown age", async () => {
    await page(serve(snapshot({ stale: true, written_at: "yesterday-ish" })));
    await screen.findByText("GitHub token");
    expect(text()).toContain("The credentials snapshot is of unknown age");
  });

  it("a failed /api/accounts is a broken line, draws no pool state, keeps the rows", async () => {
    await page(serve(snapshot(), [], { "/api/accounts": { status: 500, error: "down" } }));
    expect(
      await screen.findByText(
        "/api/accounts did not answer: the Claude logins' pool state is not shown",
      ),
    ).toBeTruthy();
    expect(screen.getByText("cuenta1")).toBeTruthy();
    expect(screen.queryByText("not in the pool")).toBeNull();
    expect(screen.queryByText("BUSY")).toBeNull();
  });
});

describe("wrong shapes and empty lists", () => {
  it("deploy_keys that is a string is Malformed in its region only", async () => {
    await page(serve(snapshot({ deploy_keys: "oops" })));
    expect(await screen.findByText(/deploy_keys — a string, not a list/)).toBeTruthy();
    expect(screen.getByText("Claude logins")).toBeTruthy();
    expect(text()).toContain("/root/.git-credentials");
  });

  it("git_token that is not an object is Malformed", async () => {
    await page(serve(snapshot({ git_token: [] })));
    expect(await screen.findByText(/git_token — a list, not an object/)).toBeTruthy();
  });

  it("one bad item is one Malformed row among good ones", async () => {
    await page(
      serve(
        snapshot({
          deploy_keys: [
            { project: "alpha", state: "present", modified_at: null, fingerprint: null },
            "junk",
            { project: 7, state: "present" },
          ],
        }),
      ),
    );
    expect(await screen.findByText("alpha")).toBeTruthy();
    expect(screen.getByText(/deploy_keys\[1\] — a string/)).toBeTruthy();
    expect(
      screen.getByText(/deploy_keys\[2\] — a number|deploy_keys\[2\] — an object/),
    ).toBeTruthy();
    expect(screen.getByText("no fingerprint")).toBeTruthy();
  });

  it("a numeric state is Malformed in its cell", async () => {
    await page(
      serve(
        snapshot({
          accounts: [
            {
              account: "cuenta1",
              container: "c1",
              state: 3,
              modified_at: null,
              logged_in_at: null,
            },
          ],
        }),
      ),
    );
    expect(await screen.findByText(/state — a number, not a string/)).toBeTruthy();
    expect(screen.getByText("cuenta1")).toBeTruthy();
  });

  it("an unknown state string renders verbatim", async () => {
    await page(
      serve(snapshot({ git_token: { path: "/p", state: "weird", modified_at: "not a date" } })),
    );
    expect(await screen.findByText("weird")).toBeTruthy();
    expect(screen.getByText("not a date")).toBeTruthy();
  });

  it("empty lists are EmptyStates in their regions", async () => {
    await page(serve(snapshot({ deploy_keys: [], accounts: [] })));
    expect(await screen.findByText("No project has a record or a key directory")).toBeTruthy();
    expect(screen.getByText("No account is configured")).toBeTruthy();
  });
});

// ---- ADR 492: the controls ------------------------------------------------

const SECRET = "ghp_SECRET_TOKEN_VALUE";
const posts = (sent: Sent[]) => sent.filter((s) => s.method === "POST");
const field = (label: string) => screen.getByLabelText(label) as HTMLInputElement;

describe("no controls without a snapshot", () => {
  it("the ErrorState and the no-snapshot banner draw none", async () => {
    await page(serve(null, ["no credentials snapshot yet"]));
    await screen.findByText("no credentials snapshot yet");
    expect(screen.queryByText("Renew")).toBeNull();
    expect(screen.queryByText("Sign in again")).toBeNull();
    cleanup();
    await page({
      "/api/credentials": { status: 500, error: "boom" },
      "/api/accounts": { data: [] },
    });
    await screen.findByText("Credential status could not be read");
    expect(screen.queryByText("Renew")).toBeNull();
  });

  it("a stale snapshot has them, a malformed row has none", async () => {
    await page(
      serve(
        snapshot({
          stale: true,
          deploy_keys: [{ project: 7 }],
          accounts: [{ account: 7 }],
        }),
      ),
    );
    await screen.findByText("GitHub token");
    expect(screen.getByText("Renew")).toBeTruthy();
    expect(screen.queryByText("Check")).toBeNull();
    expect(screen.queryByText("Sign in again")).toBeNull();
  });

  it("a failed /api/actions adds its broken line", async () => {
    await page(serve(snapshot(), [], { "/api/actions": { status: 500, error: "down" } }));
    expect(
      await screen.findByText(
        "/api/actions did not answer: a sign-in started elsewhere is not shown",
      ),
    ).toBeTruthy();
    expect(screen.getByText("Renew")).toBeTruthy();
  });
});

describe("Renew", () => {
  const accepted = { bare: record("R1", "git_token_renew"), status: 201 };

  it("posts the token and clears the field; username empty is left out", async () => {
    const r = await page(
      serve(snapshot(), [], {
        "POST /api/credentials/git-token": accepted,
        "/api/actions/R1": { data: record("R1", "git_token_renew") },
      }),
    );
    await screen.findByText("GitHub token");
    const renew = screen.getByText("Renew") as HTMLButtonElement;
    expect(renew.disabled).toBe(true);
    expect(field("Token").type).toBe("password");
    expect(field("Token").autocomplete).toBe("off");
    fireEvent.change(field("Token"), { target: { value: SECRET } });
    fireEvent.click(renew);
    expect(await screen.findByText("queued as R1, waiting for the worker")).toBeTruthy();
    expect(posts(r.sent)).toEqual([
      { method: "POST", path: "/api/credentials/git-token", body: { token: SECRET } },
    ]);
    expect(field("Token").value).toBe("");
  });

  it("sends the username when one is given, and keeps it", async () => {
    const r = await page(
      serve(snapshot(), [], {
        "POST /api/credentials/git-token": accepted,
        "/api/actions/R1": { data: record("R1", "git_token_renew") },
      }),
    );
    await screen.findByText("GitHub token");
    fireEvent.change(field("Token"), { target: { value: SECRET } });
    fireEvent.change(field("Username"), { target: { value: " bot " } });
    fireEvent.click(screen.getByText("Renew"));
    await screen.findByText("queued as R1, waiting for the worker");
    expect(posts(r.sent)[0]?.body).toEqual({ token: SECRET, username: "bot" });
    expect(field("Username").value).toBe(" bot ");
  });

  it("a done renewal reads written, with its clock", async () => {
    await page(
      serve(snapshot(), [], {
        "POST /api/credentials/git-token": accepted,
        "/api/actions/R1": {
          data: record("R1", "git_token_renew", { status: "done", finished_at: iso(5) }),
        },
      }),
    );
    await screen.findByText("GitHub token");
    fireEvent.change(field("Token"), { target: { value: SECRET } });
    fireEvent.click(screen.getByText("Renew"));
    await waitFor(() => expect(text()).toMatch(/written \d{4}-/));
  });

  it("a failed renewal quotes its error", async () => {
    await page(
      serve(snapshot(), [], {
        "POST /api/credentials/git-token": accepted,
        "/api/actions/R1": {
          data: record("R1", "git_token_renew", { status: "failed", error: "token rejected" }),
        },
      }),
    );
    await screen.findByText("GitHub token");
    fireEvent.change(field("Token"), { target: { value: SECRET } });
    fireEvent.click(screen.getByText("Renew"));
    expect(await screen.findByText("Renewal failed: token rejected")).toBeTruthy();
  });

  it.each([
    [400, "token is empty"],
    [409, "a renewal is already queued"],
    [503, "actions are not configured"],
  ])("quotes a %s under the form, clears the field, leaks nothing", async (status, message) => {
    const spies = (["log", "error", "warn", "info", "debug"] as const).map((m) =>
      vi.spyOn(console, m).mockImplementation(() => {}),
    );
    const href = window.location.href;
    await page(
      serve(snapshot(), [], { "POST /api/credentials/git-token": { status, error: message } }),
    );
    await screen.findByText("GitHub token");
    fireEvent.change(field("Token"), { target: { value: SECRET } });
    fireEvent.click(screen.getByText("Renew"));
    expect(await screen.findByText(`Refused (${status}): ${message}`)).toBeTruthy();
    expect(field("Token").value).toBe("");
    expect(document.body.innerHTML).not.toContain(SECRET);
    expect(window.location.href).toBe(href);
    for (const s of spies) expect(JSON.stringify(s.mock.calls)).not.toContain(SECRET);
  });
});

describe("Replace key and Check", () => {
  const keyed = (over: Served = {}) =>
    serve(snapshot(), [], {
      "POST /api/projects/alpha/keygen": { bare: record("K1", "keygen"), status: 201 },
      "POST /api/projects/alpha/check": { bare: record("C1", "check"), status: 201 },
      ...over,
    });

  it("asks first; Keep it sends nothing", async () => {
    const r = await page(keyed());
    await screen.findByText("Deploy keys");
    fireEvent.click(screen.getByText("Replace key"));
    expect(text()).toContain(
      "Replacing alpha's key makes its fetch and push fail until the new public half is added on GitHub as a deploy key.",
    );
    fireEvent.click(screen.getByText("Keep it"));
    expect(screen.queryByText("Replace the key")).toBeNull();
    expect(posts(r.sent)).toHaveLength(0);
  });

  it("Replace the key posts replace:true and shows the public half and fingerprint", async () => {
    const r = await page(
      keyed({
        "/api/actions/K1": {
          data: record("K1", "keygen", {
            status: "done",
            result: { public: "ssh-ed25519 AAAAC3 harness", fingerprint: "SHA256:newfp" },
          }),
        },
      }),
    );
    await screen.findByText("Deploy keys");
    fireEvent.click(screen.getByText("Replace key"));
    fireEvent.click(screen.getByText("Replace the key"));
    const pub = await screen.findByTestId("public-key");
    expect(pub.textContent).toBe("ssh-ed25519 AAAAC3 harness");
    expect(text()).toContain("SHA256:newfp");
    expect(text()).toContain("Add it on GitHub");
    expect(posts(r.sent)).toEqual([
      { method: "POST", path: "/api/projects/alpha/keygen", body: { replace: true } },
    ]);
  });

  it("a keygen without a public half is a warning", async () => {
    await page(
      keyed({
        "/api/actions/K1": { data: record("K1", "keygen", { status: "done", result: {} }) },
      }),
    );
    await screen.findByText("Deploy keys");
    fireEvent.click(screen.getByText("Replace key"));
    fireEvent.click(screen.getByText("Replace the key"));
    expect(await screen.findByText("the finished keygen reported no public key")).toBeTruthy();
  });

  it("quotes a refused replace under its control", async () => {
    await page(
      keyed({ "POST /api/projects/alpha/keygen": { status: 409, error: "a keygen is queued" } }),
    );
    await screen.findByText("Deploy keys");
    fireEvent.click(screen.getByText("Replace key"));
    fireEvent.click(screen.getByText("Replace the key"));
    expect(await screen.findByText("Refused (409): a keygen is queued")).toBeTruthy();
  });

  it("Check posts an empty body", async () => {
    const r = await page(
      keyed({ "/api/actions/C1": { data: record("C1", "check", { status: "running" }) } }),
    );
    await screen.findByText("Deploy keys");
    fireEvent.click(screen.getByText("Check"));
    expect(await screen.findByText("running")).toBeTruthy();
    expect(posts(r.sent)).toEqual([
      { method: "POST", path: "/api/projects/alpha/check", body: {} },
    ]);
  });

  it.each([
    [{ reachable: true, writable: true, detail: "ok" }, "would take a push from it. ok"],
    [{ reachable: true, writable: false, detail: "ro" }, "refuses a push from it."],
    [{ reachable: true, writable: null, detail: "" }, "write access was not tested."],
    [{ reachable: false, detail: "timeout" }, "does not answer this key yet: timeout"],
  ])("reads the check result %j", async (result, sentence) => {
    await page(
      keyed({
        "/api/actions/C1": { data: record("C1", "check", { status: "done", result }) },
      }),
    );
    await screen.findByText("Deploy keys");
    fireEvent.click(screen.getByText("Check"));
    await waitFor(() => expect(text()).toContain(sentence));
  });

  it("a failed check quotes its error and a missing record says so", async () => {
    await page(
      keyed({
        "/api/actions/C1": { data: record("C1", "check", { status: "interrupted", error: null }) },
      }),
    );
    await screen.findByText("Deploy keys");
    fireEvent.click(screen.getByText("Check"));
    expect(await screen.findByText("Check interrupted: no error was recorded")).toBeTruthy();
    cleanup();
    await page(keyed({ "/api/actions/C1": { status: 404, error: "no such action" } }));
    await screen.findByText("Deploy keys");
    fireEvent.click(screen.getByText("Check"));
    expect(await screen.findByText("the queue has no record of C1")).toBeTruthy();
  });
});

describe("the login", () => {
  const URL = "https://claude.ai/oauth/authorize?x=1";
  const started = (rec: unknown, over: Served = {}) =>
    serve(snapshot(), [], {
      "POST /api/accounts/cuenta1/login": { bare: record("L1", "account_login"), status: 201 },
      "/api/actions/L1": { data: rec },
      ...over,
    });
  const login = (status: string, result: unknown = null, error: string | null = null) =>
    record("L1", "account_login", { status, result, error, params: { account: "cuenta1" } });
  async function signIn(served: Served) {
    const r = await page(served);
    await screen.findByText("Claude logins");
    fireEvent.click(screen.getByText("Sign in again"));
    return r;
  }

  it("waits for the URL, then links it with a code field", async () => {
    const r = await signIn(started(login("pending")));
    expect(await screen.findByText("waiting for the sign-in URL")).toBeTruthy();
    expect(posts(r.sent)).toEqual([
      { method: "POST", path: "/api/accounts/cuenta1/login", body: {} },
    ]);
    expect((screen.getByText("Sign in again") as HTMLButtonElement).disabled).toBe(true);
    cleanup();
    await signIn(started(login("running", { url: URL, awaiting: "code" })));
    const link = (await screen.findByText(URL)) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe(URL);
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(field("Code").type).toBe("password");
  });

  it("a URL that is not https is Malformed and no link", async () => {
    await signIn(started(login("running", { url: "javascript:alert(1)", awaiting: "code" })));
    expect(await screen.findByText(/url — a string, not an https URL/)).toBeTruthy();
    expect(document.querySelector("a[href^='javascript']")).toBeNull();
    expect(screen.queryByLabelText("Code")).toBeNull();
  });

  it("Send code posts the code and clears the field", async () => {
    const r = await signIn(
      started(login("running", { url: URL, awaiting: "code" }), {
        "POST /api/actions/L1/login-code": { bare: { ok: true }, status: 200 },
      }),
    );
    await screen.findByLabelText("Code");
    fireEvent.change(field("Code"), { target: { value: "ABC-123" } });
    fireEvent.click(screen.getByText("Send code"));
    await waitFor(() => expect(field("Code").value).toBe(""));
    expect(posts(r.sent).at(-1)).toEqual({
      method: "POST",
      path: "/api/actions/L1/login-code",
      body: { code: "ABC-123" },
    });
    expect(document.body.innerHTML).not.toContain("ABC-123");
  });

  it("a refused code is quoted and the field is cleared anyway", async () => {
    await signIn(
      started(login("running", { url: URL, awaiting: "code" }), {
        "POST /api/actions/L1/login-code": { status: 409, error: "not awaiting a code" },
      }),
    );
    await screen.findByLabelText("Code");
    fireEvent.change(field("Code"), { target: { value: "ABC-123" } });
    fireEvent.click(screen.getByText("Send code"));
    expect(await screen.findByText("Refused (409): not awaiting a code")).toBeTruthy();
    expect(field("Code").value).toBe("");
    expect(document.body.innerHTML).not.toContain("ABC-123");
  });

  it("reads signing in, signed in, released, and failed", async () => {
    await signIn(started(login("running", { url: URL, awaiting: null, code_at: iso(3) })));
    expect(await screen.findByText("signing in")).toBeTruthy();
    cleanup();
    await signIn(started(login("done", { logged_in_at: iso(2), released: true })));
    await waitFor(() => expect(text()).toContain("released from AUTH_EXPIRED"));
    expect(text()).toMatch(/signed in \d{4}-/);
    cleanup();
    await signIn(started(login("failed", null, "login timed out")));
    expect(await screen.findByText("Sign-in failed: login timed out")).toBeTruthy();
    expect(screen.queryByText("Cancel")).toBeNull();
  });

  it("quotes a refused sign-in", async () => {
    await signIn(
      started(login("pending"), {
        "POST /api/accounts/cuenta1/login": { status: 409, error: "a login is open" },
      }),
    );
    expect(await screen.findByText("Refused (409): a login is open")).toBeTruthy();
  });

  it("Cancel is drawn while open, posts, and the record ends cancelled", async () => {
    const r = await signIn(
      started(login("pending"), {
        "POST /api/actions/L1/cancel": { bare: record("X1", "cancel"), status: 201 },
      }),
    );
    fireEvent.click(await screen.findByText("Cancel"));
    expect(await screen.findByText("cancel queued as X1")).toBeTruthy();
    expect(posts(r.sent).at(-1)).toEqual({
      method: "POST",
      path: "/api/actions/L1/cancel",
      body: {},
    });
    cleanup();
    await signIn(started(login("cancelled")));
    expect(await screen.findByText("cancelled")).toBeTruthy();
    expect(screen.queryByText("Cancel")).toBeNull();
  });

  it("quotes a refused cancel under Cancel", async () => {
    await signIn(
      started(login("running"), {
        "POST /api/actions/L1/cancel": { status: 409, error: "already final" },
      }),
    );
    fireEvent.click(await screen.findByText("Cancel"));
    expect(await screen.findByText("Refused (409): already final")).toBeTruthy();
  });

  it("adopts an open sign-in from the queue with no click", async () => {
    const open = record("L9", "account_login", {
      status: "running",
      params: { account: "cuenta1" },
      result: { url: URL, awaiting: "code" },
    });
    const other = record("L8", "account_login", { params: { account: "ghost" } });
    const r = await page(
      serve(snapshot(), [], {
        "/api/actions": { data: queue({ claimed: [open], pending: [other] }) },
        "/api/actions/L9": { data: open },
      }),
    );
    expect(await screen.findByText(URL)).toBeTruthy();
    expect(posts(r.sent)).toHaveLength(0);
    expect(screen.getByText("Cancel")).toBeTruthy();
  });
});
