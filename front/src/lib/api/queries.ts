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

export const modelOptionsQuery = queryOptions({
  queryKey: ["model-options"],
  queryFn: () => api.listModelOptions(),
});

export const roleModelsQuery = queryOptions({
  queryKey: ["role-models"],
  queryFn: () => api.listRoleModels(),
  refetchInterval: POLL_MS * 4,
});

export const backlogQuery = queryOptions({
  queryKey: ["backlog"],
  queryFn: () => api.listBacklog(),
  refetchInterval: POLL_MS * 2,
});
