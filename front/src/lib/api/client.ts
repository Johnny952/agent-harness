/**
 * Typed API client. One function per endpoint.
 *
 * **Ten of them are real.** `listTasks`, `getTask`, `listAccounts`,
 * `listEvents`, `listDebt`, `listPhases`, `listLearnings`, `listUsage`,
 * `getRoadmap` and `getCommandRequests` read
 * `observability/api/` through the console's own
 * origin — `lib/api/forward.ts` is the server half that holds the bearer, so
 * nothing here carries a credential and every URL below is relative. They resolve
 * to `ApiResult<T>`: `data` unwrapped into the rows a screen takes, `warnings`
 * riding beside it. `docs/decisions.md` ADR 16, with ADR 25 for the type.
 *
 * Everything else still resolves from `./mock/fixtures` and `./mock/ops-fixtures`,
 * because the routes behind them are tier 3 of `docs/plans/front.md`. A screen
 * reading one of those says so on itself rather than looking finished;
 * `docs/ui.md` *A region with no route says which route, and when*.
 *
 * Relative URLs mean these ten may only be called from the browser: a relative
 * `fetch` on the server has no base. Every one is behind a `useQuery` or a
 * `useEffect` today, and ADR 24 names moving one into a route loader as the case
 * that has to answer the base-URL question again.
 *
 * **An eleventh is real through another forward.** `getSettings` reads
 * `/api/settings`, which `lib/api/forward.ts` sends to the config writer rather
 * than the api, under the writer's own token (`docs/decisions.md` ADR 112). It goes
 * through the same `readEnvelope`, so a writer that refused, timed out or was never
 * configured is the same `ApiError` a screen already knows how to draw (ADR 113).
 * The mock it replaced — the Role models screen's options, map and save — is
 * gone. `putSetting` and `resetSetting` are the writes that replaced the save:
 * `PUT` and `DELETE` on `/api/settings/<key>`, through the same forward and the
 * same envelope reader, so a refusal keeps the writer's message and its status
 * (`docs/decisions.md` ADR 110 part 6, ADR 114).
 *
 * **Two more reads, and four writes, for the backlog** (`docs/decisions.md`
 * ADR 136, `docs/plans/board-service.md` §4 and §6). `getReady` reads the api's
 * `/api/ready` and `listComments` reads `GET /api/tasks/<id>/comments`, both
 * the api's and both through `readEnvelope` like the ten (ADR 135 for the
 * comments). `proposeBacklog`, `editTask`, `commentTask` and
 * `acceptTask` are the writes: `POST /api/backlog`, `PATCH /api/tasks/<id>`,
 * `POST /api/tasks/<id>/comments` and `POST /api/tasks/<id>/accept`, which the
 * forward sends to the board-service. The board-service answers its result bare,
 * not in an envelope, so they go through `readBare`, and a refusal is an
 * `ApiError` with the service's own message and status. There is no mock for any
 * of the six: like the settings writes, they are real `fetch`es in every mode.
 *
 * **One more read and one more write for the Board's Ready card** (ADR 160):
 * `getActionQueue` reads `GET /api/actions` (the api's envelope) and `startTask`
 * is `POST /api/tasks/<id>/start`, bare like the six. `getActionQueue` takes the
 * finished-list `limit` and `cancelAction` is `POST /api/actions/<id>/cancel`,
 * both for the Queue screen (ADR 163).
 *
 * **Two reads for the chat dock** (`docs/decisions.md` ADR 184, step 4 of
 * `docs/plans/main-thread.md`): `listThreads` reads `GET /api/threads` (ADR 177)
 * and `listThreadEvents` one thread's log, `GET /api/threads/<id>/events` (ADR
 * 180), both the api's envelope. They replaced the mock thread the dock drew
 * before. `sendTurn` is the dock's one write, `POST /api/threads/<id>/turns`
 * (ADR 190), bare like the others; the dock stops a turn with `cancelAction`.
 * `createThread` (`POST /api/threads`) and `updateThread` (`PATCH
 * /api/threads/<id>`) open, rename, re-pick and archive a thread (ADR 195), bare
 * too. `listTaskEvents` reads a task's hook events, `GET /api/tasks/<id>/events`
 * (ADR NNN), for the dock's execution view: the api's envelope, read from a
 * cursor like the thread log.
 */
import type {
  AcceptTaskBody,
  AcceptTaskResult,
  Account,
  ActionQueue,
  ActionRecord,
  ApiResult,
  SendTurnBody,
  CreateThreadBody,
  UpdateThreadBody,
  CommandRequestState,
  CommandRequests,
  CommentTaskBody,
  CommentTaskResult,
  DebtEntry,
  EditTaskBody,
  EditTaskResult,
  HookEvent,
  LearningEntry,
  Phase,
  ProposeBacklogBody,
  ProposeBacklogResult,
  ReadyOrder,
  Roadmap,
  Settings,
  StartTaskBody,
  Task,
  TaskComments,
  TaskDetail,
  Thread,
  ThreadEventRow,
  UsageGroup,
} from "./types";

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
 * is a real write surface to be down. Kept because three mock helpers below read
 * it, and deleting it would reshape three more functions that have no route
 * either. (Two of them write; the third is `pingActionBackend`. The
 * role-model save that used to be a writer left with its mock, ADR 113, and the
 * backlog's completion toggle with the backlog's mock, ADR 138.)
 */
const actionBackendDown = false;

/* ── The ten wired reads ──────────────────────────────────────────────────
 * One boundary function, and every one of the ten goes through it: ADR 7's
 * "one boundary function per call", inherited by the console under ADR 16.
 */

async function readEnvelope<T>(path: string, write?: WriteRequest): Promise<ApiResult<T>> {
  let res: Response;
  try {
    res = await fetch(
      path,
      write
        ? {
            method: write.method,
            headers: { accept: "application/json", "content-type": "application/json" },
            body: JSON.stringify(write.body),
          }
        : { headers: { accept: "application/json" } },
    );
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

/**
 * A write through the same reader. Only the settings forward takes one; every
 * other path is the api's, which answers a closed list of GETs (ADR 112).
 */
interface WriteRequest {
  method: "PUT" | "DELETE";
  body: Record<string, unknown>;
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

/**
 * What one task's `claude` calls cost, grouped by attempt.
 *
 * `null` is a log that exists and could not be read, with the reason in
 * `warnings`; `[]` is a task that made no call. No mapping, no sort and nothing
 * summed: the groups and their figures are the api's (`docs/decisions.md` ADR 54,
 * ADR 64).
 */
export function listUsage(taskId: string): Promise<ApiResult<UsageGroup[] | null>> {
  return readEnvelope<UsageGroup[] | null>(`/api/tasks/${encodeURIComponent(taskId)}/usage`);
}

/**
 * The project's plans in three bands, future, now and past, with each plan's
 * tasks and the tasks no plan names.
 *
 * One read and no mapping: the bands, their order and the month groups of
 * `no_plan.past` are the api's (`observability/api/app.py`, T-072), and which
 * of them a screen hides is the Roadmap screen's decision. The project slug is
 * added by the forward, server-side, from `CONSOLE_PROJECT`, the way
 * `/api/debt`'s is. ADR 24.
 */
export function getRoadmap(): Promise<ApiResult<Roadmap>> {
  return readEnvelope<Roadmap>("/api/roadmap");
}

/**
 * The command requests filed across the hive, each with its state, and the
 * ones the check refused at filing.
 *
 * One read and no mapping: the order (pending first), the states and the CLI
 * lines are the api's (`observability/api/app.py`, ADR 122). Hive-wide, so the
 * forward adds no project: `project` and `state` are filters the caller picks,
 * and an unknown one is the api's 404 or 400. Read only — a verdict is given
 * from the CLI (`docs/decisions.md` ADR 123).
 */
export function getCommandRequests(
  params: { project?: string | undefined; state?: CommandRequestState | undefined } = {},
): Promise<ApiResult<CommandRequests>> {
  const query = new URLSearchParams();
  if (params.project) query.set("project", params.project);
  if (params.state) query.set("state", params.state);
  const qs = query.toString();
  return readEnvelope<CommandRequests>(`/api/command-requests${qs ? `?${qs}` : ""}`);
}

/**
 * The config writer's settings table: every key with its effective value, its
 * source and its class, the recent console changes, and the config's own error.
 *
 * One read and no mapping. `data` is the writer's `GET /settings` as served
 * (`config_writer/app.py`, ADR 110 part 6), because the screen is a view of the
 * writer's table and a save has to send its `revision` back
 * byte for byte. `docs/decisions.md` ADR 112 for the path, ADR 113 part 2 for why
 * this is `readEnvelope` and has no poll.
 */
export function getSettings(): Promise<ApiResult<Settings>> {
  return readEnvelope<Settings>("/api/settings");
}

/**
 * Save one key: `PUT /api/settings/<key>` with `{value, revision}`.
 *
 * `revision` is the last read's, sent as it came — `null` included, which is an
 * overrides file that does not exist yet. A 200 is the writer's whole payload
 * again, the same envelope as `getSettings`; a refusal is an `ApiError` carrying
 * the writer's own message and status, because the screen tells a 409 from a 422
 * by that status and quotes the message (ADR 110 part 6, ADR 114).
 */
export function putSetting(
  key: string,
  value: unknown,
  revision: string | null,
): Promise<ApiResult<Settings>> {
  return readEnvelope<Settings>(`/api/settings/${encodeURIComponent(key)}`, {
    method: "PUT",
    body: { value, revision },
  });
}

/**
 * Reset one key: `DELETE /api/settings/<key>` with `{revision}`, which takes the
 * key out of the overrides file. A key the file does not hold is the writer's
 * 200 no-op, not an error.
 */
export function resetSetting(key: string, revision: string | null): Promise<ApiResult<Settings>> {
  return readEnvelope<Settings>(`/api/settings/${encodeURIComponent(key)}`, {
    method: "DELETE",
    body: { revision },
  });
}

/* ── The backlog: the ready order, comments, and the board-service writes ── */

/**
 * The order a worker would take tasks in, as task ids, first first.
 *
 * No parameters and no project: the api's `/api/ready` reads the harness's own
 * board and takes nothing (`observability/api/app.py`, T-088). No mapping and no
 * sort, because the order is the subject.
 */
export function getReady(): Promise<ApiResult<ReadyOrder>> {
  return readEnvelope<ReadyOrder>("/api/ready");
}

/**
 * The comments on one task, oldest first.
 *
 * The api's envelope like the other reads (`docs/decisions.md` ADR 135): `data`
 * is `null` for a comments file that exists and could not be read, with the
 * reason in `warnings`; an unknown task is the api's 404, an `ApiError`.
 */
export function listComments(taskId: string): Promise<ApiResult<TaskComments | null>> {
  return readEnvelope<TaskComments | null>(`/api/tasks/${encodeURIComponent(taskId)}/comments`);
}

/**
 * Propose a task for the backlog: `POST /api/backlog`. `created` is false when
 * the board-service found the same proposal already there, and `tasks` is what
 * it wrote (`board_service/app.py`).
 */
export function proposeBacklog(body: ProposeBacklogBody): Promise<ProposeBacklogResult> {
  return readBare<ProposeBacklogResult>("/api/backlog", { method: "POST", body });
}

/** Edit a task's backlog fields: `PATCH /api/tasks/<id>`. `changed` names the fields written. */
export function editTask(taskId: string, body: EditTaskBody): Promise<EditTaskResult> {
  return readBare<EditTaskResult>(`/api/tasks/${encodeURIComponent(taskId)}`, {
    method: "PATCH",
    body,
  });
}

/** Add a comment to a task: `POST /api/tasks/<id>/comments`. */
export function commentTask(taskId: string, body: CommentTaskBody): Promise<CommentTaskResult> {
  return readBare<CommentTaskResult>(`/api/tasks/${encodeURIComponent(taskId)}/comments`, {
    method: "POST",
    body,
  });
}

/**
 * Accept a proposed task onto the board: `POST /api/tasks/<id>/accept`. For an
 * epic, `children` maps each sub-task to the status it took.
 */
export function acceptTask(taskId: string, body: AcceptTaskBody = {}): Promise<AcceptTaskResult> {
  return readBare<AcceptTaskResult>(`/api/tasks/${encodeURIComponent(taskId)}/accept`, {
    method: "POST",
    body,
  });
}

/**
 * The action queue as the worker holds it: `GET /api/actions`, the api's envelope.
 *
 * `limit` is how many finished records to ask for. The Board passes nothing and
 * gets 1: it reads `pending` and `claimed`, which the api serves whole, and has
 * no use for the finished list (ADR 160 part 1). `/queue` passes 20 (ADR 163).
 */
export function getActionQueue(limit = 1): Promise<ApiResult<ActionQueue>> {
  return readEnvelope<ActionQueue>(`/api/actions?limit=${limit}`);
}

/**
 * Cancel a queued or running start: `POST /api/actions/<id>/cancel` with an empty
 * body. The answer is the new *cancel* action the board-service queued, not the
 * start; the worker carries the cancel out (ADR 163 part 5).
 */
export function cancelAction(actionId: string): Promise<ActionRecord> {
  return readBare<ActionRecord>(`/api/actions/${encodeURIComponent(actionId)}/cancel`, {
    method: "POST",
    body: {},
  });
}

/**
 * Queue a task's development cycle: `POST /api/tasks/<id>/start`. The body is
 * exactly `{note}` and the forward 400s any other key (ADR 157 part 1); a refusal
 * is an `ApiError` with the board-service's message.
 */
export function startTask(taskId: string, note: string | null): Promise<ActionRecord> {
  const body: StartTaskBody = { note };
  return readBare<ActionRecord>(`/api/tasks/${encodeURIComponent(taskId)}/start`, {
    method: "POST",
    body,
  });
}

/**
 * Queue a turn for a thread: `POST /api/threads/<id>/turns` with `{text}` (ADR 181,
 * ADR 190). The answer is the queued `turn` action; a refusal (400, 404, 409, the
 * 503 for an unset token) is an `ApiError` with the route's own message.
 */
export function sendTurn(threadId: string, text: string): Promise<ActionRecord> {
  const body: SendTurnBody = { text };
  return readBare<ActionRecord>(`/api/threads/${encodeURIComponent(threadId)}/turns`, {
    method: "POST",
    body,
  });
}

/**
 * Open a thread: `POST /api/threads` (ADR 195). The answer is the new record; a
 * refusal (400 for a bad value or an unknown task, 409 for a second main, 503) is
 * an `ApiError` with the service's own message.
 */
export function createThread(body: CreateThreadBody): Promise<Thread> {
  return readBare<Thread>("/api/threads", { method: "POST", body });
}

/** Change a thread: `PATCH /api/threads/<id>` (ADR 195). The answer is the changed record. */
export function updateThread(threadId: string, body: UpdateThreadBody): Promise<Thread> {
  return readBare<Thread>(`/api/threads/${encodeURIComponent(threadId)}`, {
    method: "PATCH",
    body,
  });
}

/** A write to the board-service. The forward sends the body on and nothing else. */
interface BoardWrite {
  method: "POST" | "PATCH";
  body: object;
}

/**
 * `readEnvelope`'s guards, for a payload that is not an envelope: the JSON
 * content-type check, and `{error}` on a non-2xx as an `ApiError` with the
 * status. Status 0 is the console's own origin not answering. Nothing about the
 * 2xx body is checked beyond its being JSON — the shape is the service's.
 */
async function readBare<T>(path: string, write?: BoardWrite): Promise<T> {
  let res: Response;
  try {
    res = await fetch(
      path,
      write
        ? {
            method: write.method,
            headers: { accept: "application/json", "content-type": "application/json" },
            body: JSON.stringify(write.body),
          }
        : { headers: { accept: "application/json" } },
    );
  } catch (cause) {
    throw new ApiError(`${path} could not be reached: ${String(cause)}`, 0);
  }

  const contentType = res.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    throw new ApiError(
      `${path} answered ${res.status} as ${contentType || "no content type"}, not JSON`,
      res.status,
    );
  }

  const body: unknown = await res.json();
  if (!res.ok) {
    const reported = (body as { error?: unknown } | null)?.error;
    throw new ApiError(
      typeof reported === "string" ? reported : `${path} answered ${res.status}`,
      res.status,
    );
  }
  return body as T;
}

/* ── Threads: the chat dock's reads ──────────────────────────────────────── */

/** Every thread the harness holds, `main` among them once it exists (ADR 177). */
export function listThreads(): Promise<ApiResult<Thread[]>> {
  return readEnvelope<Thread[]>("/api/threads");
}

/**
 * One thread's conversation log after line `since` (ADR 180): the rows with
 * `n > since`, at most the api's default page of 500. A missing log is `[]`; a
 * line that is not a JSON object is a row with `event: null` and a warning.
 */
export function listThreadEvents(id: string, since = 0): Promise<ApiResult<ThreadEventRow[]>> {
  return readEnvelope<ThreadEventRow[]>(
    `/api/threads/${encodeURIComponent(id)}/events?since=${since}`,
  );
}

/**
 * The hook events of a task's phases after event `since` (ADR NNN): the rows
 * whose `cwd` sits under the task's worktrees, oldest first, at most the route's
 * default page of 500. No events database yet is `[]` and a warning.
 */
export function listTaskEvents(id: string, since = 0): Promise<ApiResult<HookEvent[]>> {
  return readEnvelope<HookEvent[]>(`/api/tasks/${encodeURIComponent(id)}/events?since=${since}`);
}

/* ── Still fixtures: tier 3, and the ones that never land ───────────────── */

export async function pingActionBackend(): Promise<boolean> {
  await latency();
  return !actionBackendDown;
}

/* ── Sessions, tokens, approvals ─────────────────────────────────────── */
import type { Approval, ContainerToken, SessionInfo, SessionLogLine } from "./ops-types";
import {
  initialApprovals,
  initialTokens,
  mockSessionLines,
  mockSessions,
  nextSessionLine,
} from "./mock/ops-fixtures";

let tokenState: ContainerToken[] | null = null;
const tokens = () => (tokenState ??= initialTokens());
let approvalState: Approval[] | null = null;
const approvals = () => (approvalState ??= initialApprovals());

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
