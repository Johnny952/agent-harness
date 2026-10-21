/**
 * The Board's placement and swimlanes, apart from its markup so they are tested
 * as plain functions (`docs/plans/board-service.md` sections 4 and 8, ADR 140).
 *
 * The board holds board items only: a backlog status (`proposed`, `deferred`,
 * `dropped`) is the Backlog screen's, and an epic row is no one's task, so it
 * heads a swimlane instead of sitting in a column. Every other row is a card,
 * and every card lands in exactly one column — a status this board has no column
 * for goes to Other with its status shown, never nowhere.
 *
 * Placement, first match wins:
 * - `merged: true` is Merged, whatever the status says;
 * - `pending` is Ready when `/api/ready` names it and Waiting when it does not.
 *   With no ready order (the read failed) the two cannot be told apart, so the
 *   task goes to one Pending column the screen marks as unsplit;
 * - `in_progress` with a phase among `ROLES` and a lock that is not expired is
 *   that role's lane. With no phase (reaped, ADR 126), an expired lock (a dead
 *   dispatcher's phase) or a phase no role names, it is "no live phase", still
 *   on the board and saying which of the three it is;
 * - `blocked` and `conflict` are their own columns;
 * - `awaiting_review`, and `done` not merged (a task finished before ADR 240
 *   wrote `awaiting_review`), are Awaiting review (ADR 243);
 * - anything else — `drafted`, a word the api passed through — is Other.
 */
import { ROLES, type Role, type Task } from "@/lib/api/types";
import { compareIds, compareTasks, isBacklog, isEpic, splitInto } from "@/lib/backlog";

export type ColumnKey =
  | "ready"
  | "waiting"
  | "pending"
  | `phase:${Role}`
  | "no_phase"
  | "review"
  | "conflict"
  | "blocked"
  | "merged"
  | "other";

export interface BoardColumn {
  key: ColumnKey;
  title: string;
  /** Set on a role lane, for its badge. */
  role?: Role;
}

/**
 * The statuses the dispatcher writes. One outside these is passed through by
 * the api from a hand-edited file, and the screen warns about it as well as
 * showing it in Other.
 */
export const KNOWN_STATUSES: readonly string[] = [
  "pending",
  "in_progress",
  "blocked",
  "conflict",
  "awaiting_review",
  "done",
  "epic",
  "drafted",
  "proposed",
  "deferred",
  "dropped",
];

function capitalise(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/**
 * The columns, left to right. `readyKnown` false folds Ready and Waiting into
 * one Pending column; `withOther` adds Other only when some card needs it.
 */
export function boardColumns(readyKnown: boolean, withOther: boolean): BoardColumn[] {
  const head: BoardColumn[] = readyKnown
    ? [
        { key: "ready", title: "Ready" },
        { key: "waiting", title: "Waiting" },
      ]
    : [{ key: "pending", title: "Pending, ready unknown" }];
  const roles: BoardColumn[] = ROLES.map((role) => ({
    key: `phase:${role}` as const,
    title: capitalise(role),
    role,
  }));
  const tail: BoardColumn[] = [
    { key: "no_phase", title: "No live phase" },
    { key: "review", title: "Awaiting review" },
    { key: "conflict", title: "Conflict" },
    { key: "blocked", title: "Blocked" },
    { key: "merged", title: "Merged" },
  ];
  return [
    ...head,
    ...roles,
    ...tail,
    ...(withOther ? [{ key: "other" as const, title: "Other" }] : []),
  ];
}

/** Not a card: a backlog item, or an epic, which heads a lane. */
export function isEpicRow(task: Task): boolean {
  return task.status === "epic" || isEpic(task);
}

export function isBoardCard(task: Task): boolean {
  return !isBacklog(task) && !isEpicRow(task);
}

function isRole(phase: string | null): phase is Role {
  return phase !== null && (ROLES as readonly string[]).includes(phase);
}

/**
 * The column a task's card goes in, or `null` when it is not a card. `ready`
 * is the set `/api/ready` named, or `null` when there is no ready order.
 */
export function columnOf(task: Task, ready: ReadonlySet<string> | null): ColumnKey | null {
  if (!isBoardCard(task)) return null;
  if (task.merged === true) return "merged";
  switch (task.status) {
    case "pending":
      if (ready === null) return "pending";
      return ready.has(task.task_id) ? "ready" : "waiting";
    case "in_progress":
      return isRole(task.phase) && task.lock_expired !== true ? `phase:${task.phase}` : "no_phase";
    case "blocked":
      return "blocked";
    case "conflict":
      return "conflict";
    case "awaiting_review":
    case "done":
      return "review";
    default:
      return "other";
  }
}

/**
 * Why an `in_progress` card is in "no live phase", for its chip: between two
 * phases (no phase written), a stale phase (the lock expired under it), or a
 * phase no role names. `null` for any card elsewhere.
 */
export function phaseNote(task: Task): string | null {
  if (task.status !== "in_progress" || task.merged === true) return null;
  if (task.phase === null || task.phase === "") return "between phases";
  if (task.lock_expired === true) return `stale ${task.phase}`;
  if (!isRole(task.phase)) return `phase ${task.phase}`;
  return null;
}

export interface Lane {
  /** The epic's id, or `null` for the no-epic lane. */
  id: string | null;
  /** The epic's own row, or `null` when the read did not serve it (or no epic). */
  head: Task | null;
  /** The cards in this lane by column, each in that column's order. */
  cells: Map<ColumnKey, Task[]>;
  /** Cards in the lane, over every column. */
  count: number;
}

/**
 * The epic a card belongs to: its own `epic` field first, else the epic whose
 * `split_into` lists it (the order an epic was split in, before every child
 * carried the field), else none.
 */
export function laneOf(task: Task, parents: ReadonlyMap<string, string>): string | null {
  if (typeof task.epic === "string" && task.epic !== "") return task.epic;
  return parents.get(task.task_id) ?? null;
}

export interface Board {
  columns: BoardColumn[];
  lanes: Lane[];
  /** Cards kept by `keep`, over every lane. */
  shown: number;
  /** Every card, before `keep`. */
  cards: number;
}

/**
 * The whole board. `ready` is `/api/ready`'s order, or `null` when it failed.
 * `keep` is the screen's filter; with `filtering` set, a lane none of whose
 * cards survive it is left out, and without it every accepted epic keeps its
 * lane, empty or not.
 *
 * Lanes: one per epic that is on the board or that a card names, in the
 * dispatcher's order (`compareTasks`; an epic the read did not serve sorts by
 * its id after the served ones), then the no-epic lane last. Ready keeps the
 * ready order; every other column is in `compareTasks` order.
 */
export function buildBoard(
  tasks: Task[],
  ready: string[] | null,
  keep: (t: Task, column: ColumnKey) => boolean = () => true,
  filtering = false,
): Board {
  const readySet = ready === null ? null : new Set(ready);
  const readyIndex = new Map((ready ?? []).map((id, i) => [id, i]));
  const byId = new Map(tasks.map((t) => [t.task_id, t]));

  const parents = new Map<string, string>();
  for (const t of tasks) {
    if (!isEpicRow(t)) continue;
    for (const child of splitInto(t)) if (!parents.has(child)) parents.set(child, t.task_id);
  }

  const placed: { task: Task; column: ColumnKey; lane: string | null }[] = [];
  for (const t of tasks) {
    const column = columnOf(t, readySet);
    if (column === null) continue;
    placed.push({ task: t, column, lane: laneOf(t, parents) });
  }

  const columns = boardColumns(
    readySet !== null,
    placed.some((p) => p.column === "other"),
  );

  const laneIds = new Set<string>();
  for (const t of tasks) if (isEpicRow(t) && !isBacklog(t)) laneIds.add(t.task_id);
  for (const p of placed) if (p.lane !== null) laneIds.add(p.lane);

  const ordered = [...laneIds].sort((a, b) => {
    const ta = byId.get(a);
    const tb = byId.get(b);
    if (ta && tb) return compareTasks(ta, tb);
    if (ta) return -1;
    if (tb) return 1;
    return compareIds(a, b);
  });

  const lanes: Lane[] = [...ordered, null].map((id) => ({
    id,
    head: id === null ? null : (byId.get(id) ?? null),
    cells: new Map(columns.map((c) => [c.key, [] as Task[]])),
    count: 0,
  }));
  const laneById = new Map(lanes.map((l) => [l.id, l]));

  let shown = 0;
  for (const p of placed) {
    if (!keep(p.task, p.column)) continue;
    const lane = laneById.get(p.lane);
    lane?.cells.get(p.column)?.push(p.task);
    if (lane) lane.count += 1;
    shown += 1;
  }

  for (const lane of lanes) {
    for (const [key, cell] of lane.cells) {
      cell.sort(
        key === "ready"
          ? (a, b) => (readyIndex.get(a.task_id) ?? 0) - (readyIndex.get(b.task_id) ?? 0)
          : compareTasks,
      );
    }
  }

  const visible = lanes.filter((l) =>
    filtering ? l.count > 0 : l.id !== null || l.count > 0 || lanes.length === 1,
  );
  return { columns, lanes: visible, shown, cards: placed.length };
}
