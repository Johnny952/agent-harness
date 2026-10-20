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
