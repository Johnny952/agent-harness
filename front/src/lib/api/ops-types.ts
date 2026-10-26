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
