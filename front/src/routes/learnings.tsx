import { useMemo, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { AppShell, RefreshedAt } from "@/components/console/app-shell";
import { Banner, EmptyState, ErrorState, Mono, PageHeader } from "@/components/console/primitives";
import { learningsQuery } from "@/lib/api/queries";
import { LEARNING_TABLE_CAP, type LearningEntry } from "@/lib/api/types";
import { useSearchHotkey } from "@/hooks/use-console";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/learnings")({
  head: () => ({
    meta: [
      { title: "Learnings inbox — harness operations console" },
      {
        name: "description",
        content:
          "Confirmed, unconfirmed and refuted learnings, showing which rows still reach a running phase.",
      },
      { property: "og:title", content: "Learnings inbox — harness operations console" },
      { property: "og:description", content: "What the harness believes, and how sure it is." },
    ],
  }),
  component: LearningsPage,
});

const statusRank: Record<LearningEntry["status"], number> = {
  confirmed: 0,
  unconfirmed: 1,
  refuted: 2,
};

const statusTone: Record<LearningEntry["status"], string> = {
  confirmed: "text-success border-success/50 bg-success/10",
  unconfirmed: "text-warning border-warning/50 bg-warning/10",
  refuted: "text-destructive border-destructive/50 bg-destructive/10",
};

function LearningsPage() {
  const learnings = useQuery(learningsQuery);
  const searchRef = useSearchHotkey();
  const [q, setQ] = useState("");

  const rows = useMemo(() => {
    const list = (learnings.data ?? []).filter((l) =>
      q ? `${l.id} ${l.trigger} ${l.body}`.toLowerCase().includes(q.toLowerCase()) : true,
    );
    return [...list].sort(
      (a, b) =>
        Number(a.retired) - Number(b.retired) || statusRank[a.status] - statusRank[b.status],
    );
  }, [learnings.data, q]);

  const active = (learnings.data ?? []).filter((l) => !l.retired);

  return (
    <AppShell>
      <PageHeader
        title="Learnings inbox"
        subtitle={`Only ${LEARNING_TABLE_CAP} rows are ever handed to a running phase.`}
        right={<RefreshedAt at={learnings.dataUpdatedAt} />}
      />

      {active.length > LEARNING_TABLE_CAP && (
        <Banner tone="warning">
          {active.length} active rows, but a phase only ever receives {LEARNING_TABLE_CAP}. The{" "}
          {active.length - LEARNING_TABLE_CAP} lowest-ranked rows are not reaching any phase —
          retire or confirm some to control which ones do.
        </Banner>
      )}

      <div className="border-b border-border px-4 py-2">
        <input
          ref={searchRef}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Filter learnings…  /"
          className="mono w-64 rounded-sm border border-border bg-surface px-2 py-1 text-[11px] outline-none focus:border-ring"
        />
      </div>

      {learnings.isError ? (
        <ErrorState
          title="The learnings table could not be read"
          body="Phases will still run, but they are receiving whatever the harness cached last. Check the API before trusting this screen."
        />
      ) : learnings.isLoading ? (
        <p className="px-4 py-6 text-xs text-muted-foreground">Reading learnings…</p>
      ) : rows.length === 0 ? (
        <EmptyState
          title="No learnings recorded"
          body="Auditor phases write rows here after gate findings. Nothing has been recorded yet."
        />
      ) : (
        <table className="w-full text-[11px]">
          <thead className="sticky top-0 bg-background">
            <tr className="border-b border-border text-left">
              {["id", "scope", "status", "trigger", "body", "in table"].map((h) => (
                <th key={h} className="label-xs px-4 py-2 font-normal">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((l, i) => {
              const inTable = !l.retired && i < LEARNING_TABLE_CAP;
              return (
                <tr
                  key={l.id}
                  className={cn(
                    "border-b border-border/50 hover:bg-surface-2/50",
                    l.retired && "opacity-45",
                  )}
                >
                  <td className="mono px-4 py-1.5 text-muted-foreground">{l.id}</td>
                  <td className="px-4 py-1.5">
                    <span className="rounded-sm border border-border-strong px-1 text-[10px] uppercase text-muted-foreground">
                      {l.scope}
                    </span>
                  </td>
                  <td className="px-4 py-1.5">
                    <span
                      className={cn(
                        "rounded-sm border px-1.5 py-[1px] text-[10px] uppercase",
                        statusTone[l.status],
                      )}
                    >
                      {l.status}
                    </span>
                  </td>
                  <td className="px-4 py-1.5 text-muted-foreground">{l.trigger}</td>
                  <td className="px-4 py-1.5">{l.body}</td>
                  <td className="px-4 py-1.5">
                    {l.retired ? (
                      <span
                        className="text-[10px] italic text-muted-foreground"
                        title="Retired rows are never handed to a running phase."
                      >
                        retired — not handed to phases
                      </span>
                    ) : inTable ? (
                      <Mono className="text-[10px] text-success">in table</Mono>
                    ) : (
                      <span className="text-[10px] text-warning">
                        over the {LEARNING_TABLE_CAP} cap
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </AppShell>
  );
}
