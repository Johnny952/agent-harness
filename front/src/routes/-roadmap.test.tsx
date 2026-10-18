/**
 * The Roadmap screen, mounted whole on what `GET /api/roadmap` served: the band
 * order, a plan opening to its tasks, the *Current only* filter, the warnings
 * banner and the error state (`docs/plans/roadmap-view.md` item 5, T-075).
 *
 * The `-` on the filename is load-bearing; see `-index.test.tsx`'s header.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { Route } from "./roadmap";
import { renderRoute } from "@/test/render-route";
import type { Roadmap, RoadmapPlan, RoadmapTask } from "@/lib/api/types";

// `vitest.config.ts` sets `globals: false`, so the library's own cleanup hook
// never runs, and every render would stack in one document.
afterEach(cleanup);

const task = (id: string, state: string, merged_at: string | null = null): RoadmapTask => ({
  id,
  title: null,
  state,
  merged_at,
});

const plan = (
  file: string,
  title: string,
  status: string,
  tasks: RoadmapTask[] = [],
): RoadmapPlan => ({
  file,
  title,
  status,
  approved: "2026-10-01",
  built: status === "built" ? "2026-10-10" : null,
  supersedes: [],
  superseded_by: [],
  tasks,
});

const roadmap: Roadmap = {
  project: "ia-harness",
  bands: {
    future: [plan("idea.md", "Plan in the future", "idea")],
    now: [
      plan("building.md", "Plan being built", "building", [
        task("T-101", "in_progress"),
        task("T-100", "done", "2026-10-12T10:00:00+00:00"),
      ]),
    ],
    past: [
      plan("built.md", "Plan already built", "built", [
        task("T-090", "done", "2026-10-09T10:00:00+00:00"),
      ]),
    ],
  },
  no_plan: {
    now: [task("T-110", "pending")],
    past: [{ month: "2026-09", tasks: [task("T-050", "done", "2026-09-03T10:00:00+00:00")] }],
  },
};

const open = (path = "/roadmap", warnings: string[] = []) =>
  renderRoute(Route, "/roadmap", { "/api/roadmap": { data: roadmap, warnings } }, path);

/** The band headings, in document order. */
const bandHeadings = () =>
  screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent ?? "");

describe("the Roadmap screen", () => {
  it("lists the bands future, now, past, each with its count", async () => {
    await open();
    await screen.findByText("Plan in the future");
    expect(bandHeadings()).toEqual(["Future · 1 plan", "Now · 1 plan", "Past · 1 plan"]);
    // The no-plan groups sit at the end of the band they belong to.
    const now = screen.getByRole("region", { name: "Now" });
    expect(now.textContent).toContain("Tasks with no plan");
    const past = screen.getByRole("region", { name: "Past" });
    expect(past.textContent).toContain("Merged with no plan");
  });

  it("keeps plans collapsed until one is opened, then shows its tasks with links", async () => {
    await open();
    const row = await screen.findByRole("button", { name: /Plan being built/ });
    expect(row.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("T-101")).toBeNull();

    fireEvent.click(row);
    expect(row.getAttribute("aria-expanded")).toBe("true");
    const link = screen.getByRole("link", { name: "T-101" });
    expect(link.getAttribute("href")).toBe("/tasks/T-101");
    expect(screen.getByText("merged 2026-10-12")).toBeTruthy();
  });

  it("groups the merged tasks with no plan by month", async () => {
    await open();
    fireEvent.click(await screen.findByRole("button", { name: /Merged with no plan/ }));
    expect(screen.getByText("2026-09 · 1")).toBeTruthy();
    expect(screen.getByRole("link", { name: "T-050" })).toBeTruthy();
  });

  it("hides the past band and the merged tasks under Current only", async () => {
    await open();
    fireEvent.click(await screen.findByRole("button", { name: /Plan being built/ }));
    expect(screen.getByRole("link", { name: "T-100" })).toBeTruthy();

    const current = screen.getByRole("button", { name: "Current only" });
    fireEvent.click(current);
    await waitFor(() => expect(current.getAttribute("aria-pressed")).toBe("true"));

    expect(bandHeadings()).toEqual(["Future · 1 plan", "Now · 1 plan"]);
    expect(screen.queryByRole("region", { name: "Past" })).toBeNull();
    expect(screen.queryByText("Plan already built")).toBeNull();
    // The plan stays open; its merged task is gone and its open one is not.
    expect(screen.queryByRole("link", { name: "T-100" })).toBeNull();
    expect(screen.getByRole("link", { name: "T-101" })).toBeTruthy();
    expect(screen.getByText(/1 merged or done hidden/)).toBeTruthy();
  });

  it("reads the filter from the URL", async () => {
    await open("/roadmap?view=current");
    await screen.findByText("Plan in the future");
    expect(screen.getByRole("button", { name: "Current only" }).getAttribute("aria-pressed")).toBe(
      "true",
    );
    expect(screen.queryByRole("region", { name: "Past" })).toBeNull();
  });

  it("shows the api's warnings in the banner", async () => {
    await open("/roadmap", ["plan broken.md: no frontmatter"]);
    expect(await screen.findByText("plan broken.md: no frontmatter")).toBeTruthy();
  });

  it("shows the error state when the read fails", async () => {
    await renderRoute(Route, "/roadmap", {
      "/api/roadmap": { status: 502, error: "upstream down" },
    });
    expect(await screen.findByText("The roadmap could not be read")).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 2 })).toBeNull();
  });

  it("says so when the project has no plan and no task", async () => {
    await renderRoute(Route, "/roadmap", {
      "/api/roadmap": {
        data: {
          project: "x",
          bands: { future: [], now: [], past: [] },
          no_plan: { now: [], past: [] },
        },
      },
    });
    expect(await screen.findByText("This project has no roadmap yet")).toBeTruthy();
  });
});
