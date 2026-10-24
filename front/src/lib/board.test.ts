/** `lib/board.ts`: where a card goes, and the epic swimlanes (ADR 140). */
import { describe, expect, it } from "vitest";
import type { Task } from "@/lib/api/types";
import { boardColumns, buildBoard, columnOf, laneOf, phaseNote } from "./board";

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
    dropped_reason: null,
    dropped_by: null,
    proposed_by: null,
    project: null,
    epic: null,
    split_into: [],
    merged: false,
    title: null,
    ...over,
  };
}

const READY = new Set(["T-1"]);

describe("columnOf", () => {
  it("splits pending by the ready order", () => {
    expect(columnOf(task("T-1"), READY)).toBe("ready");
    expect(columnOf(task("T-2"), READY)).toBe("waiting");
  });

  it("puts pending in one unsplit column when there is no ready order", () => {
    expect(columnOf(task("T-1"), null)).toBe("pending");
  });

  it("puts a live in_progress phase in its role's lane", () => {
    const t = task("T-3", { status: "in_progress", phase: "implementador", lock_expired: false });
    expect(columnOf(t, READY)).toBe("phase:implementador");
    expect(phaseNote(t)).toBeNull();
  });

  it.each([
    [{ phase: null }, "between phases"],
    [{ phase: "revisor", lock_expired: true }, "stale revisor"],
    [{ phase: "integrador" }, "phase integrador"],
  ] as const)("keeps an in_progress row with %o on the board", (over, note) => {
    const t = task("T-4", { status: "in_progress", ...over });
    expect(columnOf(t, READY)).toBe("no_phase");
    expect(phaseNote(t)).toBe(note);
  });

  it("places blocked, conflict and merged", () => {
    expect(columnOf(task("T-5", { status: "blocked" }), READY)).toBe("blocked");
    expect(columnOf(task("T-6", { status: "conflict" }), READY)).toBe("conflict");
    expect(columnOf(task("T-7", { status: "done", merged: true }), READY)).toBe("merged");
    // `merged` wins over a status left behind.
    expect(columnOf(task("T-8", { status: "pending", merged: true }), READY)).toBe("merged");
  });

  it("puts awaiting_review and done-not-merged in Awaiting review, merged first", () => {
    expect(columnOf(task("T-9", { status: "awaiting_review" }), READY)).toBe("review");
    expect(columnOf(task("T-9", { status: "done" }), READY)).toBe("review");
    expect(columnOf(task("T-9", { status: "awaiting_review", merged: true }), READY)).toBe(
      "merged",
    );
  });

  it("puts drafted and an unknown status in Other", () => {
    for (const status of ["drafted", "queued"]) {
      expect(columnOf(task("T-9", { status } as unknown as Partial<Task>), READY)).toBe("other");
    }
  });

  it("makes no card of a backlog item or an epic", () => {
    for (const status of ["proposed", "deferred", "dropped", "epic"] as const) {
      expect(columnOf(task("T-10", { status }), READY)).toBeNull();
    }
    expect(columnOf(task("T-11", { split_into: ["T-12"] }), READY)).toBeNull();
  });
});

describe("boardColumns", () => {
  it("is Ready, Waiting, the roles, then the stops and Merged", () => {
    expect(boardColumns(true, false).map((c) => c.title)).toEqual([
      "Ready",
      "Waiting",
      "Cartografo",
      "Arquitecto",
      "Implementador",
      "Revisor",
      "Auditor",
      "No live phase",
      "Awaiting review",
      "Conflict",
      "Blocked",
      "Merged",
    ]);
  });

  it("folds Ready and Waiting with no ready order, and adds Other on demand", () => {
    const keys = boardColumns(false, true).map((c) => c.key);
    expect(keys[0]).toBe("pending");
    expect(keys).not.toContain("ready");
    expect(keys.at(-1)).toBe("other");
  });
});

describe("laneOf", () => {
  it("takes the epic field, else the epic that split into it", () => {
    const parents = new Map([["T-21", "T-20"]]);
    expect(laneOf(task("T-21", { epic: "T-30" }), parents)).toBe("T-30");
    expect(laneOf(task("T-21"), parents)).toBe("T-20");
    expect(laneOf(task("T-22"), parents)).toBeNull();
  });
});

describe("buildBoard", () => {
  const epic = task("T-50", { status: "epic", split_into: ["T-51", "T-52"], title: "the epic" });
  const rows = [
    task("T-53"),
    task("T-52", { epic: "T-50" }),
    task("T-51", { epic: "T-50" }),
    epic,
    task("T-60", { status: "proposed" }),
  ];

  it("gives each epic a lane and the rest a no-epic lane, last", () => {
    const b = buildBoard(rows, ["T-52", "T-51", "T-53"]);
    expect(b.lanes.map((l) => l.id)).toEqual(["T-50", null]);
    expect(b.lanes[0]?.head).toBe(epic);
    expect(b.cards).toBe(3);
  });

  it("keeps the ready order inside Ready", () => {
    const b = buildBoard(rows, ["T-52", "T-51", "T-53"]);
    expect(b.lanes[0]?.cells.get("ready")?.map((t) => t.task_id)).toEqual(["T-52", "T-51"]);
  });

  it("sorts every other column by the dispatcher's order", () => {
    const b = buildBoard(
      [
        task("T-71", { status: "blocked" }),
        task("T-70", { status: "blocked", priority: "low" }),
        task("T-72", { status: "blocked", priority: "high" }),
      ],
      [],
    );
    expect(b.lanes[0]?.cells.get("blocked")?.map((t) => t.task_id)).toEqual([
      "T-72",
      "T-71",
      "T-70",
    ]);
  });

  it("names a lane for an epic the read did not serve", () => {
    const b = buildBoard([task("T-81", { epic: "T-80" })], []);
    expect(b.lanes.map((l) => [l.id, l.head])).toEqual([["T-80", null]]);
  });

  it("keeps an accepted epic's empty lane, and drops it under a filter", () => {
    expect(buildBoard([epic], []).lanes.map((l) => l.id)).toEqual(["T-50"]);
    const filtered = buildBoard(rows, [], (t) => t.task_id === "T-53", true);
    expect(filtered.lanes.map((l) => l.id)).toEqual([null]);
    expect(filtered.shown).toBe(1);
  });

  it("hands the filter each card's column", () => {
    const b = buildBoard(rows, ["T-53"], (_t, column) => column === "ready", true);
    expect(b.shown).toBe(1);
    expect(b.lanes[0]?.cells.get("ready")?.[0]?.task_id).toBe("T-53");
  });
});
