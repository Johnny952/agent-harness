/**
 * The review's pure half (ADR NNN): the lane a task's status and `merged` put
 * it in, and the diff's files with their counts and new-side line numbers.
 */
import { describe, expect, it } from "vitest";
import { parseDiff, reviewLane } from "./review";

const DIFF_TEXT = [
  "diff --git a/src/app.py b/src/app.py",
  "index 1111111..2222222 100644",
  "--- a/src/app.py",
  "+++ b/src/app.py",
  "@@ -10,3 +10,4 @@ def main():",
  " keep = 1",
  "-gone = 2",
  "+added = 2",
  "+more = 3",
  " tail = 4",
  "diff --git a/README.md b/README.md",
  "new file mode 100644",
  "--- /dev/null",
  "+++ b/README.md",
  "@@ -0,0 +1 @@",
  "+hello",
  "",
].join("\n");

describe("reviewLane", () => {
  it("puts a done task not on main under review, a conflict under conflict, and nothing else", () => {
    expect(reviewLane({ status: "done", merged: false })).toBe("review");
    expect(reviewLane({ status: "done", merged: true })).toBeNull();
    expect(reviewLane({ status: "conflict", merged: false })).toBe("conflict");
    expect(reviewLane({ status: "in_progress", merged: false })).toBeNull();
  });
});

describe("parseDiff", () => {
  it("counts each file's lines and numbers the new side from the hunk header", () => {
    const [app, readme] = parseDiff(DIFF_TEXT) as [
      ReturnType<typeof parseDiff>[number],
      ReturnType<typeof parseDiff>[number],
    ];
    expect(app.name).toBe("src/app.py");
    expect([app.add, app.del]).toEqual([2, 1]);
    const numbered = app.lines.map((l) => [l.text, l.newLine]);
    expect(numbered).toEqual([
      ["@@ -10,3 +10,4 @@ def main():", null],
      [" keep = 1", 10],
      ["-gone = 2", null],
      ["+added = 2", 11],
      ["+more = 3", 12],
      [" tail = 4", 13],
    ]);
    expect(readme.name).toBe("README.md");
    expect([readme.add, readme.del]).toEqual([1, 0]);
  });
});
