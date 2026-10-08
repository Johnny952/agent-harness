import type { Role } from "./types";

export type LogLevel = "debug" | "info" | "warn" | "error";

export interface SessionLogLine {
  seq: number;
  container: string;
  task_id: string | null;
  role: Role | null;
  level: LogLevel;
  ts: string;
  text: string;
}

export interface SessionInfo {
  container: string;
  account: string;
  task_id: string | null;
  role: Role | null;
  live: boolean;
}

export type TokenState = "valid" | "expiring" | "expired" | "revoked" | "reauthenticating";

export interface ContainerToken {
  container: string;
  account: string;
  state: TokenState;
  expires_at: string | null;
  last_refreshed_at: string | null;
  last_error: string | null;
  /** Present only while a device-code login is pending. */
  device_code: { user_code: string; verification_url: string; expires_at: string } | null;
}

export type ApprovalKind =
  "push" | "merge-task" | "cleanup-task" | "tool-permission" | "budget-override";
export type ApprovalState = "pending" | "approved" | "rejected";

export interface Approval {
  id: string;
  kind: ApprovalKind;
  task_id: string | null;
  requested_by: string;
  requested_at: string;
  summary: string;
  state: ApprovalState;
  decided_at: string | null;
  /** push only */
  push: {
    branch: string;
    base: string;
    commits: { sha: string; message: string }[];
    diff: string;
    gate_worst: "note" | "warning" | "blocking" | null;
  } | null;
}

export interface ModelOption {
  id: string;
  label: string;
  context_k: number;
}

export interface RoleModelConfig {
  role: Role;
  model: string;
  updated_at: string | null;
}

export type BacklogKind = "task" | "epic";
export type BacklogCategory =
  "harness" | "pool" | "gates" | "learnings" | "debt" | "docs" | "infra";

export interface BacklogItem {
  id: string;
  kind: BacklogKind;
  title: string;
  category: BacklogCategory;
  /** 1 = highest. Ordering on the screen follows this number. */
  priority: number;
  created_at: string;
  completed: boolean;
  completed_at: string | null;
  /** Set when the item is (or was) worked as a harness task. */
  task_id: string | null;
  notes: string | null;
}
