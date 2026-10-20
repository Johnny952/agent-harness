/**
 * The Approvals screen's review, its pure half (ADR 219): which list a task
 * belongs to, and a unified diff cut into files with the new side's line
 * numbers, which is what a comment pinned to `file` and `line` points at;
 * and the verdicts among the task's comments (ADR 222).
 */
import type { Task, TaskComment } from "@/lib/api/types";

/** The two lists of the Approvals screen's review. */
export type ReviewLane = "review" | "conflict";

/** Which list a task belongs to, or `null` for one that is in neither. */
export function reviewLane(t: Pick<Task, "status" | "merged">): ReviewLane | null {
  if (t.status === "conflict") return "conflict";
  if (t.status === "done" && t.merged === false) return "review";
  return null;
}

/**
 * The comments that are review verdicts, in the order stored. A comment line
 * carries `verdict` only when the api found it whole (a known kind and a
 * string commit), so this checks what the type says and nothing more.
 */
export function reviewVerdicts(comments: TaskComment[]): TaskComment[] {
  return comments.filter(
    (c) =>
      typeof c.verdict === "object" &&
      c.verdict !== null &&
      typeof c.verdict.kind === "string" &&
      typeof c.verdict.commit === "string",
  );
}

export interface DiffLine {
  text: string;
  /** The line's number in the branch's file, for a context or added line. */
  newLine: number | null;
}

/** One file's lines, numbered on the new side from each `@@ -a,b +c,d @@` header. */
export function parseDiff(diff: string) {
  return diff
    .split(/^(?=diff --git )/m)
    .filter(Boolean)
    .map((chunk) => {
      const raw = chunk.split("\n");
      if (raw[raw.length - 1] === "") raw.pop();
      const name =
        /^\+\+\+ b\/(.*)$/m.exec(chunk)?.[1] ??
        /^diff --git a\/.* b\/(.*)$/m.exec(chunk)?.[1] ??
        "unknown";
      const lines: DiffLine[] = [];
      let next: number | null = null;
      let add = 0;
      let del = 0;
      let inHunk = false;
      for (const l of raw) {
        const hunk = /^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(l);
        if (hunk) {
          inHunk = true;
          next = Number(hunk[1]);
          lines.push({ text: l, newLine: null });
        } else if (!inHunk) {
          continue;
        } else if (l.startsWith("+")) {
          add++;
          lines.push({ text: l, newLine: next });
          if (next !== null) next++;
        } else if (l.startsWith("-")) {
          del++;
          lines.push({ text: l, newLine: null });
        } else if (l.startsWith("\\")) {
          lines.push({ text: l, newLine: null });
        } else {
          lines.push({ text: l, newLine: next });
          if (next !== null) next++;
        }
      }
      return { name, add, del, lines };
    });
}
