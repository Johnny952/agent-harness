/**
 * The sidebar's projects and `ProjectGate`, read from the project records
 * (`GET /api/projects`, ADR 209): titles with the slug as fallback, a project
 * with no task listed, an archived one hidden but reachable, and the tasks'
 * `project` fields as the list only when the records read fails. The routes
 * themselves are `-p.test.tsx`'s.
 *
 * The `-` on the filename is load-bearing; see `-index.test.tsx`'s header.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, screen, within } from "@testing-library/react";
import { Route as ProjectBoardRoute } from "./p.$slug.index";
import { Route as BoardRoute } from "./index";
import { renderRoute, type Served } from "@/test/render-route";
import { listedProjects, projectEntries } from "@/lib/projects";
import type { ActionQueue, ProjectRecord, Task } from "@/lib/api/types";

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
