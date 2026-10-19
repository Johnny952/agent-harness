/**
 * The Queue screen, whole, through `renderRoute` (ADR 163). `sent` keeps only a
 * request's pathname, so the `limit` the screen asked for is read off the fetch
 * mock's calls. `renderRoute` draws no Toaster, so a toast is asserted at its call.
 *
 * The `-` on the filename keeps the route plugin from reading it as a route.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { toast } from "sonner";
import { Route } from "./queue";
import { renderRoute, type Served } from "@/test/render-route";
import type { ActionQueue, ActionRecord } from "@/lib/api/types";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

afterEach(cleanup);

const FRESH = {
  heartbeat: { beat_at: new Date().toISOString() },
  stale: false,
  stale_after_seconds: 30,
};

const EMPTY: ActionQueue = {
  pending: [],
  claimed: [],
  done: [],
  done_total: 0,
  worker: FRESH,
};

function record(id: string, over: Partial<ActionRecord> = {}): ActionRecord {
  return {
    version: 1,
    id,
    kind: "start",
    project: "ia-harness",
    task_id: "T-401",
    params: null,
    actor: "operator",
    note: null,
    created_at: "2026-10-19T12:00:00Z",
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

const A = "a-20261019T120000000000Z-ab12";
const B = "a-20261019T120100000000Z-cd34";

const queueOf = (over: Partial<ActionQueue>) => ({ data: { ...EMPTY, ...over } });

async function queue(answer: Served[string], extra: Served = {}) {
  return renderRoute(Route, "/queue", { "/api/actions": answer, ...extra });
}

describe("the Queue screen's states", () => {
  it("asks for twenty finished records", async () => {
    const { fetchMock } = await queue(queueOf({ pending: [record(A)] }));
    await screen.findByText("T-401");
    const urls = fetchMock.mock.calls.map((c) => String(c[0]));
    expect(urls).toContain("/api/actions?limit=20");
  });

  it("shows a pending start with what it waits for, in take order", async () => {
    await queue(
      queueOf({
        pending: [
          record(A, { waiting: "account", note: "go", actor: "operator" }),
          record(B, { task_id: "T-402", waiting: "project" }),
        ],
      }),
    );
    expect(await screen.findByText("waiting for an account")).toBeTruthy();
    expect(screen.getByText("waiting for the project")).toBeTruthy();
    expect(screen.getByText("go")).toBeTruthy();
    expect(screen.getByText("Pending · 2")).toBeTruthy();
    const ids = screen.getAllByText(/^T-40\d$/).map((e) => e.textContent);
    expect(ids).toEqual(["T-401", "T-402"]);
  });

  it("shows a claimed start under Running", async () => {
    await queue(
      queueOf({ claimed: [record(A, { status: "claimed", claimed_at: "2026-10-19T12:00:30Z" })] }),
    );
    expect(await screen.findByText("Running · 1")).toBeTruthy();
    expect(screen.getByText("Nothing is waiting.")).toBeTruthy();
  });

  it("shows a failed done record with its error, and how many are beyond the list", async () => {
    await queue(
      queueOf({
        done: [
          record(A, {
            status: "failed",
            error: "the worktree could not be created",
            finished_at: "2026-10-19T12:05:00Z",
          }),
          record(B, { status: "done", task_id: "T-402" }),
        ],
        done_total: 7,
      }),
    );
    expect(await screen.findByText("the worktree could not be created")).toBeTruthy();
    expect(screen.getByText("failed")).toBeTruthy();
    expect(screen.getByText("done")).toBeTruthy();
    expect(screen.getByText("Finished · 2 of 7")).toBeTruthy();
  });

  it("says nothing has been queued when all three lists are empty", async () => {
    await queue({ data: EMPTY });
    expect(await screen.findByText("Nothing has been queued")).toBeTruthy();
  });

  it("says nothing is taken when the heartbeat is stale, with the threshold", async () => {
    await queue(
      queueOf({
        pending: [record(A)],
        worker: {
          heartbeat: { beat_at: "2026-10-19T11:00:00Z" },
          stale: true,
          stale_after_seconds: 45,
        },
      }),
    );
    expect(await screen.findByText(/older than 45 seconds/)).toBeTruthy();
  });

  it("says nothing is taken when no heartbeat was ever written", async () => {
    await queue(
      queueOf({
        pending: [record(A)],
        worker: { heartbeat: null, stale: true, stale_after_seconds: 30 },
      }),
    );
    expect(await screen.findByText(/No worker has written a heartbeat/)).toBeTruthy();
  });

  it("shows no worker line for a fresh heartbeat", async () => {
    await queue(queueOf({ pending: [record(A)] }));
    await screen.findByText("T-401");
    expect(screen.queryByText(/heartbeat/)).toBeNull();
  });

  it("says the read failed instead of drawing groups", async () => {
    await queue({ status: 502, error: "upstream down" });
    expect(await screen.findByText("The action queue could not be read")).toBeTruthy();
    expect(screen.queryByText(/Pending ·/)).toBeNull();
  });

  it("names a group that is not a list and still draws the others", async () => {
    await queue({
      data: { ...EMPTY, pending: "oops", claimed: [record(A, { status: "claimed" })] },
    });
    expect(await screen.findByText(/pending — a string, not a list/)).toBeTruthy();
    expect(screen.getByText("Running · 1")).toBeTruthy();
  });
});

describe("Cancel", () => {
  const cancelButtons = () => screen.queryAllByRole("button", { name: /^Cancel a-/ });

  it("is on a pending or claimed start only", async () => {
    await queue(
      queueOf({
        pending: [record(A), record("a-cancel", { kind: "cancel", params: { target: A } })],
        claimed: [record(B, { status: "claimed", task_id: "T-402" })],
        done: [record("a-done", { status: "done", task_id: "T-403" })],
        done_total: 1,
      }),
    );
    await screen.findByText("T-402");
    expect(cancelButtons().map((b) => b.getAttribute("aria-label"))).toEqual([
      `Cancel ${A}`,
      `Cancel ${B}`,
    ]);
    expect(screen.getByText(`→ ${A}`)).toBeTruthy();
  });

  it("posts an empty body to the action's cancel path after a confirmation, then reads again", async () => {
    const { sent } = await queue(queueOf({ pending: [record(A)] }), {
      [`POST /api/actions/${A}/cancel`]: { data: record("a-cancel", { kind: "cancel" }) },
    });
    fireEvent.click(await screen.findByRole("button", { name: `Cancel ${A}` }));
    const dialog = await screen.findByRole("dialog");
    expect(sent.some((r) => r.method === "POST")).toBe(false);
    const before = sent.filter((r) => r.path === "/api/actions").length;
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel action" }));
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    const post = sent.find((r) => r.method === "POST");
    expect(post?.path).toBe(`/api/actions/${A}/cancel`);
    expect(post?.body).toEqual({});
    await waitFor(() =>
      expect(sent.filter((r) => r.path === "/api/actions").length).toBeGreaterThan(before),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("toasts a refusal with the board-service's message", async () => {
    await queue(queueOf({ pending: [record(A)] }), {
      [`POST /api/actions/${A}/cancel`]: { status: 409, error: "the start is already finished" },
    });
    fireEvent.click(await screen.findByRole("button", { name: `Cancel ${A}` }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel action" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("the start is already finished"));
  });
});
