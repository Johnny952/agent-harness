import type {
  Account,
  ChatThread,
  DebtEntry,
  HookEvent,
  LearningEntry,
  Phase,
  QueuedAction,
  Task,
} from "../types";

/** Fixtures are anchored to "now" so heartbeat freshness is realistic. */
const now = () => Date.now();
const iso = (secondsAgo: number) => new Date(now() - secondsAgo * 1000).toISOString();

export function mockDebt(): DebtEntry[] {
  return [
    {
      id: "D-101",
      what: "Retry loop swallows provider 429 body",
      where: "harness/pool/client.py:212",
      fix: "Surface the provider payload into the event stream before retrying",
      card: "T-008",
      state: "blocking",
      task_id: "T-008",
    },
    {
      id: "D-102",
      what: "Worktree cleanup leaves stale lock files",
      where: "harness/worktree.py:88",
      fix: "Unlink .lock in the finally branch",
      card: "T-004",
      state: "accepted",
      task_id: "T-004",
    },
    {
      id: "D-103",
      what: "Phase byte budget hardcoded per role",
      where: "harness/phases/budget.py:14",
      fix: "Move budgets into config with per-role override",
      card: "T-011",
      state: "declared",
      task_id: "T-011",
    },
    {
      id: "D-104",
      what: "Revisor writes rejected silently",
      where: "harness/phases/revisor.py:40",
      fix: "Emit a write_rejected event instead of dropping",
      card: "T-011",
      state: "blocking",
      task_id: "T-011",
    },
    {
      id: "D-105",
      what: "Event ids reused after restart",
      where: "hooks/store.py:61",
      fix: "Persist the monotonic counter",
      card: "T-002",
      state: "rejected",
      task_id: "T-002",
    },
  ];
}

export function mockTasks(): Task[] {
  const debt = mockDebt();
  const byTask = (id: string) => debt.filter((d) => d.task_id === id);
  return [
    {
      task_id: "T-002",
      status: "done",
      owner: "cuenta1",
      heartbeat: null,
      depends_on: [],
      description: "Persist hook event ids across harness restarts",
      body: "## Goal\nEvent ids must be monotonic across restarts.\n\n### cartografo\nMapped the hook store and its two callers.\n\n### implementador\nCounter moved to sqlite; migration added.\n\n### auditor\nGates clean. Merged as 4f1ac2d.",
      debt: byTask("T-002"),
    },
    {
      task_id: "T-004",
      status: "done",
      owner: "cuenta3",
      heartbeat: null,
      depends_on: ["T-002"],
      description: "Clean up worktrees when a task is abandoned",
      body: "## Goal\nAbandoned tasks leave worktrees behind.\n\n### implementador\nCleanup path added, lock files still linger (see debt).",
      debt: byTask("T-004"),
    },
    {
      task_id: "T-007",
      status: "queued",
      owner: null,
      heartbeat: null,
      depends_on: [],
      description: "Add a dry-run flag to bootstrap-project",
      body: "## Goal\nOperators want to preview the bootstrap plan.",
      debt: [],
    },
    {
      task_id: "T-008",
      status: "blocked",
      owner: "cuenta2",
      heartbeat: iso(940),
      depends_on: ["T-004", "T-011"],
      description: "Surface provider rate-limit payloads into the event stream",
      body: "## Goal\nA 429 from the provider currently disappears into the retry loop.\n\n### cartografo\nFound three call sites that swallow the response body.\n\n### arquitecto\nProposed an event envelope `pool.rate_limited` carrying the raw payload.\n\n> Blocked on T-011: the envelope schema lands there first.",
      debt: byTask("T-008"),
    },
    {
      task_id: "T-011",
      status: "in_progress:revisor",
      owner: "cuenta1",
      heartbeat: iso(41),
      depends_on: ["T-002"],
      description: "Per-role byte budgets moved into config",
      body: "## Goal\nBudgets differ per role and are currently hardcoded.\n\n### cartografo\nBudget constants live in one module, read from four.\n\n### arquitecto\nConfig block `phases.budgets` with per-role keys, defaults preserved.\n\n### implementador\nConfig loader written, four call sites migrated, tests added.\n\n### revisor\nRound 1: requested the over-budget shrink retry be logged. Round 2 in progress.",
      debt: byTask("T-011"),
    },
    {
      task_id: "T-012",
      status: "in_progress:implementador",
      owner: "cuenta4",
      heartbeat: iso(17),
      depends_on: [],
      description: "Pool priority: workers before primary",
      body: "## Goal\nPrimary account should be the last resort.\n\n### cartografo\nRanking read from a single ordered list.\n\n### arquitecto\nRank field on each account; primary pinned to the tail.\n\n### implementador\nIn progress.",
      debt: [],
    },
    {
      task_id: "T-013",
      status: "in_progress:cartografo",
      owner: "cuenta5",
      heartbeat: iso(6),
      depends_on: ["T-012"],
      description: "Re-probe parked accounts every 60s",
      body: "## Goal\nAccounts parked over the local threshold should self-heal.",
      debt: [],
    },
    {
      task_id: "T-014",
      status: "in_progress:auditor",
      owner: "cuenta3",
      heartbeat: iso(312),
      depends_on: [],
      description: "Gate: fail the phase when tests-in-diff is empty",
      body: "## Goal\nA diff with no tests should stop at the gate.\n\n### auditor\nRunning gates; tests-run is currently warning.",
      debt: [],
    },
    {
      task_id: "T-015",
      status: "queued",
      owner: null,
      heartbeat: null,
      depends_on: ["T-008"],
      description: "Operator console: live tail transport hardening",
      body: "## Goal\nReconnect without losing events.",
      debt: [],
    },
  ];
}

export function mockPhases(): Phase[] {
  return [
    {
      id: "P-1101",
      task_id: "T-011",
      role: "cartografo",
      account: "cuenta1",
      model: "sonnet-4.5",
      started_at: iso(5400),
      duration_s: 214,
      bytes_used: 38200,
      bytes_budget: 60000,
      shrink_retry: false,
      write_rejected: false,
      envelope: {
        from_role: "cartografo",
        to_role: "arquitecto",
        summary: "Budget constants live in harness/phases/budget.py, read from four call sites.",
        artifacts: ["notes/T-011-map.md"],
        open_questions: ["Should defaults stay in code as a fallback?"],
      },
      gate_findings: [{ gate: "pointers", level: "note", detail: "4 pointers recorded" }],
      commit_sha: "9ac31de",
      worktree_path: "/srv/worktrees/T-011-cartografo",
      revision_round: null,
      learning_ids: ["L-01", "L-04"],
    },
    {
      id: "P-1102",
      task_id: "T-011",
      role: "arquitecto",
      account: "cuenta1",
      model: "sonnet-4.5",
      started_at: iso(4900),
      duration_s: 341,
      bytes_used: 71400,
      bytes_budget: 65000,
      shrink_retry: true,
      write_rejected: false,
      envelope: {
        from_role: "arquitecto",
        to_role: "implementador",
        summary: "Config block phases.budgets, per-role keys, code defaults kept as fallback.",
        artifacts: ["docs/contracts/budgets.md"],
        open_questions: [],
      },
      gate_findings: [
        { gate: "contract-docs", level: "note", detail: "budgets.md added" },
        { gate: "pointers", level: "warning", detail: "1 pointer missing a line anchor" },
      ],
      commit_sha: "b40f72c",
      worktree_path: "/srv/worktrees/T-011-arquitecto",
      revision_round: null,
      learning_ids: ["L-02"],
    },
    {
      id: "P-1103",
      task_id: "T-011",
      role: "implementador",
      account: "cuenta4",
      model: "sonnet-4.5",
      started_at: iso(3800),
      duration_s: 903,
      bytes_used: 118900,
      bytes_budget: 90000,
      shrink_retry: true,
      write_rejected: false,
      envelope: {
        from_role: "implementador",
        to_role: "revisor",
        summary: "Loader written, four call sites migrated, 6 tests added.",
        artifacts: ["harness/config/budgets.py", "tests/test_budgets.py"],
        open_questions: ["Shrink retry is not logged anywhere."],
      },
      gate_findings: [
        { gate: "tests-in-diff", level: "note", detail: "6 tests touched" },
        { gate: "tests-run", level: "warning", detail: "2 tests skipped on this runner" },
      ],
      commit_sha: "1de9004",
      worktree_path: "/srv/worktrees/T-011-implementador",
      revision_round: null,
      learning_ids: ["L-02", "L-03"],
    },
    {
      id: "P-1104",
      task_id: "T-011",
      role: "revisor",
      account: "cuenta1",
      model: "sonnet-4.5",
      started_at: iso(2600),
      duration_s: 158,
      bytes_used: 44100,
      bytes_budget: 50000,
      shrink_retry: false,
      write_rejected: true,
      envelope: {
        from_role: "revisor",
        to_role: "implementador",
        summary: "Round 1: log the shrink retry; otherwise approved.",
        artifacts: [],
        open_questions: [],
      },
      gate_findings: [
        { gate: "tests-run", level: "blocking", detail: "revisor attempted a write; rejected" },
      ],
      commit_sha: null,
      worktree_path: "/srv/worktrees/T-011-revisor",
      revision_round: 1,
      learning_ids: ["L-05"],
    },
    {
      id: "P-1105",
      task_id: "T-011",
      role: "revisor",
      account: "cuenta1",
      model: "sonnet-4.5",
      started_at: iso(240),
      duration_s: null,
      bytes_used: 12800,
      bytes_budget: 50000,
      shrink_retry: false,
      write_rejected: false,
      envelope: null,
      gate_findings: [],
      commit_sha: null,
      worktree_path: "/srv/worktrees/T-011-revisor",
      revision_round: 2,
      learning_ids: [],
    },
    {
      id: "P-0801",
      task_id: "T-008",
      role: "cartografo",
      account: "cuenta2",
      model: "sonnet-4.5",
      started_at: iso(9400),
      duration_s: 190,
      bytes_used: 29000,
      bytes_budget: 60000,
      shrink_retry: false,
      write_rejected: false,
      envelope: {
        from_role: "cartografo",
        to_role: "arquitecto",
        summary: "Three call sites swallow the 429 body.",
        artifacts: ["notes/T-008-map.md"],
        open_questions: [],
      },
      gate_findings: [{ gate: "pointers", level: "note", detail: "3 pointers recorded" }],
      commit_sha: "77c1a02",
      worktree_path: "/srv/worktrees/T-008-cartografo",
      revision_round: null,
      learning_ids: ["L-01"],
    },
    {
      id: "P-0802",
      task_id: "T-008",
      role: "arquitecto",
      account: "cuenta2",
      model: "sonnet-4.5",
      started_at: iso(8800),
      duration_s: 402,
      bytes_used: 66000,
      bytes_budget: 65000,
      shrink_retry: false,
      write_rejected: false,
      envelope: {
        from_role: "arquitecto",
        to_role: null,
        summary: "Envelope schema depends on T-011 config landing first.",
        artifacts: ["docs/contracts/pool-events.md"],
        open_questions: ["Blocked on T-011."],
      },
      gate_findings: [
        { gate: "contract-docs", level: "blocking", detail: "contract references an unlanded schema" },
      ],
      commit_sha: null,
      worktree_path: "/srv/worktrees/T-008-arquitecto",
      revision_round: null,
      learning_ids: ["L-04"],
    },
    {
      id: "P-1401",
      task_id: "T-014",
      role: "auditor",
      account: "cuenta3",
      model: "sonnet-4.5",
      started_at: iso(400),
      duration_s: null,
      bytes_used: 51000,
      bytes_budget: 55000,
      shrink_retry: false,
      write_rejected: false,
      envelope: null,
      gate_findings: [{ gate: "tests-run", level: "warning", detail: "suite running" }],
      commit_sha: null,
      worktree_path: "/srv/worktrees/T-014-auditor",
      revision_round: null,
      learning_ids: [],
    },
    {
      id: "P-1201",
      task_id: "T-012",
      role: "implementador",
      account: "cuenta4",
      model: "sonnet-4.5",
      started_at: iso(120),
      duration_s: null,
      bytes_used: 22000,
      bytes_budget: 90000,
      shrink_retry: false,
      write_rejected: false,
      envelope: null,
      gate_findings: [],
      commit_sha: null,
      worktree_path: "/srv/worktrees/T-012-implementador",
      revision_round: null,
      learning_ids: [],
    },
  ];
}

export function mockAccounts(): Account[] {
  return [
    {
      name: "cuenta1",
      container: "agent-cuenta1",
      state: "BUSY",
      usage_pct: 47,
      rate_limited_at: null,
      current_task_id: "T-011",
      is_primary: false,
      rank: 1,
      heartbeat: iso(41),
    },
    {
      name: "cuenta2",
      container: "agent-cuenta2",
      state: "COOLING_DOWN",
      usage_pct: 64,
      rate_limited_at: iso(620),
      current_task_id: null,
      is_primary: false,
      rank: 2,
      heartbeat: null,
    },
    {
      name: "cuenta3",
      container: "agent-cuenta3",
      state: "BUSY",
      usage_pct: 81,
      rate_limited_at: null,
      current_task_id: "T-014",
      is_primary: false,
      rank: 3,
      heartbeat: iso(312),
    },
    {
      name: "cuenta4",
      container: "agent-cuenta4",
      state: "BUSY",
      usage_pct: 55,
      rate_limited_at: null,
      current_task_id: "T-012",
      is_primary: false,
      rank: 4,
      heartbeat: iso(17),
    },
    {
      name: "cuenta5",
      container: "agent-cuenta5",
      state: "PRE_COOLDOWN",
      usage_pct: 93,
      rate_limited_at: null,
      current_task_id: "T-013",
      is_primary: false,
      rank: 5,
      heartbeat: iso(6),
    },
    {
      name: "cuenta6",
      container: "agent-cuenta6",
      state: "IDLE",
      usage_pct: 12,
      rate_limited_at: null,
      current_task_id: null,
      is_primary: true,
      rank: 99,
      heartbeat: null,
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

export function mockLearnings(): LearningEntry[] {
  const base: LearningEntry[] = [
    {
      id: "L-01",
      scope: "harness",
      status: "confirmed",
      trigger: "When a provider returns 429",
      body: "Keep the raw response body; the retry loop must not discard it.",
      retired: false,
    },
    {
      id: "L-02",
      scope: "project",
      status: "confirmed",
      trigger: "When editing config loaders",
      body: "Defaults stay in code as a fallback so a missing config file never hard-fails.",
      retired: false,
    },
    {
      id: "L-03",
      scope: "project",
      status: "unconfirmed",
      trigger: "When a phase exceeds its byte budget",
      body: "A shrink retry usually succeeds at ~70% of the original context.",
      retired: false,
    },
    {
      id: "L-04",
      scope: "harness",
      status: "unconfirmed",
      trigger: "When two tasks share a contract document",
      body: "Land the schema task first or the dependent gate blocks on contract-docs.",
      retired: false,
    },
    {
      id: "L-05",
      scope: "harness",
      status: "confirmed",
      trigger: "When the revisor proposes an edit",
      body: "Revisor writes are rejected by design; the edit must go back to implementador.",
      retired: false,
    },
    {
      id: "L-06",
      scope: "project",
      status: "refuted",
      trigger: "When a worktree lock is stale",
      body: "Assumed the container had died; in practice the heartbeat writer was simply paused.",
      retired: false,
    },
    {
      id: "L-07",
      scope: "harness",
      status: "refuted",
      trigger: "When usage passes 90%",
      body: "Believed the provider throttles at 90%; the threshold is local only.",
      retired: true,
    },
    {
      id: "L-08",
      scope: "project",
      status: "unconfirmed",
      trigger: "When bootstrapping a new project",
      body: "The cartografo pass is cheap enough to always run twice.",
      retired: true,
    },
  ];
  // Pad to 43 active rows so the >40 cap banner is exercised.
  for (let i = 9; i <= 44; i++) {
    base.push({
      id: `L-${String(i).padStart(2, "0")}`,
      scope: i % 3 === 0 ? "harness" : "project",
      status: i % 4 === 0 ? "unconfirmed" : i % 7 === 0 ? "refuted" : "confirmed",
      trigger: `When phase ${i} touches the pool module`,
      body: `Observation ${i}: recorded by the auditor after a gate finding.`,
      retired: i > 42,
    });
  }
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
