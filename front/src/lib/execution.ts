/**
 * The dock's execution thread (`docs/decisions.md` ADR 197, step 7 of
 * `docs/plans/main-thread.md`): which tasks are in play, and one task's cycle
 * as segments, drawn from reads the console already makes and the task's hook
 * events.
 *
 * Nothing here fetches. A phase runs `claude -p --output-format json` and keeps
 * no stream log, so what a phase did between its start and its handoff is the
 * hook events under its worktree (`GET /api/tasks/<id>/events`); who ran it is
 * its usage group (`GET /api/tasks/<id>/usage`); what it returned is its
 * `## <role>` section of the task body, with the dispatcher's gate lines under
 * it (`dispatcher.py:_run_phase`, `gates.render`). Every served value is
 * narrowed here rather than trusted.
 */
import { ROLES } from "@/lib/api/types";
import type { GateFinding, HookEvent, Role, Task, UsageGroup } from "@/lib/api/types";
import { summarizeInput, type ThreadView } from "@/lib/thread";

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function str(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

function records(queue: unknown, lists: string[]): Record<string, unknown>[] {
  if (!isObject(queue)) return [];
  return lists.flatMap((name) => {
    const list = queue[name];
    return Array.isArray(list) ? list.filter(isObject) : [];
  });
}

/** The task ids with a `start` waiting in the queue or held by the worker. */
function startsQueued(queue: unknown): Set<string> {
  return new Set(
    records(queue, ["pending", "claimed"])
      .filter((r) => r["kind"] === "start")
      .map((r) => str(r["task_id"]))
      .filter((id): id is string => id !== null),
  );
}

/**
 * The tasks the dock lists as executions (ADR 197, 2): started and not closed.
 * A task is in play while it is not merged and it is `in_progress`, runs a
 * phase, is `blocked` or `conflict` mid-cycle, is `done` or `awaiting_review`
 * and waiting for its merge, or has a `start` pending or claimed in the queue. `merged`, `dropped`,
 * a backlog item, an epic, and a plain `pending` no one started are not. A
 * running phase first, then the board's order.
 */
export function inPlay(tasks: Task[], queue: unknown): Task[] {
  const starting = startsQueued(queue);
  const shown = tasks.filter((t) => {
    if (!isObject(t) || typeof t.task_id !== "string" || t.merged === true) return false;
    if (t.status === "epic" || t.status === "dropped") return false;
    if (typeof t.phase === "string" && t.phase !== "") return true;
    if (["in_progress", "blocked", "conflict", "awaiting_review", "done"].includes(t.status))
      return true;
    return starting.has(t.task_id);
  });
  const running = (t: Task) => (typeof t.phase === "string" && t.phase !== "" ? 0 : 1);
  return shown
    .map((t, i) => ({ t, i }))
    .sort((a, b) => running(a.t) - running(b.t) || a.i - b.i)
    .map(({ t }) => t);
}

/** The selector's line for an execution: id, title, and what it is doing. */
export function executionLabel(t: Task): string {
  const doing = t.phase ? `running ${t.phase}` : t.status;
  return `${t.task_id}${t.title ? ` · ${t.title}` : ""} — ${doing}`;
}

/* ── The start note ─────────────────────────────────────────────────────── */

export interface StartNote {
  text: string;
  actor: string | null;
  at: string | null;
}

/**
 * The note of the task's latest `start` that had one, as the worker picks it
 * (`dispatcher/worker.py:_start_note`): the highest action id among the queue's
 * records. The queue serves its pending and claimed records whole and only the
 * newest finished ones, so an older start's note may be past the list: `null`
 * then, and the view says where else it is.
 */
export function startNote(queue: unknown, taskId: string): StartNote | null {
  const starts = records(queue, ["pending", "claimed", "done"])
    .filter((r) => r["kind"] === "start" && r["task_id"] === taskId)
    .filter((r) => typeof r["note"] === "string" && r["note"].trim() !== "")
    .sort((a, b) => String(a["id"] ?? "").localeCompare(String(b["id"] ?? "")));
  const last = starts[starts.length - 1];
  if (!last) return null;
  return { text: String(last["note"]), actor: str(last["actor"]), at: str(last["created_at"]) };
}

/* ── The task body's phase sections ─────────────────────────────────────── */

export interface Section {
  role: Role;
  round: number | null;
  /** The phase's handoff as the dispatcher rendered it, without the gate lines. */
  handoff: string;
  gates: GateFinding[];
}

const ROLE_HEADING = new RegExp(`^## (${ROLES.join("|")})(?: \\(round (\\d+)\\))?\\s*$`);
const GATE_LINE = /^- `([^`]+)` \(([a-z]+)\) — (.*)$/;
/** `gates.py`'s levels to the console's: *ask* wants an answer, so it reads as a warning. */
const GATE_LEVEL: Record<string, GateFinding["level"]> = {
  blocking: "blocking",
  ask: "warning",
  note: "note",
};

/**
 * Unindented when every line is: `handoff.body` indents a phase's fallback
 * prose four spaces so no line of it reads as a heading, which Markdown would
 * otherwise draw as one code block.
 */
function dedent(text: string): string {
  const lines = text.split("\n");
  if (!lines.every((l) => l.trim() === "" || l.startsWith("    "))) return text;
  return lines.map((l) => l.slice(4)).join("\n");
}

/**
 * Each phase's section of the task body, in the order written. A section is a
 * `## <role>` or `## <role> (round N)` heading at column zero, which only
 * `handoff.body` writes, and runs to the next `## ` heading of any kind. The
 * `**Dispatcher gates**` block under it is split off into findings; a gate
 * line in another shape stays in the handoff text.
 */
export function parseSections(body: unknown): Section[] {
  if (typeof body !== "string") return [];
  const out: Section[] = [];
  let current: { role: Role; round: number | null; lines: string[] } | null = null;
  const close = () => {
    if (!current) return;
    const text = current.lines.join("\n");
    const at = text.search(/(^|\n)\*\*Dispatcher gates\*\*\n/);
    const gates: GateFinding[] = [];
    let handoff = text;
    if (at >= 0) {
      handoff = text.slice(0, at);
      const rest: string[] = [];
      for (const line of text.slice(at).split("\n")) {
        const m = GATE_LINE.exec(line);
        const level = m ? GATE_LEVEL[m[2]!] : undefined;
        if (m && level) gates.push({ gate: m[1] as GateFinding["gate"], level, detail: m[3]! });
        else if (line.trim() !== "" && line.trim() !== "**Dispatcher gates**") rest.push(line);
      }
      if (rest.length) handoff = `${handoff}\n\n${rest.join("\n")}`;
    }
    out.push({
      role: current.role,
      round: current.round,
      handoff: dedent(handoff.replace(/^\n+|\s+$/g, "")),
      gates,
    });
    current = null;
  };
  for (const line of body.split("\n")) {
    const m = ROLE_HEADING.exec(line);
    if (m) {
      close();
      current = { role: m[1] as Role, round: m[2] ? Number(m[2]) : null, lines: [] };
    } else if (line.startsWith("## ")) {
      close();
    } else if (current) {
      current.lines.push(line);
    }
  }
  close();
  return out;
}

/* ── Segments ───────────────────────────────────────────────────────────── */

export interface Segment {
  key: string;
  role: string;
  round: number | null;
  account: string | null;
  model: string | null;
  /** The phase running now: no usage group or handoff yet. */
  running: boolean;
  /** `false` for the events no phase could be found for. */
  phase: boolean;
  section: Section | null;
  events: HookEvent[];
}

function time(iso: unknown): number | null {
  if (typeof iso !== "string") return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : t;
}

function groupModel(g: UsageGroup): string | null {
  const asked = str(g.phase_call?.model);
  if (asked) return asked;
  const used = g.phase_call?.models_used;
  return Array.isArray(used) && used.length ? used.join(", ") : null;
}

function groupEnd(g: UsageGroup): number | null {
  const times = (Array.isArray(g.calls) ? g.calls : [])
    .map((c) => time(c?.recorded_at))
    .filter((t): t is number => t !== null);
  return times.length ? Math.max(...times) : null;
}

function groupSessions(g: UsageGroup): Set<string> {
  return new Set(
    (Array.isArray(g.calls) ? g.calls : [])
      .map((c) => str(c?.session_id))
      .filter((s): s is string => s !== null),
  );
}

/**
 * The cycle as segments, oldest first (ADR 197, 4). The body's sections give
 * the order; each takes the unused usage group of the same role and round, for
 * its account and model. A group no section took (a phase that died before its
 * handoff) goes in by its time. The phase running now (`task.phase`) is a last
 * segment on `task.owner`, the account the dispatcher claimed it with.
 *
 * An event goes to the segment whose usage calls carry its `session_id`; failing
 * that, to the first segment whose last call is at or after it; failing that, to
 * the running segment; and failing all three, to one more segment that says no
 * phase was found for it.
 */
export function buildSegments(
  sections: Section[],
  groups: UsageGroup[] | null | undefined,
  events: HookEvent[],
  task: Pick<Task, "phase" | "owner"> | null,
): Segment[] {
  const pool = (Array.isArray(groups) ? groups : []).filter(
    (g) => isObject(g) && typeof g.role === "string",
  );
  const used = new Set<UsageGroup>();
  type Built = Segment & { group: UsageGroup | null };
  const built: Built[] = sections.map((section, i) => {
    const group =
      pool.find(
        (g) => !used.has(g) && g.role === section.role && (g.round ?? null) === section.round,
      ) ?? null;
    if (group) used.add(group);
    return {
      key: `s${i}`,
      role: section.role,
      round: section.round,
      account: group ? str(group.account) : null,
      model: group ? groupModel(group) : null,
      running: false,
      phase: true,
      section,
      events: [],
      group,
    };
  });
  pool
    .filter((g) => !used.has(g))
    .forEach((group, i) => {
      const end = groupEnd(group);
      const segment: Built = {
        key: `g${i}`,
        role: group.role,
        round: group.round ?? null,
        account: str(group.account),
        model: groupModel(group),
        running: false,
        phase: true,
        section: null,
        events: [],
        group,
      };
      const after =
        end === null
          ? -1
          : built.findIndex((s) => {
              const other = s.group ? groupEnd(s.group) : null;
              return other !== null && other > end;
            });
      if (after < 0) built.push(segment);
      else built.splice(after, 0, segment);
    });
  const phase = task && typeof task.phase === "string" && task.phase !== "" ? task.phase : null;
  if (phase) {
    built.push({
      key: "running",
      role: phase,
      round: null,
      account: str(task?.owner),
      model: null,
      running: true,
      phase: true,
      section: null,
      events: [],
      group: null,
    });
  }

  const sessions = built.map((s) => (s.group ? groupSessions(s.group) : new Set<string>()));
  const ends = built.map((s) => (s.group ? groupEnd(s.group) : null));
  const stray: HookEvent[] = [];
  for (const event of events) {
    const session = isObject(event?.payload) ? str(event.payload["session_id"]) : null;
    let at = session ? sessions.findIndex((set) => set.has(session)) : -1;
    if (at < 0) {
      const t = time(event?.created_at);
      if (t !== null) at = ends.findIndex((end) => end !== null && end >= t);
    }
    if (at < 0 && phase) at = built.length - 1;
    if (at < 0) stray.push(event);
    else built[at]!.events.push(event);
  }
  const out: Segment[] = built.map(({ group: _group, ...segment }) => segment);
  if (stray.length) {
    out.push({
      key: "stray",
      role: "unattributed",
      round: null,
      account: null,
      model: null,
      running: false,
      phase: false,
      section: null,
      events: stray,
    });
  }
  return out;
}

/* ── A segment's events as items ────────────────────────────────────────── */

export type ExecutionItem =
  | { kind: "prompt"; key: string; at: string | null; text: string }
  | { kind: "assistant"; key: string; at: string | null; text: string; subagent: boolean }
  | { kind: "notice"; key: string; at: string | null; text: string }
  | {
      kind: "tool";
      key: string;
      at: string | null;
      name: string;
      summary: string;
      result: string | null;
    }
  | { kind: "marker"; key: string; at: string | null; label: string };

function responseText(v: unknown): string | null {
  if (v === undefined || v === null) return null;
  if (typeof v === "string") return v;
  if (isObject(v)) {
    const out = [str(v["stdout"]), str(v["stderr"])].filter(Boolean).join("\n");
    if (out) return out;
  }
  return JSON.stringify(v, null, 2) ?? null;
}

const MARKERS: Record<string, string> = {
  SessionStart: "session start",
  SessionEnd: "session end",
  Stop: "stop",
  SubagentStop: "subagent stop",
};

/**
 * One segment's hook events as the dock draws them, under the thread's switch
 * (ADR 184): *Text* is what was said — the prompt the phase was given, a
 * notification, and the last assistant message a `Stop` carries when the CLI
 * sends one; *Text and commands* adds each tool call, its `PreToolUse` and
 * `PostToolUse` paired by `tool_use_id` (or, without one, the oldest open call
 * of the same tool), and the session's start and end.
 */
export function mapEvents(events: HookEvent[], view: ThreadView): ExecutionItem[] {
  const items: ExecutionItem[] = [];
  const open: { item: Extract<ExecutionItem, { kind: "tool" }>; id: string | null }[] = [];
  for (const event of events) {
    if (!isObject(event) || !isObject(event.payload)) continue;
    const p = event.payload;
    const key = `e${event.id}`;
    const at = str(event.created_at);
    switch (event.event_type) {
      case "UserPromptSubmit": {
        const text = str(p["prompt"]);
        if (text) items.push({ kind: "prompt", key, at, text });
        break;
      }
      case "Notification": {
        const text = str(p["message"]);
        if (text) items.push({ kind: "notice", key, at, text });
        break;
      }
      case "Stop":
      case "SubagentStop": {
        const text = str(p["last_assistant_message"]);
        if (text) {
          items.push({
            kind: "assistant",
            key,
            at,
            text,
            subagent: event.event_type === "SubagentStop",
          });
        } else if (view === "commands") {
          items.push({ kind: "marker", key, at, label: MARKERS[event.event_type]! });
        }
        break;
      }
      case "PreToolUse": {
        if (view !== "commands") break;
        const item = {
          kind: "tool" as const,
          key,
          at,
          name: str(p["tool_name"]) ?? "tool",
          summary: summarizeInput(p["tool_input"]),
          result: null,
        };
        items.push(item);
        open.push({ item, id: str(p["tool_use_id"]) });
        break;
      }
      case "PostToolUse": {
        if (view !== "commands") break;
        const id = str(p["tool_use_id"]);
        const name = str(p["tool_name"]) ?? "tool";
        const at_ = open.findIndex((o) =>
          id ? o.id === id : o.id === null && o.item.name === name,
        );
        const result = responseText(p["tool_response"]);
        if (at_ >= 0) {
          open[at_]!.item.result = result;
          open.splice(at_, 1);
        } else {
          items.push({
            kind: "tool",
            key,
            at,
            name,
            summary: summarizeInput(p["tool_input"]),
            result,
          });
        }
        break;
      }
      default:
        if (view === "commands") {
          items.push({
            kind: "marker",
            key,
            at,
            label: MARKERS[event.event_type] ?? event.event_type,
          });
        }
    }
  }
  return items;
}
