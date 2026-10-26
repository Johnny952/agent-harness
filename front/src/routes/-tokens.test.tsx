/**
 * The Tokens screen's states (`docs/decisions.md` ADR 489, on ADR 456 decision 7):
 * loading, a failed read, no snapshot, warnings, stale, the three regions, the
 * join with `/api/accounts`, and a row of the wrong shape. The `-` on the
 * filename is load-bearing, as in `-index.test.tsx`.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, screen } from "@testing-library/react";
import { Route } from "./tokens";
import { renderRoute, type Served } from "@/test/render-route";

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

function serve(snap: unknown, warnings: string[] = [], over: Served = {}): Served {
  return {
    "/api/credentials": { data: snap, warnings },
    "/api/accounts": { data: POOL },
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
    // The shell draws buttons of its own; the regions draw none (7c's controls are not here).
    expect(document.querySelectorAll("section button")).toHaveLength(0);
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
