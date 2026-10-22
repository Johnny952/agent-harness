/**
 * The Approvals screen's review, its pure half (ADR 219): which list a task
 * belongs to, and a unified diff cut into files with the new side's line
 * numbers, which is what a comment pinned to `file` and `line` points at;
 * and the verdicts among the task's comments (ADR 222).
 */
import type { Task, TaskComment } from "@/lib/api/types";

/** The two lists of the Approvals screen's review. */
export type ReviewLane = "review" | "conflict";

/**
 * Which list a task belongs to, or `null` for one that is in neither: an
 * `awaiting_review` task, or an older `done` one, whose work is not on main
 * is under review (ADR 249).
 */
export function reviewLane(t: Pick<Task, "status" | "merged">): ReviewLane | null {
  if (t.status === "conflict") return "conflict";
  if ((t.status === "awaiting_review" || t.status === "done") && t.merged === false) {
    return "review";
  }
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

/**
 * The dispatcher's answers to an approve (ADR 264, ADR 270): comments it wrote
 * whose text opens `approve at `, in the order stored.
 */
export function approveOutcomes(comments: TaskComment[]): TaskComment[] {
  return comments.filter(
    (c) =>
      c.author === "dispatcher" && typeof c.text === "string" && c.text.startsWith("approve at "),
  );
}

/**
 * Why a task cannot be sent back from the console, or `null` when it can (ADR
 * 272, ADR 276, ADR 278): only an `awaiting_review` or `conflict` task whose
 * work is not on main; on a conflict it answers a design conflict the
 * integrador would not settle. The sentence is the button's title. `merged`
 * must be `false`, not just absent: an unknown merged state refuses, as
 * `approveOff` does.
 */
export function requestChangesRefusal(t: Pick<Task, "status" | "merged">): string | null {
  if (t.merged === true) return "Already merged into main";
  if (t.status === "done") {
    return "Finished before review: start it again to rerun it";
  }
  if (t.status !== "awaiting_review" && t.status !== "conflict") {
    return `A task in ${t.status} is not under review`;
  }
  if (t.merged !== false) return "Not yet known whether it is on main";
  return null;
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
