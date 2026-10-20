/**
 * The project routes, `/p/$slug/` (ADR 208): the harness's own Board, Backlog and
 * task page, reused and narrowed to the tasks whose `project` is the slug; a
 * plain not-found for a slug no task names, and for a task of another project;
 * and the sidebar's "Projects" section, derived from the same `/api/tasks`.
 *
 * The `-` on the filename is load-bearing; see `-index.test.tsx`'s header.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import { Route as ProjectBoardRoute } from "./p.$slug.index";
import { Route as ProjectBacklogRoute } from "./p.$slug.backlog";
import { Route as ProjectTaskRoute } from "./p.$slug.tasks.$taskId";
import { Route as BoardRoute } from "./index";
import { renderRoute, type Served } from "@/test/render-route";
import { deriveProjects, inProject, scoped } from "@/lib/projects";
import type { ActionQueue, Task, TaskDetail } from "@/lib/api/types";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

afterEach(cleanup);

function task(id: string, over: Partial<Task> = {}): Task {
  return {
    task_id: id,
    status: "pending",
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
    project: null,
    epic: null,
    split_into: [],
    merged: false,
    title: `title of ${id}`,
    ...over,
  };
}

const EMPTY_QUEUE: ActionQueue = {
  pending: [],
  claimed: [],
  done: [],
  done_total: 0,
  worker: { heartbeat: null, stale: true, stale_after_seconds: 30 },
};

/** Two projects, and one task of the harness's own (no project). */
const TASKS: Task[] = [
  task("T-201", { project: "alpha" }),
  task("T-202", { project: "alpha", status: "proposed" }),
  task("T-301", { project: "beta" }),
  task("T-302", { project: "beta", status: "proposed" }),
  task("T-101"),
];

function boardServed(tasks: Task[] = TASKS): Served {
  return {
    "/api/tasks": { data: tasks },
    "/api/actions": { data: EMPTY_QUEUE },
    "/api/ready": { data: [] },
    "/api/accounts": { data: [] },
    "/api/debt": { data: [] },
  };
}

const sidebar = () => screen.getByRole("navigation");

describe("deriveProjects", () => {
  it("names each project once, in slug order, and none for a task with no project", () => {
    expect(
      deriveProjects([
        task("T-1", { project: "beta" }),
        task("T-2", { project: "alpha" }),
        task("T-3", { project: "beta" }),
        task("T-4"),
        task("T-5", { project: "" }),
      ]),
    ).toEqual(["alpha", "beta"]);
  });

  it("scopes to all tasks on the harness's screens and to the slug's under a project", () => {
    expect(scoped(TASKS, null)).toHaveLength(TASKS.length);
    expect(scoped(TASKS, "alpha").map((t) => t.task_id)).toEqual(["T-201", "T-202"]);
    expect(inProject(TASKS, "gamma")).toEqual([]);
  });
});

describe("the sidebar's projects", () => {
  it("lists the projects the tasks name, each linking to its board, above the harness's screens", async () => {
    await renderRoute(BoardRoute, "/", boardServed());
    const list = await within(sidebar()).findByRole("list", { name: "Projects" });
    const links = within(list).getAllByRole("link");
    expect(links.map((a) => a.textContent)).toEqual(["alpha", "beta"]);
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["/p/alpha", "/p/beta"]);
    const harness = within(sidebar()).getByRole("list", { name: "Harness" });
    expect(within(harness).getByRole("link", { name: /Board/ }).getAttribute("href")).toBe("/");
    expect(
      within(harness)
        .getByRole("link", { name: /Backlog/ })
        .getAttribute("href"),
    ).toBe("/backlog");
    // The open project's screens are listed only under a project's path.
    expect(within(list).queryByRole("link", { name: "Backlog" })).toBeNull();
  });

  it("says so when no task names a project", async () => {
    await renderRoute(BoardRoute, "/", boardServed([task("T-101")]));
    expect(await within(sidebar()).findByText("No project on the board yet.")).toBeTruthy();
  });

  it("says the projects are unread when the tasks read fails, the records unserved", async () => {
    await renderRoute(BoardRoute, "/", {
      ...boardServed(),
      "/api/tasks": { status: 503, error: "down" },
    });
    expect(
      await within(sidebar()).findByText(
        "Projects unread: neither the project records nor the task list answered.",
      ),
    ).toBeTruthy();
  });

  it("lists the open project's Board and Backlog beneath it", async () => {
    await renderRoute(ProjectBoardRoute, "/p/$slug/", boardServed(), "/p/alpha/");
    const list = await within(sidebar()).findByRole("list", { name: "Projects" });
    const hrefs = within(list)
      .getAllByRole("link")
      .map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(["/p/alpha", "/p/alpha", "/p/alpha/backlog", "/p/beta"]);
  });
});

describe("a project's board", () => {
  it("shows only that project's tasks, and links each card inside the project", async () => {
    await renderRoute(ProjectBoardRoute, "/p/$slug/", boardServed(), "/p/alpha/");
    const card = await screen.findByText("T-201");
    expect(screen.queryByText("T-301")).toBeNull();
    expect(screen.queryByText("T-101")).toBeNull();
    expect(card.closest("a")?.getAttribute("href")).toBe("/p/alpha/tasks/T-201");
    expect(screen.getByRole("heading", { name: "Board · alpha" })).toBeTruthy();
  });

  it("leaves the harness's board whole, its cards linking to /tasks/", async () => {
    await renderRoute(BoardRoute, "/", boardServed());
    const card = await screen.findByText("T-201");
    expect(screen.getByText("T-301")).toBeTruthy();
    expect(screen.getByText("T-101")).toBeTruthy();
    expect(card.closest("a")?.getAttribute("href")).toBe("/tasks/T-201");
  });

  it("is a plain not-found for a slug no task names", async () => {
    await renderRoute(ProjectBoardRoute, "/p/$slug/", boardServed(), "/p/gamma/");
    const nf = await screen.findByTestId("project-not-found");
    expect(nf.textContent).toContain("No project named gamma");
    expect(screen.queryByText("T-201")).toBeNull();
  });
});

describe("a project's backlog", () => {
  it("shows only that project's proposed tasks", async () => {
    await renderRoute(
      ProjectBacklogRoute,
      "/p/$slug/backlog",
      { "/api/tasks": { data: TASKS } },
      "/p/beta/backlog",
    );
    await screen.findByTestId("row-T-302");
    expect(screen.queryByTestId("row-T-202")).toBeNull();
    expect(screen.getByRole("heading", { name: "Backlog · beta" })).toBeTruthy();
    const id = within(screen.getByTestId("row-T-302")).getByText("T-302");
    expect(id.closest("a")?.getAttribute("href")).toBe("/p/beta/tasks/T-302");
  });

  it("is a plain not-found for a slug no task names", async () => {
    await renderRoute(
      ProjectBacklogRoute,
      "/p/$slug/backlog",
      { "/api/tasks": { data: TASKS } },
      "/p/gamma/backlog",
    );
    expect((await screen.findByTestId("project-not-found")).textContent).toContain(
      "No project named gamma",
    );
  });
});

describe("a project's task page", () => {
  function detail(id: string, project: string | null): TaskDetail {
    return { ...task(id, { project }), body: "" };
  }

  function served(d: TaskDetail): Served {
    const id = d.task_id;
    return {
      [`/api/tasks/${id}`]: { data: d },
      [`/api/tasks/${id}/usage`]: { data: [] },
      [`/api/tasks/${id}/comments`]: { data: { task_id: id, comments: [] } },
      "/api/debt": { data: [] },
      "/api/phases": { data: [] },
      "/api/learnings": { data: [] },
      "/api/tasks": { data: TASKS },
    };
  }

  it("draws the task's page for a task of the project", async () => {
    await renderRoute(
      ProjectTaskRoute,
      "/p/$slug/tasks/$taskId",
      served(detail("T-201", "alpha")),
      "/p/alpha/tasks/T-201",
    );
    await waitFor(() => expect(document.body.textContent).toContain("title of T-201"));
    expect(screen.queryByTestId("project-not-found")).toBeNull();
  });

  it("is a plain not-found for a task of another project", async () => {
    await renderRoute(
      ProjectTaskRoute,
      "/p/$slug/tasks/$taskId",
      served(detail("T-301", "beta")),
      "/p/alpha/tasks/T-301",
    );
    expect((await screen.findByTestId("project-not-found")).textContent).toContain(
      "T-301 is not a task of alpha",
    );
  });
});
