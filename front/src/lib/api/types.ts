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
 * The five statuses the dispatcher writes into a task file. Four come from
 * `dispatcher/context_transfer.py`; there is no `queued` and `in_progress` carries
 * no role: `docs/decisions.md` ADR 26. The fifth, `conflict`, is
 * `CONFLICT_STATUS` in `dispatcher/dispatcher.py`: the pre-merge check stopped a
 * merge, and the operator resolves it (`docs/plans/autonomy.md` *Decisions*, 2).
 * The api serves what the file says, so a status outside these five is possible
 * and the screens render it verbatim rather than dropping the row.
 */
export type TaskStatus =
  "pending" | "in_progress" | "blocked" | "conflict" | "done" | BacklogStatus | "epic";

/**
 * The three statuses a backlog item holds before it is on the board
 * (`dispatcher/tasks.py:BACKLOG_STATUSES`, ADR 129): `proposed` waiting for the
 * operator, `deferred` set aside, `dropped` final. `epic` is a split task's
 * status once accepted, and an epic closes to `done` or `dropped` (ADR 134).
 */
export type BacklogStatus = "proposed" | "deferred" | "dropped";

/** The three priority buckets (`dispatcher/tasks.py:PRIORITIES`, ADR 130). */
export type TaskPriority = "high" | "normal" | "low";

/** Who proposed a task (`dispatcher/tasks.py:SOURCES`, ADR 129 and ADR 131). */
export type TaskSource = "operator" | "arquitecto" | "debt";

/**
 * A debt index row's `impact`, as `dispatcher/debt.py:impact_of` reads it: one
 * of the three the auditor writes, or `none` for a typo or a missing column.
 */
export type DebtImpact = "risk" | "cost" | "cosmetic" | "none";

export interface DebtEntry {
  id: string;
  what: string;
  where: string;
  fix: string;
  /**
   * The index's `card` column (`docs/debt/README.md`), not a task id and not
   * a Kanban board card id. Written `none` since ADR 59: nothing mirrors a row.
   */
  card: string;
  /**
   * The auditor's judgement of the entry (ADR 131), which sets the priority its
   * backlog item is filed at. `none` when the index has no impact column.
   */
  impact: DebtImpact;
  /** Best-effort, from a `**resolved` prefix on the **what** cell. ADR 17. */
  resolved: boolean;
  /** Split out of `id` in `client.ts` — `T-011-D1` is task `T-011`. ADR 17. */
  task_id: string | null;
}

/**
 * One task as `GET /api/roadmap` serves it, inside a plan or under `no_plan`.
 * `observability/api/app.py`, T-072.
 */
export interface RoadmapTask {
  id: string;
  /** Always `null` today: neither a task file nor its description carries one. */
  title: string | null;
  /**
   * The task's status, verbatim. A string rather than `TaskStatus` because the
   * api passes the file through; a merge is `merged_at`, not a status.
   */
  state: string;
  /**
   * `merged.at` from the task file, as served. `null` when the task is not
   * merged, and also for a merged task whose block has no date: every task under
   * `no_plan.past` is merged, whatever this says.
   */
  merged_at: string | null;
}

/** One plan in `docs/plans/`, with the tasks that name it. */
export interface RoadmapPlan {
  /** The plan's filename, which is also its key. */
  file: string;
  title: string | null;
  /**
   * The frontmatter `status`: `idea`, `design`, `approved`, `building`, `built`,
   * `superseded` or `dropped`, or `null` when the plan carries none. The api
   * already put the plan in the band its status belongs to.
   */
  status: string | null;
  approved: string | null;
  built: string | null;
  supersedes: string[];
  superseded_by: string[];
  tasks: RoadmapTask[];
}

/** The tasks of `no_plan.past` that were merged in one month. */
export interface RoadmapMonth {
  /** `YYYY-MM`, or `null` for merged tasks whose `merged.at` is missing or not ISO. */
  month: string | null;
  tasks: RoadmapTask[];
}

/**
 * `GET /api/roadmap`'s `data`: three bands of plans, and the tasks no plan
 * names, split into unmerged (`now`) and merged by month (`past`). Each band is
 * newest first by `built`, else `approved`, undated last; months newest first,
 * the undated group last.
 */
export interface Roadmap {
  project: string;
  bands: { future: RoadmapPlan[]; now: RoadmapPlan[]; past: RoadmapPlan[] };
  no_plan: { now: RoadmapTask[]; past: RoadmapMonth[] };
}

/**
 * The four states `GET /api/command-requests` puts a request in:
 * `COMMAND_REQUEST_STATES` in `observability/api/app.py` (ADR 122), pending
 * first as the api orders them. `?state=` takes one of these and nothing else.
 */
export const COMMAND_REQUEST_STATES = ["pending", "awaiting_resume", "resumed", "denied"] as const;

export type CommandRequestState = (typeof COMMAND_REQUEST_STATES)[number];

/** Where the api resolved a request to run. `service` is null for the agent's own container. */
export interface CommandRequestTarget {
  kind: string | null;
  container: string | null;
  service: string | null;
  cwd: string | null;
}

/**
 * The run of a request, as the api names it: no output is served. `log` is the
 * run's log by its path under the task's scratch dir (`commands/<n>.log`), and
 * the card shows the path, never the text.
 */
export interface CommandRequestRan {
  exit_code: number | null;
  timed_out: boolean;
  truncated: boolean;
  log: string | null;
}

/** A verdict as `command-verdict` wrote it. `by` is null when it looked like an email. */
export interface CommandRequestVerdict {
  verdict: "run" | "allow" | "deny" | string;
  by: string | null;
  at: string | null;
  comment: string | null;
  allowed: string | null;
  resumed_at: string | null;
  ran: CommandRequestRan | null;
}

/**
 * The CLI lines that move a request on, each null where it does not apply:
 * run, deny and (for the agent's own container) allow while pending, resume
 * while awaiting it. Verdicts are given from the CLI; the card only shows these.
 */
export interface CommandRequestCommands {
  run: string | null;
  allow: string | null;
  deny: string | null;
  resume: string | null;
}

/**
 * One filed request. `argv` and `target` are null only when the request's
 * envelope is gone; `agent_reason` is the agent's own words and nothing vouches
 * for it. `docs/implementations/T-078.md`.
 */
export interface CommandRequest {
  task_id: string;
  project: string | null;
  title: string | null;
  role: string;
  filed_at: string | null;
  n: number | null;
  argv: string[] | null;
  target: CommandRequestTarget | null;
  agent_reason: string | null;
  state: CommandRequestState | string;
  verdict: CommandRequestVerdict | null;
  commands: CommandRequestCommands;
}

/** A request the check refused at filing: no number, nothing to run, only why. */
export interface RefusedCommandRequest {
  task_id: string;
  project: string | null;
  title: string | null;
  role: string;
  filed_at: string | null;
  argv: string[] | null;
  target: { kind: string | null; service: string | null };
  agent_reason: string | null;
  refused_reason: string;
}

/** `GET /api/command-requests`'s `data`. */
export interface CommandRequests {
  requests: CommandRequest[];
  refused: RefusedCommandRequest[];
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

/**
 * One `claude` call of a task, as `/api/tasks/<id>/usage` serves it
 * (`docs/decisions.md` ADR 53, ADR 54, ADR 64). An annotation of what a well-formed
 * log line holds, guarded at the render rather than trusted
 * (`docs/learnings/a-console-type-over-a-served-value-is-an-annotation.md`).
 * `total_cost_usd` is the session's cost *so far* and is never a call's own.
 */
export interface UsageLine {
  task_id: string;
  role: string;
  round: number | null;
  account: string;
  call: string;
  session_id: string | null;
  recorded_at: string;
  measured: boolean;
  raw_keys: string[];
  is_error: boolean | null;
  num_turns: number | null;
  duration_ms: number | null;
  total_cost_usd: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cache_read_input_tokens: number | null;
  cache_creation_input_tokens: number | null;
  /** ADR 68: what `--model` asked for; `null` = none passed (CLI default). Missing on lines written before ADR 68. */
  model?: string | null;
  /** ADR 68: sorted `modelUsage` key names; `null` = CLI reported none, `[]` = an empty `modelUsage`. Missing before ADR 68. */
  models_used?: string[] | null;
  /** ADR 116: what the CLI refused during the call; `null` = not reported, `[]` = none. Missing on lines written before ADR 116. */
  permission_denials?: PermissionDenial[] | null;
  /** ADR 116: how many denials past the first 50 were counted and not kept. Present only when some were. */
  permission_denials_dropped?: number;
}

/**
 * One refused tool call as the usage log keeps it (`docs/decisions.md` ADR 116):
 * three keys, every string in `tool_input` cut to 500 characters, or a
 * `{ malformed: <type name> }` for an entry the CLI printed in a shape the
 * dispatcher did not recognise.
 */
export type PermissionDenial =
  { tool_name: string; tool_use_id: string | null; tool_input: unknown } | { malformed: string };

/** One dispatch attempt of one role: the api's group block (ADR 54), same caveat. */
export interface UsageGroup {
  task_id: string;
  role: string;
  round: number | null;
  account: string;
  calls: UsageLine[];
  call_count: number;
  call_kinds: string[];
  measured_calls: number;
  unmeasured_calls: number;
  phase_call: UsageLine | null;
}

/**
 * The eighteen keys `/api/tasks` answers, as `observability/api/app.py:_task`
 * shapes them (ADR 133, ADR 137). `body` is not one of them — it is the detail
 * route's alone (`docs/decisions.md` ADR 21) — and neither is a debt list: that is
 * a filter over `/api/debt` by the task prefix of a debt id (ADR 17). Nor is a
 * card: the task file is the board's data and nothing mirrors it (ADR 59).
 */
export interface Task {
  task_id: string;
  status: TaskStatus;
  owner: string | null;
  depends_on: string[];
  heartbeat: string | null;
  /**
   * The text of the task's `description.md`, served verbatim. `null` when the
   * task has none: a render says so with `Absent`, never with an empty cell.
   */
  description: string | null;
  resolved_debt: string[];
  /**
   * The role of the phase running now, or `null` between phases (ADR 126). A
   * value beside `lock_expired: true` is a dead dispatcher's, not a running one.
   */
  phase: string | null;
  /** The api's judgement against its own expiry window; never re-derived here. */
  lock_expired: boolean | null;
  /**
   * The bucket, `normal` when the file names none. A bad value is served as
   * written beside a warning (ADR 133), hence the `string`.
   */
  priority: TaskPriority | string;
  /** Who proposed it; `null` on a task filed before ADR 129. Served as written. */
  source: TaskSource | string | null;
  /** The cleaned list (`tasks.normal_tags`): stripped, no repeats, [] for none. */
  tags: string[];
  /** The order inside the bucket, lower first; `null` sorts as 0. As written. */
  rank: number | null;
  /** The debt index entry a `source: debt` item came from, else `null` (ADR 131). */
  debt_id: string | null;
  /** The epic this task belongs to, as its id, else `null` (ADR 137). */
  epic: string | null;
  /**
   * The ids an epic was split into, served as written (ADR 137): `[]` on a
   * plain task, and possibly not a list of strings on a hand-edited file. Typed
   * `unknown` so no reader trusts it unchecked; a reader narrows it to its
   * string entries (`lib/backlog.ts:splitInto`).
   */
  split_into: unknown;
  /** Whether the task's work is on main (ADR 137). */
  merged: boolean;
  /** The task's display title (`tasks.display_title`), else `null` (ADR 137). */
  title: string | null;
}

/**
 * `/api/ready`'s `data`: the Ready column as task ids, in the order work should
 * be taken (`dispatcher/tasks.py:ready_order`, ADR 133).
 */
export type ReadyOrder = string[];

/**
 * One comment on a task (`dispatcher/tasks.py:add_comment`). `file`, `line` and
 * `commit` come together or not at all: a comment pinned to a place in the code.
 */
export interface TaskComment {
  /** ISO 8601, when it was written. */
  at: string;
  author: string;
  text: string;
  file?: string;
  line?: number;
  commit?: string;
}

/**
 * `data` of `GET /api/tasks/<id>/comments` (ADR 135), in the api's usual
 * envelope: the task, and its comments oldest first. `data` is `null` when the
 * comments file exists and could not be read, with the reason in `warnings`.
 */
export interface TaskComments {
  task_id: string;
  comments: TaskComment[];
}

/**
 * The board-service's four writes (ADR 136), as `board_service/app.py:TOOLS`
 * takes them. The task id is the path's and never the body's.
 */
export interface ProposeBacklogBody {
  title: string;
  description: string;
  priority?: TaskPriority;
  tags?: string[];
  depends_on?: string[];
  /** Sub-tasks for an epic; `after` names earlier entries by index. */
  tasks?: { title: string; description: string; after?: number[] }[];
}

/** `backlog_propose`'s answer: the id, whether it was new, and any sub-task ids. */
export interface ProposeBacklogResult {
  id: string;
  created: boolean;
  tasks: string[];
}

export interface EditTaskBody {
  title?: string;
  description?: string;
  depends_on?: string[];
  priority?: TaskPriority;
  tags?: string[];
  rank?: number;
}

/** `task_edit`'s answer: the fields that changed, [] for a no-op. */
export interface EditTaskResult {
  id: string;
  changed: string[];
}

export type CommentTaskBody =
  { text: string } | { text: string; file: string; line: number; commit: string };

/** `task_comment`'s answer: the comment as stored, and how many the task has. */
export interface CommentTaskResult {
  id: string;
  comment: TaskComment;
  comment_count: number;
}

export interface AcceptTaskBody {
  /** Where the item goes; `pending` (on the board) when absent. */
  to?: "pending" | "deferred" | "dropped";
  priority?: TaskPriority;
}

/** One record of the action queue, as `dispatcher/actions.py:create` writes it. ADR 160. */
export interface ActionRecord {
  version: number;
  id: string;
  kind: string;
  project: string | null;
  task_id: string | null;
  params: Record<string, unknown> | null;
  actor: string;
  note: string | null;
  created_at: string;
  claimed_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  status: string;
  /** `account` or `project` while the worker holds a pending one back; else null. */
  waiting: string | null;
  result: unknown;
  error: string | null;
}

/** `GET /api/actions` (`observability/api/app.py:action_queue`). */
export interface ActionQueue {
  pending: ActionRecord[];
  claimed: ActionRecord[];
  done: ActionRecord[];
  done_total: number;
  worker: {
    heartbeat: Record<string, unknown> | null;
    stale: boolean;
    stale_after_seconds: number;
  };
}

/** `POST /api/tasks/<id>/start`: exactly `{note}`, the forward refuses any other key (ADR 157). */
export interface StartTaskBody {
  note: string | null;
}

/**
 * `backlog_accept`'s answer. `children` is an epic's sub-tasks and where each
 * went, `{}` for a plain task; `epic_closed` is the epic a drop closed (ADR 134).
 */
export interface AcceptTaskResult {
  id: string;
  status: string;
  changed: boolean;
  children: Record<string, string>;
  epic_closed?: string;
}

/** `/api/tasks/<task_id>`: the list's eighteen keys plus the running record. ADR 21. */
export interface TaskDetail extends Task {
  body: string;
}

/** The five `dispatcher/state_machine.py` writes; `AUTH_EXPIRED` is ADR 76's. */
export type AccountState = "IDLE" | "BUSY" | "PRE_COOLDOWN" | "COOLING_DOWN" | "AUTH_EXPIRED";

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
  /** Epoch seconds, the unit the api serves `rate_limited_at` in. Read through `agoEpochSeconds`. */
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
  /**
   * When the provider last refused this account, in epoch seconds, as
   * `dispatcher/state_machine.py:record_rate_limit` stores it from `time.time()`
   * and the api serves it unchanged; `null` when it never was, or when the state
   * file would not parse. A number and not a `Date` string, so its age is
   * `agoEpochSeconds`'s and never `agoSeconds`'s, which would read it as
   * milliseconds (T-021).
   */
  rate_limited_at: number | null;
  /**
   * `null` when the account was never probed or its record is unreadable (the
   * latter with a warning). Always served, so not optional: the api answers the
   * key on every row (ADR 49), and the fixtures carry it as the route does.
   */
  last_probe: LastProbe | null;
}

export interface HookEvent {
  id: number;
  source_app: string;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

/**
 * One stretch of a thread on one account, model and effort (`docs/decisions.md`
 * ADR 170). A change of any of the three appends a segment carrying the last
 * one's `session_id`, so a conversation outlives an account change; the worker
 * sets `session_id` on the last segment when the CLI answers with a session
 * other than the one it resumed. `null` until a first turn has run.
 */
export interface ThreadSegment {
  session_id: string | null;
  account: string;
  model: string;
  effort: string;
  /** ISO time the segment began. */
  from: string;
}

/**
 * A thread as `GET /api/threads` serves it (ADR 170, ADR 177): the record the
 * board service writes, read as it is. `main` is the operator's thread, with no
 * project; every other one belongs to a project and may carry the task it was
 * opened from.
 */
export interface Thread {
  id: string;
  title: string;
  project: string | null;
  account: string;
  model: string;
  effort: string;
  segments: ThreadSegment[];
  created: string;
  status: "open" | "archived";
  task: string | null;
}

/**
 * One line of a thread's conversation log, as `GET /api/threads/<id>/events`
 * serves it (ADR 180): `n` counts lines from 1 and is the cursor `since` takes;
 * `event` is the parsed line, or `null` for a line that is not a JSON object
 * (which the api also names in a warning). The events are the worker's own
 * `turn_start` / `turn_end` / `unparsed` lines and the CLI's stream-json events
 * between them, so they stay a loose record here and `lib/thread.ts` reads them.
 */
export interface ThreadEventRow {
  n: number;
  event: Record<string, unknown> | null;
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

/**
 * One key of the config writer's settings table, as `GET /settings` serves it
 * (`config_writer/app.py:payload`, `docs/decisions.md` ADR 110 part 6).
 *
 * `class` is `dispatcher/config.py`'s three strings — `editable`, `read-only`,
 * `credential` — typed open because the writer owns the list and a fourth would
 * still have to render. `value` is the effective value and may be any JSON type,
 * including `null` for a key nothing sets; the Settings screen renders it by type
 * (ADR 113 part 5). `source` names which layer it came from.
 */
export interface SettingRow {
  key: string;
  class: "editable" | "read-only" | "credential" | (string & {});
  kind: string | null;
  reason: string;
  value: unknown;
  source: "override" | "config.yaml" | "default" | (string & {});
}

/**
 * One line of the writer's change log: a save or a reset made through it, newest
 * first and at most twenty (`config_writer/app.py:recent_changes`). `at` is the
 * writer's UTC ISO stamp, served verbatim.
 */
export interface SettingChange {
  at: string;
  actor: string;
  via: string;
  key: string;
  op: "set" | "reset" | (string & {});
  old: unknown;
  new: unknown;
  revision_before: string;
  revision_after: string;
}

/**
 * The writer's whole settings payload (ADR 110 part 6, ADR 112, ADR 113).
 *
 * `rows` and `recent_changes` are typed `unknown` on purpose — what the writer
 * means is `SettingRow[]` and `SettingChange[]`, and the screen narrows to those
 * only after `Array.isArray`. They are built by
 * another service from a log file and a YAML file, and the screen names a list
 * that is not a list with `Malformed` rather than trusting the type into a
 * `.map` that would take the page (`docs/decisions.md` ADR 29). `error` is the
 * config's own validation error, the one that makes the dispatcher refuse every
 * verb; `revision` is what a save or a reset sends back, verbatim.
 *
 * Three of them can be `null`, and the writer means each (`config_writer/app.py`
 * `payload`): `base` is a `config.yaml` that does not read as a mapping,
 * `overrides` is an overrides file that does not parse — every write is then a
 * 422 — and `revision` is an overrides file that does not exist yet, which a
 * save sends as `null` (ADR 110).
 */
export interface Settings {
  base: Record<string, unknown> | null;
  overrides: Record<string, unknown> | null;
  revision: string | null;
  rows: unknown;
  roles: Record<string, string[]>;
  recent_changes: unknown;
  error: string | null;
}
