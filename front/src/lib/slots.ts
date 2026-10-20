/**
 * The slots: how many phases can run at once, and why (`docs/decisions.md` ADR
 * 198, step 6 of `docs/plans/projects.md`, the second half of step 7 of
 * `docs/plans/main-thread.md`).
 *
 * One pure derivation over the three reads the console already polls, used by
 * the dock's `SlotsPanel` and by the *Start development* confirmation, so no
 * component decides what a slot is. A slot is an account. Whether it is under
 * its threshold is the dispatcher's judgement, served as `state`, and is read
 * here and never re-decided from `last_probe`. Nothing here guesses a time: a
 * reset is either an epoch the api served plus the cooldown it served, or the
 * CLI's own clause shown verbatim.
 *
 * *Waiting for the project* (ADR NNN) is the worker's word `waiting: "project"`
 * on a pending `start`: one project runs one start at a time, so the start waits
 * while another start of its project is claimed. The holder is named only when
 * the queue shows that claimed start; the reason itself is never re-derived from
 * two records sharing a project.
 */

import type { Account, Task } from "./api/types";
import { formatClock } from "./format";

/** When a closed slot reopens: an epoch in seconds, or the probe's clause as written. */
export type SlotReset = { kind: "epoch"; at: number } | { kind: "clause"; text: string };

export interface Slot {
  name: string;
  primary: boolean;
  /** The primary's session reserve, which a phase there spends from; else `null`. */
  reserve: number | null;
  open: boolean;
  /** Open with no task on it. */
  empty: boolean;
  /** Why a closed slot is closed. `null` on an open slot. */
  closedBy: string | null;
  reset: SlotReset | null;
  taskId: string | null;
  /** The role of the phase on it, from the task row; `null` when not served. */
  phase: string | null;
  /** The project of the task's claimed `start` record; `null` when not recorded. */
  project: string | null;
}

export type WaitingReason = "account" | "project" | "between" | "queued" | "no-account" | "unknown";

export interface Waiting {
  taskId: string;
  reason: WaitingReason;
  /** The words drawn after the reason: the soonest reset, the worker's word, the phase. */
  detail: string | null;
}

export interface SlotsView {
  slots: Slot[];
  free: number;
  /** Whether the task rows were read; without them a slot's phase is not known. */
  tasksKnown: boolean;
  /** `null` when the queue was not a queue object. */
  waiting: Waiting[] | null;
  /** Tasks with a pending or claimed `start`; `null` with the queue. */
  started: number | null;
  /** Those starts, one per task, claimed first; `null` with the queue. */
  starts: StartRecord[] | null;
}

/** A task's `start` record as the queue served it. */
export interface StartRecord {
  taskId: string;
  state: "pending" | "claimed";
  /** The record's `project`; `null` when not recorded. */
  project: string | null;
}

/** A queue record: anything, with the keys read here named so they read as properties. */
interface Json {
  [key: string]: unknown;
  task_id?: unknown;
  kind?: unknown;
  project?: unknown;
  waiting?: unknown;
}

function isObject(value: unknown): value is Json {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function records(list: unknown): Json[] {
  return Array.isArray(list) ? list.filter(isObject) : [];
}

/** The pool's order: workers first, then the primary, then by name. */
function ordered(accounts: Account[]): Account[] {
  return [...accounts].sort(
    (a, b) => Number(a.is_primary) - Number(b.is_primary) || a.name.localeCompare(b.name),
  );
}

/** The windows the last probe found at or over their ceiling, by the pool screen's `>=` test. */
function parkedReset(account: Account): SlotReset | null {
  const probe = account.last_probe;
  if (!probe) return null;
  const clauses: string[] = [];
  if (probe.session_pct >= probe.session_ceiling_pct && probe.session_reset) {
    clauses.push(probe.session_reset);
  }
  if (probe.week_pct >= probe.week_ceiling.pct && probe.week_reset) {
    clauses.push(probe.week_reset);
  }
  return clauses.length > 0 ? { kind: "clause", text: clauses.join("; ") } : null;
}

function closedReason(account: Account): { by: string; reset: SlotReset | null } {
  switch (account.state) {
    case "COOLING_DOWN":
      return {
        by: "refused by the provider",
        reset:
          account.rate_limited_at === null
            ? null
            : { kind: "epoch", at: account.rate_limited_at + account.quota_cooldown_seconds },
      };
    case "PRE_COOLDOWN":
      return { by: "parked over its ceiling", reset: parkedReset(account) };
    case "AUTH_EXPIRED":
      return { by: "login expired", reset: null };
    default:
      return { by: "state unreadable", reset: null };
  }
}

/** A reset as the slot line draws it: the UTC clock of an epoch, or the clause as written. */
export function resetText(reset: SlotReset): string {
  return reset.kind === "epoch"
    ? `${formatClock(new Date(reset.at * 1000).toISOString())} UTC`
    : reset.text;
}

interface Start extends StartRecord {
  waiting: string | null;
}

/** The `start` records, one per task, claimed over pending (ADR 160), claimed first. */
function startsOf(queue: Json): Start[] {
  const byTask = new Map<string, Start>();
  for (const state of ["claimed", "pending"] as const) {
    for (const r of records(queue[state])) {
      const taskId = str(r.task_id);
      if (r.kind !== "start" || taskId === null || byTask.has(taskId)) continue;
      byTask.set(taskId, { taskId, state, project: str(r.project), waiting: str(r.waiting) });
    }
  }
  return [...byTask.values()];
}

/**
 * The slots, what waits and what is started, from `GET /api/accounts`,
 * `GET /api/tasks` (`null` when that read failed) and `GET /api/actions`
 * (anything: the queue's shape is the api's, so it is guarded).
 */
export function deriveSlots(accounts: Account[], tasks: Task[] | null, queue: unknown): SlotsView {
  const starts = isObject(queue) ? startsOf(queue) : null;
  const phaseOf = new Map((tasks ?? []).map((t) => [t.task_id, t.phase]));
  const claimedProject = new Map(
    (starts ?? []).filter((s) => s.state === "claimed").map((s) => [s.taskId, s.project]),
  );

  const slots: Slot[] = ordered(accounts).map((a) => {
    const open = a.state === "IDLE" || a.state === "BUSY";
    const closed = open ? null : closedReason(a);
    // IDLE is empty and BUSY carries a task, whichever the lock file says (ADR 198, 2).
    const taskId = a.state === "BUSY" ? a.current_task_id : null;
    return {
      name: a.name,
      primary: a.is_primary,
      reserve: a.is_primary ? a.reserve_pct : null,
      open,
      empty: a.state === "IDLE",
      closedBy: closed?.by ?? null,
      reset: closed?.reset ?? null,
      taskId,
      phase: taskId === null ? null : (phaseOf.get(taskId) ?? null),
      project: taskId === null ? null : (claimedProject.get(taskId) ?? null),
      // (a BUSY slot with no task keeps all three null: each draws its own Absent mark)
    };
  });
  const free = slots.filter((s) => s.empty).length;
  const tasksKnown = tasks !== null;
  if (starts === null)
    return { slots, free, tasksKnown, waiting: null, started: null, starts: null };

  const onSlot = new Set(slots.map((s) => s.taskId).filter((id): id is string => id !== null));
  const resets = slots
    .map((s) => s.reset)
    .filter((r): r is { kind: "epoch"; at: number } => r?.kind === "epoch")
    .map((r) => r.at);
  const soonest = resets.length > 0 ? Math.min(...resets) : null;
  const closedAny = slots.some((s) => !s.open);

  const waiting: Waiting[] = [];
  for (const s of starts) {
    if (onSlot.has(s.taskId)) continue;
    if (s.state === "pending") {
      if (s.waiting === "account") {
        waiting.push({
          taskId: s.taskId,
          reason: "account",
          detail:
            soonest !== null
              ? `soonest reset ${resetText({ kind: "epoch", at: soonest })}`
              : closedAny
                ? "no reset time to compare"
                : "every slot is full",
        });
      } else if (s.waiting === "project") {
        // The worker's word; the holder is named only when the queue shows it.
        const holder =
          s.project === null
            ? undefined
            : starts.find(
                (o) => o.state === "claimed" && o.project === s.project && o.taskId !== s.taskId,
              );
        waiting.push({
          taskId: s.taskId,
          reason: "project",
          detail: holder ? `behind ${holder.taskId}` : null,
        });
      } else {
        waiting.push({ taskId: s.taskId, reason: "queued", detail: s.waiting });
      }
      continue;
    }
    const phase = phaseOf.get(s.taskId);
    if (phase === undefined) {
      waiting.push({ taskId: s.taskId, reason: "unknown", detail: null });
    } else if (phase === null) {
      waiting.push({ taskId: s.taskId, reason: "between", detail: null });
    } else {
      waiting.push({ taskId: s.taskId, reason: "no-account", detail: phase });
    }
  }
  return {
    slots,
    free,
    tasksKnown,
    waiting,
    started: starts.length,
    starts: starts.map(({ taskId, state, project }) => ({ taskId, state, project })),
  };
}

/** The reason as the waiting list words it. */
export function waitingText(w: Waiting): string {
  switch (w.reason) {
    case "account":
      return `Waiting for an account — ${w.detail}`;
    case "project":
      return w.detail ? `Waiting for the project — ${w.detail}` : "Waiting for the project";
    case "between":
      return "Between phases";
    case "queued":
      return w.detail ? `Queued — ${w.detail}` : "Queued";
    case "no-account":
      return `In ${w.detail}, on no account`;
    default:
      return "Unknown";
  }
}

/**
 * The confirmation's sentence: how many slots are free now and where a new start
 * would stand. The caller says which arm of the accounts read it is in; it never
 * names a time. Given the task's `project`, a start already in the queue for that
 * project is what this one would queue behind, named "of this project" (one
 * project runs one start at a time, ADR NNN); with no project, or none of its
 * starts in the queue, the line counts every start as before.
 */
export function standingLine(
  view: SlotsView | "loading" | "accounts-failed",
  project: string | null = null,
): string {
  if (view === "loading") return "Reading the slots…";
  if (view === "accounts-failed")
    return "/api/accounts did not answer, so the free slots are not known";
  const n = view.slots.length;
  if (n === 0) return "No account is configured; no slot can take this task";
  const primaryFree = view.slots.some((s) => s.empty && s.primary);
  const head = `${n} account${n === 1 ? "" : "s"}, ${view.free} free${primaryFree ? ", the primary among them" : ""}`;
  if (view.started === null) {
    return `${head}; /api/actions did not answer, so where this task would stand is not known`;
  }
  const ofProject =
    project === null ? 0 : (view.starts ?? []).filter((s) => s.project === project).length;
  if (ofProject > 0) {
    return `${head}; this task would queue behind ${ofProject} task${ofProject === 1 ? "" : "s"} of this project`;
  }
  if (view.started > 0) {
    return `${head}; this task would queue behind ${view.started} started task${view.started === 1 ? "" : "s"}`;
  }
  return view.free > 0
    ? `${head}; this task would start now`
    : `${head}; this task would wait for an account`;
}
