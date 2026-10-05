/**
 * `Malformed` is the one primitive whose whole job is to be correct about a
 * value nothing checked, so it is the one worth a test: every guard added by
 * T-013 ends in it, and what it prints is the operator's only account of what
 * the api actually served. ADR 29, `docs/ui.md` *A value of the wrong shape is
 * named, not rendered and not dropped*.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { Malformed } from "./primitives";

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
