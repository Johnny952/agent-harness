/**
 * The slots derivation (`docs/decisions.md` ADR 198): open and closed slots, the
 * primary's reserve, what waits and why, and the confirmation's sentence.
 */
import { describe, expect, it } from "vitest";
import type { Account, LastProbe, Task } from "./api/types";
import { deriveSlots, resetText, standingLine, waitingText } from "./slots";

function account(name: string, over: Partial<Account> = {}): Account {
  return {
    name,
    container: `c-${name}`,
    is_primary: false,
    quota_threshold_pct: 80,
    reserve_pct: 20,
    quota_cooldown_seconds: 3600,
    state: "IDLE",
    current_task_id: null,
    rate_limited_at: null,
    last_probe: null,
    ...over,
  };
}

function task(task_id: string, phase: string | null): Task {
  return { task_id, status: "in_progress", phase } as Task;
}

function start(taskId: string, over: Record<string, unknown> = {}) {
  return { kind: "start", task_id: taskId, project: "ia-harness", waiting: null, ...over };
}

const queue = (pending: unknown[] = [], claimed: unknown[] = []) => ({ pending, claimed });

describe("deriveSlots", () => {
  it("makes one open empty slot per idle account, and no more", () => {
    const v = deriveSlots([account("b"), account("a")], [], queue());
    expect(v.slots.map((s) => s.name)).toEqual(["a", "b"]);
    expect(v.slots.every((s) => s.open && s.empty)).toBe(true);
    expect(v.free).toBe(2);
  });

  it("shows the phase, task and project on a busy slot", () => {
    const v = deriveSlots(
      [account("a", { state: "BUSY", current_task_id: "T-1" })],
      [task("T-1", "implementador")],
      queue([], [start("T-1")]),
    );
    expect(v.slots[0]).toMatchObject({
      open: true,
      empty: false,
      taskId: "T-1",
      phase: "implementador",
      project: "ia-harness",
    });
    expect(v.free).toBe(0);
    expect(v.waiting).toEqual([]);
  });

  it("marks the primary with its reserve and orders it last", () => {
    const v = deriveSlots(
      [account("p", { is_primary: true, reserve_pct: 35 }), account("w")],
      [],
      queue(),
    );
    expect(v.slots.map((s) => s.name)).toEqual(["w", "p"]);
    expect(v.slots[1]).toMatchObject({ primary: true, reserve: 35, open: true });
    expect(v.slots[0]!.reserve).toBeNull();
  });

  it("closes a cooling slot with its epoch reset", () => {
    const v = deriveSlots(
      [account("a", { state: "COOLING_DOWN", rate_limited_at: 1000, quota_cooldown_seconds: 500 })],
      [],
      queue(),
    );
    expect(v.slots[0]).toMatchObject({
      open: false,
      closedBy: "refused by the provider",
      reset: { kind: "epoch", at: 1500 },
    });
    expect(resetText(v.slots[0]!.reset!)).toBe("00:25:00 UTC");
    expect(v.free).toBe(0);
  });

  it("closes a parked slot with the probe's clause, verbatim", () => {
    const probe = {
      session_pct: 90,
      session_ceiling_pct: 80,
      session_reset: "resets 3pm",
      week_pct: 10,
      week_reset: "resets Mon",
      week_ceiling: { pct: 80 },
    } as LastProbe;
    const v = deriveSlots(
      [account("a", { state: "PRE_COOLDOWN", last_probe: probe })],
      [],
      queue(),
    );
    expect(v.slots[0]).toMatchObject({
      closedBy: "parked over its ceiling",
      reset: { kind: "clause", text: "resets 3pm" },
    });
  });

  it("gives a parked slot with no probe no reset, and an expired login none either", () => {
    const v = deriveSlots(
      [account("a", { state: "PRE_COOLDOWN" }), account("b", { state: "AUTH_EXPIRED" })],
      [],
      queue(),
    );
    expect(v.slots.map((s) => [s.closedBy, s.reset])).toEqual([
      ["parked over its ceiling", null],
      ["login expired", null],
    ]);
  });

  it("closes an account whose state is unreadable", () => {
    const v = deriveSlots([account("a", { state: null })], [], queue());
    expect(v.slots[0]).toMatchObject({ open: false, closedBy: "state unreadable" });
  });

  it("lists a pending start waiting for an account, with the soonest reset", () => {
    const v = deriveSlots(
      [
        account("a", { state: "COOLING_DOWN", rate_limited_at: 2000, quota_cooldown_seconds: 100 }),
        account("b", { state: "COOLING_DOWN", rate_limited_at: 1000, quota_cooldown_seconds: 100 }),
      ],
      [],
      queue([start("T-2", { waiting: "account" })]),
    );
    expect(v.waiting).toEqual([
      { taskId: "T-2", reason: "account", detail: "soonest reset 00:18:20 UTC" },
    ]);
    expect(waitingText(v.waiting![0]!)).toBe("Waiting for an account — soonest reset 00:18:20 UTC");
  });

  it("says every slot is full when none is closed, and when no reset is comparable", () => {
    const full = deriveSlots(
      [account("a", { state: "BUSY", current_task_id: "T-1" })],
      [task("T-1", "revisor")],
      queue([start("T-2", { waiting: "account" })], [start("T-1")]),
    );
    expect(full.waiting?.[0]?.detail).toBe("every slot is full");
    const parked = deriveSlots(
      [account("a", { state: "AUTH_EXPIRED" })],
      [],
      queue([start("T-2", { waiting: "account" })]),
    );
    expect(parked.waiting?.[0]?.detail).toBe("no reset time to compare");
  });

  it("lists a claimed start with no phase as between phases", () => {
    const v = deriveSlots([account("a")], [task("T-3", null)], queue([], [start("T-3")]));
    expect(v.waiting).toEqual([{ taskId: "T-3", reason: "between", detail: null }]);
    expect(waitingText(v.waiting![0]!)).toBe("Between phases");
  });

  it("lists other pending starts as queued, with the worker's word", () => {
    const v = deriveSlots(
      [account("a")],
      [],
      queue([start("T-4", { waiting: "lane" }), start("T-5")]),
    );
    expect(v.waiting?.map(waitingText)).toEqual(["Queued — lane", "Queued"]);
  });

  it("names the task holding the project for a start waiting for the project", () => {
    const v = deriveSlots(
      [account("a"), account("b", { state: "BUSY", current_task_id: "T-12" })],
      [task("T-12", "implementador")],
      queue(
        [start("T-13", { waiting: "project" })],
        [start("T-11", { project: "other" }), start("T-12")],
      ),
    );
    const w = v.waiting?.find((x) => x.taskId === "T-13");
    expect(w).toEqual({ taskId: "T-13", reason: "project", detail: "behind T-12" });
    expect(waitingText(w!)).toBe("Waiting for the project — behind T-12");
  });

  it("says waiting for the project with no detail when the queue shows no holder", () => {
    const v = deriveSlots(
      [account("a")],
      [],
      queue([start("T-14", { waiting: "project" })], [start("T-15", { project: "other" })]),
    );
    const w = v.waiting?.find((x) => x.taskId === "T-14");
    expect(w).toEqual({ taskId: "T-14", reason: "project", detail: null });
    expect(waitingText(w!)).toBe("Waiting for the project");
    // A record with no project names no holder either, even beside a claimed start.
    const unstamped = deriveSlots(
      [account("a")],
      [],
      queue([start("T-16", { waiting: "project", project: null })], [start("T-17")]),
    );
    expect(unstamped.waiting?.find((x) => x.taskId === "T-16")).toMatchObject({
      reason: "project",
      detail: null,
    });
  });

  it("never reads waiting for the project from a shared project alone", () => {
    // Same project as a claimed start, but the worker did not say "project".
    const v = deriveSlots([account("a")], [], queue([start("T-18")], [start("T-19")]));
    expect(v.waiting?.find((w) => w.taskId === "T-18")?.reason).toBe("queued");
  });

  it("lets a claimed start win over a pending one for the same task", () => {
    const v = deriveSlots(
      [account("a")],
      [task("T-6", null)],
      queue([start("T-6")], [start("T-6")]),
    );
    expect(v.waiting).toHaveLength(1);
    expect(v.waiting?.[0]?.reason).toBe("between");
    expect(v.started).toBe(1);
  });

  it("names a phase no slot carries, and an unknown task row", () => {
    const v = deriveSlots(
      [account("a")],
      [task("T-7", "auditor")],
      queue([], [start("T-7"), start("T-8")]),
    );
    expect(v.waiting?.map(waitingText)).toEqual(["In auditor, on no account", "Unknown"]);
  });

  it("answers null for what waits when the queue is not a queue, and for tasks failed", () => {
    const v = deriveSlots([account("a", { state: "BUSY", current_task_id: "T-1" })], null, null);
    expect(v.waiting).toBeNull();
    expect(v.started).toBeNull();
    expect(v.tasksKnown).toBe(false);
    expect(v.slots[0]?.phase).toBeNull();
  });
});

describe("standingLine", () => {
  const view = (accounts: Account[], q: unknown = queue()) => deriveSlots(accounts, [], q);

  it("says the task would start now", () => {
    expect(standingLine(view([account("a"), account("b", { state: "BUSY" })]))).toBe(
      "2 accounts, 1 free; this task would start now",
    );
  });

  it("names the primary when it is a free slot", () => {
    expect(standingLine(view([account("p", { is_primary: true })]))).toBe(
      "1 account, 1 free, the primary among them; this task would start now",
    );
  });

  it("says the task would wait for an account when none is free", () => {
    expect(standingLine(view([account("a", { state: "BUSY" })]))).toBe(
      "1 account, 0 free; this task would wait for an account",
    );
  });

  it("says the task would queue behind started tasks", () => {
    expect(standingLine(view([account("a")], queue([start("T-1")], [start("T-2")])))).toBe(
      "1 account, 1 free; this task would queue behind 2 started tasks",
    );
    expect(standingLine(view([account("a")], queue([start("T-1")])))).toBe(
      "1 account, 1 free; this task would queue behind 1 started task",
    );
  });

  it("counts only the starts of the task's own project, named of this project", () => {
    const q = queue([start("T-2", { project: "other" })], [start("T-1")]);
    expect(standingLine(view([account("a")], q), "ia-harness")).toBe(
      "1 account, 1 free; this task would queue behind 1 task of this project",
    );
    expect(
      standingLine(view([account("a")], queue([start("T-3")], [start("T-1")])), "ia-harness"),
    ).toBe("1 account, 1 free; this task would queue behind 2 tasks of this project");
    // None of its project in the queue, or no project: the line counts every start.
    expect(standingLine(view([account("a")], q), "third")).toBe(
      "1 account, 1 free; this task would queue behind 2 started tasks",
    );
    expect(standingLine(view([account("a")], q), null)).toBe(
      "1 account, 1 free; this task would queue behind 2 started tasks",
    );
  });

  it("covers no account, a failed accounts read, a failed queue and loading", () => {
    expect(standingLine(view([]))).toBe("No account is configured; no slot can take this task");
    expect(standingLine("accounts-failed")).toBe(
      "/api/accounts did not answer, so the free slots are not known",
    );
    expect(standingLine(view([account("a"), account("b")], null))).toBe(
      "2 accounts, 2 free; /api/actions did not answer, so where this task would stand is not known",
    );
    expect(standingLine("loading")).toBe("Reading the slots…");
  });
});
