import type { ActionQueue, ActionRecord } from "@/lib/api/types";

/**
 * The action record `id` names, wherever the queue holds it; `undefined` while
 * it is not in the answer. The *New project* screen reads each setup action's
 * outcome this way (ADR 212).
 */
export function findAction(q: ActionQueue | undefined, id: string): ActionRecord | undefined {
  if (!q) return undefined;
  for (const list of [q.pending, q.claimed, q.done]) {
    if (!Array.isArray(list)) continue;
    const found = list.find((a) => a.id === id);
    if (found) return found;
  }
  return undefined;
}
