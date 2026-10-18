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
 * takes it as a prop, so it is reached through `renderRoute`. The columns live
 * in `BoardPage` too, so the `conflict` column is reached the same way.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import {
  RouterContextProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { Route, TaskCard } from "./index";
import { renderRoute } from "@/test/render-route";
import type { Task } from "@/lib/api/types";

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
};

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

describe("the Board's search over a task with no description", () => {
  const task = (task_id: string, description: string | null): Task => ({
    ...TASK,
    task_id,
    description,
  });

  async function board() {
    await renderRoute(Route, "/", {
      "/api/tasks": { data: [task("T-101", null), task("T-102", "the pool screen")] },
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

describe("a task the pre-merge check moved to conflict", () => {
  // The status is cast for the same reason `depends_on` is above: the second
  // test serves a word `TaskStatus` does not name, which the api can do.
  const task = (task_id: string, status: string): Task => ({ ...TASK, task_id, status }) as Task;

  // No wait here: a card in a column and a task in none are found by different
  // text, so each test waits on its own.
  async function board(...tasks: Task[]) {
    await renderRoute(Route, "/", {
      "/api/tasks": { data: tasks },
      "/api/accounts": { data: [] },
      "/api/debt": { data: [] },
    });
  }

  /** Every column's title, left to right. */
  function columnTitles(): (string | null)[] {
    return Array.from(
      document.querySelectorAll("section.panel > div:first-child > span:first-child"),
    ).map((s) => s.textContent);
  }

  /** The title of the column a card sits in. */
  function columnOf(taskId: string): string | null {
    const section = screen.getByText(taskId).closest("section");
    return section?.querySelector("div:first-child > span:first-child")?.textContent ?? null;
  }

  it("lands in the Conflict column, right after Blocked, with no warning", async () => {
    await board(task("T-201", "conflict"), task("T-202", "blocked"));
    await screen.findByText("T-201");
    expect(columnTitles()).toEqual(["Pending", "In progress", "Blocked", "Conflict", "Done"]);
    expect(columnOf("T-201")).toBe("Conflict");
    expect(columnOf("T-202")).toBe("Blocked");
    expect(screen.queryByText(/is in no column/)).toBeNull();
  });

  it("still names a status outside the five, and lists conflict among them", async () => {
    await board(task("T-203", "queued"));
    expect(
      await screen.findByText(
        /T-203: status "queued" is none of pending, in_progress, blocked, conflict or done/,
      ),
    ).toBeTruthy();
  });
});
