/**
 * The Learnings screen's two bare states, which are two different facts.
 *
 * `docs/ui.md` *Absent, empty and broken are three different things* makes Empty
 * and No match a pair of tests over two lists, in that order: the collection the
 * read served, and only then the filter's own result. The order is the half that
 * is easy to lose — a screen that tests the filtered list alone tells an operator
 * who mistyped a query that no phase has ever written an entry, which is the
 * defect `docs/debt/T-016-D1.md` carried — so the case worth pinning hardest is
 * the one a later refactor would invert: an empty collection with a query still
 * in the box keeps Empty's sentence.
 *
 * The `-` on the filename is load-bearing: `@tanstack/router-plugin` reads every
 * other file under `src/routes/` as a route, and `routeFileIgnorePrefix` defaults
 * to `-`. Without it this file would arrive in `routeTree.gen.ts` as a route no
 * phase here can regenerate.
 *
 * `LearningsEmpty` needs no router and no query client: it is a component
 * exported beside `Route` that takes the two numbers the branch turns on
 * (`docs/decisions.md` ADR 44), so the render below mounts the branch and not the
 * console.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { LearningsEmpty } from "./learnings";

// `vitest.config.ts` sets `globals: false`, so the library's own cleanup hook
// never runs, and every render would stack in one document.
afterEach(cleanup);

describe("a learnings collection that holds nothing", () => {
  it("says no phase has written an entry", () => {
    const { container } = render(<LearningsEmpty served={0} query="" />);
    expect(container.textContent).toContain("No phase has written a learning entry");
    expect(container.textContent).not.toContain("matches");
  });

  // The order ADR 44 fixes: the filter is not why there is nothing there, so the
  // query in the box does not get to change the sentence.
  it("keeps that sentence with a query still in the box", () => {
    const { container } = render(<LearningsEmpty served={0} query="obserability" />);
    expect(container.textContent).toContain("No phase has written a learning entry");
    expect(container.textContent).not.toContain("obserability");
  });
});

describe("a learnings filter that matched nothing", () => {
  it("names the query and the control that brings the rows back", () => {
    const { container } = render(<LearningsEmpty served={41} query="obserability" />);
    expect(container.textContent).toContain("No learning entry matches “obserability”");
    expect(container.textContent).toContain("Clear the filter box");
    expect(container.textContent).toContain("all 41 entries");
  });

  // What this state may not say. The subject is the filter and never the harness.
  it("says nothing about the harness", () => {
    const { container } = render(<LearningsEmpty served={41} query="obserability" />);
    expect(container.textContent).not.toContain("No phase has written a learning entry");
  });

  it("counts one served entry in the singular", () => {
    const { container } = render(<LearningsEmpty served={1} query="zzz" />);
    expect(container.textContent).toContain("the one entry");
    expect(container.textContent).not.toContain("1 entries");
  });
});
