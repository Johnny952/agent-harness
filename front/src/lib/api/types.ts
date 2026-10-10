export const ROLES = ["cartografo", "arquitecto", "implementador", "revisor", "auditor"] as const;

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

/**
 * The ten keys `/api/learnings` answers: one row per trap entry in the hive.
 *
 * An entry *is* a file — `<hive>/learnings/{inbox,harness}/<slug>.md`, written by
 * the phase that hit the trap — so `ref` is the identity, the same pointer every
 * prompt, every `learnings` CLI verb and every `refutes:` line already cites.
 *
 * The fixture's `id`, `trigger`, `body` and `retired` are gone. `id` was a
 * surrogate where the harness has a pointer; `trigger` and `body` were its words
 * for `when` and `rule`; and `retired` was a second axis beside `status` that
 * nothing on disk records. The entry body — its Symptom, its Why, its Evidence —
 * is not served at all: `rule` is the one line the dispatcher's own table shows,
 * and the file the `ref` names is the document. `docs/decisions.md` ADR 41 has
 * the table field by field, and ADR 42 the console's half.
 *
 * `scope` and `status` are `str()`-derived on the api side and so may hold any
 * string: a value outside the ones below renders verbatim rather than keying a
 * tone table, the way `PhaseRow` renders an unknown role.
 */
export interface LearningEntry {
  /** `inbox/<slug>.md` or `harness/<slug>.md` — the identity, and which side of the human review it is on. */
  ref: string;
  /** The task that found the trap. */
  task: string;
  /** The task whose auditor is due to file it; `learnings.droppable`'s own relation. */
  carried_by: string;
  /** One trap in one codebase, or one in what every project shares. */
  scope: "project" | "harness" | string;
  status: "confirmed" | "unconfirmed" | "refuted" | string;
  /** The trigger line: when this applies, as a condition the next agent can check. */
  when: string;
  /** The first non-blank line of `## Rule`, falling back to `when`. Empty if the entry carries neither. */
  rule: string;
  /** Written under a permission surface this harness no longer has. Served, never computed. */
  stale: boolean;
  /** Whether this row reaches a running phase right now — `handed(eligible(…))`, not an index into any sort. */
  in_phase_table: boolean;
  /** `learnings.MAX_ROWS`, the cap the field above is measured against, repeated per row (ADR 20's shape). */
  phase_table_cap: number;
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
  /**
   * Proposed learnings as the role wrote them: free prose, one sentence each, not
   * refs. `dispatcher/handoff.py`'s schema asks for "something true of this
   * project that the next task would want to know", and the files on disk answer
   * in sentences — some happen to quote a ref inside one and most do not. There
   * is no key here to join against `/api/learnings`, and there never was one
   * (`docs/decisions.md` ADR 42).
   */
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
 * the api is lazy. `duration_s`, `account`, `model`, `shrink_retry`,
 * `write_rejected` and `commit_sha` are recorded nowhere in `.hive/`: the
 * dispatcher knows them while it runs a phase and persists none of them.
 * `started_at`, `bytes_used` and `worktree_path` are the same word for a
 * different object in the harness — the one stamp on disk is `saved_at`, which
 * is when the phase **ended**, so no screen may label it a start or difference
 * two of them into a duration — and `bytes_budget` is a denominator whose
 * numerator is not served. `gate_findings` is prose in the task body, on the
 * detail route. `learning_ids` has no successor at all: `handoff.learnings` is
 * prose and not refs, so there is no join to make — the task detail joins
 * `/api/learnings` on `task` and `carried_by` instead (ADR 42).
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
  /**
   * When the phase **ended**. ISO-8601 UTC — but served verbatim out of the
   * file and validated nowhere, so a missing or hand-edited stamp arrives as
   * `null` or as a string no `Date` parses. `formatClock` and `agoSeconds`
   * both answer that with a sentinel; a new reader of this field owes the
   * same.
   */
  saved_at: string | null;
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
 * The ceiling one probe's week was held to, as the dispatcher's gate recorded it
 * (ADR 49). `paced` is a primary under ADR 48's ramp; otherwise `pct` is the one
 * configured threshold and the other three are `null`. A paced week that fell
 * back to `reserve_pct` keeps `paced: true` and says why in `fallback_reason`.
 */
export interface ProbeWeekCeiling {
  paced: boolean;
  pct: number;
  days_left: number | null;
  /** ISO 8601, the reset the ceiling was paced against. */
  reset: string | null;
  fallback_reason: string | null;
}

/** The last `/usage` probe of one account and what it was held to (ADR 49). */
export interface LastProbe {
  /** Epoch seconds, the unit the api serves `rate_limited_at` in. */
  probed_at: number;
  session_pct: number;
  week_pct: number;
  /** The CLI's raw reset clauses, `null` where the line carried none. */
  session_reset: string | null;
  week_reset: string | null;
  exceeds: boolean;
  session_ceiling_pct: number;
  week_ceiling: ProbeWeekCeiling;
}

/**
 * The ten keys `/api/accounts` answers, under the console's own name for one of
 * them: the api serves `current_task` and `client.ts` maps it (ADR 17).
 *
 * `rank` is gone and `heartbeat` with it — nothing ranks accounts, and an
 * account's heartbeat is the heartbeat of the task it is running, which is a join
 * across two routes (ADR 17, ADR 18). `usage_pct` is not a field: the probe's
 * percentages are served stamped, inside `last_probe` (ADR 49, narrowing ADR 18).
 * The three thresholds are per-row because the envelope has no slot beside `data`
 * for a pool-wide fact (ADR 20).
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
  /**
   * `null` when the account was never probed or its record is unreadable (the
   * latter with a warning). Optional only because the mock fixtures predate it.
   */
  last_probe?: LastProbe | null;
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
// `LEARNING_TABLE_CAP` went the same way, by ADR 42. ADR 18 kept it as "the
// console's own layout decision and nobody else's", which was true of a console
// with no route: a number of rows to draw. It is not a layout number — it is
// `dispatcher/learnings.py:MAX_ROWS`, the cap on the table a phase's prompt
// carries, and the Learnings screen's whole subject is which rows get there. It
// arrives per row now as `phase_table_cap`.
