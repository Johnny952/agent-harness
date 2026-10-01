import { useMemo, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import {
  Absent,
  EmptyState,
  ErrorState,
  Mono,
  PageHeader,
  WarningBanner,
} from "@/components/console/primitives";
import { debtQuery } from "@/lib/api/queries";
import type { DebtEntry } from "@/lib/api/types";
import { useSearchHotkey } from "@/hooks/use-console";

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

function DebtPage() {
  const debt = useQuery(debtQuery);
  const searchRef = useSearchHotkey();
  const [q, setQ] = useState("");

  const rows = useMemo(
    () =>
      (debt.data?.data ?? []).filter((d) =>
        q ? `${d.id} ${d.what} ${d.where} ${d.card}`.toLowerCase().includes(q.toLowerCase()) : true,
      ),
    [debt.data, q],
  );

  // The partition is over `resolved` and there is no severity: the index has no
  // such column, and `docs/decisions.md` ADR 17 already refused to grow one — the
  // four-state lifecycle this screen used to colour by is a debt's life inside one
  // task's handoffs, not a column of the index. `resolved` is best-effort, from a
  // `**resolved` prefix on the **what** cell, and a resolved row stays in the
  // index because a fix can be reverted.
  const open = useMemo(() => rows.filter((d) => !d.resolved), [rows]);
  const resolved = useMemo(() => rows.filter((d) => d.resolved), [rows]);

  return (
    <AppShell>
      <PageHeader
        title="Debt index"
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

      <WarningBanner warnings={debt.data?.warnings ?? []} />

      {debt.isError ? (
        <ErrorState
          title="The debt index could not be read"
          body="The harness API returned an error. Treat the board's debt counts as stale until this screen loads."
        />
      ) : debt.isLoading ? (
        <p className="px-4 py-6 text-xs text-muted-foreground">Reading debt…</p>
      ) : rows.length === 0 ? (
        <EmptyState
          title="The debt index is empty"
          body="No phase has declared debt. When one knowingly leaves something unfinished it will show here with a fix and a card."
        />
      ) : (
        <div className="space-y-4 p-4">
          <Partition title="Open" rows={open} />
          <Partition title="Resolved" rows={resolved} muted />
        </div>
      )}
    </AppShell>
  );
}

/**
 * One half of the partition, grouped by the task that declared each row.
 *
 * The task link lives on the group heading, which is the only place on this screen
 * that holds a task id: `task_id` is a split of the debt id (`T-011-D1` is task
 * `T-011`, ADR 17) and the `card` column is a *board* card id, not a task id.
 */
function Partition({
  title,
  rows,
  muted = false,
}: {
  title: string;
  rows: DebtEntry[];
  muted?: boolean;
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
              <Link to="/tasks/$taskId" params={{ taskId }}>
                <Mono className="text-[11px] font-semibold hover:underline">{taskId}</Mono>
              </Link>
            )}
            <span className="text-[10px] text-muted-foreground">{entries.length} entries</span>
          </h3>
          <DebtTable rows={entries} />
        </section>
      ))}
    </section>
  );
}

function DebtTable({ rows }: { rows: DebtEntry[] }) {
  return (
    <table className="w-full text-[11px]">
      <thead>
        <tr className="border-b border-border text-left">
          {["id", "what", "where", "fix", "card"].map((h) => (
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
            {/* Text, not a link: this is the id of a card on the Kanban board the
                dispatcher writes to, and on a harness with none configured every
                row's is the literal `none`. Linking it to /tasks/$taskId was a
                guess that the two ids were the same thing. */}
            <td className="mono px-3 py-1.5">
              {d.card && d.card !== "none" ? (
                d.card
              ) : (
                <Absent label="no card" />
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
