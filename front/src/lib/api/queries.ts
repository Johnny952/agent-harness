import { queryOptions } from "@tanstack/react-query";
import * as api from "./client";

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

export const actionsQuery = queryOptions({
  queryKey: ["actions"],
  queryFn: () => api.listActions(),
  refetchInterval: POLL_MS,
});

export const threadsQuery = queryOptions({
  queryKey: ["threads"],
  queryFn: () => api.listThreads(),
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

export const backlogQuery = queryOptions({
  queryKey: ["backlog"],
  queryFn: () => api.listBacklog(),
  refetchInterval: POLL_MS * 2,
});
