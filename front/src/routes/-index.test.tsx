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
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import {
  RouterContextProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { TaskCard } from "./index";
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
  kanban_issue_id: null,
  resolved_debt: [],
  card: null,
  lock_expired: null,
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
