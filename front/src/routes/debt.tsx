import { useMemo, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import {
  Absent,
  BrokenBanner,
  EmptyState,
  ErrorState,
  Mono,
  PageHeader,
  WarningBanner,
} from "@/components/console/primitives";
import { debtQuery, tasksQuery } from "@/lib/api/queries";
import type { DebtEntry, Task } from "@/lib/api/types";
import {
  duplicateWarning,
  itemState,
  itemsByDebtId,
  matchesQuery,
  pairEntry,
  stateLabel,
  surfaceOverlaps,
  tasksInPlay,
  type InPlay,
} from "@/lib/debt";
import { useSearchHotkey } from "@/hooks/use-console";
import { scoped, useProjectScope } from "@/lib/projects";
import { TaskLink } from "@/components/console/project-scope";

export const Route = createFileRoute("/debt")({
  head: () => ({
    meta: [
      { title: "Debt index — harness operations console" },
      {
        name: "description",
        content:
          "Every row of the debt index grouped by the task that declared it, open debt first and resolved rows below.",
      },
      { property: "og:title", content: "Debt index — harness operations console" },
      { property: "og:description", content: "What the harness knowingly left unfinished." },
    ],
  }),
  component: DebtPage,
});

/** Rows that do not split into a task id still have to go somewhere. */
const NO_TASK = "unattributed";

/**
 * Where the pairing of entries with backlog items stands. The tasks read is a
 * join, not the screen's own read: if it fails the index still renders, and the
 * item cell says the pairing is unavailable — never "not imported", which would
 * be a claim about every entry the read could not see. ADR 141.
 */
type PairingRead =
  | { state: "loading" }
  | { state: "broken" }
  | { state: "ready"; byId: Map<string, Task[]>; inPlay: InPlay[] };

/**
 * The debt index. On the harness's own screen it is the console project's; under
 * `/p/$slug/debt` (ADR 210) it is the index in that project's clone, paired with
 * that project's backlog items only.
 */
export function DebtPage() {
  const project = useProjectScope();
  const debt = useQuery(debtQuery(project));
  // The same read the Board and the Backlog make; this screen only reads it.
  const tasks = useQuery(tasksQuery);
  const searchRef = useSearchHotkey();
  const [q, setQ] = useState("");

  const pairing: PairingRead = useMemo(
    () =>
      tasks.isError
        ? { state: "broken" }
        : tasks.data
          ? {
              state: "ready",
              byId: itemsByDebtId(scoped(tasks.data.data, project)),
              inPlay: tasksInPlay(scoped(tasks.data.data, project)),
            }
          : { state: "loading" },
    [tasks.isError, tasks.data, project],
  );

  // An open entry's item id and state words are matched only once the pairing is
  // in, since only then does the row draw them (`lib/debt.ts:matchesQuery`).
  const rows = useMemo(
    () =>
      (debt.data?.data ?? []).filter((d) =>
        matchesQuery(
          d,
          q,
          pairing.state === "ready" && !d.resolved ? pairEntry(d.id, pairing.byId) : null,
        ),
      ),
    [debt.data, q, pairing],
  );

  // The collection as the read served it, which is what decides between the two
  // bare states below. Plain and unmemoised: it feeds no hook dependency, so a
  // fresh `?? []` array on each render costs nothing here.
  const served = debt.data?.data ?? [];

  // The partition is over `resolved` and there is no severity: the index has no
  // such column, and `docs/decisions.md` ADR 17 already refused to grow one — the
  // four-state lifecycle this screen used to colour by is a debt's life inside one
  // task's handoffs, not a column of the index. `resolved` is best-effort, from a
  // `**resolved` prefix on the **what** cell, and a resolved row stays in the
  // index because a fix can be reverted.
  const open = useMemo(() => rows.filter((d) => !d.resolved), [rows]);
  const resolved = useMemo(() => rows.filter((d) => d.resolved), [rows]);

  const brokenReads =
    pairing.state === "broken"
      ? [
          "/api/tasks did not answer, so no open entry can show its backlog item or " +
            "whether it was accepted. The entries below come from /api/debt and are unaffected.",
        ]
      : [];

  return (
    <AppShell>
      <PageHeader
        title={project === null ? "Debt index" : `Debt index · ${project}`}
        subtitle="Open debt first, resolved below. A resolved row stays in the index — a fix can be reverted."
        right={<RefreshedAt at={debt.dataUpdatedAt} />}
      />

      <div className="border-b border-border px-4 py-2">
        <input
          ref={searchRef}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Filter debt…  /"
          className="mono w-64 rounded-sm border border-border bg-surface px-2 py-1 text-[11px] outline-none focus:border-ring"
        />
      </div>

      <BrokenBanner reads={brokenReads} />

      <WarningBanner warnings={[...(debt.data?.warnings ?? []), ...(tasks.data?.warnings ?? [])]} />

      {debt.isError ? (
        <ErrorState
          title="The debt index could not be read"
          body="The harness API returned an error. Treat the board's debt counts as stale until this screen loads."
        />
      ) : debt.isLoading ? (
        <p className="px-4 py-6 text-xs text-muted-foreground">Reading debt…</p>
      ) : rows.length === 0 ? (
        // An empty index and a filter that matched nothing are two facts.
        // `docs/ui.md` *Absent, empty and broken are three different things*.
        <DebtEmpty served={served.length} query={q} />
      ) : (
        <div className="space-y-4 p-4">
          <Partition title="Open" rows={open} pairing={pairing} />
          <Partition title="Resolved" rows={resolved} muted />
        </div>
      )}
    </AppShell>
  );
}

/**
 * Which of the two bare states this screen is in, over the served index and not
 * over the filtered list — the order `docs/ui.md` fixes, and the same shape as
 * `LearningsEmpty` in `./learnings`. Exported so the branch renders without a
 * router or a query client (`docs/decisions.md` ADR 44).
 */
export function DebtEmpty({ served, query }: { served: number; query: string }) {
  if (served === 0)
    return (
      <EmptyState
        title="The debt index is empty"
        body="No phase has declared debt. When one knowingly leaves something unfinished it will show here with a fix and a card."
      />
    );
  // Both partitions, because a resolved row stays in the index and clearing the
  // box brings it back too.
  return (
    <EmptyState
      title={`No debt row matches “${query}”`}
      body={`Clear the filter box to see ${served === 1 ? "the one row" : `all ${served} rows`} again, open and resolved. The filter reads the id, the what, the where and the card, and an open row's item and its state.`}
    />
  );
}

/**
 * One half of the partition, grouped by the task that declared each row.
 *
 * The task link lives on the group heading, which is the only place on this screen
 * that holds a task id: `task_id` is a split of the debt id (`T-011-D1` is task
 * `T-011`, ADR 17) and the `card` column is the index's own `card` cell, not a task id.
 */
function Partition({
  title,
  rows,
  muted = false,
  pairing,
}: {
  title: string;
  rows: DebtEntry[];
  muted?: boolean;
  /** Given for the open half only: a resolved row's item is not this screen's question. */
  pairing?: PairingRead | undefined;
}) {
  const groups = useMemo(() => {
    const map = new Map<string, DebtEntry[]>();
    for (const d of rows) {
      const key = d.task_id ?? NO_TASK;
      map.set(key, [...(map.get(key) ?? []), d]);
    }
    // The unattributed group last: an id that does not split is a row to look at,
    // not one to lead with, and dropping it would lose it entirely.
    return [...map.entries()].sort(([a], [b]) =>
      a === NO_TASK ? 1 : b === NO_TASK ? -1 : a.localeCompare(b),
    );
  }, [rows]);

  if (rows.length === 0) return null;

  return (
    <section className="space-y-3">
      <h2 className="label-xs">
        {title} · {rows.length}
      </h2>
      {groups.map(([taskId, entries]) => (
        <section key={taskId} className={muted ? "panel opacity-70" : "panel"}>
          <h3 className="flex items-center gap-2 border-b border-border px-3 py-1.5">
            {taskId === NO_TASK ? (
              <span className="text-[11px] font-semibold text-muted-foreground">
                not attributable to a task id
              </span>
            ) : (
              <TaskLink taskId={taskId}>
                <Mono className="text-[11px] font-semibold hover:underline">{taskId}</Mono>
              </TaskLink>
            )}
            <span className="text-[10px] text-muted-foreground">{entries.length} entries</span>
          </h3>
          <DebtTable rows={entries} pairing={pairing} />
        </section>
      ))}
    </section>
  );
}

function DebtTable({ rows, pairing }: { rows: DebtEntry[]; pairing?: PairingRead | undefined }) {
  const heads = ["id", "what", "where", "fix", "card", ...(pairing ? ["item"] : [])];
  return (
    <table className="w-full text-[11px]">
      <thead>
        <tr className="border-b border-border text-left">
          {heads.map((h) => (
            <th key={h} className="label-xs px-3 py-1.5 font-normal">
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((d) => (
          <tr key={d.id} className="border-b border-border/50 last:border-0 hover:bg-surface-2/50">
            <td className="mono px-3 py-1.5 text-muted-foreground">{d.id}</td>
            <td className="px-3 py-1.5">{d.what}</td>
            <td className="mono px-3 py-1.5 text-muted-foreground">{d.where}</td>
            <td className="px-3 py-1.5 text-muted-foreground">{d.fix}</td>
            {/* Text, not a link: this is the index's `card` column
                (`docs/debt/README.md`), not a Kanban board card id, and since
                ADR 59 every row's is the literal `none` because nothing mirrors
                a row. It was never a task id, so linking it to /tasks/$taskId
                was a guess. */}
            <td className="mono px-3 py-1.5">
              {d.card && d.card !== "none" ? d.card : <Absent label="no card" />}
            </td>
            {pairing && (
              <td className="px-3 py-1.5">
                <div data-testid={`item-${d.id}`}>
                  <ItemCell debtId={d.id} pairing={pairing} />
                </div>
                {pairing.state === "ready" && <InPlayNote entry={d} inPlay={pairing.inPlay} />}
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * An open entry's backlog item and what became of it: a link to the item as the
 * Backlog screen links it, and its state (`lib/debt.ts:stateLabel`). Two items
 * with one `debt_id` are both drawn under a warning, since picking one would
 * hide the fault.
 */
function ItemCell({ debtId, pairing }: { debtId: string; pairing: PairingRead }) {
  if (pairing.state === "loading") return <Absent label="reading items…" />;
  if (pairing.state === "broken") return <Absent label="pairing unavailable" />;
  const p = pairEntry(debtId, pairing.byId);
  if (p.items.length === 0) return <Absent label={stateLabel({ kind: "not_imported" })} />;
  const warning = duplicateWarning(debtId, p);
  return (
    <div className="space-y-0.5">
      {warning && (
        <p role="alert" className="text-[10px] text-warning">
          {warning}
        </p>
      )}
      {p.items.map(({ task, state }) => (
        <div key={task.task_id} className="flex items-center gap-2">
          <TaskLink taskId={task.task_id}>
            <Mono className="font-semibold hover:underline">{task.task_id}</Mono>
          </TaskLink>
          <span className={state.kind === "accepted" ? "text-foreground" : "text-muted-foreground"}>
            {stateLabel(state)}
          </span>
        </div>
      ))}
    </div>
  );
}

/**
 * The tasks on the board, not merged, whose *Surface* this open entry's *where*
 * falls on (`lib/debt.ts:surfaceOverlaps`; `docs/plans/board-service.md`
 * section 5, *Debt on a surface already in play*). A pointer only: folding the
 * debt into that task is the person's call, made on the task, and nothing here
 * writes. Drawn under the item cell, and nothing at all when no task matches.
 */
function InPlayNote({ entry, inPlay }: { entry: DebtEntry; inPlay: InPlay[] }) {
  const tasks = surfaceOverlaps(entry, inPlay);
  if (tasks.length === 0) return null;
  return (
    <div className="mt-1 space-y-0.5 text-[10px]" data-testid={`in-play-${entry.id}`}>
      <p className="text-muted-foreground">where falls on the surface of</p>
      {tasks.map((task) => (
        <div key={task.task_id} className="flex items-center gap-2">
          <TaskLink taskId={task.task_id}>
            <Mono className="font-semibold hover:underline">{task.task_id}</Mono>
          </TaskLink>
          <span className="text-muted-foreground">{stateLabel(itemState(task))}</span>
        </div>
      ))}
    </div>
  );
}
