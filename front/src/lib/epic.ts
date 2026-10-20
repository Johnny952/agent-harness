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
import type { Task, UsageGroup, UsageLine } from "@/lib/api/types";

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

/**
 * One usage line's tokens as ADR 79 counts them, or `null` if it measured none:
 * the four token fields summed unweighted, the measure an epic run's spend is
 * counted in (`dispatcher/context_transfer.py:measured_tokens`, ADR 218).
 */
export function lineTokens(line: UsageLine): number | null {
  const fields = [
    line.input_tokens,
    line.output_tokens,
    line.cache_creation_input_tokens,
    line.cache_read_input_tokens,
  ];
  if (line.measured === false || fields.every((f) => f === null || f === undefined)) return null;
  return fields.reduce<number>((sum, f) => sum + (Number.isInteger(f) ? (f as number) : 0), 0);
}

/** A task's measured tokens over every call its log holds, or `null` when none was measured. */
export function taskTokens(groups: UsageGroup[]): number | null {
  let sum: number | null = null;
  for (const g of groups) {
    for (const c of g.calls) {
      const n = lineTokens(c);
      if (n !== null) sum = (sum ?? 0) + n;
    }
  }
  return sum;
}

export interface LimitSuggestion {
  /** What the limit field is prefilled with. */
  value: number;
  /** The merged children's mean, rounded: what one more child is taken to cost. */
  perTask: number;
  /** How many merged children the mean is over. */
  measured: number;
  /** The children neither merged nor dropped: what the limit is meant to cover. */
  left: number;
}

/**
 * The spending limit prefilled from the epic's own past cycles
 * (`docs/plans/board-service.md` *Per epic*): the mean measured tokens of its
 * merged children, every call of every attempt, times the children still to
 * land, on top of what a paused run has already `spent`. `usage` holds each
 * merged child's log as served; a child whose log was not read, or that measured
 * nothing, is left out of the mean. `null` when no merged child was measured or
 * nothing is left to land: the field then stays blank, never a guess.
 */
export function suggestLimit(
  children: EpicChild[],
  usage: Map<string, UsageGroup[] | null>,
  spent: number | null,
): LimitSuggestion | null {
  const costs: number[] = [];
  for (const c of children) {
    if (c.task?.merged !== true) continue;
    const groups = usage.get(c.id);
    const n = groups ? taskTokens(groups) : null;
    if (n !== null) costs.push(n);
  }
  const progress = epicProgress(children);
  const left = progress.total - progress.merged;
  if (costs.length === 0 || left <= 0) return null;
  const perTask = Math.round(costs.reduce((a, b) => a + b, 0) / costs.length);
  if (perTask < 1) return null;
  return { value: (spent ?? 0) + perTask * left, perTask, measured: costs.length, left };
}
