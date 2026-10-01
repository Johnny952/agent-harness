import { useMemo, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import { EmptyState, ErrorState, Mono, PageHeader } from "@/components/console/primitives";
import { debtQuery } from "@/lib/api/queries";
import type { DebtEntry } from "@/lib/api/types";
import { useSearchHotkey } from "@/hooks/use-console";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/debt")({
  head: () => ({
    meta: [
      { title: "Debt index — harness operations console" },
      {
        name: "description",
        content:
          "Every declared debt entry grouped by task, with blocking entries pinned to the top.",
      },
      { property: "og:title", content: "Debt index — harness operations console" },
      { property: "og:description", content: "What the harness knowingly left unfinished." },
    ],
  }),
  component: DebtPage,
});

const stateTone: Record<DebtEntry["state"], string> = {
  blocking: "text-destructive border-destructive/60 bg-destructive/15",
  declared: "text-warning border-warning/50 bg-warning/10",
  accepted: "text-success border-success/50 bg-success/10",
  rejected: "text-muted-foreground border-border-strong bg-surface-2",
};

function DebtPage() {
  const debt = useQuery(debtQuery);
  const searchRef = useSearchHotkey();
  const [q, setQ] = useState("");

  const rows = useMemo(
    () =>
      (debt.data ?? []).filter((d) =>
        q ? `${d.id} ${d.what} ${d.where} ${d.card}`.toLowerCase().includes(q.toLowerCase()) : true,
      ),
    [debt.data, q],
  );

  const blocking = rows.filter((d) => d.state === "blocking");
  const rest = rows.filter((d) => d.state !== "blocking");
  const groups = useMemo(() => {
    const map = new Map<string, DebtEntry[]>();
    for (const d of rest) map.set(d.task_id, [...(map.get(d.task_id) ?? []), d]);
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [rest]);

  return (
    <AppShell>
      <PageHeader
        title="Debt index"
        subtitle="Blocking entries are pinned — nothing merges past them."
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
          {blocking.length > 0 && (
            <section className="rounded-md border-2 border-destructive/60 bg-destructive/5">
              <h2 className="border-b border-destructive/40 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-widest text-destructive">
                Blocking · {blocking.length}
              </h2>
              <DebtTable rows={blocking} />
            </section>
          )}
          {groups.map(([taskId, entries]) => (
            <section key={taskId} className="panel">
              <h2 className="flex items-center gap-2 border-b border-border px-3 py-1.5">
                <Link to="/tasks/$taskId" params={{ taskId }}>
                  <Mono className="text-[11px] font-semibold hover:underline">{taskId}</Mono>
                </Link>
                <span className="text-[10px] text-muted-foreground">{entries.length} entries</span>
              </h2>
              <DebtTable rows={entries} />
            </section>
          ))}
        </div>
      )}
    </AppShell>
  );
}

function DebtTable({ rows }: { rows: DebtEntry[] }) {
  return (
    <table className="w-full text-[11px]">
      <thead>
        <tr className="border-b border-border text-left">
          {["id", "what", "where", "fix", "card", "state"].map((h) => (
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
            <td className="mono px-3 py-1.5">
              <Link to="/tasks/$taskId" params={{ taskId: d.card }} className="hover:underline">
                {d.card}
              </Link>
            </td>
            <td className="px-3 py-1.5">
              <span
                className={cn(
                  "rounded-sm border px-1.5 py-[1px] text-[10px] uppercase tracking-wide",
                  stateTone[d.state],
                )}
              >
                {d.state}
              </span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
