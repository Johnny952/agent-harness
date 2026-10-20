/**
 * The sidebar's projects and `ProjectGate`, read from the project records
 * (`GET /api/projects`, ADR 209): titles with the slug as fallback, a project
 * with no task listed, an archived one hidden but reachable, and the tasks'
 * `project` fields as the list only when the records read fails; each
 * project's running and waiting counts and the archived toggle (ADR NNN). The
 * routes themselves are `-p.test.tsx`'s.
 *
 * The `-` on the filename is load-bearing; see `-index.test.tsx`'s header.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { Route as ProjectBoardRoute } from "./p.$slug.index";
import { Route as BoardRoute } from "./index";
import { renderRoute, type Served } from "@/test/render-route";
import { listedProjects, projectCounts, projectEntries } from "@/lib/projects";
import type { ActionQueue, ActionRecord, ProjectRecord, Task } from "@/lib/api/types";

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

function record(slug: string, over: Partial<ProjectRecord> = {}): ProjectRecord {
  return {
    slug,
    title: slug,
    remote: null,
    base_branch: null,
    key: null,
    settings: {},
    created: "2026-10-20T12:00:00+00:00",
    status: "active",
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

/** `alpha` has tasks; `beta` has none; `old` is archived; `gamma` is named by a task only. */
const TASKS: Task[] = [task("T-201", { project: "alpha" }), task("T-401", { project: "gamma" })];
const RECORDS: ProjectRecord[] = [
  record("beta", { title: "" }),
  record("alpha", { title: "The alpha site" }),
  record("old", { title: "Old thing", status: "archived" }),
];

function served(over: Served = {}): Served {
  return {
    "/api/projects": { data: RECORDS },
    "/api/tasks": { data: TASKS },
    "/api/actions": { data: EMPTY_QUEUE },
    "/api/ready": { data: [] },
    "/api/accounts": { data: [] },
    "/api/debt": { data: [] },
    ...over,
  };
}

const sidebar = () => screen.getByRole("navigation");

async function projectLinks() {
  const list = await within(sidebar()).findByRole("list", { name: "Projects" });
  return within(list).getAllByRole("link");
}

describe("projectEntries", () => {
  it("orders by slug, shows the slug for an empty title, and lists only the active", () => {
    const entries = projectEntries(RECORDS);
    expect(entries).toEqual([
      { slug: "alpha", title: "The alpha site", status: "active" },
      { slug: "beta", title: "beta", status: "active" },
      { slug: "old", title: "Old thing", status: "archived" },
    ]);
    expect(listedProjects(entries).map((e) => e.slug)).toEqual(["alpha", "beta"]);
  });
});

describe("the sidebar's projects, from the records", () => {
  it("shows titles, the slug as fallback, a project with no task, and no archived one", async () => {
    await renderRoute(BoardRoute, "/", served());
    const links = await projectLinks();
    expect(links.map((a) => a.textContent)).toEqual(["The alpha site", "beta"]);
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["/p/alpha", "/p/beta"]);
    // The slug stays on hand behind a title.
    expect(within(sidebar()).getByText("The alpha site").getAttribute("title")).toBe("alpha");
    // A slug only a task names is not a record, so not listed; no fallback note.
    expect(within(sidebar()).queryByTestId("project-link-gamma")).toBeNull();
    expect(within(sidebar()).queryByTestId("projects-fallback")).toBeNull();
  });

  it("says so when no active project is recorded", async () => {
    await renderRoute(
      BoardRoute,
      "/",
      served({ "/api/projects": { data: [record("old", { status: "archived" })] } }),
    );
    expect(await within(sidebar()).findByText("No active project recorded yet.")).toBeTruthy();
  });

  it("falls back to the slugs the tasks name when the records read fails, and says so", async () => {
    await renderRoute(
      BoardRoute,
      "/",
      served({ "/api/projects": { status: 503, error: "api unreachable" } }),
    );
    const links = await projectLinks();
    expect(links.map((a) => a.textContent)).toEqual(["alpha", "gamma"]);
    expect(within(sidebar()).getByTestId("projects-fallback").textContent).toBe(
      "Project records unread: listed from the tasks, by slug.",
    );
  });

  it("says the projects are unread only when both reads fail", async () => {
    await renderRoute(
      BoardRoute,
      "/",
      served({
        "/api/projects": { status: 503, error: "down" },
        "/api/tasks": { status: 503, error: "down" },
      }),
    );
    expect(
      await within(sidebar()).findByText(
        "Projects unread: neither the project records nor the task list answered.",
      ),
    ).toBeTruthy();
  });
});

describe("ProjectGate, from the records", () => {
  it("knows a recorded project with no task", async () => {
    await renderRoute(ProjectBoardRoute, "/p/$slug/", served(), "/p/beta/");
    expect(await screen.findByRole("heading", { name: "Board · beta" })).toBeTruthy();
    expect(screen.queryByTestId("project-not-found")).toBeNull();
    // The open project lists its screens beneath it.
    const hrefs = (await projectLinks()).map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual([
      "/p/alpha",
      "/p/beta",
      "/p/beta",
      "/p/beta/backlog",
      "/p/beta/debt",
      "/p/beta/learnings",
      "/p/beta/roadmap",
    ]);
  });

  it("knows an archived project, hidden from the sidebar but reachable by its URL", async () => {
    await renderRoute(ProjectBoardRoute, "/p/$slug/", served(), "/p/old/");
    expect(await screen.findByRole("heading", { name: "Board · old" })).toBeTruthy();
    expect(screen.queryByTestId("project-not-found")).toBeNull();
    expect(within(sidebar()).queryByTestId("project-link-old")).toBeNull();
  });

  it("knows a slug a task names that has no record", async () => {
    await renderRoute(ProjectBoardRoute, "/p/$slug/", served(), "/p/gamma/");
    expect(await screen.findByText("T-401")).toBeTruthy();
    expect(screen.queryByTestId("project-not-found")).toBeNull();
  });

  it("is a plain not-found for a slug neither the records nor the tasks know", async () => {
    await renderRoute(ProjectBoardRoute, "/p/$slug/", served(), "/p/delta/");
    const nf = await screen.findByTestId("project-not-found");
    expect(nf.textContent).toContain("No project named delta");
    expect(nf.textContent).toContain("No project record has this slug");
  });

  it("knows a record when the tasks read fails, and draws the screen's own error", async () => {
    await renderRoute(
      ProjectBoardRoute,
      "/p/$slug/",
      served({ "/api/tasks": { status: 503, error: "down" } }),
      "/p/beta/",
    );
    expect(await screen.findByRole("heading", { name: "Board · beta" })).toBeTruthy();
    expect(screen.queryByTestId("project-not-found")).toBeNull();
  });

  it("draws the screen, not a not-found, when both reads fail", async () => {
    await renderRoute(
      ProjectBoardRoute,
      "/p/$slug/",
      served({
        "/api/projects": { status: 503, error: "down" },
        "/api/tasks": { status: 503, error: "down" },
      }),
      "/p/delta/",
    );
    expect(await screen.findByRole("heading", { name: "Board · delta" })).toBeTruthy();
    expect(screen.queryByTestId("project-not-found")).toBeNull();
  });
});

function start(taskId: string, over: Partial<ActionRecord> = {}): ActionRecord {
  return {
    version: 1,
    id: `A-${taskId}`,
    kind: "start",
    project: null,
    task_id: taskId,
    params: null,
    actor: "console",
    note: null,
    created_at: "2026-10-20T12:00:00+00:00",
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

/** `alpha`: two running (one between phases) and one started, waiting; `beta`: one waiting. */
const BUSY_TASKS: Task[] = [
  task("T-201", { project: "alpha", status: "in_progress", phase: "implementador" }),
  task("T-202", { project: "alpha", status: "in_progress", phase: null }),
  task("T-203", { project: "alpha" }),
  task("T-204", { project: "alpha", status: "in_progress", merged: true }),
  task("T-205", { project: "alpha", status: "done" }),
  task("E-1", { project: "alpha", status: "in_progress", split_into: ["T-201"] }),
  task("T-301", { project: "beta" }),
];
const BUSY_QUEUE: ActionQueue = {
  ...EMPTY_QUEUE,
  // T-201's claimed start is running, not waiting; T-301's record names its project.
  claimed: [start("T-201", { status: "claimed", project: "alpha" })],
  pending: [start("T-203", { waiting: "project" }), start("T-301", { project: "beta" })],
};

describe("projectCounts", () => {
  it("counts in_progress cards as running, and started tasks not in progress as waiting", () => {
    expect(Object.fromEntries(projectCounts(BUSY_TASKS, BUSY_QUEUE))).toEqual({
      alpha: { running: 2, waiting: 1 },
      beta: { running: 0, waiting: 1 },
    });
  });

  it("takes a start's project from its record, else its task row, and counts a task once", () => {
    const queue: ActionQueue = {
      ...EMPTY_QUEUE,
      claimed: [start("T-9", { status: "claimed" })],
      pending: [start("T-9"), start("T-8", { project: "gamma" }), start("T-7")],
    };
    // T-7 has neither a record project nor a row: it names no project.
    expect(Object.fromEntries(projectCounts([task("T-9", { project: "beta" })], queue))).toEqual({
      beta: { running: 0, waiting: 1 },
      gamma: { running: 0, waiting: 1 },
    });
  });

  it("skips a merged task's start and other kinds, and leaves waiting null when unread", () => {
    const queue: ActionQueue = {
      ...EMPTY_QUEUE,
      pending: [start("T-203", { kind: "cancel" }), start("T-204")],
    };
    expect(projectCounts(BUSY_TASKS, queue).get("alpha")).toEqual({ running: 2, waiting: 0 });
    expect(projectCounts(BUSY_TASKS, null).get("alpha")).toEqual({ running: 2, waiting: null });
    expect(projectCounts(BUSY_TASKS, null).has("beta")).toBe(false);
  });
});

describe("the sidebar's counts", () => {
  it("draws running and waiting beside each project, and nothing at zero", async () => {
    await renderRoute(
      BoardRoute,
      "/",
      served({ "/api/tasks": { data: BUSY_TASKS }, "/api/actions": { data: BUSY_QUEUE } }),
    );
    const nav = within(sidebar());
    expect((await nav.findByTestId("project-running-alpha")).textContent).toBe("2");
    expect((await nav.findByTestId("project-waiting-alpha")).getAttribute("title")).toBe(
      "1 waiting",
    );
    expect(nav.getByTestId("project-waiting-beta").textContent).toBe("1");
    expect(nav.queryByTestId("project-running-beta")).toBeNull();
    expect(nav.queryByTestId("project-counts-unread")).toBeNull();
  });

  it("draws no count, not a zero, when the task list fails, and says so", async () => {
    await renderRoute(
      BoardRoute,
      "/",
      served({
        "/api/tasks": { status: 503, error: "down" },
        "/api/actions": { data: BUSY_QUEUE },
      }),
    );
    const nav = within(sidebar());
    expect((await nav.findByTestId("project-counts-unread")).textContent).toBe(
      "Counts unread: the task list did not answer.",
    );
    expect(nav.queryByTestId("project-waiting-beta")).toBeNull();
    expect(nav.queryByTestId("project-running-alpha")).toBeNull();
    expect((await projectLinks()).map((a) => a.textContent)).toEqual(["The alpha site", "beta"]);
  });

  it("draws running but no waiting when the queue fails, and says so", async () => {
    await renderRoute(
      BoardRoute,
      "/",
      served({
        "/api/tasks": { data: BUSY_TASKS },
        "/api/actions": { status: 503, error: "down" },
      }),
    );
    const nav = within(sidebar());
    expect((await nav.findByTestId("project-counts-unread")).textContent).toBe(
      "Waiting unread: the action queue did not answer.",
    );
    expect((await nav.findByTestId("project-running-alpha")).textContent).toBe("2");
    expect(nav.queryByTestId("project-waiting-alpha")).toBeNull();
    expect(nav.queryByTestId("project-waiting-beta")).toBeNull();
  });
});

describe("the sidebar's archived toggle", () => {
  it("hides the archived by default and lists them, marked, when asked", async () => {
    await renderRoute(BoardRoute, "/", served());
    expect((await projectLinks()).map((a) => a.textContent)).toEqual(["The alpha site", "beta"]);
    const toggle = within(sidebar()).getByRole("button", { name: "Show archived (1)" });
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(toggle);
    const links = await projectLinks();
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["/p/alpha", "/p/beta", "/p/old"]);
    expect(within(sidebar()).getByTestId("project-link-old").textContent).toBe("Old thingarchived");
    const hide = within(sidebar()).getByRole("button", { name: "Hide archived (1)" });
    expect(hide.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(hide);
    expect(within(sidebar()).queryByTestId("project-link-old")).toBeNull();
  });

  it("lists an archived project when it is the only record and the toggle is on", async () => {
    await renderRoute(
      BoardRoute,
      "/",
      served({ "/api/projects": { data: [record("old", { status: "archived" })] } }),
    );
    expect(await within(sidebar()).findByText("No active project recorded yet.")).toBeTruthy();
    fireEvent.click(within(sidebar()).getByRole("button", { name: "Show archived (1)" }));
    expect(within(sidebar()).getByTestId("project-link-old")).toBeTruthy();
  });

  it("has no toggle when the list comes from the tasks, which hold no archived", async () => {
    await renderRoute(BoardRoute, "/", served({ "/api/projects": { status: 503, error: "down" } }));
    await projectLinks();
    expect(within(sidebar()).queryByRole("button", { name: /archived/ })).toBeNull();
  });
});
