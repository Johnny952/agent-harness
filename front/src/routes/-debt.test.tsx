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
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { DebtEmpty, Route } from "./debt";
import { renderRoute } from "@/test/render-route";

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
