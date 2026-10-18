/** `lib/comments.ts`: the form's limit, counted as the dispatcher counts it (ADR 139). */
import { describe, expect, it } from "vitest";
import { MAX_COMMENT_CHARS, commentAnchor, commentLength, commentProblem } from "./comments";

describe("commentProblem", () => {
  it("refuses blank text", () => {
    expect(commentProblem("")).toBe("a comment needs some text");
    expect(commentProblem(" \n\t")).toBe("a comment needs some text");
  });

  it("takes text up to the limit and refuses one character more", () => {
    expect(commentProblem("x".repeat(MAX_COMMENT_CHARS))).toBeNull();
    expect(commentProblem("x".repeat(MAX_COMMENT_CHARS + 1))).toBe(
      "20,001 characters, over the 20,000 a comment can hold",
    );
  });

  it("counts code points, as Python's len does", () => {
    expect(commentLength("😀")).toBe(1);
    expect(commentProblem("😀".repeat(MAX_COMMENT_CHARS))).toBeNull();
  });
});

describe("commentAnchor", () => {
  it("writes file:line@commit when all three are there", () => {
    expect(commentAnchor({ file: "a.py", line: 3, commit: "abc1234" })).toBe("a.py:3@abc1234");
  });

  it("shows nothing for a comment with none or only some of them", () => {
    expect(commentAnchor({})).toBeNull();
    expect(commentAnchor({ file: "a.py", line: 3 })).toBeNull();
  });
});
