/**
 * `lib/debt.ts` as plain functions: pairing an index entry with the backlog
 * item filed from it, and the state that item is in (ADR 141). The screen's own
 * tests are `routes/-debt.test.tsx`.
 */
import { describe, expect, it } from "vitest";
import type { Task } from "@/lib/api/types";
import { duplicateWarning, itemState, itemsByDebtId, pairEntry, stateLabel } from "./debt";

function task(id: string, over: Partial<Task> = {}): Task {
  return {
    task_id: id,
    status: "proposed",
    owner: null,
    depends_on: [],
    heartbeat: null,
    description: null,
    resolved_debt: [],
    lock_expired: null,
    phase: null,
    priority: "normal",
    source: "debt",
    tags: ["debt"],
    rank: null,
    debt_id: null,
    epic: null,
    split_into: [],
    merged: false,
    title: null,
    ...over,
  };
}

describe("itemState", () => {
  it("is not imported when no task carries the id", () => {
    expect(itemState(undefined)).toEqual({ kind: "not_imported" });
  });

  it.each(["proposed", "deferred", "dropped"] as const)("keeps %s in the backlog", (status) => {
    expect(itemState(task("T-1", { status }))).toEqual({ kind: "backlog", status });
  });

  it.each(["pending", "in_progress", "blocked", "conflict", "done", "epic"] as const)(
    "counts %s as accepted, naming the status",
    (status) => {
      expect(itemState(task("T-1", { status }))).toEqual({ kind: "accepted", status });
    },
  );

  it("says merged over the status word", () => {
    expect(itemState(task("T-1", { status: "done", merged: true }))).toEqual({
      kind: "accepted",
      status: "merged",
    });
  });

  it("names a status the dispatcher does not write instead of guessing", () => {
    const t = task("T-1", { status: "drafted" as Task["status"] });
    expect(itemState(t)).toEqual({ kind: "unknown", status: "drafted" });
  });
});

describe("itemsByDebtId and pairEntry", () => {
  it("pairs on debt_id alone, as dispatcher/tasks.py:find_debt_item does", () => {
    const byId = itemsByDebtId([
      task("T-10", { debt_id: "T-001-D1", source: "operator" }),
      task("T-11"),
    ]);
    const p = pairEntry("T-001-D1", byId);
    expect(p.items.map((i) => i.task.task_id)).toEqual(["T-10"]);
    expect(p.duplicate).toBe(false);
    expect(pairEntry("T-001-D2", byId).items).toEqual([]);
  });

  it("keeps both items of a shared debt_id, in id number order, and flags it", () => {
    const byId = itemsByDebtId([
      task("T-100", { debt_id: "T-001-D1" }),
      task("T-99", { debt_id: "T-001-D1", status: "pending" }),
    ]);
    const p = pairEntry("T-001-D1", byId);
    expect(p.items.map((i) => i.task.task_id)).toEqual(["T-99", "T-100"]);
    expect(p.duplicate).toBe(true);
    expect(duplicateWarning("T-001-D1", p)).toContain("2 tasks (T-99, T-100)");
  });

  it("raises no warning for one item", () => {
    const p = pairEntry("T-001-D1", itemsByDebtId([task("T-10", { debt_id: "T-001-D1" })]));
    expect(duplicateWarning("T-001-D1", p)).toBeNull();
  });
});

describe("stateLabel", () => {
  it("words each state", () => {
    expect(stateLabel({ kind: "not_imported" })).toBe("not imported");
    expect(stateLabel({ kind: "backlog", status: "deferred" })).toBe("deferred — not accepted");
    expect(stateLabel({ kind: "accepted", status: "in_progress" })).toBe("accepted — in_progress");
    expect(stateLabel({ kind: "unknown", status: "drafted" })).toContain("drafted");
  });
});
