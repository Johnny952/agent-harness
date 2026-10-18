/**
 * An epic's children and how far they are, from the task list alone (ADR 139).
 *
 * The children are `split_into`'s ids in the order written: that is the order
 * the epic was split in, and the order the page keeps. Each is looked up in one
 * `/api/tasks` read rather than one detail read per child; an id the list does
 * not hold is a child the page names as missing, never one it drops.
 *
 * Progress is the dispatcher's own closing rule (`dispatcher/tasks.py:is_settled`,
 * ADR 134): a child is settled when it is merged or `dropped`. A dropped child is
 * work nobody will do, so it is counted apart and left out of the total the
 * merged count is measured against: "n of m merged" is over the children still
 * meant to land. A missing child stays in m — the page cannot say it was dropped.
 */
import type { Task } from "@/lib/api/types";

export interface EpicChild {
  id: string;
  /** The row `/api/tasks` served for it, or `null` when the list has none. */
  task: Task | null;
}

export interface EpicProgress {
  merged: number;
  /** The children not dropped: what `merged` is counted out of. */
  total: number;
  dropped: number;
  missing: number;
  /** Every child is merged or dropped: the dispatcher would close the epic. */
  settled: boolean;
}

export function epicChildren(ids: string[], tasks: Task[]): EpicChild[] {
  const byId = new Map(tasks.map((t) => [t.task_id, t]));
  return ids.map((id) => ({ id, task: byId.get(id) ?? null }));
}

export function epicProgress(children: EpicChild[]): EpicProgress {
  let merged = 0;
  let dropped = 0;
  let missing = 0;
  for (const c of children) {
    if (!c.task) missing += 1;
    else if (c.task.merged === true) merged += 1;
    else if (c.task.status === "dropped") dropped += 1;
  }
  const total = children.length - dropped;
  return {
    merged,
    total,
    dropped,
    missing,
    settled: children.length > 0 && missing === 0 && merged + dropped === children.length,
  };
}

/** The ids a child waits on, as served, keeping only the strings (ADR 29). */
export function childDependsOn(task: Task | null): string[] {
  const v: unknown = task?.depends_on;
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}
