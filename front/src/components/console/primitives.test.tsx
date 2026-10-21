/**
 * `Malformed` is the one primitive whose whole job is to be correct about a
 * value nothing checked, so it is the one worth a test: every guard added by
 * T-013 ends in it, and what it prints is the operator's only account of what
 * the api actually served. ADR 29, `docs/ui.md` *A value of the wrong shape is
 * named, not rendered and not dropped*.
 *
 * `PageHeader` has one case, for the one thing about it a caller can get
 * wrong without the type checker saying so: `subtitle` takes a node, and the
 * node is drawn. `docs/debt/T-034-D1.md`.
 *
 * `StatusPill` has the tone of `conflict`, the one status added after the rest.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { Absent, Malformed, PageHeader, StatusPill } from "./primitives";
import type { TaskStatus } from "@/lib/api/types";

afterEach(cleanup);

describe("Malformed", () => {
  // The words are the ones an operator reading a YAML or JSON file uses, not
  // JavaScript's: `typeof []` is `"object"` and `typeof null` is `"object"`,
  // and both of those would be a lie on the screen.
  it.each([
    ["x", "a string"],
    [7, "a number"],
    [true, "a boolean"],
    [{ a: 1 }, "an object"],
    [["a"], "a list"],
    [null, "null"],
    [undefined, "undefined"],
  ])("says what %o is", (got, name) => {
    const { container } = render(<Malformed got={got} want="a list" />);
    expect(container.textContent).toBe(`${name}, not a list`);
  });

  // The label is the key as the payload spells it, so the operator can find
  // the line in the file. Without one — inside a list item, where the key is
  // already above — the sentence starts at the type.
  it("leads with the key when it has one", () => {
    const { container } = render(<Malformed label="changed" got="one-file.py" want="a list" />);
    expect(container.textContent).toBe("changed — a string, not a list");
  });

  it("says only what arrived when it has no key", () => {
    const { container } = render(<Malformed got={7} want="text" />);
    expect(container.textContent).toBe("a number, not text");
  });
});

describe("PageHeader", () => {
  // The task page hands it `Absent` for a task with no description. A string
  // is what every other screen passes, and a node has to render the same way.
  it("draws a node handed to it as the subtitle, under the title", () => {
    const { container } = render(
      <PageHeader title="T-101" subtitle={<Absent label="no description" />} />,
    );
    expect(container.querySelector("h1")?.textContent).toBe("T-101");
    expect(container.textContent).toBe("T-101no description");
  });
});

// `conflict` is the fifth status the dispatcher writes, and it reads as a stop
// that already happened, the way `blocked` does. A word outside the five is
// still drawn, muted and verbatim. `docs/ui.md` *The tone of a state*.
describe("StatusPill", () => {
  const pill = (status: string) =>
    render(<StatusPill status={status as TaskStatus} />).container.firstElementChild!;

  it("gives conflict blocked's tone", () => {
    expect(pill("conflict").className).toBe(pill("blocked").className);
    expect(pill("conflict").className).toContain("text-destructive");
  });

  it("draws awaiting_review as a warning: waiting on the person, not done", () => {
    expect(pill("awaiting_review").className).toContain("text-warning");
    expect(pill("awaiting_review").className).not.toBe(pill("done").className);
  });

  it("draws a status outside the six muted, with its own word", () => {
    const el = pill("queued");
    expect(el.textContent).toBe("queued");
    expect(el.className).toContain("text-muted-foreground");
  });
});
