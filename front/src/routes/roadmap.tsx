import { useState, type ReactNode } from "react";
import { createFileRoute, useNavigate, useSearch } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight } from "lucide-react";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import {
  Absent,
  EmptyState,
  ErrorState,
  Mono,
  PageHeader,
  StatusPill,
  WarningBanner,
} from "@/components/console/primitives";
import { cn } from "@/lib/utils";
import { useProjectScope } from "@/lib/projects";
import { TaskLink } from "@/components/console/project-scope";
import { roadmapQuery } from "@/lib/api/queries";
import type { Roadmap, RoadmapMonth, RoadmapPlan, RoadmapTask, TaskStatus } from "@/lib/api/types";

/**
 * The two values of the filter, kept in the URL as `?view=`. `full` is the
 * default and is written as no parameter at all, so the nav entry's plain
 * `/roadmap` and a copied link without `?view=` are the same screen.
 */
export type RoadmapView = "full" | "current";

/** Anything but `current` is the default: a stale or hand-typed value is not an error. */
function readView(search: unknown): RoadmapView {
  return (search as { view?: unknown } | null)?.view === "current" ? "current" : "full";
}

/** `?view=` as the route keeps it; `/p/$slug/roadmap` takes this one (ADR NNN). */
export const Route = createFileRoute("/roadmap")({
  validateSearch: (search: Record<string, unknown>): { view?: "current" } =>
    readView(search) === "current" ? { view: "current" } : {},
  head: () => ({
    meta: [
      { title: "Roadmap — harness operations console" },
      {
        name: "description",
        content:
          "The project's plans in three bands, future, now and past, newest first, with the tasks each plan names.",
      },
      { property: "og:title", content: "Roadmap — harness operations console" },
      {
        property: "og:description",
        content: "What is planned, what is being built, what shipped.",
      },
    ],
  }),
  component: RoadmapPage,
});

/**
 * Whether a task is history: merged, or `done`. The api's own merged flag is the
 * task file's `merged` block, which it serves as `merged_at`; a merged block with
 * no date reads as `null` there, and `done` is what such a task carries anyway.
 */
function isHistory(task: RoadmapTask): boolean {
  return task.merged_at !== null || task.state === "done";
}

/** Keys of the two `no_plan` groups in the expanded set, which plan files cannot collide with. */
const NO_PLAN_NOW = "no_plan:now";
const NO_PLAN_PAST = "no_plan:past";

/**
 * The roadmap: the console project's plans on the harness's own screen, and
 * under `/p/$slug/roadmap` (ADR NNN) the plans in that project's clone with the
 * tasks that name the project.
 */
export function RoadmapPage() {
  const project = useProjectScope();
  const roadmap = useQuery(roadmapQuery(project));
  // `strict: false` and a parse of its own, rather than `Route.useSearch()`: the
  // route test mounts this component on a route of its own building
  // (`src/test/render-route.tsx`), which has no `validateSearch`, and the screen
  // must read the same value under both.
  const view = readView(useSearch({ strict: false }));
  // No `from`: the screen is drawn at `/roadmap` and at `/p/$slug/roadmap`, and
  // the filter moves the search of whichever one it is on.
  const navigate = useNavigate();
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());

  const toggle = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const setView = (next: RoadmapView) =>
    void navigate({ to: ".", search: next === "current" ? { view: "current" } : {} });

  const data = roadmap.data?.data;

  return (
    <AppShell>
      <PageHeader
        title={project === null ? "Roadmap" : `Roadmap · ${project}`}
        subtitle="Future, now and past, newest first. A plan opens to the tasks that name it."
        right={<RefreshedAt at={roadmap.dataUpdatedAt} />}
      />

      <div
        className="flex items-center gap-1 border-b border-border px-4 py-2"
        role="group"
        aria-label="Roadmap view"
      >
        <ViewButton current={view} value="full" onPick={setView}>
          Full history
        </ViewButton>
        <ViewButton current={view} value="current" onPick={setView}>
          Current only
        </ViewButton>
      </div>

      <WarningBanner warnings={roadmap.data?.warnings ?? []} />

      {roadmap.isError ? (
        <ErrorState
          title="The roadmap could not be read"
          body="The harness API returned an error. The plans in docs/plans/ are still the source; this screen only reads them."
        />
      ) : roadmap.isLoading || !data ? (
        <p className="px-4 py-6 text-xs text-muted-foreground">Reading the roadmap…</p>
      ) : (
        <RoadmapBands data={data} view={view} expanded={expanded} onToggle={toggle} />
      )}
    </AppShell>
  );
}

function ViewButton({
  current,
  value,
  onPick,
  children,
}: {
  current: RoadmapView;
  value: RoadmapView;
  onPick: (v: RoadmapView) => void;
  children: string;
}) {
  const active = current === value;
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={() => onPick(value)}
      className={cn(
        "rounded-sm border px-2 py-1 text-[11px] transition-colors",
        active
          ? "border-border-strong bg-surface-2 text-foreground"
          : "border-border text-muted-foreground hover:bg-surface-2/60 hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

/**
 * The three bands in their fixed order, and the two bare states.
 *
 * Exported beside the route, as `DebtEmpty` is beside its own. *Current only*
 * drops the past band whole, `no_plan.past` with it, and every merged or done
 * task in the two bands that stay; plans themselves stay, since a plan with
 * nothing left to do in `now` is still the plan being built.
 */
export function RoadmapBands({
  data,
  view,
  expanded,
  onToggle,
}: {
  data: Roadmap;
  view: RoadmapView;
  expanded: ReadonlySet<string>;
  onToggle: (key: string) => void;
}) {
  const { bands, no_plan } = data;
  const nothing =
    bands.future.length + bands.now.length + bands.past.length === 0 &&
    no_plan.now.length === 0 &&
    no_plan.past.length === 0;
  if (nothing)
    return (
      <EmptyState
        title="This project has no roadmap yet"
        body="No plan file in docs/plans/ and no task. A plan shows here once it has a status in its frontmatter."
      />
    );

  const current = view === "current";
  const keep = (tasks: RoadmapTask[]) => (current ? tasks.filter((t) => !isHistory(t)) : tasks);

  return (
    <div className="space-y-5 p-4">
      <Band
        title="Future"
        plans={bands.future}
        keep={keep}
        expanded={expanded}
        onToggle={onToggle}
      />
      <Band
        title="Now"
        plans={bands.now}
        keep={keep}
        expanded={expanded}
        onToggle={onToggle}
        noPlan={
          no_plan.now.length > 0 && keep(no_plan.now).length > 0 ? (
            <NoPlanNow
              tasks={keep(no_plan.now)}
              open={expanded.has(NO_PLAN_NOW)}
              onToggle={() => onToggle(NO_PLAN_NOW)}
            />
          ) : null
        }
      />
      {current ? null : (
        <Band
          title="Past"
          plans={bands.past}
          keep={keep}
          expanded={expanded}
          onToggle={onToggle}
          noPlan={
            no_plan.past.length > 0 ? (
              <NoPlanPast
                months={no_plan.past}
                open={expanded.has(NO_PLAN_PAST)}
                onToggle={() => onToggle(NO_PLAN_PAST)}
              />
            ) : null
          }
        />
      )}
    </div>
  );
}

function Band({
  title,
  plans,
  keep,
  expanded,
  onToggle,
  noPlan = null,
}: {
  title: string;
  plans: RoadmapPlan[];
  keep: (tasks: RoadmapTask[]) => RoadmapTask[];
  expanded: ReadonlySet<string>;
  onToggle: (key: string) => void;
  noPlan?: ReactNode;
}) {
  return (
    <section className="space-y-2" aria-label={title}>
      <h2 className="label-xs">
        {title} · {plans.length} {plans.length === 1 ? "plan" : "plans"}
      </h2>
      {plans.length === 0 && !noPlan ? (
        <p className="text-[11px] text-muted-foreground">No plan is in this band.</p>
      ) : null}
      {plans.map((plan) => (
        <PlanRow
          key={plan.file}
          plan={plan}
          tasks={keep(plan.tasks)}
          open={expanded.has(plan.file)}
          onToggle={() => onToggle(plan.file)}
        />
      ))}
      {noPlan}
    </section>
  );
}

/** `2026-10-18T09:12:00+00:00` as `2026-10-18`; the plans carry dates, not times. */
function day(iso: string): string {
  return iso.slice(0, 10);
}

function Collapsible({
  open,
  onToggle,
  head,
  children,
}: {
  open: boolean;
  onToggle: () => void;
  head: ReactNode;
  children: ReactNode;
}) {
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <section className="panel">
      <button
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-3 py-1.5 text-left hover:bg-surface-2/50"
      >
        <Chevron className="size-3.5 shrink-0 text-muted-foreground" />
        {head}
      </button>
      {open ? <div className="border-t border-border">{children}</div> : null}
    </section>
  );
}

function PlanRow({
  plan,
  tasks,
  open,
  onToggle,
}: {
  plan: RoadmapPlan;
  tasks: RoadmapTask[];
  open: boolean;
  onToggle: () => void;
}) {
  const hidden = plan.tasks.length - tasks.length;
  return (
    <Collapsible
      open={open}
      onToggle={onToggle}
      head={
        <>
          <span className="text-xs font-semibold">
            {plan.title ?? <Mono className="text-[11px]">{plan.file}</Mono>}
          </span>
          <Mono className="text-[10px] uppercase tracking-wide text-muted-foreground">
            {plan.status ?? "no status"}
          </Mono>
          {plan.approved ? (
            <span className="text-[10px] text-muted-foreground">approved {day(plan.approved)}</span>
          ) : null}
          {plan.built ? (
            <span className="text-[10px] text-muted-foreground">built {day(plan.built)}</span>
          ) : null}
          {plan.superseded_by.length > 0 ? (
            <span className="text-[10px] text-muted-foreground">
              superseded by <Mono>{plan.superseded_by.join(", ")}</Mono>
            </span>
          ) : null}
          {plan.supersedes.length > 0 ? (
            <span className="text-[10px] text-muted-foreground">
              supersedes <Mono>{plan.supersedes.join(", ")}</Mono>
            </span>
          ) : null}
          <span className="ml-auto text-[10px] text-muted-foreground">
            {tasks.length} {tasks.length === 1 ? "task" : "tasks"}
            {hidden > 0 ? ` · ${hidden} merged or done hidden` : ""}
          </span>
        </>
      }
    >
      {tasks.length === 0 ? (
        <p className="px-3 py-1.5 text-[11px] text-muted-foreground">
          {hidden > 0 ? "Every task of this plan is merged or done." : "No task names this plan."}
        </p>
      ) : (
        <TaskTable tasks={tasks} />
      )}
    </Collapsible>
  );
}

/** The unmerged tasks no plan names, at the end of the now band. */
function NoPlanNow({
  tasks,
  open,
  onToggle,
}: {
  tasks: RoadmapTask[];
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <Collapsible
      open={open}
      onToggle={onToggle}
      head={
        <>
          <span className="text-xs font-semibold text-muted-foreground">Tasks with no plan</span>
          <span className="ml-auto text-[10px] text-muted-foreground">
            {tasks.length} {tasks.length === 1 ? "task" : "tasks"}
          </span>
        </>
      }
    >
      <TaskTable tasks={tasks} />
    </Collapsible>
  );
}

/** The merged tasks no plan names, by the month they merged in, at the end of the past band. */
function NoPlanPast({
  months,
  open,
  onToggle,
}: {
  months: RoadmapMonth[];
  open: boolean;
  onToggle: () => void;
}) {
  const count = months.reduce((n, m) => n + m.tasks.length, 0);
  return (
    <Collapsible
      open={open}
      onToggle={onToggle}
      head={
        <>
          <span className="text-xs font-semibold text-muted-foreground">Merged with no plan</span>
          <span className="ml-auto text-[10px] text-muted-foreground">
            {count} {count === 1 ? "task" : "tasks"} · {months.length}{" "}
            {months.length === 1 ? "month" : "months"}
          </span>
        </>
      }
    >
      {months.map((m) => (
        <div key={m.month ?? "undated"} className="border-b border-border/50 last:border-0">
          <h3 className="label-xs px-3 pt-1.5">
            {m.month ?? "merge date not recorded"} · {m.tasks.length}
          </h3>
          <TaskTable tasks={m.tasks} />
        </div>
      ))}
    </Collapsible>
  );
}

function TaskTable({ tasks }: { tasks: RoadmapTask[] }) {
  return (
    <table className="w-full text-[11px]">
      <tbody>
        {tasks.map((t) => (
          <tr key={t.id} className="border-b border-border/50 last:border-0 hover:bg-surface-2/50">
            <td className="w-24 px-3 py-1.5">
              <TaskLink taskId={t.id}>
                <Mono className="font-semibold hover:underline">{t.id}</Mono>
              </TaskLink>
            </td>
            <td className="px-3 py-1.5">{t.title ?? <Absent label="no title" />}</td>
            <td className="w-28 px-3 py-1.5">
              {/* Verbatim, muted when it is not one of the five: the api passes
                  the task file's status through. */}
              <StatusPill status={t.state as TaskStatus} />
            </td>
            <td className="mono w-28 px-3 py-1.5 text-muted-foreground">
              {t.merged_at ? `merged ${day(t.merged_at)}` : ""}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
