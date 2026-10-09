/**
 * Typed API client. One function per endpoint.
 *
 * **Seven of them are real.** `listTasks`, `getTask`, `listAccounts`,
 * `listEvents`, `listDebt`, `listPhases` and `listLearnings` read
 * `observability/api/` through the console's own
 * origin — `lib/api/forward.ts` is the server half that holds the bearer, so
 * nothing here carries a credential and every URL below is relative. They resolve
 * to `ApiResult<T>`: `data` unwrapped into the rows a screen takes, `warnings`
 * riding beside it. `docs/decisions.md` ADR 16, with ADR 25 for the type.
 *
 * Everything else still resolves from `./mock/fixtures` and `./mock/ops-fixtures`,
 * because the routes behind them are tier 3 of `docs/plans/front.md`:
 * `listActions`, `enqueueAction` and `listThreads` are never coming here at
 * all — ADR 19 puts the write surface
 * in another service with its own credential, and C-1 has not ruled on the chat
 * dock. A screen reading one of those says so on itself rather than looking
 * finished; `docs/ui.md` *A region with no route says which route, and when*.
 *
 * Relative URLs mean these seven may only be called from the browser: a relative
 * `fetch` on the server has no base. Every one is behind a `useQuery` or a
 * `useEffect` today, and ADR 24 names moving one into a route loader as the case
 * that has to answer the base-URL question again.
 */
import type {
  Account,
  ApiResult,
  ChatThread,
  DebtEntry,
  HookEvent,
  LearningEntry,
  Phase,
  QueuedAction,
  Task,
  TaskDetail,
} from "./types";
import { mockActions, mockThreads } from "./mock/fixtures";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

const latency = () => new Promise((r) => setTimeout(r, 120));

/**
 * Permanently false. It was flipped by `setActionBackendDown`, a control that
 * faked a failure so the mock could rehearse one, and ADR 19 is explicit that a
 * control which fakes a failure is a fixture wearing a button — so that function
 * and its `isActionBackendDown` reader are gone, with no callers anywhere in
 * `front/src` to mourn them. Nothing flips this now, and nothing will until there
 * is a real write surface to be down. Kept because the five mock write helpers
 * below read it, and deleting it would reshape five more functions that have no
 * route either.
 */
const actionBackendDown = false;

/* ── The seven wired reads ────────────────────────────────────────────────
 * One boundary function, and every one of the seven goes through it: ADR 7's
 * "one boundary function per call", inherited by the console under ADR 16.
 */

async function readEnvelope<T>(path: string): Promise<ApiResult<T>> {
  let res: Response;
  try {
    res = await fetch(path, { headers: { accept: "application/json" } });
  } catch (cause) {
    // The console's own origin did not answer. Status 0: there was no response.
    throw new ApiError(`${path} could not be reached: ${String(cause)}`, 0);
  }

  const contentType = res.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    // Flask answers its own 404 and 405 in HTML, so a routing mistake must not
    // become a parse error. docs/learnings/flask-answers-404-and-405-in-html.md,
    // and ADR 7's guard, which ADR 16 makes the console's too.
    throw new ApiError(
      `${path} answered ${res.status} as ${contentType || "no content type"}, not JSON`,
      res.status,
    );
  }

  const body: unknown = await res.json();

  if (!res.ok) {
    // A non-200 is {"error": "…"} with no warnings key at all, so the error path
    // reads a different shape and does not look for one.
    const reported = (body as { error?: unknown } | null)?.error;
    throw new ApiError(
      typeof reported === "string" ? reported : `${path} answered ${res.status}`,
      res.status,
    );
  }

  // Shallow, as ADR 16 says: `data` present, `warnings` a list of strings. Deeper
  // validation here would be a second copy of the api's own shape, maintained by
  // hand on the other side of a network.
  if (!isEnvelope(body)) {
    // Not an empty screen: a 200 that is not {data, warnings} is a different
    // service than the one this console was written against, and rendering it as
    // "nothing to show" is the failure ADR 16 exists to prevent.
    throw new ApiError(`${path} answered 200 but not {data, warnings}`, res.status);
  }

  // `data === null` and `data === []` are *not* errors — both mean "nothing to
  // show, read the warnings". The error path belongs to non-200s alone.
  return { data: body.data as T, warnings: body.warnings };
}

function isEnvelope(body: unknown): body is { data: unknown; warnings: string[] } {
  if (typeof body !== "object" || body === null) return false;
  if (!("data" in body)) return false;
  const warnings = (body as { warnings?: unknown }).warnings;
  return Array.isArray(warnings) && warnings.every((w) => typeof w === "string");
}

export function listTasks(): Promise<ApiResult<Task[]>> {
  return readEnvelope<Task[]>("/api/tasks");
}

/**
 * One task, with its running record. `data` is `null` for a card that exists and
 * could not be read, with the reason in `warnings` — a 404 is an `ApiError` and
 * that is a different screen.
 */
export function getTask(taskId: string): Promise<ApiResult<TaskDetail | null>> {
  return readEnvelope<TaskDetail | null>(`/api/tasks/${encodeURIComponent(taskId)}`);
}

/** The api serves `current_task`; the console's `Account` keeps `current_task_id`. */
type ServedAccount = Omit<Account, "current_task_id"> & { current_task: string | null };

export async function listAccounts(): Promise<ApiResult<Account[]>> {
  const { data, warnings } = await readEnvelope<ServedAccount[]>("/api/accounts");
  // The one rename the console does make, written as a destructure so the served
  // name does not linger beside the console's. ADR 17: the route's name is the
  // harness's word for the thing, so the api is not renamed to match a fixture.
  const accounts = data.map(({ current_task, ...rest }) => ({
    ...rest,
    current_task_id: current_task,
  }));
  return { data: accounts, warnings };
}

/**
 * Events above `afterId`, oldest first.
 *
 * `observability/collector/db.py:list_events` ends in `ORDER BY id DESC LIMIT ?`
 * and `routes/tail.tsx` keeps an ascending array whose next cursor is
 * `events.at(-1)?.id`. Wired together unchanged, `at(-1)` would read the *oldest*
 * id of the batch: the cursor walks backwards and the tail re-fetches the same
 * window forever while looking like it works. The reverse is here rather than in
 * `tail.tsx` so there is one place that knows the api's order —
 * `docs/decisions.md` ADR 22, which also makes that `DESC` a contract: a later
 * change to it changes this line in the same commit, or the tail stops moving.
 *
 * No `limit`: the service's `DEFAULT_EVENT_LIMIT` of 100 is the burst ceiling a
 * `since`-bounded tail wants. When more events arrive between polls than that, the
 * api answers the newest 100 above the cursor and the older ones in that window
 * are skipped. That is what the route documents and the console does not paper
 * over it — a tail that hides a gap is worse than one that has it.
 */
export async function listEvents(afterId?: number): Promise<ApiResult<HookEvent[]>> {
  const path =
    afterId === undefined ? "/api/events" : `/api/events?since=${encodeURIComponent(afterId)}`;
  const { data, warnings } = await readEnvelope<HookEvent[]>(path);
  return { data: [...data].reverse(), warnings };
}

/**
 * The task a debt id belongs to, or `null` for an id that is not shaped that way.
 *
 * Debt ids are written `T-011-D1`, so the task is the prefix and `task_id` is a
 * split rather than a column — and `Task.debt[]` is the same split read the other
 * way, a filter over this list rather than a field `/api/tasks` serves. ADR 17.
 * Exported so the screens that group by task group with the same rule.
 */
export function debtTaskId(id: string): string | null {
  return /^(.+)-D\d+$/.exec(id)?.[1] ?? null;
}

export async function listDebt(): Promise<ApiResult<DebtEntry[]>> {
  // The project slug is added by the forward, server-side, from CONSOLE_PROJECT:
  // `?project=` is the one parameter this route takes and it is not the browser's
  // to choose. ADR 24.
  const { data, warnings } = await readEnvelope<Omit<DebtEntry, "task_id">[]>("/api/debt");
  return { data: data.map((row) => ({ ...row, task_id: debtTaskId(row.id) })), warnings };
}

/**
 * The phases of one task, or of every task when no id is given.
 *
 * No reverse, no mapping and no sort: the api answers newest `saved_at` first and
 * the order a timeline shows is the screen's decision, not this boundary's —
 * `tasks.$taskId.tsx` sorts by the cycle, which is a different order on purpose
 * and says why there. Nothing is adapted either, because nothing is served under
 * another name: `revision_round` became `round` on the console's side rather than
 * on the route's, the way `pending` beat `queued` (`docs/decisions.md` ADR 27,
 * ADR 17's closing rule).
 *
 * The id is always sent by the screens that call this. The unfiltered form is
 * legal and bounded by the api's own `MAX_PHASES`; nothing here polls it.
 */
export function listPhases(taskId?: string): Promise<ApiResult<Phase[]>> {
  const path = taskId ? `/api/phases?task_id=${encodeURIComponent(taskId)}` : "/api/phases";
  return readEnvelope<Phase[]>(path);
}

/**
 * Every trap entry in the hive this project's phases could be handed.
 *
 * No mapping and no sort: the api answers in the order a phase's own table is
 * in — confirmed before unconfirmed, fresh before stale, `ref` as the tie-break
 * — and that order is the Learnings screen's subject, so reordering it here
 * would be the boundary contradicting the route (`docs/decisions.md` ADR 42).
 * Both of its callers filter rather than re-sort.
 *
 * The project slug is added by the forward, server-side, from `CONSOLE_PROJECT`,
 * the way `/api/debt`'s is: `?project=` is the one parameter this route takes and
 * it is not the browser's to choose. ADR 24.
 */
export function listLearnings(): Promise<ApiResult<LearningEntry[]>> {
  return readEnvelope<LearningEntry[]>("/api/learnings");
}

/* ── Still fixtures: tier 3, and the ones that never land ───────────────── */

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
    t.state === "reauthenticating" &&
    t.last_refreshed_at &&
    Date.now() - Date.parse(t.last_refreshed_at) > 10_000
      ? {
          ...t,
          state: "valid",
          device_code: null,
          last_error: null,
          expires_at: new Date(Date.now() + 7 * 86400_000).toISOString(),
        }
      : t,
  );
  return tokenState;
}

export async function reauthContainer(container: string): Promise<ContainerToken> {
  await latency();
  if (actionBackendDown) throw new ApiError("Action backend unreachable", 503);
  const code =
    Math.random().toString(36).slice(2, 6).toUpperCase() +
    "-" +
    Math.random().toString(36).slice(2, 6).toUpperCase();
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

export async function decideApproval(
  id: string,
  decision: "approved" | "rejected",
): Promise<Approval> {
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

export async function setRoleModel(
  role: RoleModelConfig["role"],
  model: string,
): Promise<RoleModelConfig> {
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
