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
  WarningBanner,
  gateTone,
} from "@/components/console/primitives";
import { Markdown } from "@/components/console/markdown";
import { debtQuery, taskQuery } from "@/lib/api/queries";
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
        content: `Status, owner, dependencies, the running record and the declared debt of task ${params.taskId}.`,
      },
      { property: "og:title", content: `${params.taskId} — task detail` },
      {
        property: "og:description",
        content: `The task file and the debt index, as the harness holds them, for ${params.taskId}.`,
      },
    ],
  }),
  component: TaskDetailPage,
});

function TaskDetailPage() {
  const { taskId } = Route.useParams();
  const now = useNow();
  const task = useQuery(taskQuery(taskId));
  const debt = useQuery(debtQuery);

  // Broken is a query that failed, and it never degrades into an empty state:
  // `docs/ui.md` *Absent, empty and broken are three different things*. A 404 and
  // a refused api both land here; a card that exists and could not be *read* is
  // `data: null` below, which is a different screen.
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

  const t = task.data?.data ?? null;
  const warnings = [...(task.data?.warnings ?? []), ...(debt.data?.warnings ?? [])];
  // Debt ids are shaped `T-011-D1`, and `task_id` is that split. ADR 17.
  const taskDebt = (debt.data?.data ?? []).filter((d) => d.task_id === taskId);

  return (
    <AppShell>
      <PageHeader
        title={taskId}
        subtitle={t?.description}
        right={<RefreshedAt at={task.dataUpdatedAt} />}
      />

      <WarningBanner warnings={warnings} />

      {task.isLoading ? (
        <p className="px-4 py-6 text-xs text-muted-foreground">Reading task…</p>
      ) : !t ? (
        // `data: null` on a 200: the card is there and the api could not parse it,
        // with the reason in the banner above. Not an error state — the read
        // succeeded — and not "Reading task…", which would spin forever.
        <EmptyState
          title={`${taskId} exists and could not be read`}
          body="The harness holds a card with this id but the api could not parse it. The reason is in the warning above; the file itself is under .hive/tasks/."
        />
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
              <HeartbeatDot
                heartbeat={t.heartbeat}
                lockExpired={t.lock_expired}
                now={now}
                withLabel
              />
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

          {/* A region waiting on a route is an empty state about the console, not
              about the harness — `docs/ui.md` *A region with no route says which
              route, and when*. The phase record is already on disk, one handoff per
              task and role; what is missing is the route over it. */}
          <section>
            <h2 className="label-xs mb-2">phase timeline</h2>
            <EmptyState
              title="The phase timeline is waiting on /api/phases"
              body="This console cannot see the per-phase record yet — that route is tier 2 of docs/plans/front.md. The handoffs themselves are on disk under the hive's handoffs directory, and this is not a statement about whether phases have run."
            />
          </section>

          <section className="panel px-3 py-3">
            <h2 className="label-xs mb-2">debt declared by this task</h2>
            {/* The region's own read failed, not the screen's: the task card above
                answered and is still true. Broken never degrades into an empty
                state, and "No debt declared" is that empty state told as a fact.
                `docs/ui.md` *Absent, empty and broken are three different things*. */}
            {debt.isError ? (
              <ErrorState
                title="The debt index did not answer"
                body="/api/debt could not be read, so what this task declared is unknown rather than absent. Everything above comes from the task card and is unaffected; the index itself is docs/debt/ in the repo."
              />
            ) : taskDebt.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                No debt declared. Phases record debt when they knowingly leave something unfinished.
              </p>
            ) : (
              <ul className="space-y-1.5">
                {/* `resolved` where `state` used to be: the index has no severity
                    column, and the four-state lifecycle this screen drew is a
                    debt's life inside one task's handoffs. ADR 17. */}
                {taskDebt.map((d) => (
                  <li key={d.id} className="flex flex-wrap items-center gap-2 text-[11px]">
                    <Mono className="text-muted-foreground">{d.id}</Mono>
                    <span
                      className={cn(
                        "rounded-sm border px-1 text-[10px] uppercase",
                        d.resolved ? gateTone.note : gateTone.warning,
                      )}
                    >
                      {d.resolved ? "resolved" : "open"}
                    </span>
                    <span>{d.what}</span>
                    <Mono className="text-[10px] text-muted-foreground">{d.where}</Mono>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section>
            <h2 className="label-xs mb-2">learnings handed to these phases</h2>
            <EmptyState
              title="Learnings handed to a phase are waiting on /api/learnings"
              body="Two routes, both tier 2 of docs/plans/front.md: /api/learnings for the rows, and /api/phases for the join, because the list this region drew is a phase's learning_ids read against them. The learnings tree is on disk either way."
            />
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

/**
 * No caller until `/api/phases` lands — tier 2 of `docs/plans/front.md`, and the
 * section above says so on screen rather than rendering this against a fixture
 * (ADR 19). Kept rather than deleted on the same reasoning ADR 25 gives for the
 * fixtures it kept: it still type-checks against `Phase`, and re-deleting a
 * hundred lines of a generated screen that tier 2 needs back verbatim costs more
 * in a tree C-9 keeps syncing to Lovable than the dead code does.
 */
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
