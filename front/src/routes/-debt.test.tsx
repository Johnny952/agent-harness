/**
 * The Debt screen's two bare states, in `-learnings.test.tsx`'s shape and for the
 * same reason: `docs/ui.md` *Absent, empty and broken are three different things*
 * makes Empty and No match two tests over two lists in a fixed order, and this
 * screen carried the same merged branch — the pre-existing half of
 * `docs/debt/T-016-D1.md`.
 *
 * The `-` on the filename is load-bearing; see `-index.test.tsx`'s header.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { DebtEmpty, Route } from "./debt";
import { renderRoute, type Served } from "@/test/render-route";
import type { Task } from "@/lib/api/types";

// `vitest.config.ts` sets `globals: false`, so the library's own cleanup hook
// never runs, and every render would stack in one document.
afterEach(cleanup);

describe("a debt index that holds nothing", () => {
  it("says no phase has declared debt", () => {
    const { container } = render(<DebtEmpty served={0} query="" />);
    expect(container.textContent).toContain("The debt index is empty");
    expect(container.textContent).not.toContain("matches");
  });

  // The order ADR 44 fixes: an index that really is empty keeps its own sentence
  // while a query sits in the box, because the filter is not why it is bare.
  it("keeps that sentence with a query still in the box", () => {
    const { container } = render(<DebtEmpty served={0} query="T-099-D9" />);
    expect(container.textContent).toContain("The debt index is empty");
    expect(container.textContent).not.toContain("T-099-D9");
  });
});

describe("a debt filter that matched nothing", () => {
  it("names the query and the control that brings the rows back", () => {
    const { container } = render(<DebtEmpty served={23} query="T-099-D9" />);
    expect(container.textContent).toContain("No debt row matches “T-099-D9”");
    expect(container.textContent).toContain("Clear the filter box");
    expect(container.textContent).toContain("all 23 rows");
  });

  // The count is over the served collection and both partitions, because that is
  // what clearing the box brings back — a resolved row stays in the index.
  it("says the count covers open and resolved rows", () => {
    const { container } = render(<DebtEmpty served={23} query="T-099-D9" />);
    expect(container.textContent).toContain("open and resolved");
  });

  it("says nothing about the harness", () => {
    const { container } = render(<DebtEmpty served={23} query="T-099-D9" />);
    expect(container.textContent).not.toContain("The debt index is empty");
    expect(container.textContent).not.toContain("No phase has declared debt");
  });

  it("counts one served row in the singular", () => {
    const { container } = render(<DebtEmpty served={1} query="zzz" />);
    expect(container.textContent).toContain("the one row");
    expect(container.textContent).not.toContain("1 rows");
  });
});

// The tests above hand `DebtEmpty` its count; this one lets the screen pass it.
// `rows.length` would type-check there too, and with a query that matches nothing
// it is 0 — the empty index's sentence, said over three served rows.
// `docs/debt/T-017-D1.md`.
describe("the Debt screen with a query that matches none of its rows", () => {
  it("says no row matches, and counts what was served", async () => {
    const row = (id: string) => ({
      id,
      what: "w",
      where: "x",
      fix: "f",
      card: "none",
      resolved: false,
    });
    await renderRoute(Route, "/debt", {
      "/api/debt": { data: [row("T-001-D1"), row("T-002-D1"), row("T-003-D1")] },
    });
    await screen.findByText("T-002-D1");

    fireEvent.change(screen.getByPlaceholderText(/^Filter debt/), {
      target: { value: "T-099-D9" },
    });

    expect(await screen.findByText("No debt row matches “T-099-D9”")).toBeTruthy();
    expect(document.body.textContent).toContain("all 3 rows");
    expect(document.body.textContent).not.toContain("The debt index is empty");
  });
});

// Each open entry's backlog item and whether it was accepted (ADR 141). The
// pairing itself is `lib/debt.test.ts`'s; these check what the screen draws.
describe("the Debt screen's backlog items", () => {
  const entry = (id: string, resolved = false) => ({
    id,
    what: resolved ? "**resolved** w" : "w",
    where: "x",
    fix: "f",
    card: "none",
    resolved,
  });
  const task = (id: string, over: Partial<Task> = {}): Task => ({
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
    source: "debt",
    tags: ["debt"],
    rank: null,
    debt_id: null,
    epic: null,
    split_into: [],
    merged: false,
    title: null,
    ...over,
  });
  const cell = (id: string) => screen.getByTestId(`item-${id}`);

  async function open(entries: ReturnType<typeof entry>[], tasks: Served["/api/tasks"]) {
    const r = await renderRoute(Route, "/debt", {
      "/api/debt": { data: entries },
      "/api/tasks": tasks,
    });
    await screen.findByText(entries[0]!.id);
    return r;
  }

  it("says an entry with no item is not imported", async () => {
    await open([entry("T-001-D1")], { data: [] });
    await waitFor(() => expect(cell("T-001-D1").textContent).toBe("not imported"));
    expect(within(cell("T-001-D1")).queryByRole("link")).toBeNull();
  });

  it.each(["proposed", "deferred", "dropped"] as const)(
    "links a %s item and says it was not accepted",
    async (status) => {
      await open([entry("T-001-D1")], {
        data: [task("T-200", { status, debt_id: "T-001-D1" })],
      });
      await waitFor(() =>
        expect(cell("T-001-D1").textContent).toContain(`${status} — not accepted`),
      );
      const link = within(cell("T-001-D1")).getByRole("link");
      expect(link.getAttribute("href")).toBe("/tasks/T-200");
    },
  );

  it.each([
    [{ status: "pending" as const }, "accepted — pending"],
    [{ status: "in_progress" as const }, "accepted — in_progress"],
    [{ status: "done" as const, merged: true }, "accepted — merged"],
  ])("says which board status an accepted item holds", async (over, words) => {
    await open([entry("T-001-D1")], { data: [task("T-200", { ...over, debt_id: "T-001-D1" })] });
    await waitFor(() => expect(cell("T-001-D1").textContent).toContain(words));
    expect(within(cell("T-001-D1")).getByRole("link").getAttribute("href")).toBe("/tasks/T-200");
  });

  it("shows both items of a shared debt_id under a warning", async () => {
    await open([entry("T-001-D1")], {
      data: [
        task("T-200", { debt_id: "T-001-D1" }),
        task("T-201", { status: "pending", debt_id: "T-001-D1" }),
      ],
    });
    await waitFor(() => expect(within(cell("T-001-D1")).getAllByRole("link")).toHaveLength(2));
    expect(within(cell("T-001-D1")).getByRole("alert").textContent).toContain(
      "2 tasks (T-200, T-201)",
    );
    expect(cell("T-001-D1").textContent).toContain("proposed — not accepted");
    expect(cell("T-001-D1").textContent).toContain("accepted — pending");
  });

  it("still renders the index when the tasks read fails, and never says not imported", async () => {
    await open([entry("T-001-D1"), entry("T-002-D1")], { status: 500, error: "boom" });
    await waitFor(() => expect(cell("T-001-D1").textContent).toBe("pairing unavailable"));
    expect(cell("T-002-D1").textContent).toBe("pairing unavailable");
    expect(document.body.textContent).toContain("/api/tasks did not answer");
    expect(document.body.textContent).not.toContain("not imported");
    expect(document.body.textContent).not.toContain("could not be read");
  });

  it("leaves resolved entries as they were: no item column", async () => {
    await open([entry("T-001-D1"), entry("T-002-D1", true)], {
      data: [task("T-200", { status: "pending", debt_id: "T-002-D1" })],
    });
    await waitFor(() => expect(cell("T-001-D1").textContent).toBe("not imported"));
    expect(screen.queryByTestId("item-T-002-D1")).toBeNull();
    expect(screen.getAllByRole("columnheader", { name: "item" })).toHaveLength(1);
    expect(document.body.textContent).not.toContain("T-200");
  });

  it("writes nothing", async () => {
    const r = await open([entry("T-001-D1")], { data: [task("T-200", { debt_id: "T-001-D1" })] });
    await waitFor(() => expect(cell("T-001-D1").textContent).toContain("T-200"));
    expect(r.sent.filter((s) => s.method !== "GET")).toEqual([]);
  });

  it("filters an open entry by its item's id and state words", async () => {
    await open([entry("T-001-D1"), entry("T-002-D1"), entry("T-003-D1", true)], {
      data: [task("T-097", { debt_id: "T-001-D1" })],
    });
    await waitFor(() => expect(cell("T-001-D1").textContent).toContain("T-097"));
    const box = screen.getByPlaceholderText(/^Filter debt/);

    fireEvent.change(box, { target: { value: "t-097" } });
    await waitFor(() => expect(screen.queryByText("T-002-D1")).toBeNull());
    expect(screen.getByText("T-001-D1")).toBeTruthy();

    fireEvent.change(box, { target: { value: "not imported" } });
    await waitFor(() => expect(screen.queryByText("T-001-D1")).toBeNull());
    expect(screen.getByText("T-002-D1")).toBeTruthy();
    // A resolved row draws no item, so its words are not the box's to find.
    expect(screen.queryByText("T-003-D1")).toBeNull();
  });

  const desc = (surface: string) =>
    `## Goal\n\ng\n\n## Done when\n\nd\n\n## Surface\n\n${surface}\n`;

  it("points an open entry at a task in play whose Surface its where falls on", async () => {
    await open([{ ...entry("T-001-D1"), where: "`front/src/lib/debt.ts:matchesQuery`" }], {
      data: [
        task("T-300", { status: "in_progress", description: desc("- `front/src/lib/`") }),
        task("T-301", { status: "done", merged: true, description: desc("- `front/`") }),
      ],
    });
    const note = await screen.findByTestId("in-play-T-001-D1");
    expect(note.textContent).toContain("where falls on the surface of");
    expect(note.textContent).toContain("accepted — in_progress");
    const links = within(note).getAllByRole("link");
    expect(links.map((l) => l.getAttribute("href"))).toEqual(["/tasks/T-300"]);
    // The item cell's own words are unchanged.
    expect(cell("T-001-D1").textContent).toBe("not imported");
  });

  it("points at nothing when the only overlapping task is merged", async () => {
    await open([{ ...entry("T-001-D1"), where: "`front/src/lib/debt.ts`" }], {
      data: [task("T-301", { status: "done", merged: true, description: desc("- `front/`") })],
    });
    await waitFor(() => expect(cell("T-001-D1").textContent).toBe("not imported"));
    expect(screen.queryByTestId("in-play-T-001-D1")).toBeNull();
  });
});
