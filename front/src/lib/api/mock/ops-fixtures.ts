import type { Role } from "../types";
import type {
  ModelOption,
  RoleModelConfig,
  SessionInfo,
  SessionLogLine,
  LogLevel,
} from "../ops-types";

const iso = (secondsAgo: number) => new Date(Date.now() - secondsAgo * 1000).toISOString();

export function mockSessions(): SessionInfo[] {
  return [
    {
      container: "agent-cuenta1",
      account: "cuenta1",
      task_id: "T-011",
      role: "revisor",
      live: true,
    },
    {
      container: "agent-cuenta3",
      account: "cuenta3",
      task_id: "T-012",
      role: "implementador",
      live: true,
    },
    {
      container: "agent-cuenta4",
      account: "cuenta4",
      task_id: "T-013",
      role: "cartografo",
      live: true,
    },
    {
      container: "agent-cuenta5",
      account: "cuenta5",
      task_id: "T-014",
      role: "auditor",
      live: true,
    },
    { container: "agent-cuenta2", account: "cuenta2", task_id: null, role: null, live: false },
  ];
}

const LINES: Record<string, [LogLevel, string][]> = {
  revisor: [
    ["info", "reading diff for round 2 (14 files)"],
    ["debug", "tool: read_file src/pool/lock.ts"],
    ["info", "checking contract-docs gate against docs/contracts/pool.md"],
    ["warn", "test coverage for release path missing edge: stale heartbeat"],
    ["info", "drafting review notes"],
  ],
  implementador: [
    ["info", "applying envelope from arquitecto (3.1 KB)"],
    ["debug", "tool: edit_file src/queue/actions.ts"],
    ["info", "running bun test queue/ — 42 passed"],
    ["error", "tsc: src/queue/actions.ts:88 Type 'null' is not assignable to 'string'"],
    ["info", "retrying with narrowed type"],
  ],
  cartografo: [
    ["info", "mapping module graph under src/learnings"],
    ["debug", "tool: glob src/learnings/**/*.ts (23 files)"],
    ["info", "writing pointers section"],
  ],
  auditor: [
    ["info", "auditing debt declarations for T-014"],
    ["warn", "D-104 has no card reference"],
    ["debug", "tool: grep TODO src/"],
  ],
};

let seq = 9000;
export function mockSessionLines(count = 60): SessionLogLine[] {
  const out: SessionLogLine[] = [];
  const live = mockSessions().filter((s) => s.live);
  for (let i = count; i > 0; i--) out.push(makeLine(live[i % live.length]!, i * 4));
  return out;
}
export function nextSessionLine(): SessionLogLine {
  const live = mockSessions().filter((s) => s.live);
  return makeLine(live[Math.floor(Math.random() * live.length)]!, 0);
}
function makeLine(s: SessionInfo, ago: number): SessionLogLine {
  const pool = LINES[s.role ?? "cartografo"]!;
  const [level, text] = pool[Math.floor(Math.random() * pool.length)]!;
  return {
    seq: ++seq,
    container: s.container,
    task_id: s.task_id,
    role: s.role,
    level,
    ts: iso(ago),
    text,
  };
}

// The Claude IDs are the ones config.yaml passes as `--model`
// (docs/plans/models-5-5.md, Decisions 5). The non-Claude entries stay only
// while docs/plans/omniroute.md is undecided.
export const MODEL_OPTIONS: ModelOption[] = [
  { id: "claude-opus-5-5", label: "Claude Opus 5.5", context_k: 200 },
  { id: "claude-sonnet-5-5", label: "Claude Sonnet 5.5", context_k: 200 },
  { id: "claude-haiku-5-5", label: "Claude Haiku 5.5", context_k: 200 },
  { id: "gpt-5", label: "GPT-5", context_k: 400 },
  { id: "gpt-5-mini", label: "GPT-5 mini", context_k: 400 },
  { id: "gemini-2.5-pro", label: "Gemini 2.5 Pro", context_k: 1000 },
];

export function initialRoleModels(): RoleModelConfig[] {
  // As config.yaml runs them: the cartografo on `mapping_model`, every other
  // role on `default_model`.
  const m: Record<Role, string> = {
    cartografo: "claude-sonnet-5-5",
    arquitecto: "claude-opus-5-5",
    implementador: "claude-opus-5-5",
    revisor: "claude-opus-5-5",
    auditor: "claude-opus-5-5",
  };
  return (Object.keys(m) as Role[]).map((role) => ({
    role,
    model: m[role],
    updated_at: iso(86400 * 2),
  }));
}
