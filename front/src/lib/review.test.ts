/**
 * The review's pure half (ADR 219): the lane a task's status and `merged` put
 * it in, the diff's files with their counts and new-side line numbers, and
 * the verdicts among the comments (ADR 222).
 */
import { describe, expect, it } from "vitest";
import type { Task, TaskComment } from "@/lib/api/types";
import {
  parseDiff,
  requestChangesRefusal,
  resolveConflictRefusal,
  reviewLane,
  reviewOutcomes,
  reviewVerdicts,
} from "./review";

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

describe("requestChangesRefusal", () => {
  it("is null on an awaiting_review or conflict task not on main", () => {
    expect(requestChangesRefusal({ status: "awaiting_review", merged: false })).toBeNull();
    expect(requestChangesRefusal({ status: "conflict", merged: false })).toBeNull();
  });

  it("says why for done, merged and any other status", () => {
    expect(requestChangesRefusal({ status: "conflict", merged: true })).toMatch(/merged/);
    expect(requestChangesRefusal({ status: "done", merged: false })).toMatch(
      /Finished before review/,
    );
    expect(requestChangesRefusal({ status: "awaiting_review", merged: true })).toMatch(/merged/);
    expect(requestChangesRefusal({ status: "pending", merged: false })).toMatch(/pending/);
  });

  it("refuses an awaiting_review task whose merged state is unknown", () => {
    const unknown = { status: "awaiting_review", merged: undefined } as unknown as Pick<
      Task,
      "status" | "merged"
    >;
    expect(requestChangesRefusal(unknown)).toMatch(/Not yet known/);
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

describe("reviewOutcomes", () => {
  const base: TaskComment = { at: "2026-10-21T10:00:00Z", author: "dispatcher", text: "x" };
  it("keeps the dispatcher's comments that open `approve at ` or `resolve conflict,`, in order", () => {
    const merged = { ...base, text: "approve at abc: merged" };
    const refused = { ...base, text: "approve at def: push refused" };
    const resolved = { ...base, text: "resolve conflict, asked by operator: queued" };
    const operator = { ...merged, author: "operator" };
    const operatorResolve = { ...resolved, author: "operator" };
    const other = { ...base, text: "started" };
    const textless = { ...base, text: undefined } as unknown as TaskComment;
    expect(
      reviewOutcomes([merged, operator, operatorResolve, other, textless, resolved, refused]),
    ).toEqual([merged, resolved, refused]);
  });
});

describe("resolveConflictRefusal", () => {
  it("allows only a conflict task known not to be on main", () => {
    expect(resolveConflictRefusal({ status: "conflict", merged: false })).toBeNull();
    expect(resolveConflictRefusal({ status: "conflict", merged: true })).toBe(
      "Already merged into main",
    );
    expect(resolveConflictRefusal({ status: "awaiting_review", merged: false })).toBe(
      "A task in awaiting_review is not in conflict",
    );
    expect(
      resolveConflictRefusal({ status: "conflict", merged: undefined as unknown as boolean }),
    ).toBe("Not yet known whether it is on main");
  });
});
