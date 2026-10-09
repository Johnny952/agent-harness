/**
 * One render per served value, junk included, in `payload.test.tsx`'s shape.
 *
 * The class these cover is the one a typecheck cannot see: `scope` and `status`
 * on a `/api/learnings` row are `str()`-derived from a file's own frontmatter —
 * `dispatcher/learnings.py:Entry` coerces and validates nothing — so the union
 * in `LearningEntry` is an annotation and the render is the only check there is
 * (`docs/learnings/a-console-type-over-a-served-value-is-an-annotation.md`).
 * The rest is that both of these cells are *served judgements*: a refuted row
 * and a row past the cap are both out of the phase table for different reasons,
 * and this is where that distinction is pinned.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { InPhaseTable, LearningStatus } from "./learnings";
import type { LearningEntry } from "@/lib/api/types";

// `vitest.config.ts` sets `globals: false`, so the library's own cleanup —
// which registers itself only if there is a global `afterEach` to register
// with — never runs, and every render would stack in one document.
afterEach(cleanup);

/** A served row, with the one field under test overridden. */
function entry(over: Partial<LearningEntry> = {}): LearningEntry {
  return {
    ref: "inbox/T-016-a-trap.md",
    task: "T-016",
    carried_by: "T-016",
    scope: "project",
    status: "unconfirmed",
    when: "you reach for bun in a phase",
    rule: "Read the task's own command grant first.",
    stale: false,
    in_phase_table: true,
    phase_table_cap: 40,
    ...over,
  };
}

describe("LearningStatus", () => {
  it("shows a status the harness knows", () => {
    const { container } = render(<LearningStatus entry={entry({ status: "confirmed" })} />);
    expect(container.textContent).toBe("confirmed");
  });

  // A word outside the harness's three is still the file's own answer, so it is
  // shown verbatim and muted, the way `StatusPill` treats a fifth task status.
  it("shows a status the harness does not know, verbatim", () => {
    const { container } = render(<LearningStatus entry={entry({ status: "promoted" })} />);
    expect(container.textContent).toBe("promoted");
  });

  // `confirmed (stale)` is what a phase's own prompt table reads, and the
  // qualifier is a second axis rather than a recolour: an entry a second task
  // confirmed is still confirmed under a surface that has since changed.
  it("qualifies a served stale with the status, and does not replace it", () => {
    const { container } = render(
      <LearningStatus entry={entry({ status: "confirmed", stale: true })} />,
    );
    expect(container.textContent).toBe("confirmed(stale)");
  });

  it("says nothing about staleness for a fresh entry", () => {
    const { container } = render(<LearningStatus entry={entry({ stale: false })} />);
    expect(container.textContent).not.toContain("stale");
  });
});

describe("InPhaseTable", () => {
  it("says a row reaches a phase when the api says it does", () => {
    const { container } = render(<InPhaseTable entry={entry({ in_phase_table: true })} />);
    expect(container.textContent).toBe("in table");
  });

  // The cap is read off the row, because the number is
  // `dispatcher/learnings.py:MAX_ROWS` and the console cannot see it change.
  it("names the cap off the row for a row the cap cut", () => {
    const { container } = render(
      <InPhaseTable entry={entry({ in_phase_table: false, phase_table_cap: 12 })} />,
    );
    expect(container.textContent).toBe("over the 12 cap");
  });

  // Two falses, two facts. A refuted entry is dropped by `eligible` before the
  // cap is applied at all, so naming the cap here would be a wrong reason — and
  // the api serves it with `in_phase_table: false` for exactly that reason.
  it("tells a refuted row apart from one the cap cut", () => {
    const { container } = render(
      <InPhaseTable entry={entry({ status: "refuted", in_phase_table: false })} />,
    );
    expect(container.textContent).toBe("refuted — not handed to phases");
  });

  // Served, never derived: the api decides, and a refuted row that somehow
  // arrives with `in_phase_table: true` is still reported as refuted rather than
  // as a row in the table.
  it("reads the status before the flag for a refuted row", () => {
    const { container } = render(
      <InPhaseTable entry={entry({ status: "refuted", in_phase_table: true })} />,
    );
    expect(container.textContent).toBe("refuted — not handed to phases");
  });
});
