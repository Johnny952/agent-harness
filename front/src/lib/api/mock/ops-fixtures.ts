import type { Role } from "../types";
import type {
  Approval,
  BacklogItem,
  ContainerToken,
  ModelOption,
  RoleModelConfig,
  SessionInfo,
  SessionLogLine,
  LogLevel,
} from "../ops-types";

const iso = (secondsAgo: number) => new Date(Date.now() - secondsAgo * 1000).toISOString();
const inFuture = (s: number) => new Date(Date.now() + s * 1000).toISOString();

export function mockSessions(): SessionInfo[] {
  return [
    { container: "agent-cuenta1", account: "cuenta1", task_id: "T-011", role: "revisor", live: true },
    { container: "agent-cuenta3", account: "cuenta3", task_id: "T-012", role: "implementador", live: true },
    { container: "agent-cuenta4", account: "cuenta4", task_id: "T-013", role: "cartografo", live: true },
    { container: "agent-cuenta5", account: "cuenta5", task_id: "T-014", role: "auditor", live: true },
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
  return { seq: ++seq, container: s.container, task_id: s.task_id, role: s.role, level, ts: iso(ago), text };
}

export function initialTokens(): ContainerToken[] {
  const acc = ["cuenta1", "cuenta2", "cuenta3", "cuenta4", "cuenta5", "cuenta6"];
  return acc.map((a, i) => ({
    container: `agent-${a}`,
    account: a,
    state: i === 1 ? "expired" : i === 4 ? "expiring" : i === 5 ? "revoked" : "valid",
    expires_at: i === 1 ? iso(3600) : i === 4 ? inFuture(900) : i === 5 ? null : inFuture(86400 * (3 + i)),
    last_refreshed_at: i === 5 ? null : iso(3600 * (i + 2)),
    last_error:
      i === 1 ? "401 invalid_grant: refresh token expired" : i === 5 ? "403 token revoked by provider" : null,
    device_code: null,
  }));
}

export const MODEL_OPTIONS: ModelOption[] = [
  { id: "claude-opus-4", label: "Claude Opus 4", context_k: 200 },
  { id: "claude-sonnet-4.5", label: "Claude Sonnet 4.5", context_k: 200 },
  { id: "claude-haiku-4.5", label: "Claude Haiku 4.5", context_k: 200 },
  { id: "gpt-5", label: "GPT-5", context_k: 400 },
  { id: "gpt-5-mini", label: "GPT-5 mini", context_k: 400 },
  { id: "gemini-2.5-pro", label: "Gemini 2.5 Pro", context_k: 1000 },
];

export function initialRoleModels(): RoleModelConfig[] {
  const m: Record<Role, string> = {
    cartografo: "claude-haiku-4.5",
    arquitecto: "claude-opus-4",
    implementador: "claude-sonnet-4.5",
    revisor: "claude-opus-4",
    auditor: "gpt-5-mini",
  };
  return (Object.keys(m) as Role[]).map((role) => ({ role, model: m[role], updated_at: iso(86400 * 2) }));
}

const DIFF = `diff --git a/src/pool/lock.ts b/src/pool/lock.ts
index 3f1a2c9..8b0d7e1 100644
--- a/src/pool/lock.ts
+++ b/src/pool/lock.ts
@@ -12,14 +12,19 @@ export const HEARTBEAT_STALE_S = 120;
 export async function releaseLock(account: string) {
   const lock = await readLock(account);
-  if (!lock) return;
-  await fs.rm(lockPath(account));
+  if (!lock) return { released: false, reason: "no-lock" };
+  const age = (Date.now() - Date.parse(lock.heartbeat)) / 1000;
+  if (age < HEARTBEAT_STALE_S) {
+    return { released: false, reason: "lock-live" };
+  }
+  await fs.rm(lockPath(account));
+  return { released: true };
 }
 
diff --git a/tests/pool/lock.test.ts b/tests/pool/lock.test.ts
index 11aa220..5c3e019 100644
--- a/tests/pool/lock.test.ts
+++ b/tests/pool/lock.test.ts
@@ -40,6 +40,14 @@ describe("releaseLock", () => {
   it("removes a stale lock", async () => {
     expect(await releaseLock("cuenta9")).toEqual({ released: true });
   });
+
+  it("refuses a live lock", async () => {
+    await touchHeartbeat("cuenta1");
+    expect(await releaseLock("cuenta1")).toEqual({
+      released: false,
+      reason: "lock-live",
+    });
+  });
 });
`;

export function initialApprovals(): Approval[] {
  return [
    {
      id: "AP-31", kind: "push", task_id: "T-011", requested_by: "revisor@agent-cuenta1",
      requested_at: iso(240), summary: "Push task/T-011 → main after revision round 2",
      state: "pending", decided_at: null,
      push: {
        branch: "task/T-011", base: "main", gate_worst: "warning",
        commits: [
          { sha: "a1c93f0", message: "pool: refuse release while lock is live" },
          { sha: "7e02b44", message: "tests: cover live-lock refusal" },
        ],
        diff: DIFF,
      },
    },
    {
      id: "AP-32", kind: "tool-permission", task_id: "T-012", requested_by: "implementador@agent-cuenta3",
      requested_at: iso(95), summary: "Run `bun add zod@3` inside worktree T-012", state: "pending",
      decided_at: null, push: null,
    },
    {
      id: "AP-33", kind: "budget-override", task_id: "T-008", requested_by: "arquitecto@agent-cuenta4",
      requested_at: iso(1300), summary: "Allow 1.4× byte budget for arquitecto envelope (shrink retry failed)",
      state: "pending", decided_at: null, push: null,
    },
    {
      id: "AP-34", kind: "merge-task", task_id: "T-004", requested_by: "operator",
      requested_at: iso(7200), summary: "Merge T-004 into main", state: "approved", decided_at: iso(7000), push: null,
    },
    {
      id: "AP-35", kind: "push", task_id: "T-002", requested_by: "auditor@agent-cuenta5",
      requested_at: iso(10800), summary: "Push task/T-002 → main", state: "rejected", decided_at: iso(10500),
      push: { branch: "task/T-002", base: "main", gate_worst: "blocking", commits: [{ sha: "0c9d1aa", message: "wip" }], diff: "" },
    },
  ];
}

const D = 86400;
export function initialBacklog(): BacklogItem[] {
  return [
    { id: "BL-01", kind: "epic", title: "Pool self-healing: re-probe parked accounts automatically", category: "pool", priority: 1, created_at: iso(21 * D), completed: false, completed_at: null, task_id: "T-011", notes: "Parked-over-90% accounts should re-enter the pool without operator action." },
    { id: "BL-02", kind: "task", title: "Refuse lock release while heartbeat is live", category: "pool", priority: 2, created_at: iso(19 * D), completed: true, completed_at: iso(2 * D), task_id: "T-011", notes: null },
    { id: "BL-03", kind: "epic", title: "Gate coverage: contract-docs for every role", category: "gates", priority: 3, created_at: iso(18 * D), completed: false, completed_at: null, task_id: null, notes: "Only arquitecto and implementador have contract-docs today." },
    { id: "BL-04", kind: "task", title: "Shrink retry when an envelope blows the byte budget", category: "harness", priority: 4, created_at: iso(16 * D), completed: true, completed_at: iso(5 * D), task_id: "T-008", notes: null },
    { id: "BL-05", kind: "task", title: "Cap the learnings table handed to a phase at 40 rows", category: "learnings", priority: 5, created_at: iso(14 * D), completed: false, completed_at: null, task_id: "T-013", notes: "Selection rule: confirmed first, then unconfirmed by recency." },
    { id: "BL-06", kind: "task", title: "Surface revisor write attempts as first-class events", category: "harness", priority: 6, created_at: iso(12 * D), completed: false, completed_at: null, task_id: null, notes: null },
    { id: "BL-07", kind: "epic", title: "Debt index: auto-expire accepted entries after 30 days", category: "debt", priority: 7, created_at: iso(10 * D), completed: false, completed_at: null, task_id: null, notes: null },
    { id: "BL-08", kind: "task", title: "Document the handoff envelope schema", category: "docs", priority: 8, created_at: iso(9 * D), completed: true, completed_at: iso(6 * D), task_id: "T-004", notes: null },
    { id: "BL-09", kind: "task", title: "Alert when a lock heartbeat goes stale twice in a row", category: "infra", priority: 9, created_at: iso(6 * D), completed: false, completed_at: null, task_id: null, notes: "T-008 hit this; the operator noticed by hand." },
    { id: "BL-10", kind: "task", title: "Rotate container tokens before the 7-day expiry", category: "infra", priority: 10, created_at: iso(3 * D), completed: false, completed_at: null, task_id: null, notes: null },
  ];
}
