/** `lib/epic.ts`: an epic's children in their order, and its progress (ADR 139). */
import { describe, expect, it } from "vitest";
import type { Task } from "@/lib/api/types";
import { childDependsOn, epicChildren, epicProgress } from "./epic";

function task(id: string, over: Partial<Task> = {}): Task {
  return {
    task_id: id,
    status: "pending",
    owner: null,
    depends_on: [],
    heartbeat: null,
    description: null,
    resolved_debt: [],
    lock_expired: null,
    phase: null,
    priority: "normal",
    source: null,
    tags: [],
    rank: null,
    debt_id: null,
    project: null,
    epic: "T-1",
    split_into: [],
    merged: false,
    title: null,
    ...over,
  };
}

describe("epicChildren", () => {
  it("keeps split_into's order, not the list's, and names a missing id", () => {
    const list = [task("T-2"), task("T-3"), task("T-10")];
    const kids = epicChildren(["T-10", "T-2", "T-9"], list);
    expect(kids.map((k) => k.id)).toEqual(["T-10", "T-2", "T-9"]);
    expect(kids.map((k) => k.task?.task_id ?? null)).toEqual(["T-10", "T-2", null]);
  });
});

describe("epicProgress", () => {
  it("counts merged out of the children not dropped", () => {
    const kids = epicChildren(
      ["T-2", "T-3", "T-4", "T-5"],
      [
        task("T-2", { merged: true, status: "done" }),
        task("T-3", { status: "dropped" }),
        task("T-4", { status: "in_progress" }),
        task("T-5", { status: "deferred" }),
      ],
    );
    expect(epicProgress(kids)).toEqual({
      merged: 1,
      total: 3,
      dropped: 1,
      missing: 0,
      settled: false,
    });
  });

  it("is settled when every child is merged or dropped", () => {
    const kids = epicChildren(
      ["T-2", "T-3"],
      [task("T-2", { merged: true }), task("T-3", { status: "dropped" })],
    );
    expect(epicProgress(kids).settled).toBe(true);
  });

  it("keeps a missing child in the total and never settles over it", () => {
    const p = epicProgress(epicChildren(["T-2", "T-3"], [task("T-2", { merged: true })]));
    expect(p).toEqual({ merged: 1, total: 2, dropped: 0, missing: 1, settled: false });
  });
});

describe("childDependsOn", () => {
  it("keeps the string ids and reads a non-list as none", () => {
    expect(childDependsOn(task("T-2", { depends_on: ["T-1", 3 as unknown as string] }))).toEqual([
      "T-1",
    ]);
    expect(childDependsOn(task("T-2", { depends_on: "T-1" as unknown as string[] }))).toEqual([]);
    expect(childDependsOn(null)).toEqual([]);
  });
});
