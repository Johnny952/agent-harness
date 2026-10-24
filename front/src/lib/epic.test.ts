/** `lib/epic.ts`: an epic's children in their order, and its progress (ADR 139). */
import { describe, expect, it } from "vitest";
import type { Task, UsageGroup, UsageLine } from "@/lib/api/types";
import {
  childDependsOn,
  epicChildren,
  epicProgress,
  lineTokens,
  suggestLimit,
  taskTokens,
} from "./epic";

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

/** One usage line with only the fields the measure reads set. */
function call(tokens: Partial<UsageLine> = {}): UsageLine {
  return {
    task_id: "T-2",
    role: "implementador",
    round: null,
    account: "cuenta1",
    call: "phase",
    session_id: null,
    recorded_at: "2026-10-14T10:00:00Z",
    measured: true,
    raw_keys: [],
    is_error: false,
    num_turns: null,
    duration_ms: null,
    total_cost_usd: null,
    input_tokens: null,
    output_tokens: null,
    cache_read_input_tokens: null,
    cache_creation_input_tokens: null,
    ...tokens,
  };
}

function attempt(calls: UsageLine[]): UsageGroup {
  return {
    task_id: "T-2",
    role: "implementador",
    round: null,
    account: "cuenta1",
    calls,
    call_count: calls.length,
    call_kinds: calls.map((c) => c.call),
    measured_calls: calls.length,
    unmeasured_calls: 0,
    phase_call: calls[0] ?? null,
  };
}

describe("lineTokens and taskTokens", () => {
  it("sums the four token fields, and reads an unmeasured or empty line as none", () => {
    expect(
      lineTokens(
        call({
          input_tokens: 10,
          output_tokens: 20,
          cache_creation_input_tokens: 30,
          cache_read_input_tokens: 40,
          total_cost_usd: 99,
        }),
      ),
    ).toBe(100);
    expect(lineTokens(call({ input_tokens: 5 }))).toBe(5);
    expect(lineTokens(call({ measured: false, input_tokens: 5 }))).toBeNull();
    expect(lineTokens(call())).toBeNull();
  });

  it("adds every measured call of every attempt, and is null when none measured", () => {
    expect(
      taskTokens([
        attempt([call({ input_tokens: 100 }), call()]),
        attempt([call({ output_tokens: 50 })]),
      ]),
    ).toBe(150);
    expect(taskTokens([attempt([call()])])).toBeNull();
    expect(taskTokens([])).toBeNull();
  });
});

describe("suggestLimit", () => {
  const kids = epicChildren(
    ["T-2", "T-3", "T-4", "T-5", "T-6", "T-7"],
    [
      task("T-2", { merged: true }),
      task("T-3", { merged: true }),
      task("T-4", { merged: true }),
      task("T-5", { status: "pending" }),
      task("T-6", { status: "dropped" }),
      // T-7 is not in the list: still to land, as the progress counts it.
    ],
  );

  it("takes the merged children's mean times the children still to land", () => {
    const usage = new Map([
      ["T-2", [attempt([call({ input_tokens: 1000 })])]],
      ["T-3", [attempt([call({ input_tokens: 2001 })])]],
      // T-4's log measured nothing: left out of the mean, not a zero in it.
      ["T-4", [attempt([call()])]],
    ]);
    expect(suggestLimit(kids, usage, null)).toEqual({
      value: 3002,
      perTask: 1501,
      measured: 2,
      left: 2,
    });
  });

  it("adds a paused run's spend", () => {
    const usage = new Map([["T-2", [attempt([call({ input_tokens: 1000 })])]]]);
    expect(suggestLimit(kids, usage, 700)?.value).toBe(2700);
  });

  it("is null with no measured merged child, an unread log, or nothing left", () => {
    expect(suggestLimit(kids, new Map(), null)).toBeNull();
    expect(suggestLimit(kids, new Map([["T-2", null]]), null)).toBeNull();
    const done = epicChildren(["T-2"], [task("T-2", { merged: true })]);
    expect(
      suggestLimit(done, new Map([["T-2", [attempt([call({ input_tokens: 9 })])]]]), null),
    ).toBeNull();
  });
});
