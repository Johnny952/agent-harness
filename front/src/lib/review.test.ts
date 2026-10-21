/**
 * The review's pure half (ADR 219): the lane a task's status and `merged` put
 * it in, the diff's files with their counts and new-side line numbers, and
 * the verdicts among the comments (ADR 222).
 */
import { describe, expect, it } from "vitest";
import type { TaskComment } from "@/lib/api/types";
import { approveOutcomes, parseDiff, reviewLane, reviewVerdicts } from "./review";

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
  it("puts an awaiting_review or done task not on main under review, a conflict under conflict, and nothing else", () => {
    expect(reviewLane({ status: "done", merged: false })).toBe("review");
    expect(reviewLane({ status: "done", merged: true })).toBeNull();
    expect(reviewLane({ status: "awaiting_review", merged: false })).toBe("review");
    expect(reviewLane({ status: "awaiting_review", merged: true })).toBeNull();
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

describe("reviewVerdicts", () => {
  const plain: TaskComment = { at: "2026-10-20T10:00:00Z", author: "operator", text: "hi" };
  const verdict: TaskComment = {
    ...plain,
    text: "looks right",
    verdict: { kind: "comment", commit: "a".repeat(40) },
  };
  it("keeps only the comments that carry a whole verdict, in their order", () => {
    const broken = { ...plain, verdict: { kind: "comment" } } as unknown as TaskComment;
    const nulled = { ...plain, verdict: null } as unknown as TaskComment;
    expect(reviewVerdicts([plain, verdict, broken, nulled, verdict])).toEqual([verdict, verdict]);
    expect(reviewVerdicts([])).toEqual([]);
  });
});

describe("approveOutcomes", () => {
  const base: TaskComment = { at: "2026-10-21T10:00:00Z", author: "dispatcher", text: "x" };
  it("keeps the dispatcher's comments that open `approve at `, in order", () => {
    const merged = { ...base, text: "approve at abc: merged" };
    const refused = { ...base, text: "approve at def: push refused" };
    const operator = { ...merged, author: "operator" };
    const other = { ...base, text: "started" };
    const textless = { ...base, text: undefined } as unknown as TaskComment;
    expect(approveOutcomes([merged, operator, other, textless, refused])).toEqual([
      merged,
      refused,
    ]);
  });
});
