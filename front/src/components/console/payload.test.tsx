/**
 * The first tests `front/` has ever had, and they are the shape
 * `docs/debt/T-013-D1.md` *Fix* step 2 asks for: one render per api-served
 * value, with a junk value in it. The class they cover is the one a typecheck
 * cannot see — `HandoffPayload` says `changed?: string[]`, and
 * `dispatcher/handoff.py` writes back what the role returned while
 * `observability/api/app.py:_phase` passes it through verbatim, so the type is
 * an annotation and the render is the only check there is. ADR 29.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { PhaseList, PhasePill } from "./payload";
import { asPathLine } from "@/lib/format";

// `vitest.config.ts` sets `globals: false`, so the library's own cleanup —
// which registers itself only if there is a global `afterEach` to register
// with — never runs, and every render would stack in one document.
afterEach(cleanup);

describe("PhaseList", () => {
  it("draws one line per item of a good list", () => {
    const { container } = render(<PhaseList label="changed" value={["a.py", "b.py"]} />);
    const items = container.querySelectorAll("li");
    expect(items).toHaveLength(2);
    expect(items[0]?.textContent).toBe("a.py");
    expect(items[1]?.textContent).toBe("b.py");
  });

  it("draws nothing for a key the role did not write", () => {
    const { container } = render(<PhaseList label="changed" value={undefined} />);
    expect(container.textContent).toBe("");
  });

  it("draws nothing for an empty list, which is the role leaving it blank", () => {
    const { container } = render(<PhaseList label="changed" value={[]} />);
    expect(container.textContent).toBe("");
  });

  // This one is the auditor's finding, and the reason this file exists. A
  // `changed: one-file.py` written without a dash is a string; it is not empty,
  // so a `length === 0` guard waves it through, and `.map` throws on it. The
  // whole console goes with it — the only `errorComponent` in the tree is on
  // the root route.
  it("names a string where a list was meant, instead of throwing", () => {
    const { container } = render(<PhaseList label="changed" value="one-file.py" />);
    expect(container.textContent).toBe("changed — a string, not a list");
    expect(container.querySelector("li")).toBeNull();
  });

  it("names an object where a list was meant", () => {
    const { container } = render(<PhaseList label="risks" value={{ a: 1 }} />);
    expect(container.textContent).toBe("risks — an object, not a list");
  });

  // One bad item does not cost the good ones: the rest is what the role
  // returned, and what the role returned is the question being asked.
  it("keeps the good items of a list and names only the bad one", () => {
    const { container } = render(<PhaseList label="changed" value={["a.py", 7, "b.py"]} />);
    const items = [...container.querySelectorAll("li")].map((li) => li.textContent);
    expect(items).toEqual(["a.py", "a number, not text", "b.py"]);
  });
});

describe("PhaseList with asPathLine", () => {
  const paths = (value: unknown) =>
    render(
      <PhaseList
        label="paths"
        value={value}
        line={asPathLine}
        itemWant="a path and what it holds"
      />,
    );

  it("joins a well-formed pair", () => {
    const { container } = paths([{ path: "dispatcher/gates.py", holds: "the gate" }]);
    expect(container.querySelector("li")?.textContent).toBe("dispatcher/gates.py — the gate");
  });

  it("names a pair missing its second key", () => {
    const { container } = paths([{ path: "dispatcher/gates.py" }]);
    expect(container.querySelector("li")?.textContent).toBe(
      "an object, not a path and what it holds",
    );
  });

  it("names a bare string where a pair was meant", () => {
    const { container } = paths(["dispatcher/gates.py"]);
    expect(container.querySelector("li")?.textContent).toBe(
      "a string, not a path and what it holds",
    );
  });
});

describe("PhasePill", () => {
  it("shows a word the harness knows", () => {
    const { container } = render(<PhasePill field="status" value="complete" />);
    expect(container.textContent).toBe("complete");
  });

  // A word outside the table is still the role's own answer, so it is shown
  // verbatim and muted — the way `StatusPill` treats a fifth status.
  it("shows a word the harness does not know, verbatim", () => {
    const { container } = render(<PhasePill field="verdict" value="MAYBE" />);
    expect(container.textContent).toBe("MAYBE");
  });

  it("draws nothing for a key the role did not write", () => {
    const { container } = render(<PhasePill field="status" value={undefined} />);
    expect(container.textContent).toBe("");
  });

  it("draws nothing for a key the role left blank", () => {
    const { container } = render(<PhasePill field="status" value="" />);
    expect(container.textContent).toBe("");
  });

  // `{value}` with something that is not a string in it is a child React will
  // not take, and it throws at the render rather than at the api.
  it("names a value that is not a word", () => {
    const { container } = render(<PhasePill field="status" value={3} />);
    expect(container.textContent).toBe("status — a number, not text");
  });
});
