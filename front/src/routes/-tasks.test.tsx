/**
 * The task page's header, read off the mounted route. `TaskDetailPage` builds
 * the subtitle itself and hands it to no component of its own, so there is
 * nothing to export and render alone: the page is mounted with `renderRoute`
 * and the header is read where the operator reads it. Three arms, one test
 * each: a task with no `description.md` says *no description*, a task with a
 * description shows it, and a card the api could not read claims neither.
 * `docs/debt/T-034-D1.md`.
 *
 * The page asks for four paths and all four are served, because one left out
 * answers 404 and the page draws that region's error state:
 * `docs/implementations/T-035.md` *Every read the page makes*.
 *
 * The `-` on the filename is load-bearing; see `-index.test.tsx`'s header.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, screen } from "@testing-library/react";
import { Route } from "./tasks.$taskId";
import { renderRoute } from "@/test/render-route";
import type { TaskDetail } from "@/lib/api/types";

// `vitest.config.ts` sets `globals: false`, so the library's own cleanup hook
// never runs, and every render would stack in one document.
afterEach(cleanup);

const DESCRIPTION = "the pool screen says which account holds a lock";

/**
 * A task nobody holds, with nothing below the header that could repeat the
 * description: no dependencies, an empty body.
 */
const TASK: TaskDetail = {
  task_id: "T-101",
  status: "pending",
  owner: null,
  depends_on: [],
  heartbeat: null,
  description: DESCRIPTION,
  resolved_debt: [],
  lock_expired: null,
  body: "",
};

/** The page at `/tasks/T-101`, with `data` as what the api answered for it. */
async function taskPage(data: TaskDetail | null, warnings: string[] = []) {
  await renderRoute(
    Route,
    "/tasks/$taskId",
    {
      "/api/tasks/T-101": { data, warnings },
      "/api/debt": { data: [] },
      "/api/phases": { data: [] },
      "/api/learnings": { data: [] },
    },
    "/tasks/T-101",
  );
}

/** The block the page's title sits in: the task id and the subtitle under it. */
function header(): HTMLElement {
  return screen.getByRole("heading", { level: 1, name: "T-101" }).parentElement as HTMLElement;
}

describe("the task page's header for a task with no description", () => {
  it("says so under the task id", async () => {
    await taskPage({ ...TASK, description: null });
    // The title is drawn before the read lands; this is drawn after it.
    await screen.findByText("task body");
    expect(header().textContent).toContain("no description");
    expect(header().textContent).not.toContain("null");
  });
});

describe("the task page's header for a task with a description", () => {
  it("shows the description and does not call it absent", async () => {
    await taskPage(TASK);
    await screen.findByText("task body");
    expect(header().textContent).toContain(DESCRIPTION);
    expect(screen.queryByText("no description")).toBeNull();
  });
});

describe("the task page's header for a card the api could not read", () => {
  it("claims nothing about a description", async () => {
    await taskPage(null, ["T-101: the task file could not be parsed"]);
    await screen.findByText("T-101 exists and could not be read");
    expect(header().textContent).toBe("T-101");
    expect(screen.queryByText("no description")).toBeNull();
  });
});
