export const ROLES = [
  "cartografo",
  "arquitecto",
  "implementador",
  "revisor",
  "auditor",
] as const;

export type Role = (typeof ROLES)[number];

/**
 * Every 200 the read api answers, as `client.ts` hands it on: `data` unwrapped
 * into the rows a screen takes, `warnings` riding beside it so nothing the api
 * went to the trouble of saying is dropped. `docs/decisions.md` ADR 16 and
 * ADR 25 — only a read with a route behind it carries this; the rest still have
 * bare signatures and fixtures.
 */
export interface ApiResult<T> {
  data: T;
  warnings: string[];
}

/**
 * The four `dispatcher/context_transfer.py` writes into a task file. There is no
 * `queued` and `in_progress` carries no role: `docs/decisions.md` ADR 26. The api
 * serves what the file says, so a status outside these four is possible and the
 * screens render it verbatim rather than dropping the row.
 */
export type TaskStatus = "pending" | "in_progress" | "blocked" | "done";

export interface DebtEntry {
  id: string;
  what: string;
  where: string;
  fix: string;
  /** A board card id, not a task id. On a harness with no board it is `none`. */
  card: string;
  /** Best-effort, from a `**resolved` prefix on the **what** cell. ADR 17. */
  resolved: boolean;
  /** Split out of `id` in `client.ts` — `T-011-D1` is task `T-011`. ADR 17. */
  task_id: string | null;
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

/** One card as the board reports it — `dispatcher/vibe_kanban_client.py`'s. */
export interface KanbanIssue {
  issue_id: string;
  title: string;
  status: string | null;
  simple_id: string | null;
}

/**
 * The ten keys `/api/tasks` answers. `body` is not one of them — it is the detail
 * route's alone (`docs/decisions.md` ADR 21) — and neither is a debt list: that is
 * a filter over `/api/debt` by the task prefix of a debt id (ADR 17).
 */
export interface Task {
  task_id: string;
  status: TaskStatus;
  owner: string | null;
  depends_on: string[];
  heartbeat: string | null;
  description: string;
  kanban_issue_id: string | null;
  resolved_debt: string[];
  card: KanbanIssue | null;
  /** The api's judgement against its own expiry window; never re-derived here. */
  lock_expired: boolean | null;
}

/** `/api/tasks/<task_id>`: the list's ten keys plus the running record. ADR 21. */
export interface TaskDetail extends Task {
  body: string;
}

/** The four `dispatcher/state_machine.py` writes. */
export type AccountState = "IDLE" | "BUSY" | "PRE_COOLDOWN" | "COOLING_DOWN";

/**
 * The nine keys `/api/accounts` answers, under the console's own name for one of
 * them: the api serves `current_task` and `client.ts` maps it (ADR 17).
 *
 * `usage_pct` and `rank` are gone and `heartbeat` with them — nothing persists
 * usage, nothing ranks accounts, and an account's heartbeat is the heartbeat of
 * the task it is running, which is a join across two routes (ADR 17, ADR 18). The
 * three thresholds are per-row because the envelope has no slot beside `data` for
 * a pool-wide fact (ADR 20).
 */
export interface Account {
  name: string;
  container: string;
  is_primary: boolean;
  quota_threshold_pct: number;
  reserve_pct: number;
  quota_cooldown_seconds: number;
  /** `null` when the state file would not parse; the row stays, with a warning. */
  state: AccountState | null;
  current_task_id: string | null;
  rate_limited_at: string | null;
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

// `USAGE_THRESHOLD`, `PRIMARY_RESERVE` and `COOLDOWN_S` were this harness's
// configuration copied as literals, and are read per account row now as
// `quota_threshold_pct`, `reserve_pct` and `quota_cooldown_seconds`:
// `docs/decisions.md` ADR 18, with ADR 20 for the shape. `HEARTBEAT_STALE_S` is
// deleted rather than served — the api already applies its own expiry window and
// answers `lock_expired`. Raising a threshold is one edit in `config.yaml` now,
// and the console follows without a rebuild.
//
// `LEARNING_TABLE_CAP` stays: it is the console's own layout decision and nobody
// else's.
export const LEARNING_TABLE_CAP = 40;
