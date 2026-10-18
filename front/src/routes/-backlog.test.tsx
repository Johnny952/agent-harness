/**
 * The Backlog screen over a served `/api/tasks`, whole (ADR 138): the order the
 * board keeps, the filters, the epic grouping, and the bodies its writes send to
 * the board-service — `POST /api/tasks/<id>/accept` for accept, defer and drop,
 * `PATCH /api/tasks/<id>` with `{rank}` for a reorder. The arithmetic itself is
 * `lib/backlog.test.ts`'s.
 *
 * The `-` on the filename is load-bearing; see `-index.test.tsx`'s header.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { toast } from "sonner";
import { Route } from "./backlog";
import { renderRoute, type Served } from "@/test/render-route";
import type { Task } from "@/lib/api/types";

// `renderRoute` draws no Toaster, so the toast is asserted at its call.
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

afterEach(cleanup);

function task(id: string, over: Partial<Task> = {}): Task {
  return {
    task_id: id,
    status: "proposed",
    owner: null,
    depends_on: [],
    heartbeat: null,
    description: null,
    resolved_debt: [],
    lock_expired: null,
    phase: null,
    priority: "normal",
    source: "operator",
    tags: [],
    rank: null,
    debt_id: null,
    epic: null,
    split_into: [],
    merged: false,
    title: `title of ${id}`,
    ...over,
  };
}

const rowIds = () =>
  screen.getAllByTestId(/^row-/).map((r) => r.getAttribute("data-testid")!.slice(4));

async function open(tasks: Task[], extra: Served = {}) {
  const r = await renderRoute(Route, "/backlog", { "/api/tasks": { data: tasks }, ...extra });
  return r;
}

describe("the backlog's order", () => {
  it("is the board's: bucket, then rank (unranked first), then id in number order", async () => {
    await open([
      task("T-100", { priority: "low" }),
      task("T-12", { rank: 2048 }),
      task("T-11", { rank: 1024 }),
      task("T-99"),
      task("T-100b", { priority: "bogus", rank: 4096 }),
      task("T-5", { priority: "high", rank: 9999 }),
      task("T-7", { status: "pending" }),
    ]);
    await screen.findByTestId("row-T-5");
    expect(rowIds()).toEqual(["T-5", "T-99", "T-11", "T-12", "T-100b", "T-100"]);
  });
});

describe("the backlog's filters", () => {
  const tasks = [
    task("T-1", { tags: ["debt"], source: "debt", debt_id: "T-040-D2" }),
    task("T-2", { status: "deferred", source: "arquitecto" }),
    task("T-3", { status: "dropped", source: null }),
  ];

  it("hides dropped items until the status is ticked", async () => {
    await open(tasks);
    await screen.findByTestId("row-T-1");
    expect(rowIds()).toEqual(["T-1", "T-2"]);
    fireEvent.click(screen.getByLabelText("dropped"));
    expect(rowIds()).toEqual(["T-1", "T-2", "T-3"]);
    expect(within(screen.getByTestId("row-T-3")).getByText("final")).toBeTruthy();
  });

  it("filters by tag, debt included", async () => {
    await open(tasks);
    await screen.findByTestId("row-T-1");
    fireEvent.change(screen.getByLabelText("tag filter"), { target: { value: "debt" } });
    expect(rowIds()).toEqual(["T-1"]);
  });

  it("filters by source, with none for a task that records no source", async () => {
    await open(tasks);
    await screen.findByTestId("row-T-1");
    fireEvent.change(screen.getByLabelText("source filter"), { target: { value: "arquitecto" } });
    expect(rowIds()).toEqual(["T-2"]);
    fireEvent.click(screen.getByLabelText("dropped"));
    fireEvent.change(screen.getByLabelText("source filter"), { target: { value: "none" } });
    expect(rowIds()).toEqual(["T-3"]);
  });

  it("says no item matches when the filters hide them all", async () => {
    await open(tasks);
    await screen.findByTestId("row-T-1");
    fireEvent.click(screen.getByLabelText("proposed"));
    fireEvent.click(screen.getByLabelText("deferred"));
    expect(screen.getByText("No backlog item matches the filters")).toBeTruthy();
    expect(screen.queryByText("The backlog is empty")).toBeNull();
  });

  it("links a debt item's debt id to the debt screen and its id to the task", async () => {
    await open(tasks);
    const row = await screen.findByTestId("row-T-1");
    expect(within(row).getByText("T-040-D2").closest("a")!.getAttribute("href")).toBe("/debt");
    expect(within(row).getByText("T-1").closest("a")!.getAttribute("href")).toBe("/tasks/T-1");
  });
});

describe("the backlog's verdicts", () => {
  const accepted = { data: { task_id: "T-1" } };

  it("accepts with the chosen priority", async () => {
    const { sent } = await open([task("T-1")], { "POST /api/tasks/T-1/accept": accepted });
    const row = await screen.findByTestId("row-T-1");
    fireEvent.change(within(row).getByLabelText("priority for T-1"), {
      target: { value: "high" },
    });
    fireEvent.click(within(row).getByText("accept"));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("T-1 accepted onto the board"));
    const write = sent.find((s) => s.method === "POST");
    expect(write).toEqual({
      method: "POST",
      path: "/api/tasks/T-1/accept",
      body: { to: "pending", priority: "high" },
    });
  });

  it("defers a proposed item", async () => {
    const { sent } = await open([task("T-1")], { "POST /api/tasks/T-1/accept": accepted });
    fireEvent.click(within(await screen.findByTestId("row-T-1")).getByText("defer"));
    await waitFor(() => expect(sent.some((s) => s.method === "POST")).toBe(true));
    expect(sent.find((s) => s.method === "POST")!.body).toEqual({ to: "deferred" });
  });

  it("offers no defer on a deferred item", async () => {
    await open([task("T-1", { status: "deferred" })]);
    const row = await screen.findByTestId("row-T-1");
    expect(within(row).queryByText("defer")).toBeNull();
    expect(within(row).getByText("accept")).toBeTruthy();
  });

  it("drops only after the confirm", async () => {
    const { sent } = await open([task("T-1")], { "POST /api/tasks/T-1/accept": accepted });
    const row = await screen.findByTestId("row-T-1");
    fireEvent.click(within(row).getByText("drop"));
    expect(sent.some((s) => s.method === "POST")).toBe(false);
    fireEvent.click(within(row).getByText("confirm drop"));
    await waitFor(() => expect(sent.some((s) => s.method === "POST")).toBe(true));
    expect(sent.find((s) => s.method === "POST")!.body).toEqual({ to: "dropped" });
  });

  it("shows a refusal in a toast", async () => {
    await open([task("T-1")], {
      "POST /api/tasks/T-1/accept": { status: 409, error: "T-1 is not in the backlog" },
    });
    fireEvent.click(within(await screen.findByTestId("row-T-1")).getByText("defer"));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        expect.stringContaining("T-1 is not in the backlog"),
      ),
    );
  });
});

describe("the backlog's reorder", () => {
  it("writes the midpoint rank when there is room", async () => {
    const { sent } = await open(
      [task("T-1", { rank: 1024 }), task("T-2", { rank: 2048 }), task("T-3", { rank: 3072 })],
      { "PATCH /api/tasks/T-3": { data: { task_id: "T-3" } } },
    );
    await screen.findByTestId("row-T-3");
    expect((screen.getByLabelText("move T-1 up") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("move T-3 up"));
    await waitFor(() => expect(sent.some((s) => s.method === "PATCH")).toBe(true));
    expect(sent.filter((s) => s.method === "PATCH")).toEqual([
      { method: "PATCH", path: "/api/tasks/T-3", body: { rank: 1536 } },
    ]);
  });

  it("renumbers the bucket when the ranks leave no room", async () => {
    const ok = { data: {} };
    const { sent } = await open([task("T-1"), task("T-2"), task("T-3")], {
      "PATCH /api/tasks/T-1": ok,
      "PATCH /api/tasks/T-2": ok,
      "PATCH /api/tasks/T-3": ok,
    });
    await screen.findByTestId("row-T-3");
    fireEvent.click(screen.getByLabelText("move T-1 down"));
    await waitFor(() => expect(sent.filter((s) => s.method === "PATCH")).toHaveLength(3));
    expect(sent.filter((s) => s.method === "PATCH").map((s) => [s.path, s.body])).toEqual([
      ["/api/tasks/T-2", { rank: 1024 }],
      ["/api/tasks/T-1", { rank: 2048 }],
      ["/api/tasks/T-3", { rank: 3072 }],
    ]);
  });
});

describe("an epic in the backlog", () => {
  it("groups its tasks under it and holds their accept until it is accepted", async () => {
    await open([
      task("T-10", { split_into: ["T-11", "T-12"] }),
      task("T-12", { epic: "T-10" }),
      task("T-11", { epic: "T-10" }),
      task("T-9"),
    ]);
    await screen.findByTestId("row-T-10");
    expect(rowIds()).toEqual(["T-9", "T-10", "T-11", "T-12"]);
    expect(within(screen.getByTestId("row-T-10")).getByText(/epic · 2 tasks/)).toBeTruthy();
    const child = screen.getByTestId("row-T-11");
    expect((within(child).getByText("accept") as HTMLButtonElement).disabled).toBe(true);
    expect(within(child).queryByLabelText("move T-11 up")).toBeNull();
  });

  it("lets a task of an accepted epic be accepted, under the epic's header", async () => {
    await open([
      task("T-10", { status: "epic", split_into: ["T-11"], title: "the epic" }),
      task("T-11", { epic: "T-10" }),
    ]);
    const child = await screen.findByTestId("row-T-11");
    expect(screen.getByText("the epic")).toBeTruthy();
    expect(screen.getByText("on the board")).toBeTruthy();
    expect((within(child).getByText("accept") as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("an empty backlog", () => {
  it("says the backlog is empty when nothing is proposed, deferred or dropped", async () => {
    await open([task("T-1", { status: "pending" })]);
    expect(await screen.findByText("The backlog is empty")).toBeTruthy();
    expect(screen.queryByText("No backlog item matches the filters")).toBeNull();
  });
});
