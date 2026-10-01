export const ROLES = [
  "cartografo",
  "arquitecto",
  "implementador",
  "revisor",
  "auditor",
] as const;

export type Role = (typeof ROLES)[number];

export type TaskStatus = "queued" | "blocked" | "done" | `in_progress:${Role}`;

export interface DebtEntry {
  id: string;
  what: string;
  where: string;
  fix: string;
  card: string;
  state: "declared" | "accepted" | "rejected" | "blocking";
  task_id: string;
}

export interface GateFinding {
  gate: "tests-in-diff" | "tests-run" | "contract-docs" | "pointers";
  level: "note" | "warning" | "blocking";
  detail: string;
}

export interface LearningEntry {
  id: string;
  scope: "project" | "harness";
  status: "confirmed" | "unconfirmed" | "refuted";
  trigger: string;
  body: string;
  retired: boolean;
}

export interface HandoffEnvelope {
  from_role: Role;
  to_role: Role | null;
  summary: string;
  artifacts: string[];
  open_questions: string[];
}

export interface Phase {
  id: string;
  task_id: string;
  role: Role;
  account: string;
  model: string;
  started_at: string;
  duration_s: number | null;
  bytes_used: number;
  bytes_budget: number;
  shrink_retry: boolean;
  write_rejected: boolean;
  envelope: HandoffEnvelope | null;
  gate_findings: GateFinding[];
  commit_sha: string | null;
  worktree_path: string | null;
  revision_round: number | null;
  learning_ids: string[];
}

export interface Task {
  task_id: string;
  status: TaskStatus;
  owner: string | null;
  depends_on: string[];
  heartbeat: string | null;
  description: string;
  body: string;
  debt: DebtEntry[];
}

export interface Account {
  name: string;
  container: string;
  state: "IDLE" | "BUSY" | "PRE_COOLDOWN" | "COOLING_DOWN";
  usage_pct: number;
  rate_limited_at: string | null;
  current_task_id: string | null;
  is_primary: boolean;
  rank: number;
  heartbeat: string | null;
}

export interface HookEvent {
  id: number;
  source_app: string;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export type ActionVerb =
  | "run-task"
  | "run-phase"
  | "merge-task"
  | "cleanup-task"
  | "release-account"
  | "bootstrap-project";

export interface QueuedAction {
  id: string;
  verb: ActionVerb;
  args: string[];
  enqueued_by: "operator" | "agent";
  state: "queued" | "running" | "succeeded" | "failed";
  enqueued_at: string;
  output_tail: string | null;
}

export interface ChatToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  result_summary: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  tool_calls: ChatToolCall[];
}

export interface ChatThread {
  id: string;
  title: string;
  updated_at: string;
  context_used: number;
  context_limit: number;
  messages: ChatMessage[];
}

export const USAGE_THRESHOLD = 90;
export const PRIMARY_RESERVE = 60;
export const COOLDOWN_S = 1800;
export const HEARTBEAT_STALE_S = 120;
export const LEARNING_TABLE_CAP = 40;
