/**
 * `lib/debt.ts` as plain functions: pairing an index entry with the backlog
 * item filed from it, and the state that item is in (ADR 141). The screen's own
 * tests are `routes/-debt.test.tsx`.
 */
import { describe, expect, it } from "vitest";
import type { DebtEntry, Task } from "@/lib/api/types";
import {
  duplicateWarning,
  fallsOn,
  itemState,
  itemsByDebtId,
  looksLikePath,
  matchesQuery,
  pairEntry,
  stateLabel,
  surfaceOverlaps,
  surfacePaths,
  tasksInPlay,
  wherePaths,
} from "./debt";

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
    dropped_reason: null,
    dropped_by: null,
    project: null,
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

  it.each([
    "pending",
    "in_progress",
    "blocked",
    "conflict",
    "awaiting_review",
    "done",
    "epic",
  ] as const)("counts %s as accepted, naming the status", (status) => {
    expect(itemState(task("T-1", { status }))).toEqual({ kind: "accepted", status });
  });

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

function entry(id: string, over: Partial<DebtEntry> = {}): DebtEntry {
  return {
    id,
    task_id: null,
    what: "w",
    where: "x",
    fix: "f",
    card: "none",
    impact: "none",
    resolved: false,
    ...over,
  };
}

describe("matchesQuery", () => {
  const byId = itemsByDebtId([task("T-097", { debt_id: "T-001-D1" })]);

  it("keeps every entry for an empty box", () => {
    expect(matchesQuery(entry("T-001-D1"), "", null)).toBe(true);
  });

  it("reads the four index cells, ignoring case", () => {
    const e = entry("T-001-D1", { what: "Retry loop", where: "`a/b.py`", card: "c-9" });
    for (const q of ["t-001-d1", "retry", "A/B.PY", "c-9"])
      expect(matchesQuery(e, q, null)).toBe(true);
    expect(matchesQuery(e, "nope", null)).toBe(false);
  });

  it("finds an open entry by its item's id and state words once paired", () => {
    const e = entry("T-001-D1");
    const p = pairEntry("T-001-D1", byId);
    expect(matchesQuery(e, "T-097", p)).toBe(true);
    expect(matchesQuery(e, "not accepted", p)).toBe(true);
    expect(matchesQuery(e, "not imported", p)).toBe(false);
  });

  it("finds an entry with no item by 'not imported'", () => {
    const p = pairEntry("T-002-D1", byId);
    expect(matchesQuery(entry("T-002-D1"), "Not Imported", p)).toBe(true);
  });

  it("reads no item words without a pairing", () => {
    expect(matchesQuery(entry("T-001-D1"), "T-097", null)).toBe(false);
    expect(matchesQuery(entry("T-001-D1"), "not imported", null)).toBe(false);
  });
});

describe("looksLikePath and surfacePaths", () => {
  it("tells a repo path from code and commands", () => {
    for (const p of ["front/src/x.ts", "README.md", "docs/**", "tests/test_?.py"])
      expect(looksLikePath(p)).toBe(true);
    for (const p of ["foo()", "-v", "../x", "/abs", "a b", "word", "x=1"])
      expect(looksLikePath(p)).toBe(false);
  });

  it("takes the backticked paths of a Surface, deduplicated and normalised", () => {
    expect(surfacePaths("- `./a/b.ts`\n- `a/b.ts` and `run()`\n- `docs/**`")).toEqual([
      "a/b.ts",
      "docs/**",
    ]);
    expect(surfacePaths(null)).toEqual([]);
    expect(surfacePaths("prose with front/x.ts unbackticked")).toEqual([]);
  });
});

describe("wherePaths", () => {
  it("cuts the symbol after a colon", () => {
    expect(wherePaths("`dispatcher/debt.py:_rows`, and `tests/x.py:test_y`")).toEqual([
      "dispatcher/debt.py",
      "tests/x.py",
    ]);
  });

  it("reads an unwrapped single-span cell as one token", () => {
    expect(wherePaths("dispatcher/debt.py:_unwrap_code")).toEqual(["dispatcher/debt.py"]);
  });

  it("does not read bare prose", () => {
    expect(wherePaths("harness/pool/client.py, the retry branch")).toEqual([]);
    expect(wherePaths("the `retry` branch, and/or `x.py`")).toEqual(["x.py"]);
  });
});

describe("fallsOn", () => {
  it("matches a path under a literal entry by whole segments", () => {
    expect(fallsOn(["docs/plans/x.md"], ["docs/plans"])).toBe(true);
    expect(fallsOn(["docs/plans"], ["docs/plans/x.md"])).toBe(true);
    expect(fallsOn(["docs/plans.md"], ["docs/plan"])).toBe(false);
  });

  it("follows the glob rules", () => {
    expect(fallsOn(["front/src/a.ts"], ["front/src/*.ts"])).toBe(true);
    expect(fallsOn(["front/src/lib/a.ts"], ["front/**/*.ts"])).toBe(true);
    expect(fallsOn(["back/a.ts"], ["front/**"])).toBe(false);
  });

  it("sets the append-only logs aside on both sides", () => {
    expect(fallsOn(["docs/decisions.md"], ["docs/decisions.md"])).toBe(false);
    expect(fallsOn(["docs/implementations/T-1.md"], ["docs/"])).toBe(false);
  });

  it("is false when either side names no path", () => {
    expect(fallsOn([], ["a/b.ts"])).toBe(false);
    expect(fallsOn(["a/b.ts"], [])).toBe(false);
  });
});

describe("tasksInPlay and surfaceOverlaps", () => {
  const desc = (surface: string | null) =>
    `## Goal\n\ng\n\n## Done when\n\nd\n` +
    (surface === null ? "" : `\n## Surface\n\n${surface}\n`);
  const tasks = [
    task("T-12", { status: "in_progress", description: desc("- `front/src/lib/`") }),
    task("T-9", { status: "pending", description: desc("- `front/src/lib/debt.ts`") }),
    task("T-20", { status: "done", merged: true, description: desc("- `front/`") }),
    task("T-21", { status: "proposed", description: desc("- `front/`") }),
    task("T-22", { status: "pending", description: "no headers at all, `front/`" }),
    task("T-23", { status: "pending", description: desc(null) }),
    task("T-24", { status: "pending", description: desc("the whole thing") }),
    task("T-25", { status: "pending", description: null }),
  ];

  it("keeps accepted, unmerged tasks whose Surface names a path, in id order", () => {
    expect(tasksInPlay(tasks).map((p) => p.task.task_id)).toEqual(["T-9", "T-12"]);
  });

  it("points an entry at the tasks its where falls on, never its own item", () => {
    const inPlay = tasksInPlay([
      ...tasks,
      task("T-30", { status: "pending", debt_id: "T-001-D1", description: desc("- `front/`") }),
    ]);
    const e = entry("T-001-D1", { where: "`front/src/lib/debt.ts:matchesQuery`" });
    expect(surfaceOverlaps(e, inPlay).map((t) => t.task_id)).toEqual(["T-9", "T-12"]);
    expect(surfaceOverlaps(entry("T-002-D1", { where: "`back/x.py`" }), inPlay)).toEqual([]);
    expect(surfaceOverlaps(entry("T-003-D1", { where: "the retry branch" }), inPlay)).toEqual([]);
  });
});
