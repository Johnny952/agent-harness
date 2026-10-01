import { useMemo, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { GitBranch } from "lucide-react";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import {
  EmptyState,
  ErrorState,
  GateChip,
  HeartbeatDot,
  Mono,
  PageHeader,
  RoleBadge,
  worstGate,
} from "@/components/console/primitives";
import { accountsQuery, phasesQuery, tasksQuery } from "@/lib/api/queries";
import { ROLES, type GateFinding, type Role, type Task } from "@/lib/api/types";
import { splitStatus } from "@/lib/format";
import { useNow, useSearchHotkey } from "@/hooks/use-console";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Board — harness operations console" },
      {
        name: "description",
        content:
          "Kanban of harness tasks with In Progress split into the five agent role lanes, heartbeat freshness and gate status.",
      },
      { property: "og:title", content: "Board — harness operations console" },
      {
        property: "og:description",
        content: "Live task board for a multi-agent development harness.",
      },
    ],
  }),
  component: BoardPage,
});

function BoardPage() {
  const now = useNow();
  const searchRef = useSearchHotkey();
  const tasks = useQuery(tasksQuery);
  const phases = useQuery(phasesQuery());
  const accounts = useQuery(accountsQuery);

  const [q, setQ] = useState("");
  const [roleFilter, setRoleFilter] = useState<string>("all");
  const [accountFilter, setAccountFilter] = useState<string>("all");
  const [gateFilter, setGateFilter] = useState<string>("all");
  const [dense, setDense] = useState(false);

  const gatesByTask = useMemo(() => {
    const map = new Map<string, GateFinding[]>();
    for (const p of phases.data ?? []) {
      map.set(p.task_id, [...(map.get(p.task_id) ?? []), ...p.gate_findings]);
    }
    return map;
  }, [phases.data]);

  const filtered = useMemo(() => {
    return (tasks.data ?? []).filter((t) => {
      const { role } = splitStatus(t.status);
      if (roleFilter !== "all" && role !== roleFilter) return false;
      if (accountFilter !== "all" && t.owner !== accountFilter) return false;
      if (gateFilter !== "all" && worstGate(gatesByTask.get(t.task_id) ?? []) !== gateFilter)
        return false;
      if (q && !(`${t.task_id} ${t.description}`.toLowerCase().includes(q.toLowerCase())))
        return false;
      return true;
    });
  }, [tasks.data, roleFilter, accountFilter, gateFilter, q, gatesByTask]);

  const byLane = (lane: string) => filtered.filter((t) => splitStatus(t.status).lane === lane);
  const byRole = (role: Role) =>
    filtered.filter((t) => splitStatus(t.status).role === role);

  return (
    <AppShell>
      <PageHeader
        title="Board"
        subtitle="In Progress is split into the five pipeline roles — that split is the point."
        right={
          <>
            <RefreshedAt at={tasks.dataUpdatedAt} />
            <button
              onClick={() => setDense((d) => !d)}
              className="rounded-sm border border-border px-2 py-1 text-[11px] text-muted-foreground hover:text-foreground"
            >
              {dense ? "comfortable" : "compact"}
            </button>
          </>
        }
      />

      <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2">
        <input
          ref={searchRef}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Filter tasks…  /"
          className="mono w-56 rounded-sm border border-border bg-surface px-2 py-1 text-[11px] outline-none focus:border-ring"
        />
        <Select value={roleFilter} onChange={setRoleFilter} options={["all", ...ROLES]} label="role" />
        <Select
          value={accountFilter}
          onChange={setAccountFilter}
          options={["all", ...(accounts.data ?? []).map((a) => a.name)]}
          label="account"
        />
        <Select
          value={gateFilter}
          onChange={setGateFilter}
          options={["all", "note", "warning", "blocking"]}
          label="gate"
        />
        <span className="ml-auto text-[10px] text-muted-foreground">
          {filtered.length} of {(tasks.data ?? []).length} tasks
        </span>
      </div>

      {tasks.isError ? (
        <ErrorState
          title="The task API did not answer"
          body="The board could not read tasks. Check the harness API on the Tailscale host, then reload — nothing was changed."
        />
      ) : tasks.isLoading ? (
        <p className="px-4 py-6 text-xs text-muted-foreground">Reading tasks…</p>
      ) : filtered.length === 0 ? (
        <EmptyState
          title="No task matches these filters"
          body="Clear the filter bar or widen the role and gate filters to see the rest of the board."
        />
      ) : (
        <div className="flex min-h-0 gap-3 overflow-x-auto p-3">
          <Column title="Queued" count={byLane("queued").length}>
            {byLane("queued").map((t) => (
              <TaskCard key={t.task_id} task={t} now={now} dense={dense} gates={gatesByTask.get(t.task_id) ?? []} />
            ))}
          </Column>

          <div className="flex shrink-0 flex-col rounded-md border border-border-strong bg-surface/40">
            <div className="border-b border-border px-2 py-1.5 text-[10px] uppercase tracking-widest text-muted-foreground">
              In progress · pipeline
            </div>
            <div className="flex gap-2 p-2">
              {ROLES.map((role) => (
                <Column key={role} role={role} title={role} count={byRole(role).length} lane>
                  {byRole(role).map((t) => (
                    <TaskCard
                      key={t.task_id}
                      task={t}
                      now={now}
                      dense={dense}
                      gates={gatesByTask.get(t.task_id) ?? []}
                    />
                  ))}
                </Column>
              ))}
            </div>
          </div>

          <Column title="Blocked" count={byLane("blocked").length}>
            {byLane("blocked").map((t) => (
              <TaskCard key={t.task_id} task={t} now={now} dense={dense} gates={gatesByTask.get(t.task_id) ?? []} />
            ))}
          </Column>
          <Column title="Done" count={byLane("done").length}>
            {byLane("done").map((t) => (
              <TaskCard key={t.task_id} task={t} now={now} dense={dense} gates={gatesByTask.get(t.task_id) ?? []} />
            ))}
          </Column>
        </div>
      )}
    </AppShell>
  );
}

function Select({
  value,
  onChange,
  options,
  label,
}: {
  value: string;
  onChange: (v: string) => void;
  options: readonly string[];
  label: string;
}) {
  return (
    <label className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-muted-foreground">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mono rounded-sm border border-border bg-surface px-1.5 py-1 text-[11px] normal-case text-foreground"
      >
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </label>
  );
}

function Column({
  title,
  count,
  children,
  lane = false,
  role,
}: {
  title: string;
  count: number;
  children: React.ReactNode;
  lane?: boolean;
  role?: Role;
}) {
  return (
    <section
      className={cn("flex shrink-0 flex-col", lane ? "w-[164px]" : "w-[210px] panel")}
    >
      <div className="flex items-center justify-between border-b border-border px-2 py-1.5">
        <span className="flex items-center gap-1.5 text-[10px] uppercase tracking-widest text-muted-foreground">
          {role ? <RoleBadge role={role} /> : title}
        </span>
        <span className="mono text-[10px] text-muted-foreground">{count}</span>
      </div>
      <div className="flex-1 space-y-2 p-2">
        {count === 0 && <p className="px-1 py-2 text-[10px] text-muted-foreground/70">empty</p>}
        {children}
      </div>
    </section>
  );
}

function TaskCard({
  task,
  now,
  dense,
  gates,
}: {
  task: Task;
  now: number;
  dense: boolean;
  gates: GateFinding[];
}) {
  const worst = worstGate(gates);
  const { role } = splitStatus(task.status);
  return (
    <Link
      to="/tasks/$taskId"
      params={{ taskId: task.task_id }}
      className={cn(
        "block rounded-sm border border-border bg-card transition-colors hover:border-border-strong",
        dense ? "px-2 py-1.5" : "px-2 py-2",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <Mono className="text-[11px] font-semibold">{task.task_id}</Mono>
        <HeartbeatDot heartbeat={task.heartbeat} now={now} />
      </div>
      {!dense && (
        <p className="mt-1 line-clamp-2 text-[11px] text-foreground/90">{task.description}</p>
      )}
      <div className="mt-1.5 flex flex-wrap items-center gap-1">
        {role && <RoleBadge role={role} compact />}
        {task.owner ? (
          <Mono className="text-[10px] text-muted-foreground">{task.owner}</Mono>
        ) : (
          <span className="text-[10px] italic text-muted-foreground/60">unassigned</span>
        )}
        {task.depends_on.length > 0 && (
          <span className="inline-flex items-center gap-0.5 rounded-sm border border-border-strong px-1 text-[10px] text-muted-foreground">
            <GitBranch className="size-2.5" />
            {task.depends_on.length}
          </span>
        )}
        {worst && <GateChip level={worst} />}
        {task.debt.length > 0 && (
          <span className="mono rounded-sm border border-border-strong px-1 text-[10px] text-muted-foreground">
            debt {task.debt.length}
          </span>
        )}
      </div>
    </Link>
  );
}
