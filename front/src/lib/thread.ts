/**
 * A thread's conversation log, read into what the chat dock draws
 * (`docs/decisions.md` ADR 184, step 4 of `docs/plans/main-thread.md`).
 *
 * The log is `.data/threads/<id>.jsonl` as `GET /api/threads/<id>/events` serves
 * it (ADR 180): one row per line, `{n, event}`. Between the worker's own
 * `turn_start` and `turn_end` lines sit the CLI's stream-json events, unchanged:
 * `system`/`init`, `assistant` with its content blocks, `user` with the tool
 * results, and the turn's `result`. Nothing here is a message store: the history
 * is this function over every row, so a poll that brings ten more rows redraws
 * from the whole log and the mapping has one place to be right.
 *
 * The two views are the dock's switch. *Text* is what was said: the operator's
 * `turn_start` text and the assistant's text blocks. *Commands* adds every
 * `tool_use`, collapsed to the tool and a short argument, with its `tool_result`
 * behind it and a permission denial marked. Both carry the separators, the turn
 * outcomes and the rows that could not be read, because each of those changes
 * how the text around it reads.
 *
 * A row this cannot read is an item of its own and never a thrown error: `event:
 * null` (a line the api could not parse), the worker's `unparsed` line, or an
 * event of the wrong shape. The history goes on after it.
 */

import type { Thread } from "./api/types";

export type ThreadView = "text" | "commands";

/** Where the dock's switch is remembered. Per browser, and a convenience only. */
export const VIEW_KEY = "ia-harness.thread-view";

/** The stored view, or *Text* when there is none or storage cannot be read. */
export function readStoredView(): ThreadView {
  try {
    return window.localStorage.getItem(VIEW_KEY) === "commands" ? "commands" : "text";
  } catch {
    return "text";
  }
}

export function storeView(view: ThreadView): void {
  try {
    window.localStorage.setItem(VIEW_KEY, view);
  } catch {
    // A private window or blocked storage: the switch still works, unremembered.
  }
}

/** `main` first, then the open threads, then the archived; newest first within each. */
export function orderThreads(threads: Thread[]): Thread[] {
  const rank = (t: Thread) => (t.id === "main" ? 0 : t.status === "archived" ? 2 : 1);
  return [...threads].sort(
    (a, b) => rank(a) - rank(b) || String(b.created ?? "").localeCompare(String(a.created ?? "")),
  );
}

/** What a thread runs on for a turn: a change of any of the three is a separator. */
export interface ThreadRuntime {
  account: string;
  model: string;
  effort: string;
}

export type TurnStatus = "running" | "done" | "failed" | "cancelled" | "interrupted" | "unclosed";

export type ThreadItem =
  /**
   * The heavy separator: the turn below did not resume the session above it, so
   * the model does not remember anything above this line.
   */
  | { kind: "session"; key: string; at: string | null }
  /** The light one: the same conversation, on another account, model or effort. */
  | {
      kind: "change";
      key: string;
      from: ThreadRuntime;
      to: ThreadRuntime;
      changed: (keyof ThreadRuntime)[];
    }
  | { kind: "user"; key: string; at: string | null; actor: string | null; text: string | null }
  | { kind: "assistant"; key: string; text: string }
  | {
      kind: "tool";
      key: string;
      name: string;
      summary: string;
      input: unknown;
      /** `null` while no `tool_result` names this call. */
      result: string | null;
      isError: boolean;
      denied: boolean;
      /** The queued action and task when this is a `task_start` that succeeded (ADR 190, 8). */
      dispatch: { actionId: string; taskId: string } | null;
    }
  | {
      kind: "turn";
      key: string;
      status: TurnStatus;
      at: string | null;
      error: string | null;
      costUsd: number | null;
      /** The action that ran this turn, from `turn_start.action` (ADR 173); what *Stop* cancels. */
      action: string | null;
    }
  /** A row that could not be read. `got` is what arrived, for `Malformed`. */
  | { kind: "bad"; key: string; n: number; reason: string; got: unknown; line: string | null };

/** The context and cost the dock's header shows, from the turns' `result` events. */
export interface ThreadMeter {
  /** The last `result`'s input, cache read and cache creation tokens; `null` before one. */
  contextTokens: number | null;
  /** The model's context window when the `result` names one in `modelUsage`. */
  contextWindow: number | null;
  /** Every `result`'s `total_cost_usd`, summed; `null` before one. */
  costUsd: number | null;
  turns: number;
}

type Row = { n: number; event: Record<string, unknown> | null };
/**
 * A stream event or one of its parts: anything, with the keys this module reads
 * named so they read as properties. None of them is promised to be there.
 */
interface Json {
  [key: string]: unknown;
  type?: unknown;
  event?: unknown;
  at?: unknown;
  session_id?: unknown;
  account?: unknown;
  model?: unknown;
  effort?: unknown;
  actor?: unknown;
  text?: unknown;
  status?: unknown;
  error?: unknown;
  line?: unknown;
  subtype?: unknown;
  total_cost_usd?: unknown;
  is_error?: unknown;
  result?: unknown;
  message?: unknown;
  content?: unknown;
  id?: unknown;
  name?: unknown;
  input?: unknown;
  tool_use_id?: unknown;
  permission_denials?: unknown;
  usage?: unknown;
  modelUsage?: unknown;
  input_tokens?: unknown;
  cache_read_input_tokens?: unknown;
  cache_creation_input_tokens?: unknown;
  contextWindow?: unknown;
  action?: unknown;
  task_id?: unknown;
  pending?: unknown;
  claimed?: unknown;
  kind?: unknown;
  params?: unknown;
  target?: unknown;
  thread?: unknown;
  created_at?: unknown;
}

const TURN_STATUSES = new Set(["done", "failed", "cancelled", "interrupted"]);

/** The input keys that say what a call is about, in the order they are tried. */
const SUMMARY_KEYS = ["command", "file_path", "path", "pattern", "url", "query", "description"];
const SUMMARY_MAX = 80;

function isObject(value: unknown): value is Json {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function clip(text: string, max = SUMMARY_MAX): string {
  const line = text.replace(/\s+/g, " ").trim();
  return line.length > max ? `${line.slice(0, max - 1)}…` : line;
}

/** A tool call collapsed to one short argument: its first telling key, else its JSON. */
export function summarizeInput(input: unknown): string {
  if (!isObject(input)) return input === undefined ? "" : clip(JSON.stringify(input) ?? "");
  for (const key of SUMMARY_KEYS) {
    const value = str(input[key]);
    if (value) return clip(value);
  }
  const json = JSON.stringify(input);
  return json === "{}" ? "" : clip(json);
}

/** A `tool_result`'s content, which is a string or a list of blocks, as text. */
function resultText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return content === undefined ? "" : JSON.stringify(content);
  return content
    .map((block) => {
      if (isObject(block) && block.type === "text") return str(block.text) ?? "";
      return isObject(block) && typeof block.type === "string" ? `[${block.type}]` : "";
    })
    .filter(Boolean)
    .join("\n");
}

/**
 * The record a `…__task_start` call created, when its result says so (ADR 187,
 * ADR 190 part 8): not an error, a JSON object with string `id` and `task_id`.
 * Anything else is `null` and the tool draws as a plain line.
 */
function dispatchOf(
  name: string,
  result: { text: string; isError: boolean } | undefined,
): { actionId: string; taskId: string } | null {
  if (!name.endsWith("__task_start") || !result || result.isError) return null;
  try {
    const record: unknown = JSON.parse(result.text);
    if (!isObject(record)) return null;
    const actionId = str(record.id);
    const taskId = str(record.task_id);
    return actionId && taskId ? { actionId, taskId } : null;
  } catch {
    return null;
  }
}

function runtimeOf(event: Json): ThreadRuntime {
  return {
    account: str(event.account) ?? "",
    model: str(event.model) ?? "",
    effort: str(event.effort) ?? "",
  };
}

/**
 * The tool results and the denied calls, by `tool_use_id`, over the whole log: a
 * call's result is a later row, and its denial is in the turn's `result` at the
 * end, so the calls are matched in a second pass.
 */
function indexCalls(rows: Row[]) {
  const results = new Map<string, { text: string; isError: boolean }>();
  const denied = new Set<string>();
  for (const { event } of rows) {
    if (!isObject(event)) continue;
    if (event.type === "user" && isObject(event.message)) {
      const content = event.message.content;
      if (!Array.isArray(content)) continue;
      for (const block of content) {
        if (!isObject(block) || block.type !== "tool_result") continue;
        const id = str(block.tool_use_id);
        if (id)
          results.set(id, { text: resultText(block.content), isError: block.is_error === true });
      }
    }
    if (event.type === "result" && Array.isArray(event.permission_denials)) {
      for (const denial of event.permission_denials) {
        const id = isObject(denial) ? str(denial.tool_use_id) : null;
        if (id) denied.add(id);
      }
    }
  }
  return { results, denied };
}

/** The rows as the dock draws them, in the view the switch is on. */
export function mapThread(rows: Row[], view: ThreadView): ThreadItem[] {
  const { results, denied } = indexCalls(rows);
  const items: ThreadItem[] = [];

  let turns = 0;
  let open: {
    n: number;
    at: string | null;
    error: string | null;
    cost: number | null;
    action: string | null;
  } | null = null;
  let runtime: ThreadRuntime | null = null;
  // The session the turn above ended on, which the next turn resumes or does not.
  let session: string | null = null;

  const close = (status: TurnStatus, key: string, at: string | null, error: string | null) => {
    if (!open) return;
    items.push({
      kind: "turn",
      key,
      status,
      at,
      error: error ?? open.error,
      costUsd: open.cost,
      action: open.action,
    });
    open = null;
  };

  for (const { n, event } of rows) {
    if (event === null || !isObject(event)) {
      items.push({
        kind: "bad",
        key: `${n}`,
        n,
        reason: "not a JSON object",
        got: event,
        line: null,
      });
      continue;
    }

    if (event.type === "ia_harness") {
      const at = str(event.at);
      if (event.event === "turn_start") {
        if (open) close("unclosed", `${n}-unclosed`, null, null);
        const next = runtimeOf(event);
        const resumed = str(event.session_id);
        // Never at the top: there is nothing above the first turn to forget.
        if (turns > 0 && (resumed === null || (session !== null && resumed !== session))) {
          items.push({ kind: "session", key: `${n}-session`, at });
        }
        if (runtime) {
          const changed = (["account", "model", "effort"] as const).filter(
            (k) => runtime![k] !== next[k],
          );
          if (changed.length > 0) {
            items.push({ kind: "change", key: `${n}-change`, from: runtime, to: next, changed });
          }
        }
        runtime = next;
        session = resumed;
        turns += 1;
        open = { n, at, error: null, cost: null, action: str(event.action) };
        items.push({
          kind: "user",
          key: `${n}`,
          at,
          actor: str(event.actor),
          text: str(event.text),
        });
        continue;
      }
      if (event.event === "turn_end") {
        const status = str(event.status);
        const ended = str(event.session_id);
        if (ended) session = ended;
        if (!open) continue;
        close(
          status && TURN_STATUSES.has(status) ? (status as TurnStatus) : "done",
          `${n}`,
          at,
          str(event.error),
        );
        continue;
      }
      if (event.event === "unparsed") {
        items.push({
          kind: "bad",
          key: `${n}`,
          n,
          reason: "a CLI line that is not JSON",
          got: event.line,
          line: str(event.line),
        });
      }
      continue;
    }

    if (event.type === "system") {
      if (event.subtype === "init" && open) {
        // The session this turn runs in, as the CLI reports it. Only a fallback
        // for the turn's own `turn_end`, which names the session it ended on.
        const id = str(event.session_id);
        if (id) session = id;
      }
      continue;
    }

    if (event.type === "result") {
      const id = str(event.session_id);
      if (id) session = id;
      if (open) {
        open.cost = num(event.total_cost_usd);
        if (event.is_error === true) open.error = str(event.result) ?? str(event.subtype);
      }
      continue;
    }

    if (event.type === "assistant") {
      const content = isObject(event.message) ? event.message.content : undefined;
      if (!Array.isArray(content)) {
        items.push({
          kind: "bad",
          key: `${n}`,
          n,
          reason: "an assistant event whose content is not a list",
          got: content,
          line: null,
        });
        continue;
      }
      content.forEach((block, i) => {
        if (!isObject(block)) return;
        if (block.type === "text") {
          const text = str(block.text);
          if (text && text.trim()) items.push({ kind: "assistant", key: `${n}-${i}`, text });
        } else if (block.type === "tool_use" && view === "commands") {
          const id = str(block.id);
          const result = id ? results.get(id) : undefined;
          const name = str(block.name) ?? "tool";
          items.push({
            kind: "tool",
            key: `${n}-${i}`,
            name,
            dispatch: dispatchOf(name, result),
            summary: summarizeInput(block.input),
            input: block.input,
            result: result?.text ?? null,
            isError: result?.isError ?? false,
            denied: id ? denied.has(id) : false,
          });
        }
      });
    }
    // `user` (tool results, matched above) and every other stream event
    // (`stream_event`, `rate_limit_event`, ...) draw nothing of their own.
  }

  if (open) close("running", `${(open as { n: number }).n}-running`, null, null);
  return items;
}

/** A turn the worker has not yet written to the log: a `turn` action in the queue (ADR 190, 3). */
export interface QueuedTurn {
  id: string;
  text: string;
  /** `queued` while pending, `starting` once the worker has claimed it. */
  state: "queued" | "starting";
  /** A cancel for it is already in the queue. */
  stopping: boolean;
}

function recordsOf(list: unknown): Json[] {
  return Array.isArray(list) ? list.filter(isObject) : [];
}

/**
 * The action ids a pending or claimed `cancel` targets: what the dock shows as
 * *stopping…*. Every served field is guarded, since the queue's shape is the
 * api's and not this module's.
 */
export function stoppingIds(queue: unknown): Set<string> {
  const ids = new Set<string>();
  if (!isObject(queue)) return ids;
  for (const record of [...recordsOf(queue.pending), ...recordsOf(queue.claimed)]) {
    if (record.kind !== "cancel" || !isObject(record.params)) continue;
    const target = str(record.params.target);
    if (target) ids.add(target);
  }
  return ids;
}

/**
 * The thread's `turn` actions that are pending or claimed and that no
 * `turn_start` in the log carries yet, oldest first. Once the worker writes
 * `turn_start` the history draws the turn and this drops it.
 */
export function queuedTurns(queue: unknown, threadId: string, rows: Row[]): QueuedTurn[] {
  if (!isObject(queue)) return [];
  const inLog = new Set<string>();
  for (const { event } of rows) {
    if (isObject(event) && event.type === "ia_harness" && event.event === "turn_start") {
      const action = str(event.action);
      if (action) inLog.add(action);
    }
  }
  const stopping = stoppingIds(queue);
  const out: (QueuedTurn & { createdAt: string })[] = [];
  for (const [state, list] of [
    ["queued", queue.pending],
    ["starting", queue.claimed],
  ] as const) {
    for (const record of recordsOf(list)) {
      const id = str(record.id);
      if (!id || record.kind !== "turn" || inLog.has(id) || !isObject(record.params)) continue;
      if (record.params.thread !== threadId) continue;
      out.push({
        id,
        text: str(record.params.text) ?? "",
        state,
        stopping: stopping.has(id),
        createdAt: str(record.created_at) ?? "",
      });
    }
  }
  out.sort((a, b) => a.createdAt.localeCompare(b.createdAt));
  return out.map((t) => ({ id: t.id, text: t.text, state: t.state, stopping: t.stopping }));
}

/**
 * The dock header's numbers. Context is the last `result`'s prompt side; the
 * window comes from its `modelUsage` when the CLI reports one, and the cost is
 * every turn's summed, since each turn is its own CLI run with its own total.
 */
export function threadMeter(rows: Row[]): ThreadMeter {
  const meter: ThreadMeter = { contextTokens: null, contextWindow: null, costUsd: null, turns: 0 };
  for (const { event } of rows) {
    if (!isObject(event)) continue;
    if (event.type === "ia_harness" && event.event === "turn_start") meter.turns += 1;
    if (event.type !== "result") continue;
    const cost = num(event.total_cost_usd);
    if (cost !== null) meter.costUsd = (meter.costUsd ?? 0) + cost;
    if (isObject(event.usage)) {
      const usage = event.usage;
      meter.contextTokens =
        (num(usage.input_tokens) ?? 0) +
        (num(usage.cache_read_input_tokens) ?? 0) +
        (num(usage.cache_creation_input_tokens) ?? 0);
    }
    if (isObject(event.modelUsage)) {
      const windows = Object.values(event.modelUsage)
        .map((m) => (isObject(m) ? num(m.contextWindow) : null))
        .filter((w): w is number => w !== null && w > 0);
      meter.contextWindow = windows.length > 0 ? Math.max(...windows) : null;
    }
  }
  return meter;
}
