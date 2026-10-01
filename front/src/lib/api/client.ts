/**
 * Typed API client. One function per endpoint.
 * Today every function resolves from mock fixtures; swapping to the real REST
 * backend means replacing the bodies in this single file.
 */
import type {
  Account,
  ChatThread,
  DebtEntry,
  HookEvent,
  LearningEntry,
  Phase,
  QueuedAction,
  Task,
} from "./types";
import {
  mockAccounts,
  mockActions,
  mockDebt,
  mockEvents,
  mockLearnings,
  mockPhases,
  mockTasks,
  mockThreads,
  nextMockEvent,
} from "./mock/fixtures";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

const latency = () => new Promise((r) => setTimeout(r, 120));

/** Flipped by a failing action call so read-only screens can warn. */
let actionBackendDown = false;
export const isActionBackendDown = () => actionBackendDown;

export async function listTasks(): Promise<Task[]> {
  await latency();
  return mockTasks();
}

export async function getTask(taskId: string): Promise<Task> {
  await latency();
  const task = mockTasks().find((t) => t.task_id === taskId);
  if (!task) throw new ApiError(`Task ${taskId} not found`, 404);
  return task;
}

export async function listPhases(taskId?: string): Promise<Phase[]> {
  await latency();
  const all = mockPhases();
  return taskId ? all.filter((p) => p.task_id === taskId) : all;
}

export async function listAccounts(): Promise<Account[]> {
  await latency();
  return mockAccounts();
}

export async function listEvents(afterId?: number): Promise<HookEvent[]> {
  await latency();
  const all = mockEvents();
  if (afterId === undefined) return all;
  const extra: HookEvent[] = [];
  let last = afterId;
  const burst = 1 + Math.floor(Math.random() * 3);
  for (let i = 0; i < burst; i++) {
    const e = nextMockEvent(last);
    last = e.id;
    extra.push(e);
  }
  return extra;
}

export async function listDebt(): Promise<DebtEntry[]> {
  await latency();
  return mockDebt();
}

export async function listLearnings(): Promise<LearningEntry[]> {
  await latency();
  return mockLearnings();
}

export async function listActions(): Promise<QueuedAction[]> {
  await latency();
  return mockActions();
}

export async function listThreads(): Promise<ChatThread[]> {
  await latency();
  return mockThreads();
}

export async function enqueueAction(
  verb: QueuedAction["verb"],
  args: string[],
): Promise<QueuedAction> {
  await latency();
  if (actionBackendDown) throw new ApiError("Action backend unreachable", 503);
  return {
    id: `A-${Math.floor(Math.random() * 900 + 100)}`,
    verb,
    args,
    enqueued_by: "operator",
    state: "queued",
    enqueued_at: new Date().toISOString(),
    output_tail: null,
  };
}

export async function pingActionBackend(): Promise<boolean> {
  await latency();
  return !actionBackendDown;
}

/** Test/demo hook: lets the operator simulate the action backend going away. */
export function setActionBackendDown(down: boolean) {
  actionBackendDown = down;
}

export function commandLine(verb: string, args: string[]): string {
  return `harness ${verb} ${args.join(" ")}`.trim();
}

/* ── Sessions, tokens, approvals, role models ─────────────────────────── */
import type {
  Approval,
  BacklogItem,
  ContainerToken,
  ModelOption,
  RoleModelConfig,
  SessionInfo,
  SessionLogLine,
} from "./ops-types";
import {
  MODEL_OPTIONS,
  initialApprovals,
  initialBacklog,
  initialRoleModels,
  initialTokens,
  mockSessionLines,
  mockSessions,
  nextSessionLine,
} from "./mock/ops-fixtures";

let tokenState: ContainerToken[] | null = null;
const tokens = () => (tokenState ??= initialTokens());
let approvalState: Approval[] | null = null;
const approvals = () => (approvalState ??= initialApprovals());
let modelState: RoleModelConfig[] | null = null;
const models = () => (modelState ??= initialRoleModels());

export async function listSessions(): Promise<SessionInfo[]> {
  await latency();
  return mockSessions();
}

/** Polls lines with seq > afterSeq (SSE-compatible contract). */
export async function listSessionLines(afterSeq?: number): Promise<SessionLogLine[]> {
  await latency();
  if (afterSeq === undefined) return mockSessionLines();
  return Array.from({ length: 1 + Math.floor(Math.random() * 3) }, () => nextSessionLine());
}

export async function listTokens(): Promise<ContainerToken[]> {
  await latency();
  // A pending device-code login completes after ~10s in the mock.
  tokenState = tokens().map((t) =>
    t.state === "reauthenticating" && t.last_refreshed_at && Date.now() - Date.parse(t.last_refreshed_at) > 10_000
      ? { ...t, state: "valid", device_code: null, last_error: null,
          expires_at: new Date(Date.now() + 7 * 86400_000).toISOString() }
      : t,
  );
  return tokenState;
}

export async function reauthContainer(container: string): Promise<ContainerToken> {
  await latency();
  if (actionBackendDown) throw new ApiError("Action backend unreachable", 503);
  const code = Math.random().toString(36).slice(2, 6).toUpperCase() + "-" + Math.random().toString(36).slice(2, 6).toUpperCase();
  let updated: ContainerToken | undefined;
  tokenState = tokens().map((t) => {
    if (t.container !== container) return t;
    updated = {
      ...t,
      state: "reauthenticating",
      last_refreshed_at: new Date().toISOString(),
      device_code: {
        user_code: code,
        verification_url: "https://login.provider.example/device",
        expires_at: new Date(Date.now() + 900_000).toISOString(),
      },
    };
    return updated;
  });
  if (!updated) throw new ApiError(`Container ${container} not found`, 404);
  return updated;
}

export async function listApprovals(): Promise<Approval[]> {
  await latency();
  return approvals();
}

export async function decideApproval(id: string, decision: "approved" | "rejected"): Promise<Approval> {
  await latency();
  if (actionBackendDown) throw new ApiError("Action backend unreachable", 503);
  const a = approvals().find((x) => x.id === id);
  if (!a) throw new ApiError(`Approval ${id} not found`, 404);
  if (a.state !== "pending") throw new ApiError(`Approval ${id} was already ${a.state}`, 409);
  const next = { ...a, state: decision, decided_at: new Date().toISOString() };
  approvalState = approvals().map((x) => (x.id === id ? next : x));
  return next;
}

export async function listModelOptions(): Promise<ModelOption[]> {
  await latency();
  return MODEL_OPTIONS;
}

export async function listRoleModels(): Promise<RoleModelConfig[]> {
  await latency();
  return models();
}

export async function setRoleModel(role: RoleModelConfig["role"], model: string): Promise<RoleModelConfig> {
  await latency();
  if (actionBackendDown) throw new ApiError("Action backend unreachable", 503);
  const next = { role, model, updated_at: new Date().toISOString() };
  modelState = models().map((m) => (m.role === role ? next : m));
  return next;
}

/* ── Backlog ──────────────────────────────────────────────────────────── */

let backlogState: BacklogItem[] | null = null;
const backlog = () => (backlogState ??= initialBacklog());

export async function listBacklog(): Promise<BacklogItem[]> {
  await latency();
  return backlog();
}

export async function setBacklogCompleted(id: string, completed: boolean): Promise<BacklogItem> {
  await latency();
  if (actionBackendDown) throw new ApiError("Action backend unreachable", 503);
  const item = backlog().find((b) => b.id === id);
  if (!item) throw new ApiError(`Backlog item ${id} not found`, 404);
  const next: BacklogItem = {
    ...item,
    completed,
    completed_at: completed ? new Date().toISOString() : null,
  };
  backlogState = backlog().map((b) => (b.id === id ? next : b));
  return next;
}
