/**
 * The Debt screen's pairing of an index entry with its backlog item, apart from
 * the markup so it is tested as plain functions (`docs/plans/board-service.md`
 * section 8, the Debt row; ADR 141).
 *
 * The pairing is the dispatcher's own: `dispatcher/tasks.py:find_debt_item`
 * matches on `debt_id` alone, under any status, so this does too — the
 * `source` field is not consulted. Where the dispatcher takes the first id of
 * two items that share a `debt_id`, this keeps both: two is a fault in the hive
 * an operator has to see, and picking one would hide the other.
 */
import type { Task } from "@/lib/api/types";
import { BACKLOG_STATUSES, compareIds } from "@/lib/backlog";

/**
 * What became of one entry's item.
 *
 * - `not_imported`: no task carries the entry's id.
 * - `backlog`: still `proposed`, `deferred` or `dropped` — not accepted.
 * - `accepted`: on the board under any other status the dispatcher writes, or
 *   merged; `status` says which, and `merged` wins over the status word.
 * - `unknown`: a status this console does not know, shown verbatim rather
 *   than counted as accepted or not.
 */
export type ItemState =
  | { kind: "not_imported" }
  | { kind: "backlog"; status: string }
  | { kind: "accepted"; status: string }
  | { kind: "unknown"; status: string };

/** The board statuses an accepted item can hold (`TaskStatus` less the backlog's). */
export const ACCEPTED_STATUSES: readonly string[] = [
  "pending",
  "in_progress",
  "blocked",
  "conflict",
  "done",
  "epic",
];

export function itemState(task: Task | undefined): ItemState {
  if (!task) return { kind: "not_imported" };
  if (task.merged) return { kind: "accepted", status: "merged" };
  if ((BACKLOG_STATUSES as readonly string[]).includes(task.status))
    return { kind: "backlog", status: task.status };
  if (ACCEPTED_STATUSES.includes(task.status)) return { kind: "accepted", status: task.status };
  return { kind: "unknown", status: String(task.status) };
}

/**
 * Every task that names a debt id, under that id, in id number order. A task
 * with no `debt_id` is left out; an id held by two tasks keeps both.
 */
export function itemsByDebtId(tasks: Task[]): Map<string, Task[]> {
  const out = new Map<string, Task[]>();
  for (const t of tasks) {
    if (!t.debt_id) continue;
    out.set(t.debt_id, [...(out.get(t.debt_id) ?? []), t]);
  }
  for (const list of out.values()) list.sort((a, b) => compareIds(a.task_id, b.task_id));
  return out;
}

/** One entry's items, each with its state, and whether there is more than one. */
export interface Pairing {
  items: { task: Task; state: ItemState }[];
  duplicate: boolean;
}

export function pairEntry(debtId: string, byId: Map<string, Task[]>): Pairing {
  const tasks = byId.get(debtId) ?? [];
  return {
    items: tasks.map((task) => ({ task, state: itemState(task) })),
    duplicate: tasks.length > 1,
  };
}

/** The words a state renders as. */
export function stateLabel(state: ItemState): string {
  switch (state.kind) {
    case "not_imported":
      return "not imported";
    case "backlog":
      return `${state.status} — not accepted`;
    case "accepted":
      return `accepted — ${state.status}`;
    case "unknown":
      return `status ${state.status} — not one the dispatcher writes`;
  }
}

/** The warning one entry's duplicate items raise, naming each of them. */
export function duplicateWarning(debtId: string, pairing: Pairing): string | null {
  if (!pairing.duplicate) return null;
  const ids = pairing.items.map((i) => i.task.task_id).join(", ");
  return `${debtId} is the debt_id of ${pairing.items.length} tasks (${ids}); an entry is filed once, so one of them is a fault in the hive.`;
}
