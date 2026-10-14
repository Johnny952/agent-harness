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
} from "@/components/console/primitives";
import { Markdown } from "@/components/console/markdown";
import { PhaseList, PhasePill } from "@/components/console/payload";
import { InPhaseTable, LearningStatus } from "@/components/console/learnings";
import { debtQuery, learningsQuery, phasesQuery, taskQuery, usageQuery } from "@/lib/api/queries";
import { ROLES } from "@/lib/api/types";
import type { ApiResult, Phase, Task, UsageGroup, UsageLine } from "@/lib/api/types";
import {
  agoSeconds,
  asPathLine,
  formatAge,
  formatClock,
  formatCount,
  formatMs,
  formatUsd,
  gateTone,
  roleColorVar,
} from "@/lib/format";
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
  const learnings = useQuery(learningsQuery);
  const usage = useQuery(usageQuery(taskId));

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
  // One banner, every warning, in the order the api sent them — four reads
  // concatenated rather than four banners stacked. `docs/ui.md` *A degraded
  // backend is a banner, not a blank screen*.
  const warnings = [
    ...(task.data?.warnings ?? []),
    ...(debt.data?.warnings ?? []),
    ...(phases.data?.warnings ?? []),
    ...(learnings.data?.warnings ?? []),
    ...(usage.data?.warnings ?? []),
  ];
  // Debt ids are shaped `T-011-D1`, and `task_id` is that split. ADR 17.
  const taskDebt = (debt.data?.data ?? []).filter((d) => d.task_id === taskId);
  const timeline = byCycle(phases.data?.data ?? []);
  // What this task found, plus what its auditor is due to file: the last of
  // `learnings.droppable`'s four conditions, applied alone. `droppable` also
  // wants an unreviewed inbox entry with `scope: project`, so `merge-task`
  // drops a subset of these rows, not all of them (ADR 42). No sort — the api answers in the phase table's
  // order and that is the order worth keeping (ADR 42).
  const taskLearnings = (learnings.data?.data ?? []).filter(
    (l) => l.task === taskId || l.carried_by === taskId,
  );
  // `null` is a task with no `description.md`, and the header says so. No
  // subtitle while `t` is `null`: nothing has read a task to say it of.
  const subtitle = t ? (t.description ?? <Absent label="no description" />) : undefined;

  return (
    <AppShell>
      <PageHeader
        title={taskId}
        subtitle={subtitle}
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
              {t.owner ? (
                <Mono className="text-[11px]">{t.owner}</Mono>
              ) : (
                <Absent label="no owner" />
              )}
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

          <section>
            <h2 className="label-xs mb-2">what this task&apos;s calls cost</h2>
            <UsageRegion taskId={taskId} usage={usage} />
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

          <section className="panel px-3 py-3">
            <h2 className="label-xs mb-2">learnings this task filed or carries</h2>
            {/* Not "learnings handed to these phases", which is the half that
                cannot be built: the table a phase was handed is computed at
                dispatch from the directory as it stood, the harness fingerprint
                of the day and the cap, and nothing writes it down. So this names
                the record that is missing rather than a route, and names what it
                shows instead — `docs/ui.md` *A region with no route says which
                route, and when*, third sentence, and ADR 42. */}
            <p className="mb-2 text-[11px] text-muted-foreground">
              What this task&apos;s phases filed, and what its auditor is due to file. Which table a
              phase was actually handed is recomputed at every dispatch and recorded nowhere — each
              phase&apos;s own proposed-learnings prose is in the timeline above.
            </p>
            {/* A secondary read on a discrete block, so Broken takes the region
                and not the screen, like the debt region above it. */}
            {learnings.isError ? (
              <ErrorState
                title="The learnings tree did not answer"
                body="/api/learnings could not be read, so what this task filed is unknown rather than absent. Everything above comes from the task card and is unaffected; the entries themselves are files under the hive's learnings tree."
              />
            ) : taskLearnings.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                No learning entry names this task. A phase files one when a trap costs it time, and
                the auditor of the task that carries it promotes it into the project&apos;s own
                index.
              </p>
            ) : (
              <ul className="space-y-1.5">
                {taskLearnings.map((l) => (
                  <li key={l.ref} className="flex flex-wrap items-center gap-2 text-[11px]">
                    <Mono className="text-muted-foreground">{l.ref}</Mono>
                    <LearningStatus entry={l} />
                    {/* The two halves of the task-id relation are
                        two different facts: a carried row was found by another
                        task and this task's auditor is the one due to file it. */}
                    {l.task !== taskId && (
                      <span
                        className="rounded-sm border border-border-strong px-1 text-[10px] uppercase text-muted-foreground"
                        title={`Found by ${l.task || "an unrecorded task"}; this task's auditor is due to file it.`}
                      >
                        carried
                      </span>
                    )}
                    <span>{l.rule || <Absent label="no rule" />}</span>
                    <InPhaseTable entry={l} />
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
            <rect
              x={10}
              y={nodeY(i) - 11}
              width={110}
              height={22}
              rx={4}
              fill="var(--surface-2)"
              stroke="var(--border)"
            />
            <text
              x={20}
              y={nodeY(i) + 4}
              fill="var(--muted-foreground)"
              fontSize={11}
              fontFamily="var(--font-mono)"
            >
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

/* ── The usage region ─────────────────────────────────────────────────────
 * `docs/plans/front.md` *The usage task*, ADR 54, ADR 64, ADR 67. Everything
 * below reads served values as `unknown`: the types are annotations, and the
 * log is a file a person may have edited. Nothing here sums, subtracts or
 * compares a figure: a group's numbers are the api's, and the card's two
 * counts (retries not included, lines carrying a `measured` that is neither
 * true nor false) are counts of lines, not of money or tokens.
 */

type Rec = { [K in keyof UsageLine | keyof UsageGroup]?: unknown };
const isRec = (v: unknown): v is Rec => typeof v === "object" && v !== null && !Array.isArray(v);

function UsageRegion({
  taskId,
  usage,
}: {
  taskId: string;
  usage: {
    isLoading: boolean;
    isError: boolean;
    data?: ApiResult<UsageGroup[] | null> | undefined;
  };
}) {
  if (usage.isLoading) return null;
  if (usage.isError) {
    return (
      <ErrorState
        title={`/api/tasks/${taskId}/usage did not answer`}
        body="What this task's calls cost is unknown rather than absent. Everything else on this page comes from other reads and is unaffected."
      />
    );
  }
  const data: unknown = usage.data?.data;
  if (data === null) {
    const why = (usage.data?.warnings ?? []).join(" ");
    return (
      <ErrorState
        title="The usage log could not be read"
        body={why || "The log exists and the api could not read it; no reason was served."}
      />
    );
  }
  if (!Array.isArray(data)) {
    return <Malformed label="usage" got={data} want="a list of attempts" />;
  }
  if (data.length === 0) {
    return (
      <EmptyState
        title="No claude call of this task has been recorded"
        body="The log is written once per call since T-024, so a task dispatched before that has none."
      />
    );
  }

  // Buckets by (role, round) in order of first appearance, `===` on both; a
  // group whose role or round is of the wrong shape is a bucket of its own.
  const buckets: { role: unknown; round: unknown; groups: unknown[]; solo: boolean }[] = [];
  for (const g of data as unknown[]) {
    const role = isRec(g) ? g.role : undefined;
    const round = isRec(g) ? g.round : undefined;
    const sound =
      isRec(g) && typeof role === "string" && (round === null || typeof round === "number");
    const found = sound
      ? buckets.find((b) => !b.solo && b.role === role && b.round === round)
      : undefined;
    if (found) found.groups.push(g);
    else buckets.push({ role, round, groups: [g], solo: !sound });
  }

  return (
    <div className="space-y-3">
      {buckets.map((b, i) => (
        <div key={i} className="space-y-2">
          <div className="flex items-center gap-2">
            {typeof b.role !== "string" ? (
              <Malformed label="role" got={b.role} want="a string" />
            ) : (ROLES as readonly string[]).includes(b.role) ? (
              <RoleBadge role={b.role as (typeof ROLES)[number]} />
            ) : (
              <Mono className="text-[11px]">{b.role}</Mono>
            )}
            {typeof b.round === "number" && <span className="label-xs">round {b.round}</span>}
          </div>
          <ul className="space-y-2">
            {b.groups.map((g, j) => (
              <UsageGroupRow
                key={j}
                group={g}
                attempt={b.groups.length > 1 ? `attempt ${j + 1} of ${b.groups.length}` : null}
                previous={j > 0 ? b.groups[j - 1] : undefined}
              />
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

const NO_RESULT = "no result: crashed or timed out";

/** A served number through `format`, or what is wrong with it; never a computed one. */
function Figure({
  label,
  value,
  format,
}: {
  label: string;
  value: unknown;
  format: (n: number) => string;
}) {
  return (
    <span className="text-[11px]">
      <span className="label-xs mr-1">{label}</span>
      {value === null || value === undefined ? (
        <Absent />
      ) : typeof value !== "number" || !Number.isFinite(value) ? (
        <Malformed got={value} want="a number" />
      ) : (
        <Mono>{format(value)}</Mono>
      )}
    </span>
  );
}

const NO_MODEL_KEY = "written before models were logged";

function MutedText({ children }: { children: string }) {
  return <span className="text-[11px] italic text-muted-foreground">{children}</span>;
}

/**
 * `model` and `models_used` of one line as served (ADR 68, ADR 69): a missing key, `null` and
 * `[]` read as three different things, and nothing is compared or derived.
 */
function ModelFields({ line }: { line: Rec }) {
  const model = line.model;
  const used = line.models_used;
  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
      <span className="text-[11px]">
        <span className="label-xs mr-1">model</span>
        {model === undefined ? (
          <MutedText>{NO_MODEL_KEY}</MutedText>
        ) : model === null ? (
          <MutedText>CLI default</MutedText>
        ) : typeof model === "string" ? (
          <Mono>{model}</Mono>
        ) : (
          <Malformed label="model" got={model} want="a string or null" />
        )}
      </span>
      <span className="text-[11px]">
        <span className="label-xs mr-1">models used</span>
        {used === undefined ? (
          <MutedText>{NO_MODEL_KEY}</MutedText>
        ) : used === null ? (
          <Absent />
        ) : !Array.isArray(used) ? (
          <Malformed label="models_used" got={used} want="a list or null" />
        ) : used.length === 0 ? (
          <span className="text-[11px] text-muted-foreground">empty list</span>
        ) : (
          <span className="inline-flex flex-wrap gap-1">
            {used.map((m: unknown, i: number) =>
              typeof m === "string" ? (
                <Mono key={i} className="rounded-sm border border-border px-1 text-[10px]">
                  {m}
                </Mono>
              ) : (
                <Malformed key={i} got={m} want="a model name" />
              ),
            )}
          </span>
        )}
      </span>
    </span>
  );
}

/** The figures of one line; `costLabel` heads the cost, which is never summed here. */
function LineFigures({ line, costLabel }: { line: Rec; costLabel: string }) {
  if (line.measured === false) {
    return <span className="text-[11px] italic text-muted-foreground">{NO_RESULT}</span>;
  }
  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
      {typeof line.measured !== "boolean" && (
        <Malformed label="measured" got={line.measured} want="true or false" />
      )}
      <Figure label="input tokens" value={line.input_tokens} format={formatCount} />
      <Figure label="output tokens" value={line.output_tokens} format={formatCount} />
      <Figure label="cache read" value={line.cache_read_input_tokens} format={formatCount} />
      <Figure
        label="cache creation"
        value={line.cache_creation_input_tokens}
        format={formatCount}
      />
      <Figure label="turns" value={line.num_turns} format={formatCount} />
      <Figure label="duration" value={line.duration_ms} format={formatMs} />
      <Figure label={costLabel} value={line.total_cost_usd} format={formatUsd} />
      {line.is_error === true && (
        <span className={cn("rounded-sm border px-1 text-[10px] uppercase", gateTone.warning)}>
          is_error
        </span>
      )}
    </span>
  );
}

function UsageGroupRow({
  group,
  attempt,
  previous,
}: {
  group: unknown;
  attempt: string | null;
  previous: unknown;
}) {
  if (!isRec(group)) {
    return (
      <li className="panel px-3 py-2">
        <Malformed label="attempt" got={group} want="an object" />
      </li>
    );
  }
  const prev = isRec(previous) ? previous : null;
  const failedOver =
    prev !== null && typeof group.account === "string" && prev.account !== group.account;
  const { call_count: callCount, measured_calls: measured, unmeasured_calls: unmeasured } = group;
  // The card's two counts, of lines: retries not included, and lines whose
  // `measured` is neither true nor false. Not figures, so not "computing".
  const retries = typeof callCount === "number" ? callCount - 1 : 0;
  const neither =
    typeof callCount === "number" && typeof measured === "number" && typeof unmeasured === "number"
      ? Math.max(0, callCount - measured - unmeasured)
      : 0;
  const calls = group.calls;
  const phaseCall = group.phase_call;

  return (
    <li className="panel px-3 py-2">
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        {attempt && <span className="label-xs">{attempt}</span>}
        {failedOver && (
          <span className="rounded-sm border border-border-strong px-1 text-[10px] text-muted-foreground">
            failover from {String(prev.account)}
          </span>
        )}
        {typeof group.account === "string" ? (
          <Mono>{group.account}</Mono>
        ) : (
          <Malformed label="account" got={group.account} want="a string" />
        )}
        {Array.isArray(group.call_kinds) ? (
          group.call_kinds.map((k: unknown, i: number) =>
            typeof k === "string" ? (
              <Mono key={i} className="rounded-sm border border-border px-1 text-[10px]">
                {k}
              </Mono>
            ) : (
              <Malformed key={i} got={k} want="a string" />
            ),
          )
        ) : (
          <Malformed label="call_kinds" got={group.call_kinds} want="a list" />
        )}
        {typeof callCount === "number" ? (
          <span>{callCount} calls</span>
        ) : (
          <Malformed label="call_count" got={callCount} want="a number" />
        )}
        {typeof unmeasured === "number" ? (
          unmeasured > 0 && <span>{unmeasured} unmeasured</span>
        ) : (
          <Malformed label="unmeasured_calls" got={unmeasured} want="a number" />
        )}
        {neither > 0 && <span>{neither} lines carry a measured that is neither</span>}
      </div>
      {prev !== null && failedOver && <FailedOverEnding previous={prev} />}

      <div className="mt-2 rounded-sm border border-border bg-surface-2 px-2 py-1.5">
        {phaseCall === null ? (
          <span className="text-[11px] italic text-muted-foreground">
            no phase line in this attempt
          </span>
        ) : !isRec(phaseCall) ? (
          <Malformed label="phase_call" got={phaseCall} want="an object" />
        ) : (
          <FirstCall line={phaseCall} retries={retries} />
        )}
      </div>

      <div className="mt-2">
        {Array.isArray(calls) ? (
          <Collapsible title={`calls (${calls.length})`}>
            <ul className="space-y-1.5">
              {calls.map((c: unknown, i: number) => (
                <CallLine key={i} line={c} />
              ))}
            </ul>
          </Collapsible>
        ) : (
          <Malformed label="calls" got={calls} want="a list" />
        )}
      </div>
    </li>
  );
}

/** How the earlier attempt of a failover ended: its last line says why. */
function FailedOverEnding({ previous }: { previous: Rec }) {
  const calls = previous.calls;
  const last = Array.isArray(calls) ? calls[calls.length - 1] : undefined;
  if (!isRec(last)) return null;
  return (
    <p className="mt-1 text-[11px] text-muted-foreground">
      {String(previous.account)} ended:{" "}
      {last.measured === false
        ? NO_RESULT
        : last.is_error === true
          ? "is_error on its last line"
          : "its last line carries no error"}
    </p>
  );
}

function FirstCall({ line, retries }: { line: Rec; retries: number }) {
  const label =
    retries > 0
      ? `first call; ${retries} ${retries === 1 ? "retry" : "retries"} not included`
      : "first call";
  return (
    <div className="space-y-1">
      <p className="label-xs">{label}</p>
      <ModelFields line={line} />
      <LineFigures line={line} costLabel="cost" />
    </div>
  );
}

function CallLine({ line }: { line: unknown }) {
  if (!isRec(line)) {
    return (
      <li>
        <Malformed got={line} want="an object" />
      </li>
    );
  }
  const at = line.recorded_at;
  return (
    <li className="space-y-0.5 text-[11px]">
      <div className="flex flex-wrap items-center gap-2">
        {typeof line.call === "string" ? (
          <Mono>{line.call}</Mono>
        ) : (
          <Malformed label="call" got={line.call} want="a string" />
        )}
        {typeof at === "string" ? (
          <span className="text-[10px] text-muted-foreground" title={at}>
            {formatClock(at)}
          </span>
        ) : (
          <Malformed label="recorded_at" got={at} want="a string" />
        )}
        {line.session_id === null || line.session_id === undefined ? (
          <Absent label="no session id" />
        ) : typeof line.session_id === "string" ? (
          <Mono className="text-[10px] text-muted-foreground">{line.session_id}</Mono>
        ) : (
          <Malformed label="session_id" got={line.session_id} want="a string" />
        )}
      </div>
      <ModelFields line={line} />
      <LineFigures line={line} costLabel="session cost so far" />
    </li>
  );
}
