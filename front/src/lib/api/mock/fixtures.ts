import type {
  Account,
  ChatThread,
  DebtEntry,
  HookEvent,
  LastProbe,
  LearningEntry,
  Phase,
  QueuedAction,
  Task,
} from "../types";

/**
 * **Seven of these back nothing.** `mockTasks`, `mockAccounts`, `mockDebt`,
 * `mockEvents` and `nextMockEvent` stopped being the source of a screen when
 * T-012 wired `listTasks`, `getTask`, `listAccounts`, `listEvents` and `listDebt`
 * against `observability/api/`, `mockPhases` when T-013 wired `listPhases`
 * against `/api/phases`, and `mockLearnings` when T-016 wired `listLearnings`
 * against `/api/learnings`. They are kept rather than deleted and kept conforming
 * to the *served* shapes — `docs/decisions.md` ADR 25 — so a typecheck still
 * catches a type that drifts from the route, and so a later task has a fixture it
 * can trust the shape of.
 *
 * `mockActions` and `mockThreads` still back `listActions` and `listThreads`,
 * whose routes are tier 3 of
 * `docs/plans/front.md`, and they carry no `warnings`: ADR 25 says a fixture for a
 * route with no producer cannot rehearse an envelope.
 */

/** Fixtures are anchored to "now" so heartbeat freshness is realistic. */
const now = () => Date.now();
const iso = (secondsAgo: number) => new Date(now() - secondsAgo * 1000).toISOString();

export function mockDebt(): DebtEntry[] {
  // Ids take the `T-0NN-Dn` shape the harness writes, because `task_id` is a split
  // of the id and not a column (ADR 17), and `card` is a *board* card id — `none`
  // on a harness with no board configured, which is this one.
  return [
    {
      id: "T-008-D1",
      what: "Retry loop swallows provider 429 body",
      where: "harness/pool/client.py, the retry branch",
      fix: "Surface the provider payload into the event stream before retrying",
      card: "none",
      resolved: false,
      task_id: "T-008",
    },
    {
      id: "T-004-D1",
      what: "Worktree cleanup leaves stale lock files",
      where: "harness/worktree.py, the abandon path",
      fix: "Unlink .lock in the finally branch",
      card: "none",
      resolved: true,
      task_id: "T-004",
    },
    {
      id: "T-011-D1",
      what: "Phase byte budget hardcoded per role",
      where: "harness/phases/budget.py, every caller of the constants",
      fix: "Move budgets into config with per-role override",
      card: "none",
      resolved: false,
      task_id: "T-011",
    },
    {
      id: "T-011-D2",
      what: "Revisor writes rejected silently",
      where: "harness/phases/revisor.py, the write guard",
      fix: "Emit a write_rejected event instead of dropping",
      card: "none",
      resolved: false,
      task_id: "T-011",
    },
    {
      id: "T-002-D1",
      what: "Event ids reused after restart",
      where: "hooks/store.py, the id counter",
      fix: "Persist the monotonic counter",
      card: "none",
      resolved: true,
      task_id: "T-002",
    },
  ];
}

export function mockTasks(): Task[] {
  // The served four statuses, with no role in any of them (ADR 26), and no `body`
  // or `debt[]`: the body is the detail route's (ADR 21) and the debt list is a
  // filter over /api/debt (ADR 17). `lock_expired` is the api's judgement, which is
  // why a stale heartbeat here carries `true` rather than leaving it to be derived.
  return [
    {
      task_id: "T-002",
      status: "done",
      owner: "cuenta1",
      heartbeat: null,
      depends_on: [],
      description: "Persist hook event ids across harness restarts",
      kanban_issue_id: null,
      resolved_debt: [],
      card: null,
      lock_expired: null,
    },
    {
      task_id: "T-004",
      status: "done",
      owner: "cuenta3",
      heartbeat: null,
      depends_on: ["T-002"],
      description: "Clean up worktrees when a task is abandoned",
      kanban_issue_id: null,
      resolved_debt: ["T-004-D1"],
      card: null,
      lock_expired: null,
    },
    {
      task_id: "T-007",
      status: "pending",
      owner: null,
      heartbeat: null,
      depends_on: [],
      description: "Add a dry-run flag to bootstrap-project",
      kanban_issue_id: null,
      resolved_debt: [],
      card: null,
      lock_expired: null,
    },
    {
      task_id: "T-008",
      status: "blocked",
      owner: "cuenta2",
      heartbeat: iso(940),
      depends_on: ["T-004", "T-011"],
      description: "Surface provider rate-limit payloads into the event stream",
      kanban_issue_id: null,
      resolved_debt: [],
      card: null,
      lock_expired: true,
    },
    {
      task_id: "T-011",
      status: "in_progress",
      owner: "cuenta1",
      heartbeat: iso(41),
      depends_on: ["T-002"],
      description: "Per-role byte budgets moved into config",
      kanban_issue_id: null,
      resolved_debt: [],
      card: null,
      lock_expired: false,
    },
    {
      task_id: "T-012",
      status: "in_progress",
      owner: "cuenta4",
      heartbeat: iso(17),
      depends_on: [],
      description: "Pool priority: workers before primary",
      kanban_issue_id: null,
      resolved_debt: [],
      card: null,
      lock_expired: false,
    },
    {
      task_id: "T-013",
      status: "in_progress",
      owner: "cuenta5",
      heartbeat: iso(6),
      depends_on: ["T-012"],
      description: "Re-probe parked accounts every 60s",
      kanban_issue_id: null,
      resolved_debt: [],
      card: null,
      lock_expired: false,
    },
    {
      task_id: "T-014",
      status: "in_progress",
      owner: "cuenta3",
      heartbeat: iso(312),
      depends_on: [],
      description: "Gate: fail the phase when tests-in-diff is empty",
      kanban_issue_id: null,
      resolved_debt: [],
      card: null,
      lock_expired: true,
    },
    {
      task_id: "T-015",
      status: "pending",
      owner: null,
      heartbeat: null,
      depends_on: ["T-008"],
      description: "Operator console: live tail transport hardening",
      kanban_issue_id: null,
      resolved_debt: [],
      card: null,
      lock_expired: null,
    },
  ];
}

/**
 * Backs nothing since T-013 wired `listPhases`, and narrowed to the six keys
 * `/api/phases` actually answers rather than deleted — ADR 25's rule for the five
 * fixtures T-012 retired, applied to the sixth. An `id` is `<task_id>:<role>`
 * because that is the file, `saved_at` is when the phase **ended**, and the
 * payload's key set is the role's own (`docs/decisions.md` ADR 27).
 */
export function mockPhases(): Phase[] {
  return [
    {
      id: "T-011:arquitecto",
      task_id: "T-011",
      role: "arquitecto",
      round: null,
      saved_at: iso(4900),
      handoff: {
        status: "complete",
        changed: ["docs/contracts/budgets.md: the per-role budget block"],
        verified: ["python3 -m pytest: 1173 passed"],
        pending: ["The loader itself, and the four call sites"],
        risks: ["Code defaults kept as a fallback, which a later task may read as dead"],
        paths: [{ path: "docs/contracts/budgets.md", holds: "The key names and their defaults" }],
      },
    },
    {
      id: "T-011:implementador",
      task_id: "T-011",
      role: "implementador",
      round: 2,
      saved_at: iso(3800),
      handoff: {
        status: "partial",
        changed: ["harness/config/budgets.py: the loader"],
        verified: ["python3 -m pytest: 6 new tests"],
        pending: ["The shrink retry is logged nowhere"],
        risks: [],
        resolved_debt: ["T-008-D1"],
      },
    },
    {
      id: "T-011:revisor",
      task_id: "T-011",
      role: "revisor",
      round: 2,
      saved_at: iso(2600),
      handoff: { status: "complete", verdict: "CHANGES_REQUESTED", debt_rulings: [] },
    },
    {
      // `handoff: null` is a real answer and not a missing file: the phase ran
      // and left no parseable return. ADR 27.
      id: "T-014:auditor",
      task_id: "T-014",
      role: "auditor",
      round: null,
      saved_at: iso(400),
      handoff: null,
    },
  ];
}

/** Epoch seconds, the unit `last_probe.probed_at` is served in (ADR 49). */
const epoch = (secondsAgo: number) => Math.floor(now() / 1000) - secondsAgo;

/**
 * The three shapes a `last_probe` takes, as `dispatcher/dispatcher.py:
 * _quota_decision` writes them (ADR 49), named so a test can hold one account
 * to each without restating the record. Built per call, like every fixture
 * here, so `probed_at` is anchored to the moment it is read.
 *
 * - `pacedPrimary` — ADR 48's ramp: `reserve_pct` on the session, and the week
 *   held to the paced ceiling, 4.5 days before the reset it read. The `pct` is
 *   `quota.week_ceiling`'s for that many days, margin taken.
 * - `fallbackPrimary` — the same account on a probe whose week line carried a
 *   reset clause that would not parse: still `paced: true`, the week back on
 *   the reserve, and the reason the dispatcher logged.
 * - `worker` — one configured threshold on both windows, `paced: false`, and
 *   the three pacing fields `null`.
 */
export function mockProbes(): Record<"pacedPrimary" | "fallbackPrimary" | "worker", LastProbe> {
  return {
    pacedPrimary: {
      probed_at: epoch(240),
      session_pct: 22,
      week_pct: 31,
      session_reset: "4:59pm (UTC)",
      week_reset: "Oct 15, 4:59pm (UTC)",
      exceeds: false,
      session_ceiling_pct: 60,
      week_ceiling: {
        paced: true,
        pct: 40.416666666666664,
        days_left: 4.5,
        reset: "2026-10-15T16:59:00+00:00",
        fallback_reason: null,
      },
    },
    fallbackPrimary: {
      probed_at: epoch(90),
      session_pct: 22,
      week_pct: 31,
      session_reset: "4:59pm (UTC)",
      week_reset: "Oct 15 at 5pm",
      exceeds: false,
      session_ceiling_pct: 60,
      week_ceiling: {
        paced: true,
        pct: 60,
        days_left: null,
        reset: null,
        fallback_reason: "the reset clause 'Oct 15 at 5pm' did not parse",
      },
    },
    worker: {
      probed_at: epoch(45),
      session_pct: 47.5,
      week_pct: 63,
      session_reset: "2:10pm (UTC)",
      week_reset: "Oct 13, 9am (UTC)",
      exceeds: false,
      session_ceiling_pct: 90,
      week_ceiling: { paced: false, pct: 90, days_left: null, reset: null, fallback_reason: null },
    },
  };
}

export function mockAccounts(): Account[] {
  // No `usage_pct`, no `rank` and no `heartbeat`: nothing ranks accounts, an
  // account's lock is the lock on the task it is running (ADR 17, ADR 18), and
  // the probe's percentages arrive stamped inside `last_probe` (ADR 49). The
  // three thresholds repeat per row because the envelope has no slot beside
  // `data` for a pool-wide fact (ADR 20), and they are the defaults
  // `dispatcher/config.py` carries.
  const limits = { quota_threshold_pct: 90, reserve_pct: 60, quota_cooldown_seconds: 1800 };
  const probes = mockProbes();
  return [
    {
      name: "cuenta1",
      container: "agent-cuenta1",
      is_primary: false,
      ...limits,
      state: "BUSY",
      current_task_id: "T-011",
      rate_limited_at: null,
      last_probe: probes.worker,
    },
    {
      name: "cuenta2",
      container: "agent-cuenta2",
      is_primary: false,
      ...limits,
      state: "COOLING_DOWN",
      current_task_id: null,
      rate_limited_at: iso(620),
      // Old on purpose: a refused account is not re-probed until its cooldown
      // runs out, so its record ages past the console's stale mark.
      last_probe: { ...probes.worker, probed_at: epoch(5400), session_pct: 88, week_pct: 71 },
    },
    {
      name: "cuenta3",
      container: "agent-cuenta3",
      is_primary: false,
      ...limits,
      state: "BUSY",
      current_task_id: "T-014",
      rate_limited_at: null,
      last_probe: probes.worker,
    },
    {
      name: "cuenta4",
      container: "agent-cuenta4",
      is_primary: false,
      ...limits,
      // `null` is a served value here and not an omission: the api nulls what an
      // unreadable state file says and keeps the row, with a warning — and nulls
      // `last_probe` with it, since the record lives in that same file (ADR 49).
      state: null,
      current_task_id: null,
      rate_limited_at: null,
      last_probe: null,
    },
    {
      name: "cuenta5",
      container: "agent-cuenta5",
      is_primary: false,
      ...limits,
      state: "PRE_COOLDOWN",
      current_task_id: "T-013",
      rate_limited_at: null,
      last_probe: { ...probes.worker, session_pct: 93, exceeds: true },
    },
    {
      name: "cuenta6",
      container: "agent-cuenta6",
      is_primary: true,
      ...limits,
      state: "IDLE",
      current_task_id: null,
      rate_limited_at: null,
      last_probe: probes.pacedPrimary,
    },
  ];
}

const EVENT_TYPES = [
  "phase.started",
  "phase.finished",
  "gate.finding",
  "pool.rate_limited",
  "lock.heartbeat",
  "action.enqueued",
  "debt.declared",
];
const SOURCE_APPS = ["harness", "pool", "gates", "hooks", "console"];

export function mockEvents(count = 220): HookEvent[] {
  const out: HookEvent[] = [];
  for (let i = 0; i < count; i++) {
    const id = 41200 + i;
    const type = EVENT_TYPES[i % EVENT_TYPES.length]!;
    const task = ["T-011", "T-012", "T-013", "T-014", "T-008"][i % 5]!;
    out.push({
      id,
      source_app: SOURCE_APPS[i % SOURCE_APPS.length]!,
      event_type: type,
      created_at: iso(Math.round((count - i) * 7.5)),
      payload: {
        task_id: task,
        account: `cuenta${(i % 6) + 1}`,
        role: ["cartografo", "arquitecto", "implementador", "revisor", "auditor"][i % 5],
        detail:
          type === "pool.rate_limited"
            ? "provider refused: 429 too many requests"
            : type === "gate.finding"
              ? "tests-run: 2 tests skipped on this runner"
              : "ok",
      },
    });
  }
  return out;
}

export function nextMockEvent(lastId: number): HookEvent {
  const i = lastId % 7;
  return {
    id: lastId + 1,
    source_app: SOURCE_APPS[lastId % SOURCE_APPS.length]!,
    event_type: EVENT_TYPES[i]!,
    created_at: new Date().toISOString(),
    payload: {
      task_id: ["T-011", "T-012", "T-013"][lastId % 3],
      account: `cuenta${(lastId % 6) + 1}`,
      detail: "ok",
    },
  };
}

/**
 * Conformed to what `/api/learnings` serves, which is why it is still here.
 *
 * The `L-01` ids this held were a surrogate where the harness has a pointer —
 * `ref` is `inbox/<slug>.md` or `harness/<slug>.md`, the path the entry actually
 * lives at — and `trigger`, `body` and `retired` were the console's words for
 * `when`, `rule` and a status axis that does not exist. ADR 42 narrowed the type
 * and `docs/learnings/narrowing-a-served-type-is-also-a-fixture-edit.md` is why
 * this moved with it instead of being deleted.
 *
 * `in_phase_table` and `phase_table_cap` are written out per row rather than
 * derived from the position here, because that is how the api answers them: a
 * refuted row is `false` for one reason and a row past the cap for another.
 */
export function mockLearnings(): LearningEntry[] {
  const base: LearningEntry[] = [
    {
      ref: "harness/T-009-a-read-only-sqlite-open-still-writes.md",
      task: "T-009",
      carried_by: "T-009",
      scope: "harness",
      status: "confirmed",
      when: "you open a sqlite file on a read-only mount",
      rule: "Open it with mode=ro in the URI, not with a plain path.",
      stale: false,
      in_phase_table: true,
      phase_table_cap: 40,
    },
    {
      ref: "inbox/T-012-a-default-config-must-not-hard-fail.md",
      task: "T-012",
      carried_by: "T-012",
      scope: "project",
      status: "confirmed",
      when: "you edit a config loader in dispatcher/config.py",
      rule: "Keep the defaults in code so a missing config file never hard-fails.",
      stale: false,
      in_phase_table: true,
      phase_table_cap: 40,
    },
    {
      ref: "inbox/T-013-a-shrink-retry-lands-at-about-seventy-percent.md",
      task: "T-013",
      carried_by: "T-014",
      scope: "project",
      status: "unconfirmed",
      when: "a phase exceeds its byte budget and you are sizing the retry",
      rule: "A shrink retry usually succeeds at about 70% of the original context.",
      stale: false,
      in_phase_table: true,
      phase_table_cap: 40,
    },
    {
      ref: "inbox/T-015-a-paused-heartbeat-writer-is-not-a-dead-container.md",
      task: "T-015",
      carried_by: "T-015",
      scope: "project",
      status: "unconfirmed",
      // The entry whose frontmatter carries neither, which renders as `Absent`.
      when: "",
      rule: "",
      stale: false,
      in_phase_table: true,
      phase_table_cap: 40,
    },
  ];
  for (let i = 1; i <= 36; i++) {
    base.push({
      ref: `inbox/T-016-observation-${String(i).padStart(2, "0")}.md`,
      task: "T-016",
      carried_by: "T-016",
      scope: i % 3 === 0 ? "harness" : "project",
      status: "unconfirmed",
      when: `phase ${i} touches the pool module`,
      rule: `Observation ${i}: recorded after a gate finding.`,
      stale: false,
      in_phase_table: true,
      phase_table_cap: 40,
    });
  }
  // In `ordered`'s order: confirmed, then unconfirmed and fresh, then stale,
  // then refuted, by `ref` within each band. 41 rows are eligible against a cap
  // of 40, so the stale one is the single row past the cap — the over-cap
  // banner's own count — and the refuted one is out of the table regardless.
  base.push(
    {
      ref: "inbox/T-014-land-the-schema-task-before-its-dependent.md",
      task: "T-014",
      carried_by: "T-014",
      scope: "harness",
      status: "unconfirmed",
      when: "two tasks in one cycle share a contract document",
      // Written under a permission surface this harness no longer has: shown,
      // and not counted as evidence.
      rule: "Land the schema task first or the dependent blocks on contract-docs.",
      stale: true,
      in_phase_table: false,
      phase_table_cap: 40,
    },
    {
      ref: "inbox/T-015-the-revisor-cannot-write-the-fix-itself.md",
      task: "T-015",
      carried_by: "T-015",
      scope: "harness",
      status: "refuted",
      when: "you are the revisor and want to apply the edit you are proposing",
      rule: "",
      stale: false,
      // Refuted, so `eligible` drops it before the cap is applied at all.
      in_phase_table: false,
      phase_table_cap: 40,
    },
  );
  return base;
}

export function mockActions(): QueuedAction[] {
  return [
    {
      id: "A-501",
      verb: "run-phase",
      args: ["T-011", "revisor", "--round", "2"],
      enqueued_by: "agent",
      state: "running",
      enqueued_at: iso(250),
      output_tail:
        "[revisor] loading handoff envelope from P-1103\n[revisor] 4 files in diff\n[revisor] reading harness/config/budgets.py\n[revisor] reading tests/test_budgets.py\n[revisor] drafting round 2 notes...",
    },
    {
      id: "A-502",
      verb: "run-task",
      args: ["T-013"],
      enqueued_by: "operator",
      state: "queued",
      enqueued_at: iso(180),
      output_tail: null,
    },
    {
      id: "A-503",
      verb: "release-account",
      args: ["cuenta2"],
      enqueued_by: "operator",
      state: "failed",
      enqueued_at: iso(700),
      output_tail:
        "error: account cuenta2 is inside a provider cooldown (1180s remaining)\nrefused by pool manager",
    },
    {
      id: "A-504",
      verb: "merge-task",
      args: ["T-004"],
      enqueued_by: "agent",
      state: "succeeded",
      enqueued_at: iso(4200),
      output_tail: "merged 3 commits into main as 4f1ac2d",
    },
    {
      id: "A-505",
      verb: "cleanup-task",
      args: ["T-002"],
      enqueued_by: "operator",
      state: "succeeded",
      enqueued_at: iso(5200),
      output_tail: "removed worktree /srv/worktrees/T-002-*",
    },
  ];
}

export function mockThreads(): ChatThread[] {
  return [
    {
      id: "th-main",
      title: "operator ↔ harness",
      updated_at: iso(60),
      context_used: 48200,
      context_limit: 120000,
      messages: [
        {
          id: "m1",
          role: "user",
          content: "why is T-008 blocked?",
          created_at: iso(300),
          tool_calls: [],
        },
        {
          id: "m2",
          role: "assistant",
          content:
            "T-008 is blocked on T-011. Its arquitecto phase wrote a contract that references the `phases.budgets` schema, and the contract-docs gate returned blocking because that schema has not landed yet. T-011 is currently in revisor round 2.",
          created_at: iso(290),
          tool_calls: [
            {
              id: "tc1",
              name: "get_task",
              arguments: { task_id: "T-008" },
              result_summary: "status=blocked, depends_on=[T-004, T-011]",
            },
            {
              id: "tc2",
              name: "list_gate_findings",
              arguments: { task_id: "T-008", level: "blocking" },
              result_summary: "1 blocking finding on contract-docs",
            },
          ],
        },
        {
          id: "m3",
          role: "user",
          content: "can I release cuenta2?",
          created_at: iso(70),
          tool_calls: [],
        },
        {
          id: "m4",
          role: "assistant",
          content:
            "Not yet. cuenta2 was refused by the provider 10m ago and the cooldown is 1800s, so roughly 20 minutes remain. Releasing it now would fail the same way action A-503 did.",
          created_at: iso(60),
          tool_calls: [
            {
              id: "tc3",
              name: "get_account",
              arguments: { name: "cuenta2" },
              result_summary: "state=COOLING_DOWN, rate_limited_at set",
            },
          ],
        },
      ],
    },
    {
      id: "th-pool",
      title: "pool triage",
      updated_at: iso(3600),
      context_used: 9100,
      context_limit: 120000,
      messages: [
        {
          id: "p1",
          role: "user",
          content: "list accounts over the local threshold",
          created_at: iso(3620),
          tool_calls: [],
        },
        {
          id: "p2",
          role: "assistant",
          content: "Only cuenta5 at 93%. It self-heals; a re-probe runs every 60s.",
          created_at: iso(3600),
          tool_calls: [
            {
              id: "tp1",
              name: "list_accounts",
              arguments: { min_usage: 90 },
              result_summary: "1 account",
            },
          ],
        },
      ],
    },
  ];
}
