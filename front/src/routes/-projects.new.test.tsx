/**
 * The New project form (ADR NNN): the record write, then the setup actions it
 * queues — keygen, the public key and its deploy-key instruction, Check and
 * Clone for a remote; Init for an empty repository — each outcome read back from
 * `GET /api/actions` by the id the queueing answered.
 *
 * The `-` on the filename is load-bearing; see `-index.test.tsx`'s header.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { Route } from "./projects.new";
import { findAction } from "@/lib/project-setup";
import { renderRoute, type Answer } from "@/test/render-route";
import type { ActionQueue, ActionRecord, ProjectRecord } from "@/lib/api/types";

afterEach(cleanup);

const REMOTE = "git@github.com:acme/shop.git";
const PUBLIC = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakePublicHalfOnly harness-shop";

function project(over: Partial<ProjectRecord> = {}): ProjectRecord {
  return {
    slug: "shop",
    title: "shop",
    remote: REMOTE,
    base_branch: null,
    key: null,
    settings: {},
    created: "2026-10-20T10:00:00Z",
    status: "active",
    ...over,
  };
}

function action(id: string, kind: string, over: Partial<ActionRecord> = {}): ActionRecord {
  return {
    version: 1,
    id,
    kind,
    project: "shop",
    task_id: null,
    params: {},
    actor: "operator",
    note: null,
    created_at: "2026-10-20T10:00:00Z",
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

function queue(over: Partial<ActionQueue> = {}): Answer {
  return {
    data: {
      pending: [],
      claimed: [],
      done: [],
      done_total: 0,
      worker: { heartbeat: null, stale: false, stale_after_seconds: 30 },
      ...over,
    },
  };
}

const KEY_DONE = action("a-key", "keygen", {
  status: "done",
  result: { public: PUBLIC, fingerprint: "SHA256:abc", replaced: false },
});

async function fill(slug: string, remote: string | null) {
  fireEvent.change(await screen.findByLabelText("Slug"), { target: { value: slug } });
  if (remote === null) fireEvent.click(screen.getByLabelText("Empty repository"));
  else fireEvent.change(screen.getByLabelText("Remote"), { target: { value: remote } });
  fireEvent.click(screen.getByRole("button", { name: "Create project" }));
}

describe("findAction", () => {
  it("finds an action in any list, and nothing for an id not answered", () => {
    const q = queue({ claimed: [action("a-1", "clone")], done: [KEY_DONE] }) as {
      data: ActionQueue;
    };
    expect(findAction(q.data, "a-key")?.kind).toBe("keygen");
    expect(findAction(q.data, "a-1")?.kind).toBe("clone");
    expect(findAction(q.data, "a-none")).toBeUndefined();
    expect(findAction(undefined, "a-1")).toBeUndefined();
  });
});

describe("New project", () => {
  it("writes the record, queues a keygen, and shows only the public key with the deploy-key step", async () => {
    const { sent } = await renderRoute(Route, "/projects/new", {
      "POST /api/projects": { bare: project(), status: 201 },
      "POST /api/projects/shop/keygen": { bare: action("a-key", "keygen"), status: 201 },
      "/api/actions": [queue(), queue({ done: [KEY_DONE] })],
    });
    await fill("shop", REMOTE);

    expect((await screen.findByTestId("public-key")).textContent).toBe(PUBLIC);
    expect(screen.getByText("SHA256:abc")).toBeTruthy();
    expect(screen.getByText(/Settings → Deploy keys/)).toBeTruthy();
    const writes = sent.filter((s) => s.method === "POST");
    expect(writes).toEqual([
      { method: "POST", path: "/api/projects", body: { slug: "shop", remote: REMOTE } },
      { method: "POST", path: "/api/projects/shop/keygen", body: {} },
    ]);
    expect((screen.getByRole("button", { name: "Check" }) as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it("Check reports a remote that does not answer yet, and Clone queues the clone", async () => {
    const { sent } = await renderRoute(Route, "/projects/new", {
      "POST /api/projects": {
        bare: project({ settings: { push_task_branch: false } }),
        status: 201,
      },
      "POST /api/projects/shop/keygen": { bare: action("a-key", "keygen"), status: 201 },
      "POST /api/projects/shop/check": { bare: action("a-chk", "check"), status: 201 },
      "POST /api/projects/shop/clone": { bare: action("a-cl", "clone"), status: 201 },
      "/api/actions": [
        queue(),
        queue({ done: [KEY_DONE] }),
        queue({
          done: [
            KEY_DONE,
            action("a-chk", "check", {
              status: "done",
              result: { reachable: false, detail: "Permission denied (publickey)" },
            }),
          ],
        }),
        queue({
          done: [
            KEY_DONE,
            action("a-cl", "clone", { status: "done", result: { detail: "cloned main" } }),
          ],
        }),
      ],
    });
    await fill("shop", REMOTE);
    expect(await screen.findByText(/Leave write access off/)).toBeTruthy();

    fireEvent.click(await screen.findByRole("button", { name: "Check" }));
    expect(await screen.findByText(/does not answer this key yet: Permission denied/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Clone" }));
    expect(await screen.findByText(/Clone done: cloned main/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open shop" }).getAttribute("href")).toBe("/p/shop");
    expect(sent.filter((s) => s.method === "POST").map((s) => s.path)).toEqual([
      "/api/projects",
      "/api/projects/shop/keygen",
      "/api/projects/shop/check",
      "/api/projects/shop/clone",
    ]);
  });

  it("an empty repository has no remote and queues an init, no key", async () => {
    const { sent } = await renderRoute(Route, "/projects/new", {
      "POST /api/projects": { bare: project({ remote: null }), status: 201 },
      "POST /api/projects/shop/init": { bare: action("a-in", "init"), status: 201 },
      "/api/actions": [
        queue(),
        queue({
          done: [action("a-in", "init", { status: "done", result: { detail: "empty main" } })],
        }),
      ],
    });
    await fill("shop", null);
    expect(await screen.findByText(/Init done: empty main/)).toBeTruthy();
    expect(screen.queryByTestId("public-key")).toBeNull();
    expect(sent.filter((s) => s.method === "POST")).toEqual([
      { method: "POST", path: "/api/projects", body: { slug: "shop", remote: null } },
      { method: "POST", path: "/api/projects/shop/init", body: {} },
    ]);
  });

  it("a failed keygen shows its error and offers a replacing key", async () => {
    const { sent } = await renderRoute(Route, "/projects/new", {
      "POST /api/projects": { bare: project(), status: 201 },
      "POST /api/projects/shop/keygen": [
        { bare: action("a-key", "keygen"), status: 201 },
        { bare: action("a-key2", "keygen"), status: 201 },
      ],
      "/api/actions": [
        queue(),
        queue({
          done: [
            action("a-key", "keygen", {
              status: "failed",
              error: "shop already has a deploy key; replacing it was not asked",
            }),
          ],
        }),
      ],
    });
    await fill("shop", REMOTE);
    expect(await screen.findByText(/already has a deploy key/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /replacing the old one/ }));
    await waitFor(() =>
      expect(sent.filter((s) => s.method === "POST").at(-1)).toEqual({
        method: "POST",
        path: "/api/projects/shop/keygen",
        body: { replace: true },
      }),
    );
  });

  it("a refused record shows the service's message and queues nothing", async () => {
    const { sent } = await renderRoute(Route, "/projects/new", {
      "POST /api/projects": { status: 409, error: "project 'shop' already has a record" },
      "/api/actions": queue(),
    });
    await fill("shop", REMOTE);
    expect(await screen.findByText("project 'shop' already has a record")).toBeTruthy();
    expect(sent.filter((s) => s.method === "POST").map((s) => s.path)).toEqual(["/api/projects"]);
  });
});
