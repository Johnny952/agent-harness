/**
 * `lib/backlog.ts` as plain functions: the board's order, the rank a reorder
 * writes and the epic grouping (ADR 138). The screen's own tests are
 * `routes/-backlog.test.tsx`.
 */
import { describe, expect, it } from "vitest";
import type { Task } from "@/lib/api/types";
import {
  MAX_RANK,
  RANK_STEP,
  compareIds,
  groupBacklog,
  rankOf,
  reorderWrites,
  splitInto,
} from "./backlog";

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
    source: null,
    tags: [],
    rank: null,
    debt_id: null,
    epic: null,
    split_into: [],
    merged: false,
    title: null,
    ...over,
  };
}

describe("compareIds", () => {
  it("compares digit runs as numbers", () => {
    expect(["T-100", "T-9", "T-10"].sort(compareIds)).toEqual(["T-9", "T-10", "T-100"]);
  });
});

describe("rankOf", () => {
  it("takes an integer in range and reads anything else as 0", () => {
    expect(rankOf({ rank: 5 })).toBe(5);
    expect(rankOf({ rank: null })).toBe(0);
    expect(rankOf({ rank: -1 })).toBe(0);
    expect(rankOf({ rank: 1.5 })).toBe(0);
    expect(rankOf({ rank: MAX_RANK + 2 })).toBe(0);
  });
});

describe("splitInto", () => {
  it("keeps a list's string entries and drops anything else", () => {
    expect(splitInto({ split_into: ["T-2", 3, "T-4"] })).toEqual(["T-2", "T-4"]);
    expect(splitInto({ split_into: "T-2" })).toEqual([]);
    expect(splitInto({ split_into: null })).toEqual([]);
  });
});

describe("reorderWrites", () => {
  const bucket = [task("T-1", { rank: 10 }), task("T-2", { rank: 20 }), task("T-3", { rank: 30 })];

  it("writes the midpoint between the new neighbours", () => {
    expect(reorderWrites(bucket, "T-1", "T-2", "down")).toEqual([{ taskId: "T-1", rank: 25 }]);
    expect(reorderWrites(bucket, "T-3", "T-1", "up")).toEqual([{ taskId: "T-3", rank: 4 }]);
  });

  it("leaves a step after the last item when it moves to the end", () => {
    expect(reorderWrites(bucket, "T-1", "T-3", "down")).toEqual([
      { taskId: "T-1", rank: 30 + RANK_STEP / 2 },
    ]);
  });

  it("renumbers in the new order and writes only the ranks that change", () => {
    const tight = [
      task("T-1", { rank: 1024 }),
      task("T-2", { rank: 1025 }),
      task("T-3", { rank: 3072 }),
    ];
    expect(reorderWrites(tight, "T-3", "T-2", "up")).toEqual([
      { taskId: "T-3", rank: 2048 },
      { taskId: "T-2", rank: 3072 },
    ]);
  });

  it("writes nothing for an id outside the bucket", () => {
    expect(reorderWrites(bucket, "T-9", "T-1", "up")).toEqual([]);
    expect(reorderWrites(bucket, "T-1", "T-9", "up")).toEqual([]);
  });
});

describe("groupBacklog", () => {
  it("puts an epic's backlog tasks under it and leaves the board's tasks out", () => {
    const groups = groupBacklog([
      task("T-5", { status: "epic", split_into: ["T-6", "T-7"] }),
      task("T-7", { epic: "T-5" }),
      task("T-6", { epic: "T-5", status: "deferred" }),
      task("T-8", { status: "pending" }),
      task("T-9", { epic: "T-404" }),
    ]);
    expect(
      groups.map((g) => [
        g.id,
        g.headInBacklog,
        g.head?.task_id ?? null,
        g.children.map((c) => c.task_id),
      ]),
    ).toEqual([
      ["T-5", false, "T-5", ["T-6", "T-7"]],
      ["T-404", false, null, ["T-9"]],
    ]);
  });
});
