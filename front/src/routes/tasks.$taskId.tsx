import { useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import {
  Absent,
  EmptyState,
  ErrorState,
  GateChip,
  HeartbeatDot,
  Mono,
  PageHeader,
  RoleBadge,
  StatusPill,
  gateTone,
} from "@/components/console/primitives";
import { Markdown } from "@/components/console/markdown";
import { debtQuery, learningsQuery, phasesQuery, taskQuery } from "@/lib/api/queries";
import type { Phase, Task } from "@/lib/api/types";
import { agoSeconds, formatAge, formatBytes, formatClock, formatDuration, roleColorVar } from "@/lib/format";
import { useNow } from "@/hooks/use-console";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/tasks/$taskId")({
  head: ({ params }) => ({
    meta: [
      { title: `${params.taskId} — task detail` },
      {
        name: "description",
        content: `Phase timeline, handoff envelopes, byte budgets and gate findings for task ${params.taskId}.`,
      },
      { property: "og:title", content: `${params.taskId} — task detail` },
      {
        property: "og:description",
        content: `Phase-by-phase record for harness task ${params.taskId}.`,
      },
    ],
  }),
  component: TaskDetailPage,
});

function TaskDetailPage() {
  const { taskId } = Route.useParams();
  const now = useNow();
  const task = useQuery(taskQuery(taskId));
  const phases = useQuery(phasesQuery(taskId));
  const debt = useQuery(debtQuery);
  const learnings = useQuery(learningsQuery);

  if (task.isError) {
    return (
      <AppShell>
        <ErrorState
          title={`Could not read ${taskId}`}
          body="The harness API returned an error or no such task exists. Go back to the board and confirm the id."
        />
      </AppShell>
    );
  }

  const t = task.data;
  const phaseLearningIds = new Set((phases.data ?? []).flatMap((p) => p.learning_ids));
  const handed = (learnings.data ?? []).filter((l) => phaseLearningIds.has(l.id));
  const taskDebt = (debt.data ?? []).filter((d) => d.task_id === taskId);

  return (
    <AppShell>
      <PageHeader
        title={taskId}
        subtitle={t?.description}
        right={<RefreshedAt at={task.dataUpdatedAt} />}
      />

      {!t ? (
        <p className="px-4 py-6 text-xs text-muted-foreground">Reading task…</p>
      ) : (
        <div className="space-y-4 p-4">
          <div className="panel flex flex-wrap items-center gap-4 px-3 py-2">
            <Field label="status">
              <StatusPill status={t.status} />
            </Field>
            <Field label="owner">
              {t.owner ? <Mono className="text-[11px]">{t.owner}</Mono> : <Absent label="no owner" />}
            </Field>
            <Field label="lock heartbeat">
              {t.heartbeat ? (
                <HeartbeatDot heartbeat={t.heartbeat} now={now} withLabel />
              ) : (
                <Absent label="no lock held" />
              )}
            </Field>
            <Field label="depends on">
              {t.depends_on.length ? (
                <span className="flex gap-1">
                  {t.depends_on.map((d) => (
                    <Link key={d} to="/tasks/$taskId" params={{ taskId: d }}>
                      <Mono className="rounded-sm border border-border-strong px-1 text-[10px] hover:text-foreground">
                        {d}
                      </Mono>
                    </Link>
                  ))}
                </span>
              ) : (
                <Absent label="nothing" />
              )}
            </Field>
          </div>

          <DependencyGraph task={t} />

          <section className="panel px-3 py-3">
            <h2 className="label-xs mb-2">task body</h2>
            <BodySections body={t.body} />
          </section>

          <section>
            <h2 className="label-xs mb-2">phase timeline</h2>
            {phases.isLoading ? (
              <p className="text-xs text-muted-foreground">Reading phases…</p>
            ) : (phases.data ?? []).length === 0 ? (
              <EmptyState
                title="No phase has run for this task"
                body="Start one from the Queue screen with run-task, or run a single phase with run-phase."
              />
            ) : (
              <ol className="space-y-2">
                {(phases.data ?? []).map((p) => (
                  <PhaseRow key={p.id} phase={p} now={now} />
                ))}
              </ol>
            )}
          </section>

          <section className="panel px-3 py-3">
            <h2 className="label-xs mb-2">debt declared by this task</h2>
            {taskDebt.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                No debt declared. Phases record debt when they knowingly leave something unfinished.
              </p>
            ) : (
              <ul className="space-y-1.5">
                {taskDebt.map((d) => (
                  <li key={d.id} className="flex flex-wrap items-center gap-2 text-[11px]">
                    <Mono className="text-muted-foreground">{d.id}</Mono>
                    <span
                      className={cn(
                        "rounded-sm border px-1 text-[10px] uppercase",
                        d.state === "blocking" ? gateTone.blocking : gateTone.note,
                      )}
                    >
                      {d.state}
                    </span>
                    <span>{d.what}</span>
                    <Mono className="text-[10px] text-muted-foreground">{d.where}</Mono>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="panel px-3 py-3">
            <h2 className="label-xs mb-2">learnings handed to these phases</h2>
            {handed.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                No learning rows were handed to these phases.
              </p>
            ) : (
              <ul className="space-y-1.5">
                {handed.map((l) => (
                  <li key={l.id} className="flex flex-wrap items-baseline gap-2 text-[11px]">
                    <Mono className="text-muted-foreground">{l.id}</Mono>
                    <span className="label-xs">{l.scope}</span>
                    <span className="text-muted-foreground">{l.trigger}</span>
                    <span>{l.body}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </AppShell>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="label-xs">{label}</p>
      <div className="mt-0.5">{children}</div>
    </div>
  );
}

function BodySections({ body }: { body: string }) {
  const chunks = body.split(/\n(?=### )/);
  const [head, ...rest] = chunks;
  return (
    <div className="space-y-2">
      <Markdown source={head ?? ""} />
      {rest.map((chunk, i) => (
        <Collapsible key={i} title={chunk.split("\n")[0]?.replace("### ", "") ?? "phase"}>
          <Markdown source={chunk.split("\n").slice(1).join("\n")} />
        </Collapsible>
      ))}
    </div>
  );
}

function Collapsible({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-sm border border-border">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-1.5 px-2 py-1 text-left text-[11px]"
      >
        <ChevronRight className={cn("size-3 transition-transform", open && "rotate-90")} />
        <span className="uppercase tracking-wide text-muted-foreground">{title}</span>
      </button>
      {open && <div className="border-t border-border px-3 py-2">{children}</div>}
    </div>
  );
}

function DependencyGraph({ task }: { task: Task }) {
  const deps = task.depends_on;
  if (deps.length === 0) {
    return (
      <div className="panel px-3 py-3">
        <h2 className="label-xs mb-1">dependency graph</h2>
        <p className="text-xs text-muted-foreground">
          This task has no dependencies — it can run as soon as an account frees up.
        </p>
      </div>
    );
  }
  const width = 460;
  const height = 60 + deps.length * 34;
  const nodeY = (i: number) => 30 + i * 34;
  return (
    <div className="panel px-3 py-3">
      <h2 className="label-xs mb-2">dependency graph</h2>
      <svg width={width} height={height} className="max-w-full">
        {deps.map((d, i) => (
          <g key={d}>
            <path
              d={`M 120 ${nodeY(i)} C 190 ${nodeY(i)}, 210 ${height / 2}, 280 ${height / 2}`}
              fill="none"
              stroke="var(--border-strong)"
              strokeWidth={1}
            />
            <rect x={10} y={nodeY(i) - 11} width={110} height={22} rx={4} fill="var(--surface-2)" stroke="var(--border)" />
            <text x={20} y={nodeY(i) + 4} fill="var(--muted-foreground)" fontSize={11} fontFamily="var(--font-mono)">
              {d}
            </text>
          </g>
        ))}
        <rect
          x={280}
          y={height / 2 - 13}
          width={130}
          height={26}
          rx={4}
          fill="var(--surface)"
          stroke="var(--border-strong)"
        />
        <text
          x={292}
          y={height / 2 + 4}
          fill="var(--foreground)"
          fontSize={11}
          fontFamily="var(--font-mono)"
        >
          {task.task_id}
        </text>
      </svg>
    </div>
  );
}

function PhaseRow({ phase, now }: { phase: Phase; now: number }) {
  const over = phase.bytes_used > phase.bytes_budget;
  const pct = Math.round((phase.bytes_used / phase.bytes_budget) * 100);
  return (
    <li
      className={cn(
        "panel px-3 py-2",
        phase.revision_round ? "ml-6 border-l-2" : "",
      )}
      style={phase.revision_round ? { borderLeftColor: roleColorVar.revisor } : undefined}
    >
      <div className="flex flex-wrap items-center gap-2">
        <RoleBadge role={phase.role} />
        {phase.revision_round && (
          <span className="label-xs">revision round {phase.revision_round}</span>
        )}
        <Mono className="text-[11px]">{phase.account}</Mono>
        <Mono className="text-[10px] text-muted-foreground">{phase.model}</Mono>
        <span className="text-[10px] text-muted-foreground">
          started {formatClock(phase.started_at)} · {formatAge(agoSeconds(phase.started_at, now))} ago ·{" "}
          {formatDuration(phase.duration_s)}
        </span>
        <span className="ml-auto flex items-center gap-1">
          {phase.gate_findings.map((g, i) => (
            <GateChip key={i} level={g.level} label={g.gate} />
          ))}
        </span>
      </div>

      {phase.write_rejected && (
        <p className="mt-2 rounded-sm border border-destructive/50 bg-destructive/10 px-2 py-1 text-[11px] text-destructive">
          This revisor phase attempted a write. Revisor is read-only by design, so the write was
          rejected — the change has to go back to implementador.
        </p>
      )}

      <div className="mt-2 grid gap-3 md:grid-cols-2">
        <div>
          <p className="label-xs mb-1">byte budget</p>
          <div className="h-2 w-full overflow-hidden rounded-full bg-surface-2">
            <div
              className="h-full rounded-full"
              style={{
                width: `${Math.min(100, pct)}%`,
                backgroundColor: over ? "var(--destructive)" : "var(--info)",
              }}
            />
          </div>
          <p className={cn("mt-1 text-[10px]", over ? "text-destructive" : "text-muted-foreground")}>
            {formatBytes(phase.bytes_used)} of {formatBytes(phase.bytes_budget)}
            {over && ` · over by ${pct - 100}%`}
            {over && (phase.shrink_retry ? " · shrink retry ran" : " · no shrink retry")}
          </p>
        </div>
        <div>
          <p className="label-xs mb-1">result</p>
          <p className="text-[11px]">
            commit{" "}
            {phase.commit_sha ? (
              <Mono>{phase.commit_sha}</Mono>
            ) : (
              <Absent label="no commit" />
            )}
          </p>
          <p className="text-[10px] text-muted-foreground">
            {phase.worktree_path ?? <Absent label="no worktree" />}
          </p>
        </div>
      </div>

      <div className="mt-2 rounded-sm border border-border bg-surface-2 px-2 py-1.5">
        <p className="label-xs mb-1">handoff envelope</p>
        {phase.envelope ? (
          <div className="space-y-1 text-[11px]">
            <p>
              <Mono className="text-[10px] text-muted-foreground">
                {phase.envelope.from_role} → {phase.envelope.to_role ?? "—"}
              </Mono>
            </p>
            <p>{phase.envelope.summary}</p>
            {phase.envelope.artifacts.length > 0 && (
              <p className="mono text-[10px] text-muted-foreground">
                {phase.envelope.artifacts.join("  ")}
              </p>
            )}
            {phase.envelope.open_questions.map((q, i) => (
              <p key={i} className="text-[10px] text-warning">
                open: {q}
              </p>
            ))}
          </div>
        ) : (
          <Absent label="not handed off yet" />
        )}
      </div>
    </li>
  );
}
