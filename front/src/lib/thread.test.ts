/**
 * `mapThread` and `threadMeter` over logs shaped as the worker writes them
 * (`dispatcher/worker.py`): its own `turn_start` / `turn_end` / `unparsed` lines
 * and the CLI's stream-json events between them. `docs/decisions.md` ADR 184 is
 * the mapping these hold the dock to; ADR 180 is the row shape.
 */
import { describe, expect, it } from "vitest";
import type { Thread } from "./api/types";
import { mapThread, orderThreads, summarizeInput, threadMeter, type ThreadItem } from "./thread";

type Event = Record<string, unknown> | null;

function rows(...events: Event[]) {
  return events.map((event, i) => ({ n: i + 1, event }));
}

function start(text: string, over: Record<string, unknown> = {}) {
  return {
    type: "ia_harness",
    event: "turn_start",
    at: "2026-10-19T10:00:00+00:00",
    action: "a-1",
    actor: "operator",
    text,
    account: "cuenta1",
    model: "opus",
    effort: "high",
    session_id: null,
    ...over,
  };
}

function end(status: string, over: Record<string, unknown> = {}) {
  return {
    type: "ia_harness",
    event: "turn_end",
    at: "2026-10-19T10:01:00+00:00",
    action: "a-1",
    status,
    error: null,
    session_id: "s-1",
    ...over,
  };
}

const init = (session: string) => ({ type: "system", subtype: "init", session_id: session });

function assistant(...content: unknown[]) {
  return { type: "assistant", message: { role: "assistant", content } };
}

function toolResult(id: string, content: unknown, isError = false) {
  return {
    type: "user",
    message: {
      role: "user",
      content: [{ type: "tool_result", tool_use_id: id, content, is_error: isError }],
    },
  };
}

function result(over: Record<string, unknown> = {}) {
  return {
    type: "result",
    subtype: "success",
    is_error: false,
    result: "done",
    session_id: "s-1",
    total_cost_usd: 0.25,
    usage: { input_tokens: 10, cache_read_input_tokens: 1000, cache_creation_input_tokens: 200 },
    ...over,
  };
}

/** One turn with a text answer, a Bash call and its result. */
const oneTurn = rows(
  start("what is running?"),
  init("s-1"),
  assistant(
    { type: "text", text: "Let me look." },
    { type: "tool_use", id: "tu-1", name: "Bash", input: { command: "ls -la /tmp" } },
  ),
  toolResult("tu-1", "a\nb"),
  assistant({ type: "text", text: "Nothing is." }),
  result(),
  end("done"),
);

const kinds = (items: ThreadItem[]) => items.map((i) => i.kind);

describe("the text view", () => {
  it("is the turn_start text and the assistant's text blocks", () => {
    const items = mapThread(oneTurn, "text");
    expect(kinds(items)).toEqual(["user", "assistant", "assistant", "turn"]);
    expect(items[0]).toMatchObject({ kind: "user", text: "what is running?", actor: "operator" });
    expect(items[1]).toMatchObject({ text: "Let me look." });
    expect(items[2]).toMatchObject({ text: "Nothing is." });
    expect(items[3]).toMatchObject({ kind: "turn", status: "done", costUsd: 0.25 });
  });
});

describe("the commands view", () => {
  it("adds each tool_use with a short argument and its result", () => {
    const items = mapThread(oneTurn, "commands");
    expect(kinds(items)).toEqual(["user", "assistant", "tool", "assistant", "turn"]);
    expect(items[2]).toMatchObject({
      kind: "tool",
      name: "Bash",
      summary: "ls -la /tmp",
      result: "a\nb",
      isError: false,
      denied: false,
    });
  });

  it("reads a result given as blocks, and marks an error", () => {
    const log = rows(
      start("x"),
      assistant({ type: "tool_use", id: "tu-1", name: "Read", input: { file_path: "/a" } }),
      toolResult("tu-1", [{ type: "text", text: "no such file" }, { type: "image" }], true),
      end("done"),
    );
    expect(mapThread(log, "commands")[1]).toMatchObject({
      summary: "/a",
      result: "no such file\n[image]",
      isError: true,
    });
  });

  it("marks a call the turn's result names in permission_denials", () => {
    const log = rows(
      start("x"),
      assistant({ type: "tool_use", id: "tu-9", name: "Write", input: { file_path: "/etc/x" } }),
      toolResult("tu-9", "permission denied", true),
      result({ permission_denials: [{ tool_name: "Write", tool_use_id: "tu-9", tool_input: {} }] }),
      end("done"),
    );
    expect(mapThread(log, "commands")[1]).toMatchObject({ kind: "tool", denied: true });
  });

  it("leaves a call with no result yet as such", () => {
    const log = rows(
      start("x"),
      assistant({ type: "tool_use", id: "tu-1", name: "Grep", input: { pattern: "TODO" } }),
    );
    expect(mapThread(log, "commands")[1]).toMatchObject({ summary: "TODO", result: null });
  });
});

describe("summarizeInput", () => {
  it("falls back to clipped JSON, and to nothing for no input", () => {
    expect(summarizeInput({ a: 1 })).toBe('{"a":1}');
    expect(summarizeInput({})).toBe("");
    expect(summarizeInput({ command: "x".repeat(200) })).toHaveLength(80);
  });
});

describe("the separators", () => {
  it("draws a light one when account, model or effort changes, naming what changed", () => {
    const log = rows(
      start("one"),
      init("s-1"),
      end("done"),
      start("two", { session_id: "s-1", account: "cuenta2", effort: "low" }),
      end("done"),
    );
    const items = mapThread(log, "text");
    expect(kinds(items)).toEqual(["user", "turn", "change", "user", "turn"]);
    expect(items[2]).toMatchObject({
      kind: "change",
      changed: ["account", "effort"],
      from: { account: "cuenta1", effort: "high" },
      to: { account: "cuenta2", effort: "low" },
    });
  });

  it("draws none when the next turn runs on the same three and resumes", () => {
    const log = rows(start("one"), end("done"), start("two", { session_id: "s-1" }), end("done"));
    expect(kinds(mapThread(log, "text"))).toEqual(["user", "turn", "user", "turn"]);
  });

  it("draws the heavy one for a turn that resumed no session", () => {
    const log = rows(start("one"), end("done"), start("two"), end("done", { session_id: "s-2" }));
    expect(kinds(mapThread(log, "text"))).toEqual(["user", "turn", "session", "user", "turn"]);
  });

  it("draws the heavy one for a turn that resumed another session", () => {
    const log = rows(
      start("one"),
      end("done"),
      start("two", { session_id: "s-other" }),
      end("done"),
    );
    expect(kinds(mapThread(log, "text"))).toContain("session");
  });

  it("reads the session from init and result when the turn_end names none", () => {
    const log = rows(
      start("one"),
      init("s-7"),
      result({ session_id: "s-7" }),
      end("interrupted", { session_id: null }),
      start("two", { session_id: "s-7" }),
    );
    expect(kinds(mapThread(log, "text"))).not.toContain("session");
  });

  it("draws neither above the first turn", () => {
    expect(kinds(mapThread(rows(start("one")), "text"))[0]).toBe("user");
  });
});

describe("the turn outcomes", () => {
  it("shows a turn_start with no turn_end as running", () => {
    const items = mapThread(rows(start("x"), assistant({ type: "text", text: "working" })), "text");
    expect(items.at(-1)).toMatchObject({ kind: "turn", status: "running" });
  });

  it("shows a failed turn with its error", () => {
    const log = rows(start("x"), end("failed", { error: "exit 1: rate limited" }));
    expect(mapThread(log, "text").at(-1)).toMatchObject({
      kind: "turn",
      status: "failed",
      error: "exit 1: rate limited",
    });
  });

  it("shows a cancelled turn as cancelled", () => {
    const log = rows(start("x"), end("cancelled"));
    expect(mapThread(log, "text").at(-1)).toMatchObject({ status: "cancelled" });
  });

  it("carries an error result's text onto the turn", () => {
    const log = rows(
      start("x"),
      result({ is_error: true, subtype: "error_max_turns", result: null }),
      end("failed"),
    );
    expect(mapThread(log, "text").at(-1)).toMatchObject({ error: "error_max_turns" });
  });

  it("closes a turn the next turn_start overtook, and says so", () => {
    const log = rows(start("one"), start("two", { session_id: "s-1" }));
    const items = mapThread(log, "text");
    expect(items.map((i) => (i.kind === "turn" ? i.status : i.kind))).toEqual([
      "user",
      "unclosed",
      "user",
      "running",
    ]);
  });
});

describe("a row it cannot read", () => {
  it("is an item of its own, and the history goes on after it", () => {
    const log = rows(
      start("x"),
      null,
      { type: "ia_harness", event: "unparsed", at: "t", line: "not json {" },
      assistant("not a block"),
      { type: "assistant", message: { content: "a string" } },
      assistant({ type: "text", text: "still here" }),
      end("done"),
    );
    const items = mapThread(log, "commands");
    expect(kinds(items)).toEqual(["user", "bad", "bad", "bad", "assistant", "turn"]);
    expect(items[1]).toMatchObject({ kind: "bad", n: 2, got: null });
    expect(items[2]).toMatchObject({ kind: "bad", n: 3, line: "not json {" });
    expect(items[3]).toMatchObject({ kind: "bad", n: 5, got: "a string" });
    expect(items[4]).toMatchObject({ text: "still here" });
  });

  it("shows in the text view too", () => {
    expect(kinds(mapThread(rows(null), "text"))).toEqual(["bad"]);
  });
});

describe("threadMeter", () => {
  it("is the last result's context and every result's cost", () => {
    const log = rows(
      start("one"),
      result({ total_cost_usd: 0.5 }),
      end("done"),
      start("two", { session_id: "s-1" }),
      result({
        total_cost_usd: 0.25,
        usage: { input_tokens: 5, cache_read_input_tokens: 2000, cache_creation_input_tokens: 0 },
        modelUsage: { "claude-opus": { contextWindow: 200000 } },
      }),
      end("done"),
    );
    expect(threadMeter(log)).toEqual({
      contextTokens: 2005,
      contextWindow: 200000,
      costUsd: 0.75,
      turns: 2,
    });
  });

  it("is empty before a result", () => {
    expect(threadMeter(rows(start("x"), null))).toEqual({
      contextTokens: null,
      contextWindow: null,
      costUsd: null,
      turns: 1,
    });
  });
});

describe("orderThreads", () => {
  const thread = (id: string, over: Partial<Thread> = {}): Thread => ({
    id,
    title: id,
    project: id === "main" ? null : "ia-harness",
    account: "cuenta1",
    model: "opus",
    effort: "high",
    segments: [],
    created: "2026-10-01T00:00:00+00:00",
    status: "open",
    task: null,
    ...over,
  });

  it("puts main first, then the open threads newest first, then the archived", () => {
    const ordered = orderThreads([
      thread("old", { created: "2026-10-01T00:00:00+00:00" }),
      thread("gone", { status: "archived", created: "2026-10-18T00:00:00+00:00" }),
      thread("main"),
      thread("new", { created: "2026-10-10T00:00:00+00:00" }),
    ]);
    expect(ordered.map((t) => t.id)).toEqual(["main", "new", "old", "gone"]);
  });
});
