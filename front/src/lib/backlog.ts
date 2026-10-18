/**
 * The Backlog screen's arithmetic, apart from its markup so it is tested as
 * plain functions: the order, the epic grouping, the filters and the rank a
 * reorder writes (`docs/plans/board-service.md` sections 4 and 8, ADR 138).
 *
 * The order is the dispatcher's own (`dispatcher/tasks.py:order_key`), copied
 * rule for rule rather than re-imagined: the bucket (`high`, `normal`, `low`; a
 * bad or missing priority sorts as `normal`, `bucket_of`), then the rank (a
 * missing or bad one sorts as 0, `rank_of` — so an unranked item comes *first*
 * in its bucket, in id order, which is the order items filed before ADR 130
 * were filed in), then the id in number order (`_id_key`: `T-99` before
 * `T-100`). The screen must not show an order the board will not keep.
 */
import type { BacklogStatus, Task, TaskPriority } from "@/lib/api/types";

export const BACKLOG_STATUSES: readonly BacklogStatus[] = ["proposed", "deferred", "dropped"];
export const PRIORITIES: readonly TaskPriority[] = ["high", "normal", "low"];
/** `dispatcher/tasks.py:RANK_STEP`: the gap a renumbered bucket leaves between items. */
export const RANK_STEP = 1024;
/** `dispatcher/tasks.py:MAX_RANK`: the largest rank a write takes. */
export const MAX_RANK = 2 ** 53 - 1;

export function isBacklog(task: Task): boolean {
  return (BACKLOG_STATUSES as readonly string[]).includes(task.status);
}

/**
 * A task's `split_into` as the string ids it holds. The api serves the field as
 * written (ADR 137), so a hand-edited file can put anything there; anything
 * but a list drops to `[]` and a non-string entry drops out.
 */
export function splitInto(task: Pick<Task, "split_into">): string[] {
  const v = task.split_into;
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

/** An epic, as the dispatcher tells one: it was split, and it is no one's task. */
export function isEpic(task: Task): boolean {
  return splitInto(task).length > 0 && !task.epic;
}

/** `bucket_of`: the served priority when it is one of the three, else `normal`. */
export function bucketOf(task: Pick<Task, "priority">): TaskPriority {
  return (PRIORITIES as readonly string[]).includes(task.priority)
    ? (task.priority as TaskPriority)
    : "normal";
}

/** `rank_of`: an integer in 0..MAX_RANK, else 0. */
export function rankOf(task: Pick<Task, "rank">): number {
  const r = task.rank;
  return typeof r === "number" && Number.isInteger(r) && r >= 0 && r <= MAX_RANK ? r : 0;
}

/** `_id_key`: digit runs compare as numbers and sort before text. */
export function compareIds(a: string, b: string): number {
  const parts = (id: string) => id.split(/(\d+)/).filter((p) => p !== "");
  const pa = parts(a);
  const pb = parts(b);
  for (let i = 0; i < Math.min(pa.length, pb.length); i++) {
    const x = pa[i] ?? "";
    const y = pb[i] ?? "";
    const xd = /^\d+$/.test(x);
    const yd = /^\d+$/.test(y);
    if (xd !== yd) return xd ? -1 : 1;
    if (xd) {
      const d = Number(x) - Number(y);
      if (d !== 0) return d;
    } else if (x !== y) {
      return x < y ? -1 : 1;
    }
  }
  return pa.length - pb.length;
}

/** `order_key`: bucket, rank, id. */
export function compareTasks(a: Task, b: Task): number {
  return (
    PRIORITIES.indexOf(bucketOf(a)) - PRIORITIES.indexOf(bucketOf(b)) ||
    rankOf(a) - rankOf(b) ||
    compareIds(a.task_id, b.task_id)
  );
}

/**
 * One top-level row of the screen and the backlog tasks under it.
 *
 * A backlog item outside any epic is a group of its own with no children. A
 * backlog item whose `epic` names another task goes under that task's id: the
 * head is the epic's own row when the read served it — a backlog item itself,
 * or an epic already on the board (`status: epic`) — and `null` when it did not.
 */
export interface BacklogGroup {
  id: string;
  head: Task | null;
  /** The head is a backlog item, so it takes the actions; else it only names the epic. */
  headInBacklog: boolean;
  children: Task[];
}

export function groupBacklog(tasks: Task[]): BacklogGroup[] {
  const byId = new Map(tasks.map((t) => [t.task_id, t]));
  const groups = new Map<string, BacklogGroup>();
  const group = (id: string) => {
    let g = groups.get(id);
    if (!g) {
      const head = byId.get(id) ?? null;
      g = { id, head, headInBacklog: head !== null && isBacklog(head), children: [] };
      groups.set(id, g);
    }
    return g;
  };
  for (const t of tasks) {
    if (!isBacklog(t)) continue;
    if (t.epic && t.epic !== t.task_id) group(t.epic).children.push(t);
    else group(t.task_id);
  }
  const out = [...groups.values()];
  for (const g of out) g.children.sort(compareTasks);
  // A group sorts by its head, or by its first task when the epic was not served.
  // Every group holds a head or a child, so the `!` never meets an empty group.
  const lead = (g: BacklogGroup) => (g.head ?? g.children[0])!;
  return out.sort((a, b) => compareTasks(lead(a), lead(b)));
}

/** `null` is "any". `source: "none"` matches a task with no source (ADR 129's older files). */
export interface BacklogFilter {
  statuses: readonly string[];
  tag: string | null;
  source: string | null;
}

export const NO_SOURCE = "none";

export function matches(task: Task, f: BacklogFilter): boolean {
  if (!f.statuses.includes(task.status)) return false;
  if (f.tag !== null && !task.tags.includes(f.tag)) return false;
  if (f.source !== null && (task.source ?? NO_SOURCE) !== f.source) return false;
  return true;
}

/**
 * The groups any member of which matches, with only the matching children.
 * The head stays drawn whenever its group shows — the children are its — and
 * `headMatches` says whether it matched on its own account.
 */
export function filterGroups(
  groups: BacklogGroup[],
  f: BacklogFilter,
): (BacklogGroup & { headMatches: boolean })[] {
  const out: (BacklogGroup & { headMatches: boolean })[] = [];
  for (const g of groups) {
    const headMatches = g.headInBacklog && g.head !== null && matches(g.head, f);
    const children = g.children.filter((c) => matches(c, f));
    if (headMatches || children.length > 0) out.push({ ...g, children, headMatches });
  }
  return out;
}

/** A row reorder can move: a top-level backlog item that is not dropped. */
export function isReorderable(g: BacklogGroup): boolean {
  return g.headInBacklog && g.head !== null && g.head.status !== "dropped";
}

export interface RankWrite {
  taskId: string;
  rank: number;
}

/**
 * The `rank` writes that put `taskId` just before (`up`) or just after (`down`)
 * `neighborId`, over `bucket`: every reorderable item of the moving item's
 * bucket in screen order, the ones a filter hides included, so a move past a
 * visible neighbour keeps every hidden item where it was.
 *
 * One write when the ranks either side leave room: the midpoint, strictly
 * between them. Else the bucket is renumbered `RANK_STEP` apart in the new
 * order and only the ranks that change are written — the room the next move
 * needs. Nothing when either id is not in `bucket`.
 */
export function reorderWrites(
  bucket: Task[],
  taskId: string,
  neighborId: string,
  dir: "up" | "down",
): RankWrite[] {
  const self = bucket.find((t) => t.task_id === taskId);
  const rest = bucket.filter((t) => t.task_id !== taskId);
  const at = rest.findIndex((t) => t.task_id === neighborId);
  if (!self || at < 0) return [];
  const slot = dir === "up" ? at : at + 1;
  const before = rest[slot - 1];
  const after = rest[slot];
  const lo = before ? rankOf(before) : -1;
  const hi = after ? rankOf(after) : Math.min(lo + RANK_STEP, MAX_RANK + 1);
  if (hi - lo >= 2) return [{ taskId, rank: lo + Math.floor((hi - lo) / 2) }];
  const order = [...rest.slice(0, slot), self, ...rest.slice(slot)];
  return order
    .map((t, i) => ({ task: t, rank: (i + 1) * RANK_STEP }))
    .filter(({ task, rank }) => task.rank !== rank)
    .map(({ task, rank }) => ({ taskId: task.task_id, rank }));
}
