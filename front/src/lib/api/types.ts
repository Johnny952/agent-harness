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

/**
 * A gate note as the approvals surface draws one.
 *
 * Not a phase's: `dispatcher/gates.py` renders its output as prose into the task
 * file body under a `**Dispatcher gates**` heading, which `/api/tasks/<task_id>`
 * serves as `body` and the detail screen already renders. There is no structured
 * gate record for `/api/phases` to serve, so this type describes the approvals
 * surface alone now (`docs/decisions.md` ADR 27).
 */
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

/**
 * What a phase returned, as the role wrote it and the dispatcher saved it.
 *
 * Every field is optional because the key set belongs to the *role*: the schema
 * is `dispatcher/handoff.py:schema_for` over `_PROPERTIES` and `_ROLE_EXTRAS`, so
 * the arquitecto and the auditor carry nine keys, the implementador adds
 * `resolved_debt`, and the revisor adds `debt_rulings` and `verdict`. The api
 * serves the payload whole and holds no copy of that schema, so a key this
 * interface does not name is not an error — it is a role this console has not
 * been taught, and a renderer guards every list with `?? []` rather than
 * assuming one. `docs/decisions.md` ADR 27.
 *
 * This replaces `HandoffEnvelope`, which was a letter — a from, a to and a prose
 * summary. The harness's handoff is a structured report and the recipient is
 * named nowhere in it; ADR 27's table says where each of those five fields went.
 */
export interface HandoffPayload {
  /** `complete | partial | blocked`, which `dispatcher/handoff.py` validates. */
  status?: "complete" | "partial" | "blocked" | string;
  /** `APPROVED` or `CHANGES_REQUESTED`, on a revisor's return only. */
  verdict?: string;
  changed?: string[];
  verified?: string[];
  pending?: string[];
  risks?: string[];
  /** Inbox filenames as the role wrote them, not ids: the join needs `/api/learnings`. */
  learnings?: string[];
  paths?: { path: string; holds: string }[];
  subagents?: { id: string; doing: string }[];
  debt?: unknown[];
  debt_rulings?: unknown[];
  resolved_debt?: string[];
}

/**
 * The six keys `/api/phases` answers: one row per handoff file on disk.
 *
 * A phase row *is* that file — `<hive_tasks_dir>/<task_id>/handoffs/<role>.json`,
 * one per role, overwritten each round — so `id` is `<task_id>:<role>` and there
 * is no history of earlier rounds to show.
 *
 * Thirteen fields of the fixture's `Phase` are gone and the reason is not that
 * the api is lazy. `started_at`, `duration_s`, `account`, `model`,
 * `shrink_retry`, `write_rejected` and `commit_sha` are recorded nowhere in
 * `.hive/`: the dispatcher knows them while it runs a phase and persists only
 * `saved_at`, which is when the phase **ended** — never a start, so no screen may
 * label it one or difference two of them into a duration. `bytes_used` and
 * `worktree_path` are the same word for a different object in the harness, and
 * `bytes_budget` is a denominator whose numerator is not served. `gate_findings`
 * is prose in the task body, on the detail route. `learning_ids` travels as
 * `handoff.learnings` and the join still waits for `/api/learnings`.
 * `docs/decisions.md` ADR 27 has the whole table, field by field, and a later
 * task that wants one of them reaches it before reaching for the api.
 *
 * `role` is typed `Role` for the five this harness runs, and a renderer still
 * tolerates a string outside them: the api serves what the filename and the
 * envelope say, and validates neither.
 */
export interface Phase {
  id: string;
  task_id: string;
  role: Role;
  /** The envelope's `round`, which is what the fixture called `revision_round`. */
  round: number | null;
  /** When the phase **ended**. ISO-8601 UTC. */
  saved_at: string;
  /** `null` is a real answer: the phase ran and left no parseable return. */
  handoff: HandoffPayload | null;
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
