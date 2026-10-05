import { useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import {
  Absent,
  EmptyState,
  ErrorState,
  HeartbeatDot,
  Malformed,
  Mono,
  PageHeader,
  RoleBadge,
  StatusPill,
  WarningBanner,
  gateTone,
} from "@/components/console/primitives";
import { Markdown } from "@/components/console/markdown";
import { PhaseList, PhasePill, asPathLine } from "@/components/console/payload";
import { debtQuery, phasesQuery, taskQuery } from "@/lib/api/queries";
import { ROLES } from "@/lib/api/types";
import type { Phase, Task } from "@/lib/api/types";
import { agoSeconds, formatAge, formatClock, roleColorVar } from "@/lib/format";
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
  const phases = useQuery(phasesQuery(taskId));

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
  // One banner, every warning, in the order the api sent them — three reads
  // concatenated rather than three banners stacked. `docs/ui.md` *A degraded
  // backend is a banner, not a blank screen*.
  const warnings = [
    ...(task.data?.warnings ?? []),
    ...(debt.data?.warnings ?? []),
    ...(phases.data?.warnings ?? []),
  ];
  // Debt ids are shaped `T-011-D1`, and `task_id` is that split. ADR 17.
  const taskDebt = (debt.data?.data ?? []).filter((d) => d.task_id === taskId);
  const timeline = byCycle(phases.data?.data ?? []);

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
              {!Array.isArray(t.depends_on) ? (
                <Malformed got={t.depends_on} want="a list" />
              ) : t.depends_on.length ? (
                <span className="flex gap-1">
                  {t.depends_on.map((d, i) =>
                    typeof d === "string" ? (
                      <Link key={d} to="/tasks/$taskId" params={{ taskId: d }}>
                        <Mono className="rounded-sm border border-border-strong px-1 text-[10px] hover:text-foreground">
                          {d}
                        </Mono>
                      </Link>
                    ) : (
                      <Malformed key={i} got={d} want="a task id" />
                    ),
                  )}
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
            {/* A secondary read on a discrete block, so Broken takes the region
                and not the screen: the task card above answered and is still
                true. `docs/ui.md` *Absent, empty and broken are three different
                things*, and the debt region below is the same shape. */}
            {phases.isError ? (
              <ErrorState
                title="/api/phases did not answer"
                body="The per-phase record could not be read, so what each phase returned is unknown rather than absent. Everything above comes from the task card and is unaffected; the handoffs themselves are on disk under the hive's handoffs directory."
              />
            ) : timeline.length === 0 ? (
              // An empty state about the *harness*, not about the console: the
              // route exists now, so naming one would send an operator looking
              // for a bug in the api. `docs/ui.md` *A region with no route…*
              <EmptyState
                title="No phase of this task has left a handoff"
                body="One record per role is written when a phase ends, so a task whose first phase is still running has none yet — and a task dispatched before the harness kept these records has none at all."
              />
            ) : (
              <ul className="space-y-2">
                {timeline.map((phase) => (
                  <PhaseRow key={phase.id} phase={phase} now={now} />
                ))}
              </ul>
            )}
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
            {/* Still waiting, and on one route now rather than two: ADR 19 says
                this names what is missing, and only /api/learnings is. */}
            <EmptyState
              title="Learnings handed to a phase are waiting on /api/learnings"
              body="Tier 2 of docs/plans/front.md, and the half of the join that does not exist yet. The timeline above carries each phase's own learnings lines, which name inbox filenames rather than ids; the records on the other end of those names need that route. The learnings tree is on disk either way."
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
  // Widened back to `unknown` on purpose: `Task["depends_on"]` is `string[]`
  // because that is what a well-formed task file holds, not because anything
  // checked — `depends_on: T-001` written without a dash reaches here as a
  // string. Inside an <svg> that is a thrown React child, and the only
  // `errorComponent` in this console is on the root route, so the whole page
  // would go for one line of YAML. `docs/decisions.md` ADR 29.
  const raw: unknown = task.depends_on;
  const deps: string[] | null =
    Array.isArray(raw) && raw.every((d) => typeof d === "string") ? raw : null;
  if (deps === null) {
    return (
      <div className="panel px-3 py-3">
        <h2 className="label-xs mb-1">dependency graph</h2>
        <Malformed label="depends on" got={raw} want="a list of task ids" />
      </div>
    );
  }
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
 * The timeline's order: by the cycle, not by the clock.
 *
 * `/api/phases` answers newest `saved_at` first, which is the right default for a
 * route whose unfiltered form is a feed. A timeline of one task reads
 * arquitecto → implementador → revisor → auditor, and that order survives a
 * `saved_at` the api could not judge — the route serves the value verbatim
 * (`docs/decisions.md` ADR 10's rule, ADR 27 for this field), so it is not a key
 * to sort a screen on alone. A role outside `ROLES` goes last rather than first,
 * because the api validates neither the filename nor the envelope's `role`.
 */
function byCycle(phases: Phase[]): Phase[] {
  const rank = (role: Phase["role"]) => {
    const index = (ROLES as readonly string[]).indexOf(role);
    return index === -1 ? ROLES.length : index;
  };
  return [...phases].sort(
    (a, b) => rank(a.role) - rank(b.role) || String(a.saved_at).localeCompare(String(b.saved_at)),
  );
}

/**
 * One handoff file, as `/api/phases` serves it.
 *
 * The row the fixture drew is thinner now and true: no account, no model, no
 * duration, no byte bar, no gate chips, no commit and no worktree, because the
 * harness records none of them per phase — `docs/decisions.md` ADR 27 sorts all
 * twenty-two fields and a later task that wants one reads that table first. What
 * it gains is the payload the role actually returned.
 */
function PhaseRow({ phase, now }: { phase: Phase; now: number }) {
  const handoff = phase.handoff;
  // The api serves whatever the filename says, so a role this console does not
  // know still renders — as its own string, since there is no hue for it.
  const known = (ROLES as readonly string[]).includes(phase.role);
  return (
    <li
      className={cn("panel px-3 py-2", phase.round ? "ml-6 border-l-2" : "")}
      style={phase.round ? { borderLeftColor: roleColorVar.revisor } : undefined}
    >
      <div className="flex flex-wrap items-center gap-2">
        {known ? (
          <RoleBadge role={phase.role} />
        ) : (
          <Mono className="text-[11px]">{phase.role}</Mono>
        )}
        {phase.round !== null && <span className="label-xs">round {phase.round}</span>}
        <PhasePill field="status" value={handoff?.status} />
        <PhasePill field="verdict" value={handoff?.verdict} />
        {/* *ended*, and never *started*: `saved_at` is the only stamp a record
            carries and the harness writes no start. `docs/ui.md` *Times are
            absolute, ages are relative, and ages tick*. */}
        <span className="ml-auto text-[10px] text-muted-foreground">
          ended {formatClock(phase.saved_at)} · {formatAge(agoSeconds(phase.saved_at, now))} ago
        </span>
      </div>

      <div className="mt-2 rounded-sm border border-border bg-surface-2 px-2 py-1.5">
        <p className="label-xs mb-1">what the phase returned</p>
        {handoff === null ? (
          // A phase ran and left nothing parseable — which is what the record
          // says, not "not handed off yet". ADR 27.
          <Absent label="no structured return" />
        ) : (
          <div className="space-y-1.5">
            {/* Every list is guarded twice over: the key set belongs to the
                role, so a key this screen names may simply not be on this
                payload — and `HandoffPayload` only annotates what a well-formed
                file holds, so the shape is checked at the render rather than
                trusted from the type. `paths` is mapped inside `PhaseList` for
                the second reason: mapping it here runs before any guard can.
                `docs/learnings/a-console-type-over-a-served-value-is-an-annotation.md`. */}
            <PhaseList label="changed" value={handoff.changed} />
            <PhaseList label="verified" value={handoff.verified} />
            <PhaseList label="pending" value={handoff.pending} />
            <PhaseList label="risks" value={handoff.risks} />
            <PhaseList
              label="paths"
              value={handoff.paths}
              line={asPathLine}
              itemWant="a path and what it holds"
            />
            <PhaseList label="learnings" value={handoff.learnings} />
          </div>
        )}
      </div>
    </li>
  );
}
