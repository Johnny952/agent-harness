import { mutationOptions, queryOptions, type QueryClient } from "@tanstack/react-query";
import * as api from "./client";
import type {
  AcceptTaskBody,
  CommandRequestState,
  CommentTaskBody,
  EditTaskBody,
  ProposeBacklogBody,
} from "./types";

/** Nothing polls faster than 2s. */
export const POLL_MS = 2500;

export const tasksQuery = queryOptions({
  queryKey: ["tasks"],
  queryFn: () => api.listTasks(),
  refetchInterval: POLL_MS,
});

export const taskQuery = (taskId: string) =>
  queryOptions({
    queryKey: ["task", taskId],
    queryFn: () => api.getTask(taskId),
    refetchInterval: POLL_MS,
  });

export const phasesQuery = (taskId?: string) =>
  queryOptions({
    queryKey: ["phases", taskId ?? "all"],
    queryFn: () => api.listPhases(taskId),
    refetchInterval: POLL_MS,
  });

export const accountsQuery = queryOptions({
  queryKey: ["accounts"],
  queryFn: () => api.listAccounts(),
  refetchInterval: POLL_MS,
});

export const debtQuery = queryOptions({
  queryKey: ["debt"],
  queryFn: () => api.listDebt(),
  refetchInterval: POLL_MS * 2,
});

/** Plans change on a merge, not every second: the debt index's pace. */
export const roadmapQuery = queryOptions({
  queryKey: ["roadmap"],
  queryFn: () => api.getRoadmap(),
  refetchInterval: POLL_MS * 2,
});

/** Keyed by the state filter, so each filter keeps its own cache. ADR 123. */
export const commandRequestsQuery = (state?: CommandRequestState) =>
  queryOptions({
    queryKey: ["command-requests", state ?? "all"],
    queryFn: () => api.getCommandRequests({ state }),
    refetchInterval: POLL_MS,
  });

export const learningsQuery = queryOptions({
  queryKey: ["learnings"],
  queryFn: () => api.listLearnings(),
  refetchInterval: POLL_MS * 2,
});

/** An append-only log of finished calls: it moves slower than a phase does. */
export const usageQuery = (taskId: string) =>
  queryOptions({
    queryKey: ["usage", taskId],
    queryFn: () => api.listUsage(taskId),
    refetchInterval: POLL_MS * 2,
  });

/** The ready order moves when a task does, so it polls with the board. */
export const readyQuery = queryOptions({
  queryKey: ["ready"],
  queryFn: () => api.getReady(),
  refetchInterval: POLL_MS,
});

/** A task's comments. Polled slowly; a comment written here refetches at once. */
export const commentsQuery = (taskId: string) =>
  queryOptions({
    queryKey: ["comments", taskId],
    queryFn: () => api.listComments(taskId),
    refetchInterval: POLL_MS * 2,
  });

/**
 * After a board-service write, every read it can have moved: the board, the
 * ready order, and — for a write to one task — that task and its comments.
 * Settled rather than succeeded, because a refusal can follow a write another
 * tab made, and the screen should show the board as it now is.
 */
function invalidateBoard(qc: QueryClient, taskId?: string) {
  const keys: unknown[][] = [["tasks"], ["ready"]];
  if (taskId) keys.push(["task", taskId], ["comments", taskId]);
  return Promise.all(keys.map((queryKey) => qc.invalidateQueries({ queryKey })));
}

/** `POST /api/backlog`. The new task's id is in the result, so only the board refetches. */
export const proposeBacklogMutation = (qc: QueryClient) =>
  mutationOptions({
    mutationKey: ["board", "propose"],
    mutationFn: (body: ProposeBacklogBody) => api.proposeBacklog(body),
    onSettled: () => invalidateBoard(qc),
  });

export const editTaskMutation = (qc: QueryClient) =>
  mutationOptions({
    mutationKey: ["board", "edit"],
    mutationFn: ({ taskId, body }: { taskId: string; body: EditTaskBody }) =>
      api.editTask(taskId, body),
    onSettled: (_data, _error, { taskId }) => invalidateBoard(qc, taskId),
  });

export const commentTaskMutation = (qc: QueryClient) =>
  mutationOptions({
    mutationKey: ["board", "comment"],
    mutationFn: ({ taskId, body }: { taskId: string; body: CommentTaskBody }) =>
      api.commentTask(taskId, body),
    onSettled: (_data, _error, { taskId }) => invalidateBoard(qc, taskId),
  });

/** An epic's accept moves its children too, so the whole board refetches anyway. */
export const acceptTaskMutation = (qc: QueryClient) =>
  mutationOptions({
    mutationKey: ["board", "accept"],
    mutationFn: ({ taskId, body }: { taskId: string; body?: AcceptTaskBody }) =>
      api.acceptTask(taskId, body),
    onSettled: (_data, _error, { taskId }) => invalidateBoard(qc, taskId),
  });

/**
 * The action queue the worker reads, as the Board takes it (`limit=1`). Key
 * `["action-queue"]`, which a start's or a cancel's invalidation refetches. ADR 160.
 */
export const actionQueueQuery = queryOptions({
  queryKey: ["action-queue"],
  queryFn: () => api.getActionQueue(),
  refetchInterval: POLL_MS,
});

/** `POST /api/tasks/<id>/start`. The queue and the board refetch, settled either way. */
export const startTaskMutation = (qc: QueryClient) =>
  mutationOptions({
    mutationKey: ["board", "start"],
    mutationFn: ({ taskId, note }: { taskId: string; note: string | null }) =>
      api.startTask(taskId, note),
    onSettled: () =>
      Promise.all(
        [["action-queue"], ["tasks"]].map((queryKey) => qc.invalidateQueries({ queryKey })),
      ),
  });

/** How many finished records the Queue screen asks for; `done_total` counts the rest. */
export const QUEUE_DONE_LIMIT = 20;

/** The Queue screen's read: the same queue with a finished list. Under `["action-queue"]`. ADR 163. */
export const actionQueueListQuery = queryOptions({
  queryKey: ["action-queue", "list"],
  queryFn: () => api.getActionQueue(QUEUE_DONE_LIMIT),
  refetchInterval: POLL_MS,
});

/** `POST /api/actions/<id>/cancel`. The queue and the board refetch, settled either way. */
export const cancelActionMutation = (qc: QueryClient) =>
  mutationOptions({
    mutationKey: ["queue", "cancel"],
    mutationFn: (actionId: string) => api.cancelAction(actionId),
    onSettled: () =>
      Promise.all(
        [["action-queue"], ["tasks"]].map((queryKey) => qc.invalidateQueries({ queryKey })),
      ),
  });

/**
 * The thread list for the chat dock's selector (ADR 184). Polled like the other
 * lists, so a thread the board service opens, or a segment a model change
 * appends, shows without a reload. The log itself is not a query: it is read
 * page by page from a cursor, in `components/console/use-thread-events.ts`.
 */
export const threadsQuery = queryOptions({
  queryKey: ["threads"],
  queryFn: () => api.listThreads(),
  refetchInterval: POLL_MS,
});

/** `POST /api/threads/<id>/turns` (ADR 190). The queue refetches, settled either way, so the queued turn is drawn at once. */
export const sendTurnMutation = (qc: QueryClient) =>
  mutationOptions({
    mutationKey: ["threads", "turn"],
    mutationFn: ({ threadId, text }: { threadId: string; text: string }) =>
      api.sendTurn(threadId, text),
    onSettled: () => qc.invalidateQueries({ queryKey: ["action-queue"] }),
  });

export const actionBackendQuery = queryOptions({
  queryKey: ["action-backend"],
  queryFn: () => api.pingActionBackend(),
  refetchInterval: POLL_MS * 4,
});

export const sessionsQuery = queryOptions({
  queryKey: ["sessions"],
  queryFn: () => api.listSessions(),
  refetchInterval: POLL_MS * 2,
});

export const tokensQuery = queryOptions({
  queryKey: ["tokens"],
  queryFn: () => api.listTokens(),
  refetchInterval: POLL_MS,
});

export const approvalsQuery = queryOptions({
  queryKey: ["approvals"],
  queryFn: () => api.listApprovals(),
  refetchInterval: POLL_MS,
});

/**
 * The config writer's settings table, read once per visit and never polled
 * (`docs/decisions.md` ADR 113 part 2). The plan's *Stale* state is answered by
 * the refetch after every save or reset (ADR 114), and by the writer's revision
 * check, not by a timer: a page that rewrote itself every few seconds would
 * move a row out from under the control an operator is about to use.
 */
export const settingsQuery = queryOptions({
  queryKey: ["settings"],
  queryFn: () => api.getSettings(),
});
