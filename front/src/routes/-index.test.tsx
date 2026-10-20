/**
 * The board card's one guard. A payload of the wrong shape is named and not
 * rendered — `docs/decisions.md` ADR 29, `docs/ui.md` *A value of the wrong
 * shape is named, not rendered and not dropped* — and every other screen enforces
 * that by failing loudly: a `.map` over a string throws, a date over an object
 * shows its own words. `depends_on` on this card is the exception. It is read for
 * its `.length`, and `.length` over a string is a number, so a `depends_on:
 * T-001` written without a dash would draw a confident count where the
 * dependency count goes, in a card that looks exactly like a correct one. Nothing
 * else in the console can be wrong this quietly, which is why the branch exists
 * and why it is the branch worth a test.
 *
 * The `-` on the filename is load-bearing: `@tanstack/router-plugin` reads every
 * other file under `src/routes/` as a route, and `routeFileIgnorePrefix` defaults
 * to `-`. Without it this file would arrive in `routeTree.gen.ts` as a route no
 * phase here can regenerate.
 *
 * `TaskCard` is wrapped in a `<Link>`, so it needs a router in context. The one
 * below is built here, out of the single route a card links to, rather than taken
 * from `src/router.tsx`: mounting one card should not mount the console.
 *
 * The file also holds the description's absent arm, and the one render of the
 * whole route here: the Board's search lives in `BoardPage` and no component
 * takes it as a prop, so it is reached through `renderRoute`. The columns and
 * the epic swimlanes live in `BoardPage` too, so they are reached the same way;
 * their arithmetic is `lib/board.test.ts`'s, and this file checks the screen
 * draws it (ADR 140).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { toast } from "sonner";
import {
  RouterContextProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { Route, TaskCard } from "./index";
import { renderRoute, type Served } from "@/test/render-route";
import type { ActionQueue, ActionRecord, Task } from "@/lib/api/types";

// `renderRoute` draws no Toaster, so the toast is asserted at its call.
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

// `vitest.config.ts` sets `globals: false`, so the library's own cleanup hook
// never runs, and every render would stack in one document.
afterEach(cleanup);

const NOW = Date.parse("2026-10-08T12:00:00Z");

/**
 * A card with nothing to say but its id. The heartbeat is absent and so is the
 * api's judgement on the lock, which is the pair that renders as a bare dot with
 * no words: every assertion below reads `textContent`, and an age that ticks
 * would put digits of its own on the card.
 */
const TASK: Task = {
  task_id: "T-014",
  status: "in_progress",
  owner: "cuenta1",
  depends_on: [],
  heartbeat: null,
  description: "the pointers in docs/ still land somewhere",
  resolved_debt: [],
  lock_expired: null,
  phase: null,
  priority: "normal",
  source: null,
  tags: [],
  rank: null,
  debt_id: null,
  project: null,
  epic: null,
  split_into: [],
  merged: false,
  title: null,
};

const EMPTY_QUEUE: ActionQueue = {
  pending: [],
  claimed: [],
  done: [],
  done_total: 0,
  worker: { heartbeat: null, stale: true, stale_after_seconds: 30 },
};

/** An action record as `dispatcher/actions.py:create` writes it, a start by default. */
function action(taskId: string, over: Partial<ActionRecord> = {}): ActionRecord {
  return {
    version: 1,
    id: `act-${taskId}`,
    kind: "start",
    project: "ia-harness",
    task_id: taskId,
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

/** The one route a card links to, and no component mounted on it. */
function cardRouter() {
  const rootRoute = createRootRoute();
  const taskRoute = createRoute({ getParentRoute: () => rootRoute, path: "/tasks/$taskId" });
  return createRouter({
    routeTree: rootRoute.addChildren([taskRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
}

/**
 * `depends_on` is `string[]` in `types.ts`, and the subject here is what the api
 * actually served, so the value arrives as `unknown` and is cast at this
 * boundary. The cast is the test's premise, not a convenience: the type is a
 * claim about a json body nothing on this side validates.
 */
function renderCard(dependsOn: unknown, debtCount = 0) {
  const task = { ...TASK, depends_on: dependsOn } as Task;
  return render(
    <RouterContextProvider router={cardRouter()}>
      <TaskCard task={task} now={NOW} dense={false} debtCount={debtCount} />
    </RouterContextProvider>,
  );
}

/** The dependency count, which is the one chip carrying the branch icon. */
function dependencyChip(container: HTMLElement): HTMLElement | null {
  return container.querySelector(".lucide-git-branch")?.parentElement ?? null;
}

describe("a dependency list the api served as a list", () => {
  it("counts it", () => {
    const { container } = renderCard(["T-001", "T-013"]);
    expect(dependencyChip(container)?.textContent).toBe("2");
  });

  it("draws no chip at all when there are no dependencies", () => {
    const { container } = renderCard([]);
    expect(dependencyChip(container)).toBeNull();
    expect(container.textContent).not.toContain("depends on");
  });
});

describe("a dependency list of the wrong shape", () => {
  it("names a string instead of counting its characters", () => {
    const { container } = renderCard("T-001");
    expect(container.textContent).toContain("depends on — a string, not a list");
    // The number the unguarded render would draw: `"T-001".length` is 5, and a 5
    // on this card is indistinguishable from a task with five dependencies.
    expect(dependencyChip(container)).toBeNull();
    expect(container.textContent).not.toContain("5");
  });

  it.each([
    [null, "null"],
    [undefined, "undefined"],
    [7, "a number"],
    [{ "T-001": true }, "an object"],
  ])("says what %o is", (served, named) => {
    const { container } = renderCard(served);
    expect(container.textContent).toContain(`depends on — ${named}, not a list`);
    expect(dependencyChip(container)).toBeNull();
  });

  it("leaves the rest of the card standing, link included", () => {
    // The point of naming a bad value rather than throwing: the operator who has
    // to go fix the task file can still click through to it.
    const { container } = renderCard("T-001", 2);
    expect(container.querySelector("a")?.getAttribute("href")).toBe("/tasks/T-014");
    expect(container.textContent).toContain("T-014");
    expect(container.textContent).toContain("the pointers in docs/ still land somewhere");
    expect(container.textContent).toContain("debt 2");
  });
});

describe("a task the api served with no description", () => {
  it("says so on the tile instead of leaving it blank", () => {
    const { container } = render(
      <RouterContextProvider router={cardRouter()}>
        <TaskCard task={{ ...TASK, description: null }} now={NOW} dense={false} debtCount={0} />
      </RouterContextProvider>,
    );
    expect(container.textContent).toContain("no description");
    expect(container.textContent).not.toContain("null");
  });
});

describe("the project on a card (ADR 204)", () => {
  function renderProject(project: string | null) {
    return render(
      <RouterContextProvider router={cardRouter()}>
        <TaskCard task={{ ...TASK, project }} now={NOW} dense={false} debtCount={0} />
      </RouterContextProvider>,
    );
  }

  it("shows the project slug", () => {
    renderProject("ia-harness");
    expect(screen.getByTestId("project-mark").textContent).toBe("ia-harness");
  });

  it("draws the absent mark for a task with no project, not a guess", () => {
    const { container } = renderProject(null);
    expect(screen.queryByTestId("project-mark")).toBeNull();
    expect(container.textContent).toContain("no project");
    expect(container.textContent).not.toContain("null");
  });
});

describe("the Board's search over a task with no description", () => {
  const task = (task_id: string, description: string | null): Task => ({
    ...TASK,
    task_id,
    description,
  });

  async function board() {
    await renderRoute(Route, "/", {
      "/api/tasks": { data: [task("T-101", null), task("T-102", "the pool screen")] },
      "/api/actions": { data: EMPTY_QUEUE },
      "/api/ready": { data: [] },
      "/api/accounts": { data: [] },
      "/api/debt": { data: [] },
    });
    await screen.findByText("T-101");
  }

  it("does not match the text null", async () => {
    await board();
    fireEvent.change(screen.getByPlaceholderText(/^Filter tasks/), { target: { value: "null" } });
    expect(await screen.findByText("No task matches these filters")).toBeTruthy();
  });

  it("still finds the task by its id, and says it has no description", async () => {
    await board();
    fireEvent.change(screen.getByPlaceholderText(/^Filter tasks/), { target: { value: "T-101" } });
    expect(await screen.findByText("no description")).toBeTruthy();
    expect(screen.queryByText("T-102")).toBeNull();
  });
});

describe("the board's columns and swimlanes", () => {
  // The status is cast for the same reason `depends_on` is above: some tests
  // serve a word `TaskStatus` does not name, which the api can do.
  const task = (task_id: string, over: Record<string, unknown> = {}): Task =>
    ({ ...TASK, task_id, status: "pending", ...over }) as Task;

  type ReadyAnswer = { data: string[]; warnings?: string[] } | { status: number; error: string };

  async function board(tasks: Task[], ready: ReadyAnswer = { data: [] }) {
    return renderRoute(Route, "/", {
      "/api/tasks": { data: tasks },
      "/api/actions": { data: EMPTY_QUEUE },
      "/api/ready": ready,
      "/api/accounts": { data: [] },
      "/api/debt": { data: [] },
    });
  }

  /** The key of the column a card sits in. */
  function columnOf(taskId: string): string | null {
    return screen.getByText(taskId).closest("[data-column]")?.getAttribute("data-column") ?? null;
  }

  /** The lane a card sits in: an epic's id, or `none`. */
  function laneOf(taskId: string): string | null {
    return screen.getByText(taskId).closest("[data-lane]")?.getAttribute("data-lane") ?? null;
  }

  /** The column titles of the first lane, left to right. */
  function columnTitles(): (string | null)[] {
    const lane = document.querySelector("[data-lane]");
    return Array.from(
      lane?.querySelectorAll("section.panel > div:first-child > span:first-child") ?? [],
    ).map((s) => s.textContent);
  }

  /** The cards of one column in one lane, top to bottom. */
  function cardsIn(lane: string, column: string): string[] {
    return Array.from(
      document.querySelectorAll(`[data-lane="${lane}"] [data-column="${column}"] a`),
    ).map((a) => a.getAttribute("href") ?? "");
  }

  it("draws Ready, Waiting, a lane per role, the stops and Merged", async () => {
    await board([task("T-201")], { data: ["T-201"] });
    await screen.findByText("T-201");
    expect(columnTitles()).toEqual([
      "Ready",
      "Waiting",
      "Cartografo",
      "Arquitecto",
      "Implementador",
      "Revisor",
      "Auditor",
      "No live phase",
      "Awaiting review",
      "Conflict",
      "Blocked",
      "Merged",
    ]);
    expect(screen.getByText("not built: no status says this yet")).toBeTruthy();
  });

  it("places every card in its column", async () => {
    await board(
      [
        task("T-211"),
        task("T-212"),
        task("T-213", { status: "in_progress", phase: "revisor", lock_expired: false }),
        task("T-214", { status: "blocked" }),
        task("T-215", { status: "conflict" }),
        task("T-216", { status: "done", merged: true }),
      ],
      { data: ["T-211"] },
    );
    await screen.findByText("T-211");
    expect(columnOf("T-211")).toBe("ready");
    expect(columnOf("T-212")).toBe("waiting");
    expect(columnOf("T-213")).toBe("phase:revisor");
    expect(columnOf("T-214")).toBe("blocked");
    expect(columnOf("T-215")).toBe("conflict");
    expect(columnOf("T-216")).toBe("merged");
    expect(screen.queryByText(/is in Other/)).toBeNull();
  });

  it("keeps a stale phase and a task between phases on the board, and says which", async () => {
    await board([
      task("T-221", { status: "in_progress", phase: "implementador", lock_expired: true }),
      task("T-222", { status: "in_progress", phase: null }),
      task("T-223", { status: "in_progress", phase: "arquitecto", lock_expired: false }),
    ]);
    await screen.findByText("T-221");
    expect(columnOf("T-221")).toBe("no_phase");
    expect(columnOf("T-222")).toBe("no_phase");
    expect(columnOf("T-223")).toBe("phase:arquitecto");
    expect(screen.getByText("stale implementador")).toBeTruthy();
    expect(screen.getByText("between phases")).toBeTruthy();
  });

  it("makes no card of a backlog item or an epic", async () => {
    await board([
      task("T-231", { status: "proposed" }),
      task("T-232", { status: "deferred" }),
      task("T-233", { status: "dropped" }),
      task("T-234", { status: "epic", split_into: ["T-235"], title: "the epic" }),
      task("T-235", { epic: "T-234" }),
    ]);
    await screen.findByText("T-235");
    expect(screen.queryByText("T-231")).toBeNull();
    expect(screen.queryByText("T-232")).toBeNull();
    expect(screen.queryByText("T-233")).toBeNull();
    // The epic's id is its lane's header, not a card in any column.
    expect(screen.getByText("T-234").closest("[data-column]")).toBeNull();
  });

  it("gives each epic a lane linked to it with its progress, and the rest a no-epic lane", async () => {
    await board([
      task("T-240", { status: "epic", split_into: ["T-241", "T-242"], title: "the console" }),
      task("T-241", { epic: "T-240", status: "done", merged: true }),
      task("T-242", { epic: "T-240" }),
      task("T-243"),
    ]);
    await screen.findByText("T-243");
    expect(laneOf("T-241")).toBe("T-240");
    expect(laneOf("T-242")).toBe("T-240");
    expect(laneOf("T-243")).toBe("none");
    const lanes = Array.from(document.querySelectorAll("[data-lane]")).map((l) =>
      l.getAttribute("data-lane"),
    );
    expect(lanes).toEqual(["T-240", "none"]);
    const head = screen.getByText("the console").closest("a");
    expect(head?.getAttribute("href")).toBe("/tasks/T-240");
    expect(screen.getByText("1 of 2 merged")).toBeTruthy();
    expect(screen.getByText("No epic")).toBeTruthy();
  });

  it("keeps the ready order inside Ready, and the dispatcher's order elsewhere", async () => {
    await board(
      [
        task("T-251"),
        task("T-252"),
        task("T-253"),
        task("T-254", { status: "blocked", priority: "low" }),
        task("T-255", { status: "blocked", priority: "high" }),
      ],
      { data: ["T-253", "T-251", "T-252"] },
    );
    await screen.findByText("T-251");
    expect(cardsIn("none", "ready")).toEqual(["/tasks/T-253", "/tasks/T-251", "/tasks/T-252"]);
    expect(cardsIn("none", "blocked")).toEqual(["/tasks/T-255", "/tasks/T-254"]);
  });

  it("reads /api/tasks and /api/ready once each", async () => {
    const { sent } = await board([task("T-256")], { data: ["T-256"] });
    await screen.findByText("T-256");
    expect(sent.filter((r) => r.path === "/api/tasks")).toHaveLength(1);
    expect(sent.filter((r) => r.path === "/api/ready")).toHaveLength(1);
  });

  it.each([
    ["an error", { status: 500, error: "boom" }],
    ["no ready order", { data: [], warnings: ["/hive/tasks: no ready order: bad field"] }],
  ] as const)(
    "says so when the ready read gives %s, rather than calling it Waiting",
    async (_n, ready) => {
      await board([task("T-261")], ready as ReadyAnswer);
      await screen.findByText("T-261");
      expect(columnOf("T-261")).toBe("pending");
      expect(screen.getByText(/\/api\/ready gave no ready order/)).toBeTruthy();
      expect(columnTitles()).not.toContain("Waiting");
      expect(columnTitles()[0]).toBe("Pending, ready unknown");
    },
  );

  it("shows a status it has no column for in Other, and names an unknown one", async () => {
    await board([
      task("T-271", { status: "done" }),
      task("T-272", { status: "drafted" }),
      task("T-273", { status: "queued" }),
    ]);
    await screen.findByText("T-271");
    expect(columnOf("T-271")).toBe("other");
    expect(columnOf("T-272")).toBe("other");
    expect(columnOf("T-273")).toBe("other");
    expect(
      screen.getByText(/T-273: status "queued" is not one the dispatcher writes/),
    ).toBeTruthy();
    expect(screen.queryByText(/T-272: status/)).toBeNull();
  });

  it("filters by column", async () => {
    await board([task("T-281"), task("T-282", { status: "blocked" })], { data: ["T-281"] });
    await screen.findByText("T-281");
    fireEvent.change(screen.getByLabelText(/column/), { target: { value: "blocked" } });
    expect(await screen.findByText("1 of 2 cards")).toBeTruthy();
    expect(screen.queryByText("T-281")).toBeNull();
    expect(screen.getByText("T-282")).toBeTruthy();
  });

  it("shows the card's title", async () => {
    await board([task("T-291", { title: "the board's lanes", description: "long text" })]);
    expect(await screen.findByText("the board's lanes")).toBeTruthy();
    expect(screen.getByText("T-291").closest("a")?.getAttribute("href")).toBe("/tasks/T-291");
  });
});

describe("Start development on a Ready card", () => {
  const task = (task_id: string): Task => ({ ...TASK, task_id, status: "pending" });

  type QueueAnswer = { data: ActionQueue } | { status: number; error: string };

  async function board(queue: QueueAnswer = { data: EMPTY_QUEUE }, extra: Served = {}) {
    const out = await renderRoute(Route, "/", {
      "/api/tasks": { data: [task("T-301"), task("T-302")] },
      "/api/actions": queue,
      "/api/ready": { data: ["T-301"] },
      "/api/accounts": { data: [] },
      "/api/debt": { data: [] },
      ...extra,
    });
    await screen.findByText("T-301");
    return out;
  }

  const queueOf = (over: Partial<ActionQueue>): { data: ActionQueue } => ({
    data: { ...EMPTY_QUEUE, ...over },
  });

  const column = (key: string) => document.querySelector(`[data-column="${key}"]`) as HTMLElement;
  const startButton = () => screen.queryByRole("button", { name: "Start development" });

  async function open() {
    fireEvent.click(await screen.findByRole("button", { name: "Start development" }));
    return await screen.findByRole("dialog");
  }

  it("shows the button on a Ready card and not on a Waiting one", async () => {
    await board();
    expect(
      await within(column("ready")).findByRole("button", { name: "Start development" }),
    ).toBeTruthy();
    expect(within(column("waiting")).queryByRole("button")).toBeNull();
    expect(within(column("waiting")).getByText("T-302")).toBeTruthy();
  });

  it("shows Queued, and what it waits for, instead of the button for a pending start", async () => {
    await board(queueOf({ pending: [action("T-301", { waiting: "account" })] }));
    expect(await screen.findByText("Queued, waiting for an account")).toBeTruthy();
    expect(startButton()).toBeNull();
  });

  it("shows Queued alone for a pending start nothing holds back", async () => {
    await board(queueOf({ pending: [action("T-301")] }));
    expect(await screen.findByText("Queued")).toBeTruthy();
    expect(startButton()).toBeNull();
  });

  it("shows Running instead of the button for a claimed start", async () => {
    await board(queueOf({ claimed: [action("T-301", { status: "claimed" })] }));
    expect(await screen.findByText("Running")).toBeTruthy();
    expect(startButton()).toBeNull();
  });

  it("is not hidden by another task's start or by another kind of action", async () => {
    await board(
      queueOf({
        pending: [action("T-302"), action("T-301", { kind: "cancel", id: "act-cancel" })],
      }),
    );
    expect(await screen.findByRole("button", { name: "Start development" })).toBeTruthy();
  });

  it("still offers the button, and says the read failed, when /api/actions does not answer", async () => {
    await board({ status: 500, error: "boom" });
    expect(await screen.findByRole("button", { name: "Start development" })).toBeTruthy();
    expect(screen.getByText(/\/api\/actions did not answer/)).toBeTruthy();
  });

  it("sends the note with the start, and reads the queue again", async () => {
    const { sent } = await board(undefined, {
      "POST /api/tasks/T-301/start": { data: action("T-301") },
    });
    const dialog = await open();
    fireEvent.change(within(dialog).getByLabelText(/Note/), { target: { value: " go slowly " } });
    const before = sent.filter((r) => r.path === "/api/actions").length;
    fireEvent.click(within(dialog).getByRole("button", { name: "Start" }));
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    const post = sent.find((r) => r.method === "POST");
    expect(post?.path).toBe("/api/tasks/T-301/start");
    expect(post?.body).toEqual({ note: " go slowly " });
    await waitFor(() =>
      expect(sent.filter((r) => r.path === "/api/actions").length).toBeGreaterThan(before),
    );
  });

  it("sends a blank note as null", async () => {
    const { sent } = await board(undefined, {
      "POST /api/tasks/T-301/start": { data: action("T-301") },
    });
    const dialog = await open();
    fireEvent.change(within(dialog).getByLabelText(/Note/), { target: { value: "  \n " } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Start" }));
    await waitFor(() => expect(sent.some((r) => r.method === "POST")).toBe(true));
    expect(sent.find((r) => r.method === "POST")?.body).toEqual({ note: null });
  });

  it("toasts a refusal and keeps the dialog and the note", async () => {
    await board(undefined, {
      "POST /api/tasks/T-301/start": {
        status: 409,
        error: "a start for task T-301 is already queued",
      },
    });
    const dialog = await open();
    fireEvent.change(within(dialog).getByLabelText(/Note/), { target: { value: "keep me" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Start" }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("a start for task T-301 is already queued"),
    );
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect((within(dialog).getByLabelText(/Note/) as HTMLTextAreaElement).value).toBe("keep me");
  });

  it("refuses a note over the comment limit before sending it", async () => {
    await board();
    const dialog = await open();
    fireEvent.change(within(dialog).getByLabelText(/Note/), {
      target: { value: "x".repeat(20001) },
    });
    expect(
      (within(dialog).getByRole("button", { name: "Start" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(within(dialog).getByText(/20,001 characters, over the 20,000/)).toBeTruthy();
  });

  it("sends the pool floor typed in the dialog (ADR NNN)", async () => {
    const { sent } = await board(undefined, {
      "POST /api/tasks/T-301/start": { data: action("T-301") },
    });
    const dialog = await open();
    fireEvent.change(within(dialog).getByLabelText("Pool floor"), { target: { value: "20" } });
    expect(within(dialog).getByTestId("start-floor-help").textContent).toContain("less than 20%");
    fireEvent.click(within(dialog).getByRole("button", { name: "Start" }));
    await waitFor(() => expect(sent.some((r) => r.method === "POST")).toBe(true));
    expect(sent.find((r) => r.method === "POST")?.body).toEqual({ note: null, floor: 20 });
  });

  it("leaves the floor out when blank, and names the default", async () => {
    const { sent } = await board(undefined, {
      "POST /api/tasks/T-301/start": { data: action("T-301") },
    });
    const dialog = await open();
    expect(within(dialog).getByTestId("start-floor-help").textContent).toContain("the default 5%");
    fireEvent.click(within(dialog).getByRole("button", { name: "Start" }));
    await waitFor(() => expect(sent.some((r) => r.method === "POST")).toBe(true));
    expect(sent.find((r) => r.method === "POST")?.body).toEqual({ note: null });
  });

  it("refuses a floor that is not a whole percent before sending it", async () => {
    const { sent } = await board();
    const dialog = await open();
    for (const value of ["101", "2.5"]) {
      fireEvent.change(within(dialog).getByLabelText("Pool floor"), { target: { value } });
      expect(
        (within(dialog).getByRole("button", { name: "Start" }) as HTMLButtonElement).disabled,
      ).toBe(true);
      expect(
        within(dialog).getByText(`${value} is not a whole percent from 0 to 100.`),
      ).toBeTruthy();
    }
    expect(sent.some((r) => r.method === "POST")).toBe(false);
  });

  const acct = (name: string, state: string) => ({
    name,
    container: name,
    is_primary: false,
    quota_threshold_pct: 80,
    reserve_pct: 20,
    quota_cooldown_seconds: 3600,
    state,
    current_task: null,
    rate_limited_at: null,
    last_probe: null,
  });

  it("says in the confirmation how many slots are free and that the task would start now", async () => {
    await board(undefined, {
      "/api/accounts": { data: [acct("cuenta1", "IDLE"), acct("cuenta2", "BUSY")] },
    });
    const dialog = await open();
    expect(
      await within(dialog).findByText("2 accounts, 1 free; this task would start now"),
    ).toBeTruthy();
  });

  it("says in the confirmation that the task would queue behind started tasks", async () => {
    await board(queueOf({ pending: [action("T-999")] }), {
      "/api/accounts": { data: [acct("cuenta1", "IDLE")] },
    });
    const dialog = await open();
    expect(
      await within(dialog).findByText(
        "1 account, 1 free; this task would queue behind 1 started task",
      ),
    ).toBeTruthy();
  });

  it("says in the confirmation that the task would queue behind a task of its project", async () => {
    await board(queueOf({ claimed: [action("T-999", { status: "claimed" })] }), {
      "/api/tasks": {
        data: [
          { ...task("T-301"), project: "ia-harness" },
          { ...task("T-302"), project: "ia-harness" },
        ],
      },
      "/api/accounts": { data: [acct("cuenta1", "IDLE")] },
    });
    const dialog = await open();
    expect(
      await within(dialog).findByText(
        "1 account, 1 free; this task would queue behind 1 task of this project",
      ),
    ).toBeTruthy();
  });

  it("says in the confirmation when /api/accounts does not answer", async () => {
    await board(undefined, { "/api/accounts": { status: 500, error: "boom" } });
    const dialog = await open();
    expect(
      await within(dialog).findByText(
        "/api/accounts did not answer, so the free slots are not known",
      ),
    ).toBeTruthy();
  });

  it("does not follow the card's link when the button is clicked", async () => {
    const { router } = await board();
    await open();
    expect(router.state.location.pathname).toBe("/");
  });
});
