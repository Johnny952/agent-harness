/**
 * The comment form's checks, before anything is sent (ADR 139). The limit is
 * `dispatcher/tasks.py:MAX_COMMENT_CHARS`, and Python's `len` counts code
 * points, so this does too: `"😀".length` is 2 in JavaScript and 1 there.
 * The board-service still decides; this only spares a write it would refuse.
 */
import type { TaskComment } from "@/lib/api/types";

export const MAX_COMMENT_CHARS = 20_000;

/** The text's length as the dispatcher counts it. */
export function commentLength(text: string): number {
  return [...text].length;
}

/** Why the form will not post `text`, or `null` when it will. */
export function commentProblem(text: string): string | null {
  if (!text.trim()) return "a comment needs some text";
  const n = commentLength(text);
  if (n > MAX_COMMENT_CHARS) {
    return `${n.toLocaleString("en-US")} characters, over the ${MAX_COMMENT_CHARS.toLocaleString("en-US")} a comment can hold`;
  }
  return null;
}

/**
 * The comment's place in the code as `file:line@commit`, or `null` for a
 * comment pinned nowhere. The three come together or not at all, so a comment
 * holding only some of them — a hand-edited file — shows none.
 */
export function commentAnchor(c: Pick<TaskComment, "file" | "line" | "commit">): string | null {
  const { file, line, commit } = c;
  if (typeof file !== "string" || !file) return null;
  if (typeof line !== "number" || !Number.isInteger(line)) return null;
  if (typeof commit !== "string" || !commit) return null;
  return `${file}:${line}@${commit}`;
}
