/**
 * The execution thread's shaping (`docs/decisions.md` ADR 197): which tasks are
 * in play, the start note, the body's phase sections and gate lines, the
 * segments and where each event goes, and the two views of a segment's events.
 */
import { describe, expect, it } from "vitest";
import type { HookEvent, Task, UsageGroup, UsageLine } from "@/lib/api/types";
import {
  buildSegments,
  executionLabel,
  inPlay,
  mapEvents,
  parseSections,
  startNote,
} from "./execution";

export function task(id: string, over: Partial<Task> = {}): Task {
  return {
    task_id: id,
    status: "pending",
    owner: null,
    depends_on: [],
    heartbeat: null,
    description: null,
    resolved_debt: [],
    phase: null,
    lock_expired: null,
    priority: "normal",
    source: "operator",
    tags: [],
    rank: null,
    debt_id: null,
    project: null,
    epic: null,
    split_into: [],
    merged: false,
    title: `Task ${id}`,
    ...over,
  };
}

function call(session: string, at: string, over: Partial<UsageLine> = {}): UsageLine {
  return {
    call: "phase",
    session_id: session,
    recorded_at: at,
    model: "opus",
    models_used: ["claude-opus"],
    ...over,
  } as UsageLine;
}

function group(
  role: string,
  round: number | null,
  calls: UsageLine[],
  account = "cuenta2",
): UsageGroup {
  return {
    task_id: "T-1",
    role,
    round,
    account,
    calls,
    call_count: calls.length,
    call_kinds: ["phase"],
    measured_calls: calls.length,
    unmeasured_calls: 0,
    phase_call: calls[calls.length - 1] ?? null,
  };
}

function event(
  id: number,
  type: string,
  payload: Record<string, unknown>,
  at = "2026-10-20T10:00:00.000Z",
): HookEvent {
  return { id, source_app: "ia-harness", event_type: type, payload, created_at: at };
}

const QUEUE = (over: Record<string, unknown[]> = {}) => ({
  pending: [],
  claimed: [],
  done: [],
  done_total: 0,
  worker: { heartbeat: null, stale: false, stale_after_seconds: 30 },
  ...over,
});

describe("inPlay", () => {
  it("lists started tasks and leaves out closed and unstarted ones", () => {
    const tasks = [
      task("T-pending"),
      task("T-queued"),
      task("T-progress", { status: "in_progress" }),
      task("T-blocked", { status: "blocked" }),
      task("T-conflict", { status: "conflict" }),
      task("T-done", { status: "done" }),
      task("T-merged", { status: "done", merged: true }),
      task("T-dropped", { status: "dropped" }),
      task("T-proposed", { status: "proposed" }),
      task("T-deferred", { status: "deferred" }),
      task("T-epic", { status: "epic" }),
      task("T-running", { status: "in_progress", phase: "implementador", owner: "cuenta2" }),
    ];
    const queue = QUEUE({ pending: [{ id: "a-1", kind: "start", task_id: "T-queued" }] });
    expect(inPlay(tasks, queue).map((t) => t.task_id)).toEqual([
      "T-running",
      "T-queued",
      "T-progress",
      "T-blocked",
      "T-conflict",
      "T-done",
    ]);
  });

  it("takes no start from a finished record, and survives a queue in another shape", () => {
    const tasks = [task("T-1")];
    expect(inPlay(tasks, QUEUE({ done: [{ id: "a-1", kind: "start", task_id: "T-1" }] }))).toEqual(
      [],
    );
    expect(inPlay(tasks, null)).toEqual([]);
    expect(inPlay(tasks, { pending: "nope" })).toEqual([]);
  });

  it("labels an entry with its id, title and what it does", () => {
    expect(executionLabel(task("T-1", { phase: "revisor" }))).toBe(
      "T-1 · Task T-1 — running revisor",
    );
    expect(executionLabel(task("T-1", { status: "done", title: null }))).toBe("T-1 — done");
  });
});

describe("startNote", () => {
  it("takes the latest start of the task that has a note", () => {
    const queue = QUEUE({
      done: [
        {
          id: "a-20261020T100000000000Z-0001",
          kind: "start",
          task_id: "T-1",
          note: "first",
          actor: "operator",
        },
        { id: "a-20261020T110000000000Z-0002", kind: "start", task_id: "T-1", note: "  " },
        { id: "a-20261020T120000000000Z-0003", kind: "start", task_id: "T-2", note: "other" },
      ],
      claimed: [
        {
          id: "a-20261020T105000000000Z-0004",
          kind: "start",
          task_id: "T-1",
          note: "second",
          actor: "operator",
          created_at: "2026-10-20T10:50:00Z",
        },
      ],
    });
    expect(startNote(queue, "T-1")).toEqual({
      text: "second",
      actor: "operator",
      at: "2026-10-20T10:50:00Z",
    });
    expect(startNote(queue, "T-3")).toBeNull();
    expect(startNote(undefined, "T-1")).toBeNull();
  });
});

const BODY = [
  "Some description.",
  "",
  "## cartografo",
  "",
  "Mapped the module.",
  "",
  "## implementador",
  "",
  "**Summary**",
  "",
  "Built it.",
  "",
  "**Dispatcher gates**",
  "- `tests-run` (blocking) — no test run recorded",
  "- `pointers` (ask) — a pointer is stale",
  "- `contract-docs` (note) — docs touched",
  "",
  "## revisor (round 2)",
  "",
  "    fallback prose",
  "    ## not a heading",
  "",
  "## Notes",
  "",
  "operator text",
].join("\n");

describe("parseSections", () => {
  it("splits the phases, their rounds, their handoffs and their gates", () => {
    const sections = parseSections(BODY);
    expect(sections.map((s) => [s.role, s.round])).toEqual([
      ["cartografo", null],
      ["implementador", null],
      ["revisor", 2],
    ]);
    expect(sections[0]!.handoff).toBe("Mapped the module.");
    expect(sections[1]!.handoff).toBe("**Summary**\n\nBuilt it.");
    expect(sections[1]!.gates).toEqual([
      { gate: "tests-run", level: "blocking", detail: "no test run recorded" },
      { gate: "pointers", level: "warning", detail: "a pointer is stale" },
      { gate: "contract-docs", level: "note", detail: "docs touched" },
    ]);
    // The fallback's indent comes off; the section ends at the next `## ` heading.
    expect(sections[2]!.handoff).toBe("fallback prose\n## not a heading");
    expect(sections[2]!.gates).toEqual([]);
  });

  it("answers nothing for a body that is not a string or has no phase", () => {
    expect(parseSections(null)).toEqual([]);
    expect(parseSections("## Notes\n\ntext")).toEqual([]);
  });
});

describe("buildSegments", () => {
  const sections = parseSections(BODY);
  const groups = [
    group("cartografo", null, [call("s-carto", "2026-10-20T10:10:00.000Z")], "cuenta1"),
    group("implementador", null, [
      call("s-impl", "2026-10-20T11:00:00.000Z", { model: null, models_used: ["a", "b"] }),
    ]),
    group("revisor", 2, [call("s-rev", "2026-10-20T12:00:00.000Z")]),
    // A phase that died before its handoff: placed by its time.
    group("arquitecto", null, [call("s-arq", "2026-10-20T10:30:00.000Z")]),
  ];

  it("orders the sections, takes each one's account and model, and places a group no section took", () => {
    const segments = buildSegments(sections, groups, [], task("T-1"));
    expect(segments.map((s) => [s.role, s.account, s.model])).toEqual([
      ["cartografo", "cuenta1", "opus"],
      ["arquitecto", "cuenta2", "opus"],
      ["implementador", "cuenta2", "a, b"],
      ["revisor", "cuenta2", "opus"],
    ]);
    expect(segments[1]!.section).toBeNull();
  });

  it("appends the running phase on the task's owner", () => {
    const segments = buildSegments([], [], [], task("T-1", { phase: "auditor", owner: "cuenta2" }));
    expect(segments).toMatchObject([
      { role: "auditor", account: "cuenta2", running: true, model: null },
    ]);
  });

  it("attributes events by session, then by time, then to the running phase, else to no phase", () => {
    const events = [
      event(1, "PreToolUse", { session_id: "s-impl" }, "2026-10-20T13:00:00.000Z"),
      event(2, "Notification", { message: "m" }, "2026-10-20T10:05:00.000Z"),
      event(3, "Notification", { message: "late" }, "2026-10-20T13:00:00.000Z"),
    ];
    const done = buildSegments(sections, groups, events, task("T-1"));
    expect(done.find((s) => s.role === "implementador")!.events.map((e) => e.id)).toEqual([1]);
    expect(done.find((s) => s.role === "cartografo")!.events.map((e) => e.id)).toEqual([2]);
    expect(done[done.length - 1]).toMatchObject({ role: "unattributed", phase: false });
    expect(done[done.length - 1]!.events.map((e) => e.id)).toEqual([3]);

    const running = buildSegments(sections, groups, events, task("T-1", { phase: "auditor" }));
    expect(running[running.length - 1]).toMatchObject({ role: "auditor", running: true });
    expect(running[running.length - 1]!.events.map((e) => e.id)).toEqual([3]);
  });
});

describe("mapEvents", () => {
  const events = [
    event(1, "SessionStart", { session_id: "s" }),
    event(2, "UserPromptSubmit", { prompt: "do the task" }),
    event(3, "PreToolUse", {
      tool_name: "Bash",
      tool_input: { command: "ls" },
      tool_use_id: "tu-1",
    }),
    event(4, "PostToolUse", {
      tool_name: "Bash",
      tool_response: { stdout: "a.txt" },
      tool_use_id: "tu-1",
    }),
    event(5, "PreToolUse", { tool_name: "Read", tool_input: { file_path: "/x" } }),
    event(6, "PostToolUse", { tool_name: "Read", tool_response: "contents" }),
    event(7, "Notification", { message: "waiting" }),
    event(8, "Stop", { last_assistant_message: "Done." }),
    event(9, "Stop", {}),
  ];

  it("keeps what was said under Text", () => {
    expect(mapEvents(events, "text").map((i) => i.kind)).toEqual(["prompt", "notice", "assistant"]);
  });

  it("adds the paired tool calls and the markers under Text and commands", () => {
    const items = mapEvents(events, "commands");
    expect(items.map((i) => i.kind)).toEqual([
      "marker",
      "prompt",
      "tool",
      "tool",
      "notice",
      "assistant",
      "marker",
    ]);
    expect(items[2]).toMatchObject({ name: "Bash", summary: "ls", result: "a.txt" });
    expect(items[3]).toMatchObject({ name: "Read", result: "contents" });
  });
});
